"""Verification Engine - state machine, verification selection, retry/escalation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from agentos.models import (
    TaskState,
    VerificationType,
    VerificationOutcome,
    VerificationRecord,
    utc_now_iso,
    new_id,
)
from agentos.store import Store


@dataclass
class VerificationPolicy:
    """Policy for verification requirements per task class."""

    task_class: str
    required_types: list[VerificationType] = field(default_factory=list)
    min_confidence: float = 0.7
    max_retries: int = 2
    escalation_path: list[str] = field(default_factory=list)
    independence_required: bool = False


DEFAULT_POLICIES: dict[str, VerificationPolicy] = {
    "code_change": VerificationPolicy(
        task_class="code_change",
        required_types=[VerificationType.TEST, VerificationType.BUILD, VerificationType.DIFF],
        min_confidence=0.8,
        max_retries=2,
        escalation_path=["ESCALATE_WORKER", "ESCALATE_SUPERVISOR", "ESCALATE_HUMAN"],
    ),
    "research": VerificationPolicy(
        task_class="research",
        required_types=[VerificationType.SOURCE, VerificationType.EXTERNAL_CONFIRMATION],
        min_confidence=0.7,
        max_retries=1,
        escalation_path=["ESCALATE_SUPERVISOR", "ESCALATE_HUMAN"],
    ),
    "database": VerificationPolicy(
        task_class="database",
        required_types=[VerificationType.DATABASE, VerificationType.RUNTIME],
        min_confidence=0.85,
        max_retries=2,
        escalation_path=["ESCALATE_WORKER", "ESCALATE_SUPERVISOR"],
    ),
    "api": VerificationPolicy(
        task_class="api",
        required_types=[VerificationType.API, VerificationType.RUNTIME],
        min_confidence=0.8,
        max_retries=2,
        escalation_path=["ESCALATE_WORKER", "ESCALATE_SUPERVISOR"],
    ),
    "deterministic": VerificationPolicy(
        task_class="deterministic",
        required_types=[VerificationType.BUILD],
        min_confidence=0.95,
        max_retries=1,
        independence_required=False,
    ),
    "default": VerificationPolicy(
        task_class="default",
        required_types=[VerificationType.TEST],
        min_confidence=0.7,
        max_retries=2,
        escalation_path=["ESCALATE_WORKER", "ESCALATE_SUPERVISOR", "ESCALATE_HUMAN"],
    ),
}


def verification_required(task_class: str) -> VerificationPolicy:
    """Return the verification policy for a task class."""
    return DEFAULT_POLICIES.get(task_class, DEFAULT_POLICIES["default"])


def select_verifier(task_class: str, result: dict[str, Any],
                    independence_required: bool = False) -> tuple[str, VerificationType]:
    """Select the cheapest sufficient verifier for a task result.

    Returns (verifier_id, verification_type).
    """
    policy = verification_required(task_class)

    # Prefer deterministic checks first
    if VerificationType.DIFF in policy.required_types:
        return ("diff_verifier", VerificationType.DIFF)
    if VerificationType.BUILD in policy.required_types:
        return ("build_verifier", VerificationType.BUILD)
    if VerificationType.DATABASE in policy.required_types:
        return ("db_verifier", VerificationType.DATABASE)
    if VerificationType.API in policy.required_types:
        return ("api_verifier", VerificationType.API)
    if VerificationType.SOURCE in policy.required_types:
        return ("source_verifier", VerificationType.SOURCE)
    if VerificationType.EXTERNAL_CONFIRMATION in policy.required_types:
        return ("ext_conf_verifier", VerificationType.EXTERNAL_CONFIRMATION)
    if VerificationType.TEST in policy.required_types:
        return ("test_verifier", VerificationType.TEST)
    if VerificationType.RUNTIME in policy.required_types:
        return ("runtime_verifier", VerificationType.RUNTIME)

    # Fallback
    return ("generic_verifier", VerificationType.CUSTOM)


_VERIFIER_BY_TYPE = {
    VerificationType.TEST: "test_verifier",
    VerificationType.BUILD: "build_verifier",
    VerificationType.RUNTIME: "runtime_verifier",
    VerificationType.DIFF: "diff_verifier",
    VerificationType.API: "api_verifier",
    VerificationType.DATABASE: "db_verifier",
    VerificationType.SOURCE: "source_verifier",
    VerificationType.SCREENSHOT: "screenshot_verifier",
    VerificationType.EXTERNAL_CONFIRMATION: "ext_conf_verifier",
    VerificationType.INDEPENDENT_REVIEW: "independent_review_verifier",
    VerificationType.CUSTOM: "generic_verifier",
}


class VerificationEngine:
    """Manages the verification lifecycle for tasks."""

    def __init__(self, store: Store, policies: dict[str, VerificationPolicy] | None = None):
        self.store = store
        self.policies = policies or DEFAULT_POLICIES

    def start_verification(self, task_id: str, task_class: str,
                           result: dict[str, Any]) -> list[VerificationRecord]:
        """Start verification for a completed task. Returns list of verification records."""
        policy = self.policies.get(task_class, self.policies["default"])
        records = []

        for vtype in policy.required_types:
            verifier_id = _VERIFIER_BY_TYPE.get(vtype, "generic_verifier")
            record = VerificationRecord(
                id=new_id("verify"),
                task_id=task_id,
                verification_type=vtype,
                verifier=verifier_id,
                started_at=utc_now_iso(),
            )
            records.append(record)
            self.store.save_verification_record(record)

        return records

    def run_verification(self, record: VerificationRecord,
                         executor_fn: callable,
                         policy: VerificationPolicy | None = None) -> VerificationRecord:
        """Run a single verification using the provided executor function.

        executor_fn(record) -> (bool success, dict evidence, str failure_reason)
        """
        verification_policy = policy or self.policies["default"]
        try:
            success, evidence, failure_reason = executor_fn(record)
            record.evidence_refs = evidence.get("refs", []) if isinstance(evidence, dict) else []
            record.confidence = float(evidence.get("confidence", 0.0)) if isinstance(evidence, dict) else 0.0

            if success and record.confidence >= verification_policy.min_confidence:
                record.result = VerificationOutcome.ACCEPT
            elif record.confidence >= 0.5:
                record.result = VerificationOutcome.RETRY_WITH_FEEDBACK
                record.failure_reason = "Confidence below threshold"
            else:
                record.result = VerificationOutcome.RETRY_SAME_WORKER
                record.failure_reason = failure_reason or "Verification failed"

        except Exception as e:
            record.result = VerificationOutcome.RETRY_SAME_WORKER
            record.failure_reason = str(e)

        record.completed_at = utc_now_iso()
        self.store.save_verification_record(record)
        return record

    def evaluate_outcomes(self, task_id: str, records: list[VerificationRecord],
                          attempt: int, task_class: str | None = None) -> tuple[VerificationOutcome, str | None]:
        """Evaluate all verification outcomes and decide next action."""
        if not records:
            return VerificationOutcome.REJECT, "No verification records"
        policy = self.policies.get(
            task_class or self._infer_task_class(task_id),
            self.policies["default"],
        )

        # All accept?
        if all(r.result == VerificationOutcome.ACCEPT for r in records):
            return VerificationOutcome.ACCEPT, None

        # Any reject?
        if any(r.result == VerificationOutcome.REJECT for r in records):
            return VerificationOutcome.REJECT, "Verification rejected"

        # Retry with feedback?
        if any(r.result == VerificationOutcome.RETRY_WITH_FEEDBACK for r in records):
            if attempt < policy.max_retries:
                return VerificationOutcome.RETRY_WITH_FEEDBACK, "Feedback available for retry"
            else:
                return self._escalate(policy, attempt)

        # Retry same worker?
        if any(r.result == VerificationOutcome.RETRY_SAME_WORKER for r in records):
            if attempt < policy.max_retries:
                return VerificationOutcome.RETRY_SAME_WORKER, "Retry with same worker"
            else:
                return self._escalate(policy, attempt)

        return VerificationOutcome.REJECT, "No clear verification outcome"

    def _escalate(self, policy: VerificationPolicy, attempt: int) -> tuple[VerificationOutcome, str]:
        """Determine escalation path."""
        for esc in policy.escalation_path:
            if esc == "ESCALATE_WORKER" and attempt < policy.max_retries + 1:
                return VerificationOutcome.ESCALATE_WORKER, "Escalate to different worker"
            if esc == "ESCALATE_SUPERVISOR":
                return VerificationOutcome.ESCALATE_SUPERVISOR, "Escalate to supervisor"
            if esc == "ESCALATE_HUMAN":
                return VerificationOutcome.ESCALATE_HUMAN, "Escalate to human"
        return VerificationOutcome.REJECT, "Max retries exceeded"

    def _infer_task_class(self, task_id: str) -> str:
        """Infer task class from task_id prefix or stored metadata."""
        # Simple heuristic - in real system would query task metadata
        if task_id.startswith("code_"):
            return "code_change"
        if task_id.startswith("research_"):
            return "research"
        if task_id.startswith("db_"):
            return "database"
        if task_id.startswith("api_"):
            return "api"
        if task_id.startswith("det_"):
            return "deterministic"
        return "default"

    def get_verification_history(self, task_id: str) -> list[VerificationRecord]:
        """Get all verification records for a task."""
        return self.store.list_verification_records(task_id)


def run_verification_pipeline(task_id: str, task_class: str, result: dict[str, Any],
                              store: Store, executor_fns: dict[VerificationType, callable]) -> VerificationOutcome:
    """Convenience function to run full verification pipeline."""
    engine = VerificationEngine(store)
    records = engine.start_verification(task_id, task_class, result)

    for attempt in range(3):  # max 3 attempts
        for record in records:
            fn = executor_fns.get(record.verification_type)
            if fn:
                engine.run_verification(record, fn)

        outcome, reason = engine.evaluate_outcomes(task_id, records, attempt)
        if outcome == VerificationOutcome.ACCEPT:
            return outcome
        if outcome in (VerificationOutcome.REJECT, VerificationOutcome.ESCALATE_HUMAN):
            return outcome
        # Otherwise retry loop continues

    return VerificationOutcome.REJECT