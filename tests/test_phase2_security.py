"""Phase 2 security tests — fail-closed controls, no secrets, bounded execution."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agentos.autonomous_loop import AutonomousLoop, LoopLimits, LoopStatus
from agentos.project_contract import (
    ContractEnforcer,
    ContractStore,
    activate,
    draft_contract,
)


class TestTowerFailClosed(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        os.environ["AGENTOS_HOME"] = self.temp_dir
        from agentos.services import AgentOS

        self.app = AgentOS(home=self.temp_dir)
        from agentos.tower import Tower

        self.tower = Tower(self.app)

    def test_cancel_without_confirmation_does_not_cancel(self) -> None:
        obj = self.app.create_objective("to cancel")
        self.tower.selected = 0
        self.tower._stdscr = MagicMock()
        with patch.object(type(self.tower), "_confirm", return_value=False):
            self.tower._cancel_objective()
        reloaded = self.app.store.get_objective(obj.id)
        assert reloaded is not None
        self.assertNotEqual(reloaded.status.value, "CANCELLED")
        self.assertTrue(any("aborted" in m for m in self.tower.messages))

    def test_cancel_without_ui_does_not_cancel(self) -> None:
        obj = self.app.create_objective("to cancel")
        self.tower.selected = 0
        self.tower._stdscr = None
        self.tower._cancel_objective()
        reloaded = self.app.store.get_objective(obj.id)
        assert reloaded is not None
        self.assertNotEqual(reloaded.status.value, "CANCELLED")

    def test_confirm_requires_exactly_y(self) -> None:
        stdscr = MagicMock()
        stdscr.getmaxyx.return_value = (40, 100)
        for key in (ord("y"),):
            stdscr.getch.return_value = key
            self.assertTrue(self.tower._confirm(stdscr, "x"))
        for key in (ord("n"), ord("Y"), 27, ord("q"), 10):
            stdscr.getch.return_value = key
            self.assertFalse(self.tower._confirm(stdscr, "x"))

    def test_no_shell_true_in_tower_source(self) -> None:
        import agentos.tower as tower_mod

        source = Path(tower_mod.__file__).read_text()
        self.assertNotIn("shell=True", source)


class TestLoopSecurity(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        os.environ["AGENTOS_HOME"] = self.temp_dir
        from agentos.services import AgentOS

        self.app = AgentOS(home=self.temp_dir)
        self.loop = AutonomousLoop(self.app, home=self.app.home)

    def test_free_only_is_default(self) -> None:
        self.assertTrue(self.loop.limits.free_only)
        self.assertEqual(self.loop.limits.max_cost_usd, 0.0)

    def test_paid_blocked_without_authorization(self) -> None:
        self.assertFalse(self.loop.paid_execution_allowed(None))
        self.assertFalse(self.loop.paid_execution_allowed([]))
        self.assertFalse(self.loop.paid_execution_allowed([{"kind": "verify"}]))

    def test_paid_allowed_only_with_free_only_off_and_grant(self) -> None:
        self.loop.limits.free_only = False
        self.assertTrue(self.loop.paid_execution_allowed([{"grant": "paid"}]))

    def test_state_files_contain_no_secret_keys(self) -> None:
        self.loop.state.objective_id = "obj_test"
        self.loop._save()
        self.loop.save_limits()
        for name in ("loop_state.json", "loop_limits.json"):
            text = (self.app.home / name).read_text().lower()
            for banned in ("api_key", "apikey", "token", "password", "secret"):
                self.assertNotIn(banned, text)
            json.loads((self.app.home / name).read_text())  # valid JSON

    def test_history_size_never_unbounded_after_many_records(self) -> None:
        for i in range(300):
            self.loop._record("X", f"entry {i}", True)
        self.assertLessEqual(len(self.loop.state.history), 200)

    def test_limits_have_hard_maximums(self) -> None:
        limits = LoopLimits()
        self.assertGreater(limits.max_cycles, 0)
        self.assertGreater(limits.max_seconds, 0)
        self.assertLessEqual(limits.max_workers, 64)


class TestContractSecurity(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.store = ContractStore(self.home)
        self.enforcer = ContractEnforcer(self.store)

    def test_missing_contract_blocks_consequential_action(self) -> None:
        ok, _ = self.enforcer.check_action("proj", "execute", cost_estimate_usd=1.0)
        self.assertFalse(ok)

    def test_contract_files_contain_no_secrets(self) -> None:
        c = draft_contract("c1", "proj", ["execute"])
        activate(c, self.store)
        text = (self.store.dir / "c1.json").read_text().lower()
        for banned in ("api_key", "apikey", "password", "secret"):
            self.assertNotIn(banned, text)

    def test_violation_recorded_without_stack_trace_leak(self) -> None:
        c = draft_contract("c1", "proj", ["execute"])
        activate(c, self.store)
        self.enforcer.check_action("proj", "git.push")
        reloaded = self.store.get("c1")
        assert reloaded is not None
        self.assertEqual(len(reloaded.violations), 1)
        self.assertNotIn("Traceback", reloaded.violations[0]["detail"])

    def test_closed_contract_blocks(self) -> None:
        c = draft_contract("c1", "proj", ["execute"])
        activate(c, self.store)
        self.enforcer.close("proj")
        ok, _ = self.enforcer.check_action("proj", "execute")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
