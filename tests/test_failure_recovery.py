"""Failure recovery tests — real failure paths, recovery, and interrupt recovery."""

from __future__ import annotations

import os
import tempfile
import unittest

from agentos.autonomous_loop import AutonomousLoop, LoopStatus
from agentos.engine import AgentOSError
from agentos.models import ObjectiveState


class TestRecoverStateMachine(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        os.environ["AGENTOS_HOME"] = self.temp_dir
        from agentos.services import AgentOS

        self.app = AgentOS(home=self.temp_dir)

    def test_recover_unknown_objective_raises(self) -> None:
        with self.assertRaises(AgentOSError):
            self.app.recover("obj_missing")

    def test_recover_completed_is_noop(self) -> None:
        obj = self.app.create_objective(
            "ok", operation="echo", params={"message": "x"}
        )
        self.app.run(obj.id, force=True, timeout_seconds=60.0)
        obj2 = self.app.store.get_objective(obj.id)
        assert obj2 is not None
        self.assertEqual(obj2.status, ObjectiveState.COMPLETED)
        report = self.app.recover(obj.id)
        self.assertEqual(report.state, ObjectiveState.COMPLETED)
        self.assertIn("nothing to recover", report.next_action)

    def test_failed_objective_recoverable_after_fix(self) -> None:
        obj = self.app.create_objective(
            "broken", operation="definitely.missing.capability"
        )
        report = self.app.run(obj.id, force=True, timeout_seconds=30.0)
        self.assertEqual(report.state, ObjectiveState.BLOCKED)
        # recovery attempt still blocked (capability still missing)
        report2 = self.app.recover(obj.id)
        self.assertEqual(report2.state, ObjectiveState.BLOCKED)

    def test_blocked_then_recovered_when_capability_arrives(self) -> None:
        obj = self.app.create_objective(
            "deferred", operation="echo", params={"message": "hi"}
        )
        # simulate a temporary failure: mark failed then recover
        objective = self.app.store.get_objective(obj.id)
        assert objective is not None
        objective.status = ObjectiveState.FAILED
        self.app.store.save_objective(objective)
        report = self.app.recover(obj.id)
        self.assertEqual(report.state, ObjectiveState.COMPLETED)
        objective = self.app.store.get_objective(obj.id)
        assert objective is not None
        self.assertEqual(objective.verification_status.value, "VERIFIED")

    def test_bounded_recover_respects_max_attempts(self) -> None:
        from agentos.adapters.base import AdapterResult
        from agentos.engine import bounded_recover as module_bounded_recover
        from agentos.models import Objective

        objective = Objective(
            id="obj_x", title="x", description="x"
        )
        result = AdapterResult(ok=False, error="always fails")
        outcome = module_bounded_recover(objective, result, max_attempts=2)
        self.assertLessEqual(outcome.attempts, 2)
        self.assertIn(
            outcome.action, ("reject", "blocked", "backoff", "retry", "correct", "report")
        )


class TestInterruptRecovery(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        os.environ["AGENTOS_HOME"] = self.temp_dir
        from agentos.services import AgentOS

        self.app = AgentOS(home=self.temp_dir)

    def test_interrupt_then_resume_runs(self) -> None:
        loop = AutonomousLoop(self.app, home=self.app.home)
        loop.state.status = LoopStatus.RUNNING
        loop.interrupt()
        self.assertEqual(loop.state.status, LoopStatus.INTERRUPTED)
        obj = self.app.create_objective(
            "resume test", operation="echo", params={"message": "hi"}
        )
        loop.resume(obj.id)
        self.assertEqual(loop.state.status, LoopStatus.RUNNING)
        report = loop.run(obj.id)
        self.assertIn(
            report["final_status"],
            (LoopStatus.STOPPED, LoopStatus.LIMIT_REACHED),
        )

    def test_run_while_running_raises(self) -> None:
        loop = AutonomousLoop(self.app, home=self.app.home)
        loop._executing = True
        obj = self.app.create_objective("x")
        with self.assertRaises(AgentOSError):
            loop.run(obj.id)

    def test_crash_recovery_then_manual_resume(self) -> None:
        loop = AutonomousLoop(self.app, home=self.app.home)
        loop.state.status = LoopStatus.RUNNING
        loop._save()
        # simulate process restart
        loop2 = AutonomousLoop(self.app, home=self.app.home)
        self.assertEqual(loop2.state.status, LoopStatus.INTERRUPTED)
        obj = self.app.create_objective(
            "post-crash", operation="echo", params={"message": "hi"}
        )
        loop2.resume(obj.id)
        report = loop2.run(obj.id)
        self.assertNotEqual(report["final_status"], LoopStatus.RUNNING)


if __name__ == "__main__":
    unittest.main()
