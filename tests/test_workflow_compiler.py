"""Tests for the Workflow Compiler - compilation, matching, versioning, maturity."""

from __future__ import annotations

import os
import tempfile
import unittest

from agentos.models import (
    ExecutionPlan,
    Objective,
    ObjectiveState,
    PlanStep,
    Strategy,
    VerificationStatus,
    WorkflowDefinition,
    WorkflowStatus,
    WorkflowMaturity,
)
from agentos.store import Store
from agentos.workflow_compiler import (
    compile_workflow_from_executions,
    match_workflow,
    WorkflowMatch,
    WorkflowCompiler,
    _find_common_sequence,
    _extract_common_inputs,
    _extract_common_outputs,
    _extract_preconditions,
    _extract_evidence_refs,
    _preferred_executors,
    _fallback_executors,
)


class TestWorkflowCompilerHelpers(unittest.TestCase):
    def test_find_common_sequence_single_execution(self):
        executions = [[
            {"id": "s1", "operation": "op1", "capability_id": "cap1"},
            {"id": "s2", "operation": "op2", "capability_id": "cap2"},
        ]]
        common = _find_common_sequence(executions)
        self.assertEqual(len(common), 2)

    def test_find_common_sequence_multiple_same(self):
        executions = [
            [{"id": "s1", "operation": "op1", "capability_id": "cap1"}, {"id": "s2", "operation": "op2", "capability_id": "cap2"}],
            [{"id": "s1", "operation": "op1", "capability_id": "cap1"}, {"id": "s2", "operation": "op2", "capability_id": "cap2"}],
        ]
        common = _find_common_sequence(executions)
        self.assertEqual(len(common), 2)

    def test_find_common_sequence_rejects_reordered_steps(self):
        executions = [
            [{"id": "s1", "operation": "a"}, {"id": "s2", "operation": "b"}],
            [{"id": "s2", "operation": "b"}, {"id": "s1", "operation": "a"}],
        ]
        self.assertEqual(_find_common_sequence(executions), [])

    def test_find_common_sequence_different(self):
        executions = [
            [{"id": "s1", "operation": "op1", "capability_id": "cap1"}],
            [{"id": "s1", "operation": "opX", "capability_id": "capX"}],
        ]
        common = _find_common_sequence(executions)
        self.assertEqual(len(common), 0)

    def test_extract_common_inputs(self):
        executions = [
            {"request": {"file": "a.py", "mode": "read"}},
            {"request": {"file": "b.py", "mode": "read"}},
        ]
        inputs = _extract_common_inputs(executions)
        self.assertIn("file", inputs)
        self.assertIn("mode", inputs)

    def test_extract_common_outputs(self):
        executions = [
            {"output": {"result": "ok", "count": 1}},
            {"output": {"result": "ok", "count": 2}},
        ]
        outputs = _extract_common_outputs(executions)
        self.assertIn("result", outputs)
        self.assertIn("count", outputs)

    def test_extract_preconditions(self):
        executions = [{"capability_id": "cap1"}, {"capability_id": "cap2"}]
        preconditions = _extract_preconditions(executions)
        self.assertIn("cache_available", preconditions)
        self.assertIn("capability_ready:cap1", preconditions)
        self.assertIn("capability_ready:cap2", preconditions)

    def test_extract_evidence_refs(self):
        executions = [
            {"evidence_refs": ["ref1", "ref2"]},
            {"evidence_refs": ["ref2", "ref3"]},
        ]
        refs = _extract_evidence_refs(executions)
        self.assertIn("ref1", refs)
        self.assertIn("ref2", refs)
        self.assertIn("ref3", refs)

    def test_preferred_executors(self):
        executions = [
            {"capability_id": "cap1"},
            {"capability_id": "cap1"},
            {"capability_id": "cap2"},
        ]
        preferred = _preferred_executors(executions)
        self.assertEqual(preferred[0], "cap1")
        self.assertEqual(preferred[1], "cap2")

    def test_fallback_executors(self):
        executions = [
            {"capability_id": "cap1"},
            {"capability_id": "cap1"},
            {"capability_id": "cap2"},
        ]
        fallback = _fallback_executors(executions)
        self.assertEqual(fallback[0], "cap2")


