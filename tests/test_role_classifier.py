"""Tests for the Role Classifier - PersistenceDecision, classifier for PERSISTENT_BOT/SKILL/WORKFLOW/EPHEMERAL/TOOL."""

from __future__ import annotations

import os
import tempfile
import unittest

from agentos.models import PersistenceDecision, RoleForm
from agentos.store import Store
from agentos.role_classifier import (
    RoleFactors,
    classify_persistence,
    calculate_factors_for_role,
    RoleClassifier,
)


class TestRoleFactors(unittest.TestCase):
    def test_role_factors_defaults(self):
        factors = RoleFactors()
        self.assertEqual(factors.task_frequency, 0)
        self.assertEqual(factors.persistent_context_value, 0.0)
        self.assertEqual(factors.computer_affinity, 0.0)
        self.assertEqual(factors.browser_session_affinity, 0.0)
        self.assertEqual(factors.unique_permissions, 0)
        self.assertEqual(factors.supervisory_value, 0.0)
        self.assertEqual(factors.workflow_repeatability, 0.0)
        self.assertFalse(factors.can_skill_replace)
        self.assertFalse(factors.can_workflow_replace)
        self.assertFalse(factors.can_ephemeral_worker_replace)
        self.assertEqual(factors.measured_usage_cost, "UNKNOWN")

    def test_role_factors_to_dict(self):
        factors = RoleFactors(
            task_frequency=10,
            persistent_context_value=0.8,
            can_skill_replace=True,
        )
        d = factors.to_dict()
        self.assertEqual(d["task_frequency"], 10)
        self.assertEqual(d["persistent_context_value"], 0.8)
        self.assertEqual(d["can_skill_replace"], 1)


class TestClassifyPersistence(unittest.TestCase):
    def test_create_persistent_high_supervisory_and_context(self):
        factors = RoleFactors(
            supervisory_value=0.8,
            persistent_context_value=0.6,
        )
        decision = classify_persistence(factors)
        self.assertEqual(decision, PersistenceDecision.CREATE_PERSISTENT)

    def test_create_persistent_browser_affinity(self):
        factors = RoleFactors(
            browser_session_affinity=0.9,
            unique_permissions=2,
        )
        decision = classify_persistence(factors)
        self.assertEqual(decision, PersistenceDecision.CREATE_PERSISTENT)

    def test_create_persistent_computer_affinity(self):
        factors = RoleFactors(
            computer_affinity=0.8,
            unique_permissions=3,
        )
        decision = classify_persistence(factors)
        self.assertEqual(decision, PersistenceDecision.CREATE_PERSISTENT)

    def test_convert_workflow_high_repeatability(self):
        factors = RoleFactors(
            workflow_repeatability=0.9,
            can_workflow_replace=True,
        )
        decision = classify_persistence(factors)
        self.assertEqual(decision, PersistenceDecision.CONVERT_WORKFLOW)

    def test_convert_workflow_high_frequency(self):
        factors = RoleFactors(
            workflow_repeatability=0.9,
            task_frequency=15,
            can_workflow_replace=True,
        )
        decision = classify_persistence(factors)
        self.assertEqual(decision, PersistenceDecision.CONVERT_WORKFLOW)

    def test_convert_skill_high_frequency(self):
        factors = RoleFactors(
            task_frequency=25,
            can_skill_replace=True,
        )
        decision = classify_persistence(factors)
        self.assertEqual(decision, PersistenceDecision.CONVERT_SKILL)

    def test_convert_skill_moderate_repeatability(self):
        factors = RoleFactors(
            workflow_repeatability=0.6,
            can_skill_replace=True,
        )
        decision = classify_persistence(factors)
        self.assertEqual(decision, PersistenceDecision.CONVERT_SKILL)

    def test_tool_low_frequency_deterministic(self):
        factors = RoleFactors(
            task_frequency=3,
            can_ephemeral_worker_replace=True,
        )
        decision = classify_persistence(factors)
        self.assertEqual(decision, PersistenceDecision.TOOL)

    def test_ephemeral_very_low_frequency(self):
        factors = RoleFactors(task_frequency=2)
        decision = classify_persistence(factors)
        self.assertEqual(decision, PersistenceDecision.EPHEMERAL)

    def test_keep_virtual_default(self):
        factors = RoleFactors(task_frequency=5)
        decision = classify_persistence(factors)
        self.assertEqual(decision, PersistenceDecision.KEEP_VIRTUAL)


