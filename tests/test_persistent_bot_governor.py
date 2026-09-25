"""Tests for Persistent Bot Governor - materialization decision framework."""

from __future__ import annotations

import os
import tempfile
import unittest

from agentos.models import PersistenceDecision
from agentos.store import Store
from agentos.persistent_bot_governor import (
    MaterializationPlan,
    PersistentBotGovernor,
)
from agentos.role_classifier import calculate_factors_for_role, classify_persistence


class TestMaterializationPlan(unittest.TestCase):
    def test_materialization_plan_creation(self):

plan = MaterializationPlan(

    role_id="test_role",

    decision=PersistenceDecision.CREATE_PERSISTENT,

    factors=None,  # Will be set in real usage

    estimated_cost="HIGH",

    required_resources=["Grok subscription", "Cloud browser"],

    timeline="1-2 weeks",

    approval_required=True,

)

self.assertEqual(plan.role_id, "test_role")

self.assertEqual(plan.decision, PersistenceDecision.CREATE_PERSISTENT)

self.assertTrue(plan.approval_required)


class TestPersistentBotGovernor(unittest.TestCase):
    def setUp(self):

self.tmp = tempfile.TemporaryDirectory()

self.store = Store(os.path.join(self.tmp.name, "test.db"))

self.governor = PersistentBotGovernor(self.store)

    def tearDown(self):

self.tmp.cleanup()

    def test_evaluate_virtual_role_create_persistent(self):

# Add metrics suggesting high supervisory value and browser affinity

self.store.record_executor_metric({

    "id": "m1",

    "executor": "grokbot_office",

    "runtime": "browser",

    "model_provider": "grok",

    "task_class": "supervisor_task",

    "success": 1,

    "verification_result": "VERIFIED",

    "latency_ms": 5000,

    "retry_count": 0,

    "human_correction": 0,

    "context_size": 2000,

})

self.store.record_executor_metric({

    "id": "m2",

    "executor": "grokbot_office",

    "runtime": "browser",

    "model_provider": "grok",

    "task_class": "supervisor_task",

    "success": 1,

    "verification_result": "VERIFIED",

    "latency_ms": 5500,

    "retry_count": 0,

    "human_correction": 0,

    "context_size": 2100,

})


plan = self.governor.evaluate_virtual_role("grokbot_office")

self.assertEqual(plan.role_id, "grokbot_office")

self.assertEqual(plan.decision, PersistenceDecision.CREATE_PERSISTENT)

self.assertTrue(plan.approval_required)

self.assertIn("Grok subscription", plan.required_resources)

self.assertIn("Cloud browser", plan.required_resources)

    def test_evaluate_virtual_role_convert_workflow(self):

# Add metrics suggesting high workflow repeatability

for i in range(10):

    self.store.record_executor_metric({


"id": f"m{i}",


"executor": "repeated_worker",


"runtime": "local",


"model_provider": "openai",


"task_class": "code_review",


"success": 1,


"verification_result": "VERIFIED",


"latency_ms": 1000,


"retry_count": 0,


"human_correction": 0,


"context_size": 500,

    })


plan = self.governor.evaluate_virtual_role("repeated_worker")

# High repeatability -> CONVERT_WORKFLOW

self.assertEqual(plan.decision, PersistenceDecision.CONVERT_WORKFLOW)

    def test_evaluate_virtual_role_keep_virtual(self):

# Very few executions

self.store.record_executor_metric({

    "id": "m1",

    "executor": "rare_worker",

    "runtime": "local",

    "model_provider": "openai",

    "task_class": "rare_task",

    "success": 1,

})


plan = self.governor.evaluate_virtual_role("rare_worker")

# Low frequency + high success rate -> TOOL (can be replaced by deterministic tool)

self.assertEqual(plan.decision, PersistenceDecision.TOOL)

    def test_estimate_cost_create_persistent(self):

plan = self.governor.evaluate_virtual_role("test_role")

# Cost should be a string

self.assertIsInstance(plan.estimated_cost, str)

self.assertGreater(len(plan.estimated_cost), 0)

    def test_estimate_cost_convert_workflow(self):

# Add metrics for workflow conversion

for i in range(5):

    self.store.record_executor_metric({


"id": f"m{i}",


"executor": "wf_worker",


"runtime": "local",


"model_provider": "openai",


"task_class": "code_review",


"success": 1,

    })


