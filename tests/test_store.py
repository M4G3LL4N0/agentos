"""Tests for sqlite persistence, including survival across restarts."""

import tempfile
import unittest
from pathlib import Path

from agentos.events import EventBus
from agentos.models import (
    Capability,
    CapabilityType,
    Event,
    EventType,
    Evidence,
    Execution,
    ExecutionStatus,
    Failure,
    Objective,
    ObjectiveState,
    VerificationResult,
    new_id,
    utc_now_iso,
)
from agentos.store import Store


class StoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "agentos.db"
        self.store = Store(self.path)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def reopen(self) -> Store:
        self.store.close()
        self.store = Store(self.path)
        return self.store


class TestObjectives(StoreTestCase):
    def test_save_get_and_list(self) -> None:
        objective = Objective(id=new_id("obj"), title="t", description="d")
        self.store.save_objective(objective)
        fetched = self.store.get_objective(objective.id)
        self.assertIsNotNone(fetched)
        assert fetched is not None
        self.assertEqual(fetched.title, "t")
        self.assertEqual(len(self.store.list_objectives()), 1)

    def test_update_persists(self) -> None:
        objective = Objective(id=new_id("obj"), title="t", description="d")
        self.store.save_objective(objective)
        objective.status = ObjectiveState.RUNNING
        self.store.save_objective(objective)
        fetched = self.store.get_objective(objective.id)
        assert fetched is not None
        self.assertEqual(fetched.status.value, "RUNNING")

    def test_unknown_objective_returns_none(self) -> None:
        self.assertIsNone(self.store.get_objective("obj_missing"))

    def test_survives_restart(self) -> None:
        objective = Objective(id=new_id("obj"), title="persist me", description="d")
        objective.add_evidence(Evidence(kind="k", detail="v"))
        self.store.save_objective(objective)
        store2 = self.reopen()
        fetched = store2.get_objective(objective.id)
        assert fetched is not None
        self.assertEqual(fetched.title, "persist me")
        self.assertEqual(len(fetched.evidence), 1)

    def test_malformed_json_fails_closed(self) -> None:
        objective = Objective(id="obj_corrupt", title="corrupt", description="")
        self.store.save_objective(objective)
        self.store.conn.execute(
            "UPDATE objectives SET context_json=? WHERE id=?",
            ("{", objective.id),
        )
        self.store.conn.commit()
        with self.assertRaises(ValueError):
            self.store.get_objective(objective.id)

    def test_filter_by_status_and_parent(self) -> None:
        parent = Objective(id=new_id("obj"), title="parent", description="d")
        self.store.save_objective(parent)
        child = Objective(
            id=new_id("obj"), title="child", description="d", parent_id=parent.id
        )
        self.store.save_objective(child)
        self.assertEqual(len(self.store.list_objectives(status="CREATED")), 2)
        children = self.store.list_objectives(parent_id=parent.id)
        self.assertEqual([c.id for c in children], [child.id])


class TestCapabilities(StoreTestCase):
    def test_save_list_delete(self) -> None:
        capability = Capability(
            id="shell",
            name="Local shell",
            type=CapabilityType.CLI,
            description="run commands",
            operations=["shell.run"],
            adapter="shell",
        )
        self.store.save_capability(capability)
        self.assertEqual(len(self.store.list_capabilities()), 1)
        fetched = self.store.get_capability("shell")
        assert fetched is not None
        self.assertEqual(fetched.operations, ["shell.run"])
        self.assertTrue(self.store.delete_capability("shell"))
        self.assertFalse(self.store.delete_capability("shell"))
        self.assertEqual(self.store.list_capabilities(), [])

    def test_capabilities_survive_restart(self) -> None:
        capability = Capability(
            id="echo",
            name="Echo",
            type=CapabilityType.TOOL,
            description="echo",
            operations=["echo"],
            adapter="echo",
        )
        self.store.save_capability(capability)
        store2 = self.reopen()
        self.assertEqual(len(store2.list_capabilities()), 1)


class TestExecutionsEventsVerificationsFailures(StoreTestCase):
    def test_execution_round_trip(self) -> None:
        execution = Execution(
            id=new_id("exec"),
            objective_id="obj_1",
            capability_id="shell",
            operation="shell.run",
            output={"exit_code": 0},
        )
        execution.finish(ExecutionStatus.EXECUTED)
        self.store.save_execution(execution)
        fetched = self.store.get_execution(execution.id)
        assert fetched is not None
        self.assertEqual(fetched.status, ExecutionStatus.EXECUTED)
        self.assertEqual(len(self.store.list_executions(objective_id="obj_1")), 1)

    def test_events_recorded_in_order(self) -> None:
        bus = EventBus(self.store)
        bus.emit(EventType.OBJECTIVE_CREATED, objective_id="obj_1")
        bus.emit(EventType.OBJECTIVE_STARTED, objective_id="obj_1")
        events = self.store.list_events(objective_id="obj_1")
        self.assertEqual(
            [e.event_type for e in events],
            [EventType.OBJECTIVE_CREATED, EventType.OBJECTIVE_STARTED],
        )
        self.assertEqual(len(self.store.list_events()), 2)

    def test_verification_and_failure(self) -> None:
        verification = VerificationResult(
            id=new_id("vrf"),
            objective_id="obj_1",
            execution_id="exec_1",
            verified=False,
            method="exit_code",
            evidence=[Evidence(kind="exit_code", detail="exit code 1 != 0")],
        )
        self.store.save_verification(verification)
        self.assertEqual(len(self.store.list_verifications("obj_1")), 1)
        failure = Failure(
            id=new_id("fail"),
            objective_id="obj_1",
            execution_id="exec_1",
            failure_type="exit_code",
            detail="exit code 1 != 0",
        )
        self.store.save_failure(failure)
        failures = self.store.list_failures("obj_1")
        self.assertEqual(len(failures), 1)
        self.assertEqual(self.store.mark_failures_recovered("obj_1"), 1)
        self.assertTrue(self.store.list_failures("obj_1")[0].recovered)

    def test_all_tables_survive_restart(self) -> None:
        bus = EventBus(self.store)
        bus.emit(EventType.OBJECTIVE_CREATED, objective_id="obj_1")
        self.store.save_execution(
            Execution(
                id=new_id("exec"),
                objective_id="obj_1",
                capability_id="shell",
                operation="shell.run",
            )
        )
        store2 = self.reopen()
        counts = store2.counts()
        self.assertEqual(counts["events"], 1)
        self.assertEqual(counts["executions"], 1)

    def test_counts_and_integrity(self) -> None:
        counts = self.store.counts()
        self.assertEqual(
            set(counts),
            {
                "objectives",
                "executions",
                "events",
                "capabilities",
                "verifications",
                "failures",
                "plans",
                "patterns",
                "gaps",
                "quality",
                "federation_jobs",
                "federation_results",
                "federation_cells",
                "usage_snapshots",
                "usage_ledger",
                "routing_outcomes",
                "job_verifications",
            },
        )
        self.assertEqual(self.store.integrity_ok(), [])


if __name__ == "__main__":
    unittest.main()