class TestCompileWorkflowFromExecutions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_compile_returns_none_for_insufficient_executions(self):
        executions = [
            {"status": "VERIFIED_COMPLETE", "strategy": "SEQUENTIAL"},
            {"status": "VERIFIED_COMPLETE", "strategy": "SEQUENTIAL"},
        ]
        result = compile_workflow_from_executions("test_class", executions, self.store)
        self.assertIsNone(result)

    def test_compile_returns_none_for_different_strategies(self):
        executions = [
            {"status": "VERIFIED_COMPLETE", "strategy": "SEQUENTIAL"},
            {"status": "VERIFIED_COMPLETE", "strategy": "PARALLEL"},
            {"status": "VERIFIED_COMPLETE", "strategy": "SEQUENTIAL"},
        ]
        result = compile_workflow_from_executions("test_class", executions, self.store)
        self.assertIsNone(result)

    def test_compile_creates_workflow(self):
        executions = [
            {
                "status": "VERIFIED_COMPLETE",
                "strategy": "SEQUENTIAL",
                "plan_steps": [
                    {"id": "s1", "operation": "op1", "capability_id": "cap1"},
                    {"id": "s2", "operation": "op2", "capability_id": "cap2"},
                ],
                "request": {"file": "a.py"},
                "output": {"result": "ok"},
                "capability_id": "cap1",
                "evidence_refs": ["ref1"],
            },
            {
                "status": "VERIFIED_COMPLETE",
                "strategy": "SEQUENTIAL",
                "plan_steps": [
                    {"id": "s1", "operation": "op1", "capability_id": "cap1"},
                    {"id": "s2", "operation": "op2", "capability_id": "cap2"},
                ],
                "request": {"file": "b.py"},
                "output": {"result": "ok"},
                "capability_id": "cap1",
                "evidence_refs": ["ref2"],
            },
            {
                "status": "VERIFIED_COMPLETE",
                "strategy": "SEQUENTIAL",
                "plan_steps": [
                    {"id": "s1", "operation": "op1", "capability_id": "cap1"},
                    {"id": "s2", "operation": "op2", "capability_id": "cap2"},
                ],
                "request": {"file": "c.py"},
                "output": {"result": "ok"},
                "capability_id": "cap2",
                "evidence_refs": ["ref3"],
            },
        ]
        wf = compile_workflow_from_executions("code_review", executions, self.store)
        self.assertIsNotNone(wf)
        self.assertIsInstance(wf, WorkflowDefinition)
        self.assertEqual(wf.task_class, "code_review")
        self.assertEqual(wf.status, WorkflowStatus.OBSERVED)
        self.assertEqual(wf.maturity, WorkflowMaturity.FIRST_RUN)
        self.assertEqual(len(wf.steps), 2)
        self.assertIn("cap1", wf.capability_requirements)


class TestWorkflowMatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_match_workflow_no_workflows(self):
        result = match_workflow("code_review", {"file": "test.py"}, self.store)
        self.assertIsInstance(result, WorkflowMatch)
        self.assertIsNone(result.workflow)
        self.assertEqual(result.match_score, 0.0)

    def test_match_workflow_includes_observed(self):
        wf = WorkflowDefinition(
            id="wf1",
            name="code_review_wf",
            task_class="code_review",
            preconditions=["input:file"],
            inputs={"file": {"type": "str"}},
            status=WorkflowStatus.OBSERVED,
        )
        self.store.save_workflow(wf)

        result = match_workflow("code_review", {"file": "test.py"}, self.store)
        self.assertIsNotNone(result.workflow)
        self.assertEqual(result.workflow.id, "wf1")

    def test_match_workflow_rejects_unavailable_capability(self):
        wf = WorkflowDefinition(
            id="wf1",
            name="code_review_wf",
            task_class="code_review",
            preconditions=["capability_ready:missing"],
            inputs={"file": {"type": "str"}},
            status=WorkflowStatus.OBSERVED,
        )
        self.store.save_workflow(wf)

        result = match_workflow("code_review", {"file": "test.py"}, self.store)
        self.assertIsNone(result.workflow)

    def test_match_workflow_with_eligible(self):
        wf = WorkflowDefinition(
            id="wf1",
            name="code_review_wf",
            task_class="code_review",
            preconditions=["cache_available"],
            inputs={"file": {"type": "str"}},
            status=WorkflowStatus.VERIFIED,
        )
        self.store.save_workflow(wf)

        result = match_workflow("code_review", {"file": "test.py"}, self.store)
        self.assertIsNotNone(result.workflow)
        self.assertEqual(result.workflow.id, "wf1")
        self.assertGreater(result.match_score, 0.0)

    def test_match_workflow_preconditions_not_met(self):
        wf = WorkflowDefinition(
            id="wf1",
            name="code_review_wf",
            task_class="code_review",
            preconditions=["input:missing_input"],
            inputs={"file": {"type": "str"}},
            status=WorkflowStatus.VERIFIED,
        )
        self.store.save_workflow(wf)

        result = match_workflow("code_review", {"file": "test.py"}, self.store)
        # Should match but with lower score due to unmet preconditions (input:missing_input not in inputs)
        self.assertIsNotNone(result.workflow)
        self.assertLess(result.match_score, 0.7)