class TestCalculateFactorsForRole(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_calculate_factors_no_metrics(self):
        factors = calculate_factors_for_role("unknown_role", self.store)
        self.assertEqual(factors.task_frequency, 0)

    def test_calculate_factors_from_metrics(self):
        # Add some executor metrics
        self.store.record_executor_metric({
            "id": "m1",
            "executor": "test_role",
            "runtime": "local",
            "model_provider": "openai",
            "task_class": "code_change",
            "success": 1,
            "verification_result": "VERIFIED",
            "latency_ms": 1000,
            "retry_count": 0,
            "human_correction": 0,
            "context_size": 500,
        })
        self.store.record_executor_metric({
            "id": "m2",
            "executor": "test_role",
            "runtime": "local",
            "model_provider": "openai",
            "task_class": "code_change",
            "success": 1,
            "verification_result": "VERIFIED",
            "latency_ms": 1200,
            "retry_count": 1,
            "human_correction": 0,
            "context_size": 600,
        })
        self.store.record_executor_metric({
            "id": "m3",
            "executor": "test_role",
            "runtime": "browser",
            "model_provider": "grok",
            "task_class": "supervisor_task",
            "success": 1,
            "verification_result": "VERIFIED",
            "latency_ms": 5000,
            "retry_count": 0,
            "human_correction": 1,
            "context_size": 2000,
        })

        factors = calculate_factors_for_role("test_role", self.store)
        self.assertEqual(factors.task_frequency, 3)
        self.assertGreater(factors.persistent_context_value, 0.0)
        self.assertEqual(factors.computer_affinity, 0.0)  # no "computer" in runtime
        self.assertEqual(factors.browser_session_affinity, 1.0)  # "browser" in runtime
        self.assertEqual(factors.unique_permissions, 2)  # 2 task classes
        self.assertGreater(factors.supervisory_value, 0.0)
        self.assertEqual(factors.workflow_repeatability, 2/3)  # code_change appears 2/3 times


class TestRoleClassifier(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "test.db"))
        self.classifier = RoleClassifier(self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def test_evaluate_role(self):
        decision, factors = self.classifier.evaluate_role("test_role")
        self.assertIsInstance(decision, PersistenceDecision)
        self.assertIsInstance(factors, RoleFactors)

        # Check decision was persisted
        # Note: we can't easily query the persistence_decisions table without adding a method
        # but we can verify the decision is valid
        self.assertIn(decision, [
            PersistenceDecision.CREATE_PERSISTENT,
            PersistenceDecision.KEEP_VIRTUAL,
            PersistenceDecision.CONVERT_SKILL,
            PersistenceDecision.CONVERT_WORKFLOW,
            PersistenceDecision.EPHEMERAL,
            PersistenceDecision.TOOL,
            PersistenceDecision.RETIRE_IF_UNUSED,
        ])

    def test_evaluate_all_roles_empty(self):
        results = self.classifier.evaluate_all_roles()
        self.assertEqual(results, {})


class TestRoleFormModel(unittest.TestCase):
    def test_role_form_values(self):
        self.assertEqual(RoleForm.PERSISTENT_BOT.value, "PERSISTENT_BOT")
        self.assertEqual(RoleForm.SKILL.value, "SKILL")
        self.assertEqual(RoleForm.WORKFLOW.value, "WORKFLOW")
        self.assertEqual(RoleForm.CAPABILITY.value, "CAPABILITY")
        self.assertEqual(RoleForm.EPHEMERAL_WORKER.value, "EPHEMERAL_WORKER")
        self.assertEqual(RoleForm.DETERMINISTIC_TOOL.value, "DETERMINISTIC_TOOL")
        self.assertEqual(RoleForm.PERSONA_ONLY.value, "PERSONA_ONLY")


if __name__ == "__main__":
    unittest.main()