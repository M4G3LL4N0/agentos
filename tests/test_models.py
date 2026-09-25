"""Tests for core domain models: enums, serialization round-trips."""

import unittest

from agentos.models import (
    Capability,
    CapabilityType,
    Event,
    EventType,
    Evidence,
    Execution,
    ExecutionStatus,
    Failure,
    HealthStatus,
    Objective,
    ObjectiveState,
    Priority,
    Report,
    VerificationResult,
    VerificationStatus,
    new_id,
)


class TestEnums(unittest.TestCase):
    def test_objective_states(self) -> None:
        self.assertEqual(
            [s.value for s in ObjectiveState],
            [
                "CREATED",
                "READY",
                "RUNNING",
                "BLOCKED",
                "VERIFYING",
                "COMPLETED",
                "FAILED",
                "CANCELLED",
            ],
        )

    def test_event_types_cover_required_names(self) -> None:
        values = {e.value for e in EventType}
        for required in (
            "objective.created",
            "objective.started",
            "objective.completed",
            "objective.failed",
            "capability.discovered",
            "capability.selected",
            "execution.started",
            "execution.completed",
            "execution.failed",
            "verification.started",
            "verification.passed",
            "verification.failed",
            "recovery.started",
            "recovery.completed",
        ):
            self.assertIn(required, values)


class TestObjective(unittest.TestCase):
    def test_round_trip(self) -> None:
        objective = Objective(
            id=new_id("obj"),
            title="Write a file",
            description="write hello to disk",
            priority=Priority.HIGH,
            constraints=["operation:fs.write"],
            context={"operation": "fs.write", "params": {"path": "/tmp/x"}},
            parent_id=None,
        )
        restored = Objective.from_dict(objective.to_dict())
        self.assertEqual(restored.id, objective.id)
        self.assertEqual(restored.status, ObjectiveState.CREATED)
        self.assertEqual(restored.priority, Priority.HIGH)
        self.assertEqual(restored.constraints, ["operation:fs.write"])
        self.assertEqual(restored.context["operation"], "fs.write")
        self.assertIsNone(restored.parent_id)
        self.assertEqual(restored.verification_status, VerificationStatus.UNVERIFIED)

    def test_evidence_round_trip(self) -> None:
        objective = Objective(id=new_id("obj"), title="t", description="d")
        objective.add_evidence(
            Evidence(kind="exit_code", detail="exit code 0", source="shell")
        )
        restored = Objective.from_dict(objective.to_dict())
        self.assertEqual(len(restored.evidence), 1)
        self.assertEqual(restored.evidence[0].kind, "exit_code")

    def test_mark_updated_changes_timestamp(self) -> None:
        objective = Objective(id=new_id("obj"), title="t", description="d")
        before = objective.updated_at
        objective.mark_updated()
        self.assertGreaterEqual(objective.updated_at, before)


class TestCapability(unittest.TestCase):
    def test_round_trip_with_optional_fields(self) -> None:
        capability = Capability(
            id="shell",
            name="Local shell",
            type=CapabilityType.CLI,
            description="run commands",
            operations=["shell.run"],
            adapter="shell",
        )
        restored = Capability.from_dict(capability.to_dict())
        self.assertTrue(restored.supports("shell.run"))
        self.assertFalse(restored.supports("fs.read"))
        self.assertTrue(restored.is_usable())
        self.assertIsNone(restored.cost)
        self.assertIsNone(restored.latency)

    def test_down_health_is_not_usable(self) -> None:
        capability = Capability(
            id="x",
            name="x",
            type=CapabilityType.TOOL,
            description="x",
            health=HealthStatus.DOWN,
        )
        self.assertFalse(capability.is_usable())


class TestExecution(unittest.TestCase):
    def test_round_trip_and_finish(self) -> None:
        execution = Execution(
            id=new_id("exec"),
            objective_id="obj_1",
            capability_id="shell",
            operation="shell.run",
        )
        execution.output = {"exit_code": 0}
        execution.finish(ExecutionStatus.EXECUTED)
        restored = Execution.from_dict(execution.to_dict())
        self.assertEqual(restored.status, ExecutionStatus.EXECUTED)
        self.assertIsNotNone(restored.finished_at)
        self.assertEqual(restored.output["exit_code"], 0)


class TestVerificationResult(unittest.TestCase):
    def test_round_trip(self) -> None:
        result = VerificationResult(
            id=new_id("vrf"),
            objective_id="obj_1",
            execution_id="exec_1",
            verified=True,
            method="exit_code",
            evidence=[Evidence(kind="exit_code", detail="exit code 0")],
        )
        restored = VerificationResult.from_dict(result.to_dict())
        self.assertTrue(restored.verified)
        self.assertEqual(restored.method, "exit_code")
        self.assertEqual(len(restored.evidence), 1)


class TestEventAndFailure(unittest.TestCase):
    def test_event_round_trip(self) -> None:
        event = Event(
            id=new_id("evt"),
            event_type=EventType.OBJECTIVE_STARTED,
            objective_id="obj_1",
            payload={"attempt": 1},
        )
        restored = Event.from_dict(event.to_dict())
        self.assertEqual(restored.event_type, EventType.OBJECTIVE_STARTED)
        self.assertEqual(restored.payload["attempt"], 1)

    def test_failure_round_trip(self) -> None:
        failure = Failure(
            id=new_id("fail"),
            objective_id="obj_1",
            execution_id="exec_1",
            failure_type="exit_code",
            detail="exit code 1 != 0",
            attempt=2,
        )
        restored = Failure.from_dict(failure.to_dict())
        self.assertEqual(restored.failure_type, "exit_code")
        self.assertFalse(restored.recovered)
        self.assertEqual(restored.attempt, 2)


class TestReport(unittest.TestCase):
    def test_to_dict(self) -> None:
        report = Report(
            objective_id="obj_1",
            state=ObjectiveState.COMPLETED,
            verification_status=VerificationStatus.VERIFIED,
            capability_id="shell",
            execution_id="exec_1",
            attempt=1,
        )
        data = report.to_dict()
        self.assertEqual(data["state"], "COMPLETED")
        self.assertEqual(data["verification_status"], "VERIFIED")
        self.assertIsNone(data["next_action"])


if __name__ == "__main__":
    unittest.main()