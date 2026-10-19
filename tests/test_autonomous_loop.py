"""Tests for the bounded autonomous operating loop."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from agentos.autonomous_loop import (
    AutonomousLoop,
    LoopLimits,
    LoopStage,
    LoopStage as LS,
    LoopStatus,
    LOOP_ORDER,
)


class TestLoopLimits(unittest.TestCase):
    def test_defaults(self) -> None:
        limits = LoopLimits()
        self.assertEqual(limits.max_cycles, 5)
        self.assertTrue(limits.free_only)

    def test_round_trip(self) -> None:
        limits = LoopLimits(max_cycles=3, max_seconds=60.0, free_only=False)
        restored = LoopLimits.from_dict(json.loads(json.dumps(limits.to_dict())))
        self.assertEqual(restored.max_cycles, 3)
        self.assertFalse(restored.free_only)


class TestAutonomousLoop(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        os.environ["AGENTOS_HOME"] = self.temp_dir
        from agentos.services import AgentOS

        self.app = AgentOS(home=self.temp_dir)
        self.loop = AutonomousLoop(self.app, home=self.app.home)

    def test_state_persists(self) -> None:
        self.loop.state.status = LoopStatus.PAUSED
        self.loop._save()
        loop2 = AutonomousLoop(self.app, home=self.app.home)
        self.assertEqual(loop2.state.status, LoopStatus.PAUSED)

    def test_crash_recovery_marks_interrupted(self) -> None:
        self.loop.state.status = LoopStatus.RUNNING
        self.loop._save()
        loop2 = AutonomousLoop(self.app, home=self.app.home)
        self.assertEqual(loop2.state.status, LoopStatus.INTERRUPTED)
        self.assertIn("interrupted", loop2.state.last_error)

    def test_pause_resume(self) -> None:
        self.loop.state.status = LoopStatus.RUNNING
        self.loop.pause()
        self.assertEqual(self.loop.state.status, LoopStatus.PAUSED)
        self.loop.resume("obj_x")
        self.assertEqual(self.loop.state.status, LoopStatus.RUNNING)
        self.assertEqual(self.loop.state.objective_id, "obj_x")

    def test_interrupt(self) -> None:
        self.loop.state.status = LoopStatus.RUNNING
        self.loop.interrupt()
        self.assertEqual(self.loop.state.status, LoopStatus.INTERRUPTED)

    def test_paid_execution_blocked_when_free_only(self) -> None:
        allowed = self.loop.paid_execution_allowed([{"kind": "paid"}])
        self.assertFalse(allowed)
        self.loop.limits.free_only = False
        allowed = self.loop.paid_execution_allowed([{"kind": "paid"}])
        self.assertTrue(allowed)
        allowed = self.loop.paid_execution_allowed([])
        self.assertFalse(allowed)

    def test_cycle_limit_blocks_run(self) -> None:
        self.loop.limits.max_cycles = 0
        objective = self.app.create_objective("bounded test")
        report = self.loop.run(objective.id)
        self.assertEqual(report["final_status"], LoopStatus.LIMIT_REACHED)
        self.assertIn("cycle limit", report["termination"])

    def test_unknown_objective_raises(self) -> None:
        from agentos.engine import AgentOSError

        with self.assertRaises(AgentOSError):
            self.loop.run("obj_does_not_exist")

    def test_stage_order_is_ten_stages(self) -> None:
        self.assertEqual(len(LOOP_ORDER), 10)
        self.assertEqual(LOOP_ORDER[0], LoopStage.INSPECT)
        self.assertEqual(LOOP_ORDER[-1], LoopStage.COMPLETE)

    def test_full_run_safe_echo_objective(self) -> None:
        objective = self.app.create_objective(
            "loop echo test", operation="echo", params={"message": "hi"}
        )
        report = self.loop.run(objective.id)
        self.assertIn("stages", report)
        self.assertEqual(report["final_status"] in (LoopStatus.STOPPED, LoopStatus.LIMIT_REACHED), True)
        self.assertTrue(len(report["stages"]) >= 5)
        stages = [s["stage"] for s in report["stages"]]
        self.assertIn("INSPECT", stages)
        self.assertIn("ROUTE", stages)
        # state persisted for recovery
        self.assertTrue((self.app.home / "loop_state.json").exists())

    def test_history_bounded(self) -> None:
        for i in range(250):
            self.loop.state.history.append({"at": "t", "stage": "X", "detail": str(i), "ok": True})
        self.loop._record("X", "one more", True)
        self.assertLessEqual(len(self.loop.state.history), 200)

    def test_limits_save_load(self) -> None:
        self.loop.limits.max_cycles = 9
        self.loop.save_limits()
        loop2 = AutonomousLoop(self.app, home=self.app.home)
        self.assertEqual(loop2.limits.max_cycles, 9)


if __name__ == "__main__":
    unittest.main()
