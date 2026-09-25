"""Tests for the Fleet Monitor - event-driven detection, incident clustering, retry suppression."""

from __future__ import annotations

import os
import tempfile
import unittest

from agentos.models import Capability, CapabilityType, HealthStatus, IncidentRecord
from agentos.store import Store
from agentos.fleet_monitor import (
    FleetSignal,
    detect_signals,
    cluster_failures,
    create_incident_from_cluster,
    suppress_redundant_retries,
    route_around_incident,
    escalate_once,
    resolve_incident,
    FleetMonitor,
    SIGNAL_TYPES,
)


class TestFleetSignal(unittest.TestCase):
    def test_fleet_signal_creation(self):
        signal = FleetSignal(
            signal_type="job_stalled",
            job_id="job_123",
            executor="worker_1",
            task_class="code_change",
            timestamp="2026-09-24T10:00:00Z",
            details={"stalled_duration": 300},
        )
        self.assertEqual(signal.signal_type, "job_stalled")
        self.assertEqual(signal.job_id, "job_123")
        self.assertEqual(signal.executor, "worker_1")


class TestSignalTypes(unittest.TestCase):
    def test_signal_types_defined(self):
        self.assertIn("job_stalled", SIGNAL_TYPES)
        self.assertIn("job_duplicate", SIGNAL_TYPES)
        self.assertIn("job_orphaned", SIGNAL_TYPES)
        self.assertIn("retry_storm", SIGNAL_TYPES)
        self.assertIn("queue_congestion", SIGNAL_TYPES)
        self.assertIn("deadline_risk", SIGNAL_TYPES)
        self.assertIn("quota_exhaustion", SIGNAL_TYPES)
        self.assertIn("unexpected_spend", SIGNAL_TYPES)
        self.assertIn("provider_degradation", SIGNAL_TYPES)
        self.assertIn("unverified_completion", SIGNAL_TYPES)
        self.assertIn("common_blocker", SIGNAL_TYPES)
        self.assertIn("inefficient_fanout", SIGNAL_TYPES)
        self.assertIn("unused_persistent_agent", SIGNAL_TYPES)
        self.assertIn("repeat_human_correction", SIGNAL_TYPES)


class TestClusterFailures(unittest.TestCase):
    def test_cluster_failures_empty(self):
        clusters = cluster_failures([])
        self.assertEqual(clusters, [])

    def test_cluster_failures_groups_by_signature(self):
        metrics = [
            {"executor": "worker_1", "task_class": "code_change", "final_outcome": "failed", "error": "auth failed", "success": 0},
            {"executor": "worker_1", "task_class": "code_change", "final_outcome": "failed", "error": "auth failed", "success": 0},
            {"executor": "worker_2", "task_class": "code_change", "final_outcome": "failed", "error": "timeout", "success": 0},
        ]
        clusters = cluster_failures(metrics)
        self.assertEqual(len(clusters), 1)  # Only the first two cluster together (same error)
        self.assertEqual(len(clusters[0]), 2)

    def test_cluster_failures_only_failed(self):
        metrics = [
            {"executor": "worker_1", "task_class": "code_change", "final_outcome": "failed", "success": 0},
            {"executor": "worker_1", "task_class": "code_change", "final_outcome": "success", "success": 1},
        ]
        clusters = cluster_failures(metrics)
        # Single failed item doesn't form a cluster (need >= 2)
        self.assertEqual(len(clusters), 0)


class TestCreateIncidentFromCluster(unittest.TestCase):
    def test_create_incident_from_cluster(self):
        cluster = [
            {
                "executor": "worker_1",
                "task_class": "code_change",
                "final_outcome": "failed",
                "error": "authentication failed for github",
                "id": "job_1",
                "created_at": "2026-09-24T10:00:00Z",
            },
            {
                "executor": "worker_1",
                "task_class": "code_change",
                "final_outcome": "failed",
                "error": "auth failed",
                "id": "job_2",
                "created_at": "2026-09-24T10:05:00Z",
            },
        ]
        incident = create_incident_from_cluster(cluster)
        self.assertIsNotNone(incident)
        self.assertIsInstance(incident, IncidentRecord)
        self.assertIn("auth", incident.probable_cause.lower())
        self.assertEqual(len(incident.affected_jobs), 2)
        self.assertIn("worker_1", incident.affected_executors)
        self.assertGreater(incident.confidence, 0.0)

    def test_create_incident_unknown_cause(self):
        cluster = [
            {
                "executor": "worker_1",
                "task_class": "code_change",
                "final_outcome": "failed",
                "error": "unknown error xyz",
                "id": "job_1",
                "created_at": "2026-09-24T10:00:00Z",
            },
        ]
        incident = create_incident_from_cluster(cluster)
        self.assertEqual(incident.probable_cause, "Unknown")