plan = self.governor.evaluate_virtual_role("wf_worker")

self.assertIn("LOW", plan.estimated_cost.upper())

    def test_required_resources(self):

# Browser affinity -> cloud browser resources

self.store.record_executor_metric({

    "id": "m1",

    "executor": "browser_worker",

    "runtime": "browser",

    "model_provider": "grok",

    "task_class": "web_task",

    "success": 1,

})


plan = self.governor.evaluate_virtual_role("browser_worker")

self.assertIn("Grok subscription", plan.required_resources)

self.assertIn("Cloud browser", plan.required_resources)

    def test_approve_materialization(self):

self.store.record_executor_metric({

    "id": "m1",

    "executor": "approve_test",

    "runtime": "browser",

    "model_provider": "grok",

    "task_class": "supervisor",

    "success": 1,

})


plan = self.governor.evaluate_virtual_role("approve_test")

self.assertTrue(plan.approval_required)

self.assertEqual(len(self.governor.pending_decisions), 1)


# Approve

approved_plan = self.governor.approve_materialization("approve_test", True, "admin")

self.assertIsNotNone(approved_plan)

self.assertFalse(approved_plan.approval_required)

self.assertEqual(approved_plan.to_dict()["role_id"], "approve_test")

row = self.store.conn.execute(

    "SELECT decision, approval_status, approved_by FROM persistence_decisions "

    "WHERE role_id=? ORDER BY rowid DESC LIMIT 1",

    ("approve_test",),

).fetchone()

self.assertEqual(row["decision"], PersistenceDecision.CREATE_PERSISTENT.value)

self.assertEqual(row["approval_status"], "APPROVED")

self.assertEqual(row["approved_by"], "admin")

self.assertEqual(len(self.governor.pending_decisions), 0)


# Reject another

self.store.record_executor_metric({

    "id": "m2",

    "executor": "reject_test",

    "runtime": "browser",

    "model_provider": "grok",

    "task_class": "supervisor",

    "success": 1,

})

plan2 = self.governor.evaluate_virtual_role("reject_test")

rejected_plan = self.governor.approve_materialization("reject_test", False, "admin")

self.assertIsNotNone(rejected_plan)

self.assertEqual(rejected_plan.decision, PersistenceDecision.KEEP_VIRTUAL)

    def test_get_pending_approvals(self):

self.store.record_executor_metric({

    "id": "m1",

    "executor": "pending_test",

    "runtime": "browser",

    "model_provider": "grok",

    "task_class": "supervisor",

    "success": 1,

})

plan = self.governor.evaluate_virtual_role("pending_test")


pending = self.governor.get_pending_approvals()

self.assertEqual(len(pending), 1)

self.assertEqual(pending[0].role_id, "pending_test")

    def test_get_materialization_summary(self):

summary = self.governor.get_materialization_summary()

self.assertIn("pending_approvals", summary)

self.assertIn("decisions_by_type", summary)

self.assertIn("total_evaluated", summary)


class TestClassifyPersistenceIntegration(unittest.TestCase):
    def setUp(self):

self.tmp = tempfile.TemporaryDirectory()

self.store = Store(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):

self.tmp.cleanup()

    def test_evaluate_virtual_role_integration(self):

# This tests the full flow from metrics -> factors -> classification -> plan

self.store.record_executor_metric({

    "id": "m1",

    "executor": "integration_test",

    "runtime": "browser",

    "model_provider": "grok",

    "task_class": "supervisor",

    "success": 1,

    "verification_result": "VERIFIED",

    "latency_ms": 5000,

    "retry_count": 0,

    "human_correction": 0,

    "context_size": 2000,

})


# Calculate factors

from agentos.role_classifier import calculate_factors_for_role

factors = calculate_factors_for_role("integration_test", self.store)



# Should have browser affinity

self.assertEqual(factors.browser_session_affinity, 1.0)

# Should have unique permissions (at least 1 task class)

self.assertGreaterEqual(factors.unique_permissions, 1)

# Should have some supervisory value

self.assertGreater(factors.supervisory_value, 0.0)


# Classify

from agentos.role_classifier import classify_persistence

decision = classify_persistence(factors)



# Should be CREATE_PERSISTENT due to browser affinity + supervisory

self.assertEqual(decision, PersistenceDecision.CREATE_PERSISTENT)


if __name__ == "__main__":
    unittest.main()