"""Tests for the execution engine: the full vertical loop."""

import tempfile
import unittest
from pathlib import Path

from agentos.engine import AgentOSError, Engine, EngineOptions
from agentos.events import EventBus
from agentos.models import EventType, Objective, ObjectiveState, new_id
from agentos.registry import CapabilityRegistry
from agentos.store import Store


class EngineTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "agentos.db")
        self.bus = EventBus(self.store)
        self.registry = CapabilityRegistry(self.store, self.bus)
        self.engine = Engine(self.store, self.bus, self.registry)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def make_objective(self, title: str = "t", **kwargs) -> Objective:
        objective = Objective(
            id=new_id("obj"), title=title, description=kwargs.pop("description", "d"), **kwargs
        )
        self.store.save_objective(objective)
        return objective

    def event_types(self, objective_id: str) -> list[str]:
        return [
            e.event_type.value for e in self.store.list_events(objective_id=objective_id)
        ]


class TestSuccessfulLoop(EngineTestCase):
    def test_echo_objective_completes(self) -> None:
        objective = self.make_objective(
            "echo test", context={"operation": "echo", "params": {"hello": "world"}}
        )
        report = self.engine.run(objective.id)
        self.assertEqual(report.state, ObjectiveState.COMPLETED)
        stored = self.store.get_objective(objective.id)
        assert stored is not None
        self.assertEqual(stored.status, ObjectiveState.COMPLETED)
        self.assertEqual(stored.verification_status.value, "VERIFIED")
        self.assertIsNone(stored.next_action)
        self.assertTrue(len(stored.evidence) > 0)
        types = self.event_types(objective.id)
        for required in (
            "objective.started",
            "capability.selected",
            "execution.started",
            "execution.completed",
            "verification.started",
            "verification.passed",
            "objective.completed",
        ):
            self.assertIn(required, types)

    def test_shell_objective_completes(self) -> None:
        objective = self.make_objective(
            "run a command",
            context={"operation": "shell.run", "params": {"command": "echo shell-ok"}},
        )
        report = self.engine.run(objective.id)
        self.assertEqual(report.state, ObjectiveState.COMPLETED)
        self.assertEqual(report.capability_id, "shell")
        self.assertIn("shell-ok", report.result["stdout"])

    def test_filesystem_write_then_verify_file_exists(self) -> None:
        target = Path(self.tmp.name) / "out" / "note.txt"
        objective = self.make_objective(
            "write a file",
            context={
                "operation": "fs.write",
                "params": {"path": str(target), "content": "proof"},
                "verify": {"method": "file_exists"},
            },
        )
        report = self.engine.run(objective.id)
        self.assertEqual(report.state, ObjectiveState.COMPLETED)
        self.assertTrue(target.exists())

    def test_operation_override_wins(self) -> None:
        objective = self.make_objective("anything")
        report = self.engine.run(
            objective.id, EngineOptions(operation="echo", max_attempts=1)
        )
        self.assertEqual(report.state, ObjectiveState.COMPLETED)

    def test_terminal_objective_refuses_rerun_without_force(self) -> None:
        objective = self.make_objective(
            "echo test", context={"operation": "echo"}
        )
        self.engine.run(objective.id)
        report = self.engine.run(objective.id)
        self.assertEqual(report.state, ObjectiveState.COMPLETED)
        self.assertIn("already terminal", report.next_action or "")
        forced = self.engine.run(objective.id, EngineOptions(force=True))
        self.assertEqual(len(self.store.list_executions(objective.id)), 2)
        self.assertEqual(forced.state, ObjectiveState.COMPLETED)

    def test_unknown_objective_raises(self) -> None:
        with self.assertRaises(AgentOSError):
            self.engine.run("obj_missing")


