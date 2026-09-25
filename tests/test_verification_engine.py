"""Tests for the Verification Engine - state machine, verifier selection, retry/escalation."""

from __future__ import annotations

import os
import tempfile
import unittest

from agentos.models import (
    VerificationType,
    VerificationOutcome,
    VerificationRecord,
    TaskState,
)
from agentos.store import Store
from agentos.verification_engine import (
    VerificationPolicy,
    verification_required,
    select_verifier,
    VerificationEngine,
    run_verification_pipeline,
    DEFAULT_POLICIES,
)


class TestVerificationPolicy(unittest.TestCase):
    def test_default_policies_exist(self):
        self.assertIn("code_change", DEFAULT_POLICIES)
        self.assertIn("research", DEFAULT_POLICIES)
        self.assertIn("database", DEFAULT_POLICIES)
        self.assertIn("api", DEFAULT_POLICIES)
        self.assertIn("deterministic", DEFAULT_POLICIES)
        self.assertIn("default", DEFAULT_POLICIES)

    def test_code_change_policy(self):
        policy = DEFAULT_POLICIES["code_change"]
        self.assertEqual(policy.task_class, "code_change")
        self.assertIn(VerificationType.TEST, policy.required_types)
        self.assertIn(VerificationType.BUILD, policy.required_types)
        self.assertIn(VerificationType.DIFF, policy.required_types)
        self.assertEqual(policy.min_confidence, 0.8)
        self.assertEqual(policy.max_retries, 2)

    def test_deterministic_policy(self):
        policy = DEFAULT_POLICIES["deterministic"]
        self.assertIn(VerificationType.BUILD, policy.required_types)
        self.assertEqual(policy.min_confidence, 0.95)
        self.assertFalse(policy.independence_required)

    def test_verification_required_returns_policy(self):
        policy = verification_required("code_change")
        self.assertEqual(policy.task_class, "code_change")
        # Unknown task class falls back to default
        policy = verification_required("unknown_class")
        self.assertEqual(policy.task_class, "default")


class TestSelectVerifier(unittest.TestCase):
    def test_select_verifier_for_code_change(self):
        verifier_id, vtype = select_verifier("code_change", {}, independence_required=False)
        self.assertEqual(vtype, VerificationType.DIFF)
        self.assertEqual(verifier_id, "diff_verifier")

    def test_select_verifier_for_database(self):
        verifier_id, vtype = select_verifier("database", {})
        self.assertEqual(vtype, VerificationType.DATABASE)
        self.assertEqual(verifier_id, "db_verifier")

    def test_select_verifier_fallback(self):
        verifier_id, vtype = select_verifier("unknown", {})
        self.assertEqual(vtype, VerificationType.TEST)
        self.assertEqual(verifier_id, "test_verifier")


class TestVerificationEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "test.db"))
        self.engine = VerificationEngine(self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def test_start_verification_creates_records(self):
        records = self.engine.start_verification("task_123", "code_change", {"output": "test"})
        self.assertEqual(len(records), 3)  # TEST, BUILD, DIFF for code_change
        for r in records:
            self.assertIsInstance(r, VerificationRecord)
            self.assertEqual(r.task_id, "task_123")
            self.assertEqual(r.verifier, "diff_verifier" if r.verification_type == VerificationType.DIFF else "test_verifier" if r.verification_type == VerificationType.TEST else "build_verifier")

    def test_run_verification_accept(self):
        record = VerificationRecord(
            id="v1",
            task_id="task_123",
            verification_type=VerificationType.TEST,
            verifier="test_verifier",
        )
        def mock_executor(rec):
            return True, {"confidence": 0.9, "refs": ["ref1"]}, None
        result = self.engine.run_verification(record, mock_executor)
        self.assertEqual(result.result, VerificationOutcome.ACCEPT)
        self.assertEqual(result.confidence, 0.9)
        self.assertEqual(result.evidence_refs, ["ref1"])

    def test_run_verification_retry_with_feedback(self):
        record = VerificationRecord(
            id="v1",
            task_id="task_123",
            verification_type=VerificationType.TEST,
            verifier="test_verifier",
        )
        def mock_executor(rec):
            return True, {"confidence": 0.6, "refs": []}, "low confidence"
        result = self.engine.run_verification(record, mock_executor)
        # Below min_confidence (0.7) but above 0.5 -> RETRY_WITH_FEEDBACK
        self.assertEqual(result.result, VerificationOutcome.RETRY_WITH_FEEDBACK)

    def test_run_verification_retry_same_worker(self):
        record = VerificationRecord(
            id="v1",
            task_id="task_123",
            verification_type=VerificationType.TEST,
            verifier="test_verifier",
        )
        def mock_executor(rec):
            return False, {"confidence": 0.3, "refs": []}, "failed"
        result = self.engine.run_verification(record, mock_executor)
        self.assertEqual(result.result, VerificationOutcome.RETRY_SAME_WORKER)

    def test_evaluate_outcomes_empty_fails_closed(self):
        outcome, reason = self.engine.evaluate_outcomes("obj_empty", [], 1)
        self.assertEqual(outcome, VerificationOutcome.REJECT)
        self.assertIn("No verification records", reason)

    def test_evaluate_outcomes_all_accept(self):
        records = [
            VerificationRecord(id="v1", task_id="t1", verification_type=VerificationType.TEST, verifier="v", result=VerificationOutcome.ACCEPT),
            VerificationRecord(id="v2", task_id="t1", verification_type=VerificationType.BUILD, verifier="v", result=VerificationOutcome.ACCEPT),
        ]
        outcome, reason = self.engine.evaluate_outcomes("task_1", records, 0)
        self.assertEqual(outcome, VerificationOutcome.ACCEPT)

    def test_evaluate_outcomes_retry_with_feedback(self):
        records = [
            VerificationRecord(id="v1", task_id="t1", verification_type=VerificationType.TEST, verifier="v", result=VerificationOutcome.RETRY_WITH_FEEDBACK),
        ]
        outcome, reason = self.engine.evaluate_outcomes("task_1", records, 0)
        self.assertEqual(outcome, VerificationOutcome.RETRY_WITH_FEEDBACK)

    def test_evaluate_outcomes_escalate_after_max_retries(self):
        records = [
            VerificationRecord(id="v1", task_id="t1", verification_type=VerificationType.TEST, verifier="v", result=VerificationOutcome.RETRY_SAME_WORKER),
        ]
        # Attempt 2 (0-indexed) >= max_retries (2) -> escalate
        outcome, reason = self.engine.evaluate_outcomes("task_1", records, 2)
        self.assertEqual(outcome, VerificationOutcome.ESCALATE_WORKER)


class TestRunVerificationPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_pipeline_accept(self):
        def test_executor(rec):
            return True, {"confidence": 0.9, "refs": ["ref1"]}, None

        executor_fns = {
            VerificationType.TEST: test_executor,
            VerificationType.BUILD: test_executor,
            VerificationType.DIFF: test_executor,
        }

        outcome = run_verification_pipeline("task_1", "code_change", {"output": "test"}, self.store, executor_fns)
        self.assertEqual(outcome, VerificationOutcome.ACCEPT)


class TestVerificationRecordModel(unittest.TestCase):
    def test_verification_record_to_from_dict(self):
        record = VerificationRecord(
            id="v1",
            task_id="t1",
            verification_type=VerificationType.TEST,
            verifier="test_verifier",
            evidence_refs=["ref1", "ref2"],
            result=VerificationOutcome.ACCEPT,
            confidence=0.95,
        )
        d = record.to_dict()
        self.assertEqual(d["verification_type"], "TEST")
        self.assertEqual(d["result"], "ACCEPT")

        restored = VerificationRecord.from_dict(d)
        self.assertEqual(restored.id, "v1")
        self.assertEqual(restored.verification_type, VerificationType.TEST)
        self.assertEqual(restored.result, VerificationOutcome.ACCEPT)
        self.assertEqual(restored.evidence_refs, ["ref1", "ref2"])


if __name__ == "__main__":
    unittest.main()