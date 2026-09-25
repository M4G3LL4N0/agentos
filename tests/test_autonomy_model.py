"""Tests for the Autonomy Model - progressive autonomy states with evidence-based promotion."""

from __future__ import annotations

import os
import tempfile
import unittest

from agentos.models import AutonomyState
from agentos.store import Store
from agentos.autonomy_model import (
    AutonomyRecord,
    AutonomyModel,
    AUTONOMY_THRESHOLDS,
    REVOCATION_TRIGGERS,
)


class TestAutonomyThresholds(unittest.TestCase):
    def test_thresholds_defined_for_all_states(self):
        for state in AutonomyState:
            if state not in (AutonomyState.SUSPENDED, AutonomyState.REVOKED):
                self.assertIn(state, AUTONOMY_THRESHOLDS)

    def test_discovered_to_observe_thresholds(self):
        t = AUTONOMY_THRESHOLDS[AutonomyState.DISCOVERED]
        self.assertEqual(t["next"], AutonomyState.OBSERVE)
        self.assertEqual(t["min_successful_verified"], 0)

    def test_observe_to_propose_thresholds(self):
        t = AUTONOMY_THRESHOLDS[AutonomyState.OBSERVE]
        self.assertEqual(t["next"], AutonomyState.PROPOSE)
        self.assertEqual(t["min_successful_verified"], 3)
        self.assertEqual(t["max_failures"], 5)
        self.assertEqual(t["min_verification_pass_rate"], 0.5)

    def test_propose_to_approval_required_thresholds(self):
        t = AUTONOMY_THRESHOLDS[AutonomyState.PROPOSE]
        self.assertEqual(t["next"], AutonomyState.APPROVAL_REQUIRED)
        self.assertEqual(t["min_successful_verified"], 10)
        self.assertEqual(t["min_verification_pass_rate"], 0.7)

    def test_approval_required_to_limited_autonomy_thresholds(self):
        t = AUTONOMY_THRESHOLDS[AutonomyState.APPROVAL_REQUIRED]
        self.assertEqual(t["next"], AutonomyState.LIMITED_AUTONOMY)
        self.assertEqual(t["min_successful_verified"], 25)
        self.assertEqual(t["min_verification_pass_rate"], 0.8)

    def test_limited_autonomy_to_certified_thresholds(self):
        t = AUTONOMY_THRESHOLDS[AutonomyState.LIMITED_AUTONOMY]
        self.assertEqual(t["next"], AutonomyState.CERTIFIED_AUTONOMY)
        self.assertEqual(t["min_successful_verified"], 50)
        self.assertEqual(t["min_verification_pass_rate"], 0.9)

    def test_certified_autonomy_terminal(self):
        t = AUTONOMY_THRESHOLDS[AutonomyState.CERTIFIED_AUTONOMY]
        self.assertIsNone(t["next"])

    def test_revocation_triggers(self):
        self.assertEqual(REVOCATION_TRIGGERS["security_incident"], AutonomyState.REVOKED)
        self.assertEqual(REVOCATION_TRIGGERS["scope_violation"], AutonomyState.SUSPENDED)
        self.assertEqual(REVOCATION_TRIGGERS["verification_failure_rate"], 0.3)
        self.assertEqual(REVOCATION_TRIGGERS["human_correction_spike"], 3)


class TestAutonomyRecord(unittest.TestCase):
    def test_autonomy_record_defaults(self):
        record = AutonomyRecord(
            id="ar1",
            entity_type="worker",
            entity_id="worker_1",
        )
        self.assertEqual(record.state, AutonomyState.DISCOVERED)
        self.assertEqual(record.successful_verified_tasks, 0)
        self.assertEqual(record.verification_pass_rate, 0.0)

    def test_autonomy_record_to_from_dict(self):
        record = AutonomyRecord(
            id="ar1",
            entity_type="worker",
            entity_id="worker_1",
            state=AutonomyState.LIMITED_AUTONOMY,
            successful_verified_tasks=10,
            failures=1,
            verification_pass_rate=0.9,
        )
        d = record.to_dict()
        self.assertEqual(d["state"], "LIMITED_AUTONOMY")
        self.assertEqual(d["successful_verified_tasks"], 10)
        self.assertEqual(d["verification_pass_rate"], 0.9)

        restored = AutonomyRecord.from_dict(d)
        self.assertEqual(restored.state, AutonomyState.LIMITED_AUTONOMY)
        self.assertEqual(restored.successful_verified_tasks, 10)
        self.assertEqual(restored.verification_pass_rate, 0.9)