class TestFailureAndRecovery(EngineTestCase):
    def test_failing_command_records_bounded_failures(self) -> None:
        objective = self.make_objective(
            "run a failing command",
            context={"operation": "shell.run", "params": {"command": "exit 1"}},
        )
        report = self.engine.run(objective.id, EngineOptions(max_attempts=2))
        self.assertEqual(report.state, ObjectiveState.FAILED)
        stored = self.store.get_objective(objective.id)
        assert stored is not None
        self.assertEqual(stored.status, ObjectiveState.FAILED)
        self.assertEqual(stored.verification_status.value, "FAILED")
        self.assertIsNotNone(stored.next_action)
        self.assertIsNotNone(stored.failure)
        executions = self.store.list_executions(objective.id)
        self.assertEqual(len(executions), 2)
        failures = self.store.list_failures(objective.id)
        self.assertEqual(len(failures), 2)
        types = self.event_types(objective.id)
        self.assertIn("objective.failed", types)
        self.assertIn("verification.failed", types)
        self.assertIn("recovery.started", types)

    def test_retry_then_success_marks_failures_recovered(self) -> None:
        sentinel = Path(self.tmp.name) / "sentinel"
        command = (
            f'if [ -f "{sentinel}" ]; then exit 0; '
            f'else touch "{sentinel}"; exit 1; fi'
        )
        objective = self.make_objective(
            "flaky command",
            context={"operation": "shell.run", "params": {"command": command}},
        )
        report = self.engine.run(objective.id, EngineOptions(max_attempts=3))
        self.assertEqual(report.state, ObjectiveState.COMPLETED)
        self.assertEqual(
            len(self.store.list_executions(objective.id)), 2
        )
        failures = self.store.list_failures(objective.id)
        self.assertEqual(len(failures), 1)
        self.assertTrue(failures[0].recovered)

    def test_unretryable_failure_stops_after_one_attempt(self) -> None:
        objective = self.make_objective(
            "read missing file",
            context={
                "operation": "fs.read",
                "params": {"path": str(Path(self.tmp.name) / "nope.txt")},
            },
        )
        report = self.engine.run(objective.id, EngineOptions(max_attempts=3))
        self.assertEqual(report.state, ObjectiveState.FAILED)
        self.assertEqual(len(self.store.list_executions(objective.id)), 1)
        self.assertEqual(report.attempt, 1)
        stored = self.store.get_objective(objective.id)
        assert stored is not None and stored.failure is not None
        self.assertEqual(stored.failure["type"], "not_found")

    def test_unknown_operation_blocks_with_next_action(self) -> None:
        objective = self.make_objective(
            "teleport", context={"operation": "teleport.run"}
        )
        report = self.engine.run(objective.id)
        self.assertEqual(report.state, ObjectiveState.BLOCKED)
        assert report.next_action is not None
        self.assertIn("teleport.run", report.next_action)
        self.assertIn("capability.missing", self.event_types(objective.id))

    def test_recover_reruns_failed_objective(self) -> None:
        objective = self.make_objective(
            "run a failing command",
            context={"operation": "shell.run", "params": {"command": "exit 1"}},
        )
        self.engine.run(objective.id, EngineOptions(max_attempts=1))
        report = self.engine.recover(objective.id, EngineOptions(max_attempts=1))
        self.assertEqual(report.state, ObjectiveState.FAILED)
        self.assertEqual(len(self.store.list_executions(objective.id)), 2)
        types = self.event_types(objective.id)
        self.assertIn("recovery.started", types)

    def test_recover_refuses_non_failed(self) -> None:
        objective = self.make_objective("echo test", context={"operation": "echo"})
        self.engine.run(objective.id)
        report = self.engine.recover(objective.id)
        self.assertEqual(report.state, ObjectiveState.COMPLETED)
        self.assertIn("nothing to recover", report.next_action or "")

    def test_error_is_never_promoted_to_success(self) -> None:
        objective = self.make_objective(
            "run a failing command with chatty output",
            context={
                "operation": "shell.run",
                "params": {"command": "echo all good; exit 2"},
            },
        )
        report = self.engine.run(objective.id, EngineOptions(max_attempts=1))
        self.assertEqual(report.state, ObjectiveState.FAILED)


class TestVerifyLatest(EngineTestCase):
    def test_reverify_passes(self) -> None:
        objective = self.make_objective("echo test", context={"operation": "echo"})
        self.engine.run(objective.id)
        verification = self.engine.verify_latest(objective.id)
        self.assertTrue(verification.verified)

    def test_reverify_detects_removed_file(self) -> None:
        target = Path(self.tmp.name) / "proof.txt"
        target.write_text("proof", encoding="utf-8")
        objective = self.make_objective(
            "write a file",
            context={
                "operation": "fs.write",
                "params": {"path": str(target), "content": "proof"},
                "verify": {"method": "file_exists"},
            },
        )
        self.engine.run(objective.id)
        target.unlink()
        verification = self.engine.verify_latest(objective.id)
        self.assertFalse(verification.verified)

    def test_verify_with_no_executions_raises(self) -> None:
        objective = self.make_objective("nothing run")
        with self.assertRaises(AgentOSError):
            self.engine.verify_latest(objective.id)


if __name__ == "__main__":
    unittest.main()