class TestSuppressRedundantRetries(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_suppress_redundant_retries(self):
        incident = IncidentRecord(
            id="inc1",
            signature="test",
            affected_jobs=["job_1", "job_2", "job_3"],
            affected_executors=["worker_1"],
        )
        count = suppress_redundant_retries(self.store, incident)
        self.assertEqual(count, 3)


class TestRouteAroundIncident(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_route_around_incident(self):
        self.store.save_capability(Capability(
            id="worker_1", name="worker_1", type=CapabilityType.CLI,
            description="fixture", operations=["task.run"], health=HealthStatus.AVAILABLE,
        ))
        self.store.save_capability(Capability(
            id="worker_2", name="worker_2", type=CapabilityType.CLI,
            description="fixture", operations=["task.run"], health=HealthStatus.AVAILABLE,
        ))
        incident = IncidentRecord(
            id="inc1",
            signature="test",
            affected_jobs=["job_1"],
            affected_executors=["worker_1"],
        )
        alternatives = route_around_incident(self.store, incident)
        self.assertEqual(alternatives, ["worker_2"])


class TestEscalateOnce(unittest.TestCase):
    def test_escalate_once(self):
        incident = IncidentRecord(
            id="inc1",
            signature="worker_1|code_change|Authentication failure",
            affected_jobs=["job_1", "job_2"],
            affected_executors=["worker_1"],
            probable_cause="Authentication failure",
        )
        escalation = escalate_once(incident)
        self.assertTrue(escalation["escalated"])
        self.assertEqual(escalation["incident_id"], "inc1")
        self.assertEqual(escalation["escalation_level"], "supervisor")
        self.assertIn("Authentication failure", escalation["reason"])


class TestResolveIncident(unittest.TestCase):
    def test_resolve_incident(self):
        incident = IncidentRecord(
            id="inc1",
            signature="test",
            affected_jobs=["job_1"],
            affected_executors=["worker_1"],
            status="OPEN",
        )
        resolved = resolve_incident(incident, "Fixed auth config", "lesson_123")
        self.assertEqual(resolved.status, "RESOLVED")
        self.assertEqual(resolved.resolution, "Fixed auth config")
        self.assertEqual(resolved.lesson_ref, "lesson_123")


class TestFleetMonitor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "test.db"))
        self.monitor = FleetMonitor(self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def test_process_signal_no_existing_incident(self):
        signal = FleetSignal(
            signal_type="job_stalled",
            job_id="job_1",
            executor="worker_1",
            task_class="code_change",
            timestamp="2026-09-24T10:00:00Z",
            details={},
        )
        # With no metrics, no clusters will form
        actions = self.monitor.process_signal(signal)
        self.assertEqual(actions, [])

    def test_detect_signals_uses_measured_metrics(self):
        for index in range(2):
            self.store.record_executor_metric({
                "id": f"metric-{index}",
                "job_id": f"job-{index}",
                "executor": "worker_1",
                "task_class": "task",
                "success": 0,
                "final_outcome": "failed",
                "error": "auth failed",
            })
        self.store.record_executor_metric({
            "id": "retry-metric",
            "job_id": "job-retry",
            "executor": "worker_1",
            "task_class": "task",
            "success": 0,
            "retry_count": 3,
            "final_outcome": "failed",
        })
        signals = detect_signals(self.store)
        self.assertIn("common_blocker", {signal.signal_type for signal in signals})
        self.assertIn("retry_storm", {signal.signal_type for signal in signals})

    def test_incident_update_and_hydration_preserve_affected_jobs(self):
        incident = IncidentRecord(
            id="persisted_incident",
            signature="worker_1|task|Unknown",
            affected_jobs=["job_1", "job_2"],
            affected_executors=["worker_1"],
        )
        self.store.save_incident(incident)
        incident.affected_jobs.append("job_3")
        incident.status = "ACTIVE"
        self.store.save_incident(incident)
        self.assertEqual(
            self.store.get_incident(incident.id).affected_jobs,
            ["job_1", "job_2", "job_3"],
        )
        monitor = FleetMonitor(self.store)
        self.assertIn(incident.id, {item.id for item in monitor.get_active_incidents()})

    def test_suppression_is_persisted_and_cleared_on_resolution(self):
        incident = IncidentRecord(
            id="suppression_incident",
            signature="suppression",
            affected_jobs=["job_1"],
        )
        self.store.save_incident(incident)
        self.assertEqual(suppress_redundant_retries(self.store, incident), 1)
        self.assertTrue(self.store.is_fleet_job_suppressed("job_1"))
        self.monitor.resolve_incident(incident.id, "fixed")
        self.assertFalse(self.store.is_fleet_job_suppressed("job_1"))

    def test_get_active_incidents(self):
        incidents = self.monitor.get_active_incidents()
        self.assertEqual(incidents, [])

    def test_get_fleet_health(self):
        health = self.monitor.get_fleet_health()
        self.assertIn("total_incidents", health)
        self.assertIn("open_incidents", health)
        self.assertIn("resolved_incidents", health)
        self.assertIn("affected_executors", health)
        self.assertIn("top_causes", health)
        self.assertIn("no_action", health)
        self.assertTrue(health["no_action"])


class TestIncidentRecordModel(unittest.TestCase):
    def test_incident_record_to_from_dict(self):
        incident = IncidentRecord(
            id="inc1",
            signature="worker_1|code_change|auth",
            affected_jobs=["job_1", "job_2"],
            affected_executors=["worker_1"],
            first_seen="2026-09-24T10:00:00Z",
            last_seen="2026-09-24T10:10:00Z",
            probable_cause="Authentication failure",
            confidence=0.8,
            status="OPEN",
            mitigation="Rotated credentials",
            resolution="",
            lesson_ref="lesson_123",
        )
        d = incident.to_dict()
        self.assertEqual(d["probable_cause"], "Authentication failure")
        self.assertEqual(d["confidence"], 0.8)
        self.assertEqual(d["affected_jobs"], ["job_1", "job_2"])

        restored = IncidentRecord.from_dict(d)
        self.assertEqual(restored.id, "inc1")
        self.assertEqual(restored.probable_cause, "Authentication failure")
        self.assertEqual(restored.confidence, 0.8)


if __name__ == "__main__":
    unittest.main()