class TestWorkflowCompiler(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "test.db"))
        self.compiler = WorkflowCompiler(self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def test_try_compile_uses_verified_plan_history(self):
        for index in range(3):
            objective = Objective(
                id=f"obj{index}",
                title=f"review {index}",
                description="verified review",
                status=ObjectiveState.COMPLETED,
                strategy=Strategy.SEQUENTIAL.value,
                result={"status": "ok"},
                verification_status=VerificationStatus.VERIFIED,
                context={
                    "taskClass": "code_review",
                    "params": {"file": f"file{index}.py"},
                },
            )
            self.store.save_objective(objective)
            self.store.save_plan(ExecutionPlan(
                id=f"plan{index}",
                objective_id=objective.id,
                strategy=Strategy.SEQUENTIAL,
                steps=[
                    PlanStep(id=f"step{index}a", kind="operation", operation="read", capability_id="filesystem"),
                    PlanStep(id=f"step{index}b", kind="operation", operation="review", capability_id="echo"),
                ],
            ))

        workflow = self.compiler.try_compile("code_review")
        self.assertIsNotNone(workflow)
        self.assertEqual(workflow.task_class, "code_review")
        self.assertEqual(len(workflow.steps), 2)
        self.assertEqual(workflow.status, WorkflowStatus.OBSERVED)

    def test_malformed_workflow_json_fails_closed(self):
        workflow = WorkflowDefinition(id="wf_corrupt", name="corrupt", task_class="test")
        self.store.save_workflow(workflow)
        self.store.conn.execute(
            "UPDATE workflows SET steps_json=? WHERE id=?",
            ("{", workflow.id),
        )
        self.store.conn.commit()
        with self.assertRaises(ValueError):
            self.store.get_workflow(workflow.id)

    def test_store_update_preserves_all_workflow_fields(self):
        workflow = WorkflowDefinition(
            id="wf1",
            name="original",
            task_class="test",
            inputs={"old": {"type": "str"}},
            preconditions=["cache_available"],
            steps=[{"id": "old", "operation": "old"}],
            capability_requirements=["old"],
            outputs={"old": {"type": "str"}},
        )
        self.store.save_workflow(workflow)
        workflow.name = "updated"
        workflow.task_class = "updated"
        workflow.inputs = {"new": {"type": "str"}}
        workflow.preconditions = ["input:new"]
        workflow.steps = [{"id": "new", "operation": "new"}]
        workflow.capability_requirements = ["new"]
        workflow.preferred_executors = ["new"]
        workflow.fallback_executors = ["fallback"]
        workflow.outputs = {"new": {"type": "str"}}
        self.store.save_workflow(workflow)

        stored = self.store.get_workflow("wf1")
        self.assertEqual(stored.name, "updated")
        self.assertEqual(stored.task_class, "updated")
        self.assertEqual(stored.inputs, {"new": {"type": "str"}})
        self.assertEqual(stored.preconditions, ["input:new"])
        self.assertEqual(stored.steps, [{"id": "new", "operation": "new"}])
        self.assertEqual(stored.capability_requirements, ["new"])
        self.assertEqual(stored.preferred_executors, ["new"])
        self.assertEqual(stored.fallback_executors, ["fallback"])
        self.assertEqual(stored.outputs, {"new": {"type": "str"}})

    def test_promote_workflow(self):
        wf = WorkflowDefinition(
            id="wf1",
            name="test_wf",
            task_class="test",
            status=WorkflowStatus.OBSERVED,
        )
        self.store.save_workflow(wf)
        for _ in range(5):
            self.compiler.record_workflow_use("wf1", True)

        updated = self.store.get_workflow("wf1")
        self.assertEqual(updated.status, WorkflowStatus.VERIFIED)
        self.assertEqual(updated.maturity, WorkflowMaturity.REPEAT)

    def test_promote_workflow_rejects_unverified_draft(self):
        wf = WorkflowDefinition(
            id="draft_wf",
            name="draft",
            task_class="test",
            status=WorkflowStatus.DRAFT,
        )
        self.store.save_workflow(wf)
        self.assertFalse(
            self.compiler.promote_workflow("draft_wf", WorkflowStatus.VERIFIED)
        )

    def test_advance_maturity(self):
        wf = WorkflowDefinition(
            id="wf1",
            name="test_wf",
            task_class="test",
            status=WorkflowStatus.VERIFIED,
            maturity=WorkflowMaturity.FIRST_RUN,
        )
        self.store.save_workflow(wf)
        for _ in range(5):
            self.compiler.record_workflow_use("wf1", True)

        ok = self.compiler.advance_maturity("wf1")
        self.assertTrue(ok)
        self.assertEqual(self.store.get_workflow("wf1").maturity, WorkflowMaturity.REPEAT)

        for _ in range(5):
            self.compiler.record_workflow_use("wf1", True)
        updated = self.store.get_workflow("wf1")
        self.assertEqual(updated.maturity, WorkflowMaturity.MATURE)

    def test_record_workflow_use_auto_promotes(self):
        wf = WorkflowDefinition(
            id="wf1",
            name="test_wf",
            task_class="test",
            status=WorkflowStatus.OBSERVED,
            maturity=WorkflowMaturity.FIRST_RUN,
        )
        self.store.save_workflow(wf)

        # Record 5 uses with 90% success
        for i in range(5):
            self.compiler.record_workflow_use("wf1", True)

        updated = self.store.get_workflow("wf1")
        self.assertEqual(updated.metrics["uses"], 5)
        self.assertEqual(updated.metrics["successes"], 5)
        # Should be promoted to VERIFIED after 5 uses with 90%+ success
        self.assertEqual(updated.status, WorkflowStatus.VERIFIED)

    def test_record_workflow_use_failure(self):
        wf = WorkflowDefinition(
            id="wf1",
            name="test_wf",
            task_class="test",
        )
        self.store.save_workflow(wf)

        self.compiler.record_workflow_use("wf1", False)
        updated = self.store.get_workflow("wf1")
        self.assertEqual(updated.metrics["failures"], 1)

    def test_get_workflow_stats(self):
        wf1 = WorkflowDefinition(id="wf1", name="wf1", task_class="test", status=WorkflowStatus.VERIFIED, maturity=WorkflowMaturity.MATURE)
        wf1.metrics = {"uses": 10, "successes": 9}
        wf2 = WorkflowDefinition(id="wf2", name="wf2", task_class="test", status=WorkflowStatus.OBSERVED, maturity=WorkflowMaturity.FIRST_RUN)
        wf3 = WorkflowDefinition(id="wf3", name="wf3", task_class="other", status=WorkflowStatus.CERTIFIED, maturity=WorkflowMaturity.MATURE)
        for wf in [wf1, wf2, wf3]:
            self.store.save_workflow(wf)

        stats = self.compiler.get_workflow_stats("test")
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["by_status"]["VERIFIED"], 1)
        self.assertEqual(stats["by_status"]["OBSERVED"], 1)
        self.assertEqual(stats["by_maturity"]["MATURE"], 1)
        self.assertEqual(stats["total_uses"], 10)


