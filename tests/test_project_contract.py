"""Tests for project contracts — fail-closed enforcement."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentos.engine import AgentOSError
from agentos.project_contract import (
    Authorization,
    ContractEnforcer,
    ContractStatus,
    ContractStore,
    EvidenceRule,
    ProjectContract,
    activate,
    draft_contract,
)


class TestContractLifecycle(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.store = ContractStore(self.home)

    def test_draft_not_active(self) -> None:
        c = draft_contract("c1", "proj", ["execute"])
        self.store.save(c)
        self.assertIsNone(self.store.active_for_project("proj"))

    def test_activate_and_get(self) -> None:
        c = draft_contract("c1", "proj", ["execute"])
        activate(c, self.store)
        active = self.store.active_for_project("proj")
        self.assertIsNotNone(active)
        self.assertEqual(active.contract_id, "c1")
        self.assertEqual(active.status, ContractStatus.ACTIVE)

    def test_activate_violated_raises(self) -> None:
        c = draft_contract("c1", "proj", ["execute"])
        c.status = ContractStatus.VIOLATED
        with self.assertRaises(AgentOSError):
            activate(c, self.store)

    def test_activate_closed_raises(self) -> None:
        c = draft_contract("c1", "proj", ["execute"])
        c.status = ContractStatus.CLOSED
        with self.assertRaises(AgentOSError):
            activate(c, self.store)

    def test_round_trip(self) -> None:
        c = draft_contract(
            "c1", "proj", ["execute", "verify"],
            authorizations=["verify"],
            evidence=[EvidenceRule(kind="verification", min_count=1)],
        )
        self.store.save(c)
        loaded = self.store.get("c1")
        assert loaded is not None
        self.assertEqual(loaded.scope, ["execute", "verify"])
        self.assertEqual(loaded.authorizations[0].grant, "verify")


class TestEnforcement(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.store = ContractStore(self.home)
        self.enforcer = ContractEnforcer(self.store)

    def _active(self, **kwargs) -> ProjectContract:
        c = draft_contract("c1", "proj", ["execute"], **kwargs)
        return activate(c, self.store)

    def test_no_contract_blocks(self) -> None:
        ok, reason = self.enforcer.check_action("proj", "execute")
        self.assertFalse(ok)
        self.assertIn("no active contract", reason)

    def test_in_scope_allowed(self) -> None:
        self._active()
        ok, reason = self.enforcer.check_action(
            "proj", "execute", evidence_count={"verification": 1}
        )
        self.assertTrue(ok, reason)

    def test_out_of_scope_blocks_and_violates(self) -> None:
        c = self._active()
        ok, reason = self.enforcer.check_action("proj", "push")
        self.assertFalse(ok)
        self.assertIn("outside contract scope", reason)
        reloaded = self.store.get("c1")
        assert reloaded is not None
        self.assertEqual(reloaded.status, ContractStatus.VIOLATED)

    def test_free_only_blocks_paid(self) -> None:
        self._active(free_only=True)
        ok, reason = self.enforcer.check_action("proj", "execute", cost_estimate_usd=1.5)
        self.assertFalse(ok)
        self.assertIn("free_only", reason)

    def test_cost_ceiling(self) -> None:
        self._active(free_only=False, cost_ceiling_usd=2.0)
        ok, reason = self.enforcer.check_action("proj", "execute", cost_estimate_usd=3.0)
        self.assertFalse(ok)
        self.assertIn("cost ceiling", reason)

    def test_missing_evidence_blocks(self) -> None:
        self._active()
        ok, reason = self.enforcer.check_action(
            "proj", "execute", evidence_count={"verification": 0}
        )
        self.assertFalse(ok)
        self.assertIn("missing required evidence", reason)

    def test_evidence_present_allows(self) -> None:
        self._active()
        ok, reason = self.enforcer.check_action(
            "proj", "execute", evidence_count={"verification": 1}
        )
        self.assertTrue(ok, reason)

    def test_has_authorization(self) -> None:
        self._active(authorizations=["verify"])
        self.assertTrue(self.enforcer.has_authorization("proj", "verify"))
        self.assertFalse(self.enforcer.has_authorization("proj", "paid"))

    def test_expired_blocks(self) -> None:
        import time

        c = draft_contract("c1", "proj", ["execute"])
        c.expires_at = str(time.time() - 10)
        activate(c, self.store)
        ok, reason = self.enforcer.check_action("proj", "execute")
        self.assertFalse(ok)
        self.assertIn("expired", reason)
        reloaded = self.store.get("c1")
        assert reloaded is not None
        self.assertEqual(reloaded.status, ContractStatus.EXPIRED)

    def test_close(self) -> None:
        self._active()
        closed = self.enforcer.close("proj")
        assert closed is not None
        self.assertEqual(closed.status, ContractStatus.CLOSED)
        self.assertIsNone(self.store.active_for_project("proj"))


if __name__ == "__main__":
    unittest.main()