class TestAutonomyModel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "test.db"))
        self.model = AutonomyModel(self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_record(self):
        record = self.model.create_record("worker", "worker_1")
        self.assertEqual(record.entity_type, "worker")
        self.assertEqual(record.entity_id, "worker_1")
        self.assertEqual(record.state, AutonomyState.DISCOVERED)

        # Check it was persisted
        stored = self.store.get_autonomy_record("worker", "worker_1")
        self.assertIsNotNone(stored)
        self.assertEqual(stored["entity_type"], "worker")

    def test_get_or_create(self):
        record1 = self.model.get_or_create("worker", "worker_1")
        record2 = self.model.get_or_create("worker", "worker_1")
        self.assertEqual(record1.id, record2.id)

    def test_record_success(self):
        record = self.model.record_success("worker", "worker_1", verification_passed=True)
        self.assertEqual(record.successful_verified_tasks, 1)
        self.assertEqual(record.verification_pass_rate, 1.0)

        record = self.model.record_success("worker", "worker_1", verification_passed=False)
        self.assertEqual(record.successful_verified_tasks, 1)
        self.assertEqual(record.failures, 1)
        self.assertEqual(record.verification_pass_rate, 0.5)

    def test_record_failure(self):
        record = self.model.record_failure("worker", "worker_1")
        self.assertEqual(record.failures, 1)
        self.assertEqual(record.verification_pass_rate, 0.0)

    def test_record_security_incident(self):
        record = self.model.record_security_incident("worker", "worker_1")
        self.assertEqual(record.security_incidents, 1)
        # Should be revoked immediately
        self.assertEqual(record.state, AutonomyState.REVOKED)

    def test_record_human_correction(self):
        record = self.model.record_human_correction("worker", "worker_1")
        self.assertEqual(record.human_corrections, 1)

    def test_record_scope_violation(self):
        record = self.model.record_scope_violation("worker", "worker_1")
        self.assertEqual(record.scope_violations, 1)
        # Should be suspended
        self.assertEqual(record.state, AutonomyState.SUSPENDED)

    def test_promotion_observe_to_propose(self):
        record = self.model.get_or_create("worker", "worker_1")
        # Manually set up for promotion
        record.successful_verified_tasks = 3
        record.failures = 0
        record.verification_pass_rate = 1.0
        record.security_incidents = 0
        record.human_corrections = 0
        record.scope_violations = 0

        self.model._check_promotion(record)
        self.assertEqual(record.state, AutonomyState.OBSERVE)

    def test_promotion_propose_to_approval(self):
        record = self.model.get_or_create("worker", "worker_1")
        record.state = AutonomyState.OBSERVE
        record.successful_verified_tasks = 10
        record.failures = 1
        record.verification_pass_rate = 0.8
        record.security_incidents = 0
        record.human_corrections = 0
        record.scope_violations = 0

        self.model._check_promotion(record)
        self.assertEqual(record.state, AutonomyState.PROPOSE)

    def test_promotion_to_certified(self):
        record = self.model.get_or_create("worker", "worker_1")
        record.state = AutonomyState.LIMITED_AUTONOMY
        record.successful_verified_tasks = 50
        record.failures = 0
        record.verification_pass_rate = 0.95
        record.security_incidents = 0
        record.human_corrections = 0
        record.scope_violations = 0

        self.model._check_promotion(record)
        self.assertEqual(record.state, AutonomyState.CERTIFIED_AUTONOMY)

    def test_revocation_on_security_incident(self):
        record = self.model.get_or_create("worker", "worker_1")
        record.state = AutonomyState.CERTIFIED_AUTONOMY
        record.security_incidents = 1

        self.model._check_revocation(record)
        self.assertEqual(record.state, AutonomyState.REVOKED)

    def test_revocation_on_verification_failure_rate(self):
        record = self.model.get_or_create("worker", "worker_1")
        record.state = AutonomyState.LIMITED_AUTONOMY
        record.verification_pass_rate = 0.2  # Below 0.3 threshold

        self.model._check_revocation(record)
        self.assertEqual(record.state, AutonomyState.SUSPENDED)

    def test_revocation_on_human_correction_spike(self):
        record = self.model.get_or_create("worker", "worker_1")
        record.state = AutonomyState.LIMITED_AUTONOMY
        record.human_corrections = 3

        self.model._check_revocation(record)
        self.assertEqual(record.state, AutonomyState.SUSPENDED)

    def test_can_act_autonomously(self):
        # DISCOVERED - cannot act
        record = self.model.get_or_create("worker", "worker_1")
        can, reason = self.model.can_act_autonomously("worker", "worker_1")
        self.assertFalse(can)
        self.assertIn("discovered", reason.lower())

        # OBSERVE - cannot act
        record.state = AutonomyState.OBSERVE
        self.store.save_autonomy_record(record.to_dict())
        can, reason = self.model.can_act_autonomously("worker", "worker_1")
        self.assertFalse(can)

        # PROPOSE - cannot act
        record.state = AutonomyState.PROPOSE
        self.store.save_autonomy_record(record.to_dict())
        can, reason = self.model.can_act_autonomously("worker", "worker_1")
        self.assertFalse(can)

        # APPROVAL_REQUIRED - cannot act
        record.state = AutonomyState.APPROVAL_REQUIRED
        self.store.save_autonomy_record(record.to_dict())
        can, reason = self.model.can_act_autonomously("worker", "worker_1")
        self.assertFalse(can)

        # LIMITED_AUTONOMY - can act for read/analyze/suggest
        record.state = AutonomyState.LIMITED_AUTONOMY
        self.store.save_autonomy_record(record.to_dict())
        can, reason = self.model.can_act_autonomously("worker", "worker_1", "read")
        self.assertTrue(can)
        can, reason = self.model.can_act_autonomously("worker", "worker_1", "write")
        self.assertFalse(can)

        # CERTIFIED_AUTONOMY - can act
        record.state = AutonomyState.CERTIFIED_AUTONOMY
        self.store.save_autonomy_record(record.to_dict())
        can, reason = self.model.can_act_autonomously("worker", "worker_1", "any")
        self.assertTrue(can)

        # SUSPENDED - cannot act
        record.state = AutonomyState.SUSPENDED
        self.store.save_autonomy_record(record.to_dict())
        can, reason = self.model.can_act_autonomously("worker", "worker_1")
        self.assertFalse(can)

        # REVOKED - cannot act
        record.state = AutonomyState.REVOKED
        self.store.save_autonomy_record(record.to_dict())
        can, reason = self.model.can_act_autonomously("worker", "worker_1")
        self.assertFalse(can)

    def test_promote_to_manual(self):
        record = self.model.get_or_create("worker", "worker_1")
        record.successful_verified_tasks = 100
        record.failures = 0
        record.verification_pass_rate = 1.0
        record.security_incidents = 0
        record.human_corrections = 0
        record.scope_violations = 0
        self.store.save_autonomy_record(record.to_dict())

        # Promote to CERTIFIED
        result = self.model.promote_to("worker", "worker_1", "CERTIFIED_AUTONOMY")
        self.assertIsNotNone(result)
        self.assertEqual(result.state, AutonomyState.CERTIFIED_AUTONOMY)

        # Demote to SUSPENDED
        result = self.model.promote_to("worker", "worker_1", "SUSPENDED")
        self.assertIsNotNone(result)
        self.assertEqual(result.state, AutonomyState.SUSPENDED)


class TestAutonomyStateModel(unittest.TestCase):
    def test_autonomy_state_values(self):
        self.assertEqual(AutonomyState.DISCOVERED.value, "DISCOVERED")
        self.assertEqual(AutonomyState.OBSERVE.value, "OBSERVE")
        self.assertEqual(AutonomyState.PROPOSE.value, "PROPOSE")
        self.assertEqual(AutonomyState.APPROVAL_REQUIRED.value, "APPROVAL_REQUIRED")
        self.assertEqual(AutonomyState.LIMITED_AUTONOMY.value, "LIMITED_AUTONOMY")
        self.assertEqual(AutonomyState.CERTIFIED_AUTONOMY.value, "CERTIFIED_AUTONOMY")
        self.assertEqual(AutonomyState.SUSPENDED.value, "SUSPENDED")
        self.assertEqual(AutonomyState.REVOKED.value, "REVOKED")


if __name__ == "__main__":
    unittest.main()