class TestWorkflowDefinitionModel(unittest.TestCase):
    def test_workflow_definition_to_from_dict(self):
        wf = WorkflowDefinition(
            id="wf1",
            name="test_wf",
            task_class="code_review",
            version=2,
            inputs={"file": {"type": "str"}},
            preconditions=["cache_available"],
            steps=[{"id": "s1", "operation": "op1"}],
            capability_requirements=["cap1"],
            preferred_executors=["cap1"],
            fallback_executors=["cap2"],
            cache_policy="semantic",
            approval_policy="auto",
            verification_policy="test",
            outputs={"report": {"type": "str"}},
            success_evidence=["ref1"],
            failure_handling={"retry": 2},
            metrics={"uses": 5},
            status=WorkflowStatus.CERTIFIED,
            maturity=WorkflowMaturity.MATURE,
        )
        d = wf.to_dict()
        self.assertEqual(d["status"], "CERTIFIED")
        self.assertEqual(d["maturity"], "MATURE")
        self.assertEqual(d["version"], 2)

        restored = WorkflowDefinition.from_dict(d)
        self.assertEqual(restored.id, "wf1")
        self.assertEqual(restored.status, WorkflowStatus.CERTIFIED)
        self.assertEqual(restored.maturity, WorkflowMaturity.MATURE)
        self.assertEqual(restored.version, 2)
        self.assertEqual(restored.inputs, {"file": {"type": "str"}})
        self.assertEqual(restored.steps, [{"id": "s1", "operation": "op1"}])


if __name__ == "__main__":
    unittest.main()