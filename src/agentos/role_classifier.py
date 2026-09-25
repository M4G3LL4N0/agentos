"""Role Classifier - PersistenceDecision for bot materialization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentos.models import (
    PersistenceDecision,
    RoleForm,
    utc_now_iso,
    new_id,
)
from agentos.store import Store


@dataclass
class RoleFactors:
    """Factors for persistence decision."""

    task_frequency: int = 0
    persistent_context_value: float = 0.0
    computer_affinity: float = 0.0
    browser_session_affinity: float = 0.0
    unique_permissions: int = 0
    supervisory_value: float = 0.0
    workflow_repeatability: float = 0.0
    can_skill_replace: bool = False
    can_workflow_replace: bool = False
    can_ephemeral_worker_replace: bool = False
    measured_usage_cost: str = "UNKNOWN"

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_frequency": self.task_frequency,
            "persistent_context_value": self.persistent_context_value,
            "computer_affinity": self.computer_affinity,
            "browser_session_affinity": self.browser_session_affinity,
            "unique_permissions": self.unique_permissions,
            "supervisory_value": self.supervisory_value,
            "workflow_repeatability": self.workflow_repeatability,
            "can_skill_replace": int(self.can_skill_replace),
            "can_workflow_replace": int(self.can_workflow_replace),
            "can_ephemeral_worker_replace": int(self.can_ephemeral_worker_replace),
            "measured_usage_cost": self.measured_usage_cost,
        }


def classify_persistence(factors: RoleFactors) -> PersistenceDecision:
    """Classify role into persistence decision based on factors."""

    # High supervisory value + persistent context -> persistent bot
    if factors.supervisory_value > 0.7 and factors.persistent_context_value > 0.5:
        return PersistenceDecision.CREATE_PERSISTENT

    # Browser session affinity -> persistent bot (GrokBot)
    if factors.browser_session_affinity > 0.8 and factors.unique_permissions >= 1:
        return PersistenceDecision.CREATE_PERSISTENT

    # Computer affinity + unique permissions -> persistent bot
    if factors.computer_affinity > 0.7 and factors.unique_permissions >= 2:
        return PersistenceDecision.CREATE_PERSISTENT

    # High workflow repeatability -> workflow
    if factors.workflow_repeatability > 0.8:
        if factors.can_workflow_replace:
            return PersistenceDecision.CONVERT_WORKFLOW
        if factors.task_frequency >= 10:
            return PersistenceDecision.CONVERT_WORKFLOW

    # High task frequency + skill replacement possible -> skill
    if factors.task_frequency >= 20 and factors.can_skill_replace:
        return PersistenceDecision.CONVERT_SKILL

    # Moderate repeatability + skill possible -> skill
    if factors.workflow_repeatability > 0.5 and factors.can_skill_replace:
        return PersistenceDecision.CONVERT_SKILL

    # Low frequency, deterministic -> tool
    if factors.task_frequency < 5 and factors.can_ephemeral_worker_replace:
        return PersistenceDecision.TOOL

    # Very low frequency, no special needs -> ephemeral worker
    if factors.task_frequency < 3:
        return PersistenceDecision.EPHEMERAL

    # Default: keep virtual until more evidence
    return PersistenceDecision.KEEP_VIRTUAL


def calculate_factors_for_role(role_id: str, store: Store) -> RoleFactors:
    """Calculate persistence factors from stored data."""
    # Get execution metrics for this role
    metrics = store.get_executor_metrics(executor=role_id, limit=200)

    if not metrics:
        return RoleFactors()

    task_frequency = len(metrics)
    success_rate = sum(m.get("success", 0) for m in metrics) / max(task_frequency, 1)
    avg_retry = sum(m.get("retry_count", 0) for m in metrics) / max(task_frequency, 1)

    # Infer context value from human corrections and context size
    human_corrections = sum(m.get("human_correction", 0) for m in metrics)
    avg_context = sum(m.get("context_size", 0) for m in metrics) / max(task_frequency, 1)
    persistent_context_value = min(1.0, (human_corrections + avg_context / 1000) / max(task_frequency, 1))

    # Check computer/browser affinity from runtime
    runtimes = {m.get("runtime", "") for m in metrics}
    computer_affinity = 1.0 if "computer" in " ".join(runtimes).lower() else 0.0
    browser_session_affinity = 1.0 if "browser" in " ".join(runtimes).lower() else 0.0

    # Unique permissions - would come from capability registry
    # For now infer from task class diversity
    task_classes = {m.get("task_class", "") for m in metrics}
    unique_permissions = len(task_classes)

    # Supervisory value - does this role manage others?
    # Inferred from task classes containing "supervisor" or "chief"
    supervisory_tasks = sum(1 for m in metrics
                           if any(k in m.get("task_class", "").lower()
                                 for k in ["supervisor", "chief", "manage", "orchestrate"]))
    supervisory_value = supervisory_tasks / max(task_frequency, 1)

    # Workflow repeatability - same task class with same inputs
    workflow_repeatability = 0.0
    if task_frequency > 1:
        # Simple heuristic: if same task_class appears repeatedly with success
        class_counts = {}
        for m in metrics:
            tc = m.get("task_class", "")
            if tc:
                class_counts[tc] = class_counts.get(tc, 0) + 1
        max_class_freq = max(class_counts.values()) if class_counts else 0
        workflow_repeatability = min(1.0, max_class_freq / max(task_frequency, 1))

    # Can skill/workflow/ephemeral replace - heuristic
    can_skill_replace = success_rate > 0.8 and avg_retry < 1.5
    can_workflow_replace = workflow_repeatability > 0.7 and success_rate > 0.85
    can_ephemeral_worker_replace = task_frequency < 10 and success_rate > 0.7

    return RoleFactors(
        task_frequency=task_frequency,
        persistent_context_value=persistent_context_value,
        computer_affinity=computer_affinity,
        browser_session_affinity=browser_session_affinity,
        unique_permissions=unique_permissions,
        supervisory_value=supervisory_value,
        workflow_repeatability=workflow_repeatability,
        can_skill_replace=can_skill_replace,
        can_workflow_replace=can_workflow_replace,
        can_ephemeral_worker_replace=can_ephemeral_worker_replace,
        measured_usage_cost="UNKNOWN",  # Would come from usage_ledger
    )


class RoleClassifier:
    """Classifies roles for persistence decisions."""

    def __init__(self, store: Store):
        self.store = store

    def evaluate_role(self, role_id: str) -> tuple[PersistenceDecision, RoleFactors]:
        """Evaluate a role and return persistence decision."""
        factors = calculate_factors_for_role(role_id, self.store)
        decision = classify_persistence(factors)

        # Persist the decision
        self.store.save_persistence_decision({
            "id": new_id("persist"),
            "role_id": role_id,
            "task_frequency": factors.task_frequency,
            "persistent_context_value": factors.persistent_context_value,
            "computer_affinity": factors.computer_affinity,
            "browser_session_affinity": factors.browser_session_affinity,
            "unique_permissions": factors.unique_permissions,
            "supervisory_value": factors.supervisory_value,
            "workflow_repeatability": factors.workflow_repeatability,
            "can_skill_replace": int(factors.can_skill_replace),
            "can_workflow_replace": int(factors.can_workflow_replace),
            "can_ephemeral_worker_replace": int(factors.can_ephemeral_worker_replace),
            "measured_usage_cost": factors.measured_usage_cost,
            "decision": decision.value,
            "created_at": utc_now_iso(),
        })

        return decision, factors

    def evaluate_all_roles(self) -> dict[str, tuple[PersistenceDecision, RoleFactors]]:
        """Evaluate all known roles from executor metrics."""
        # Get unique executors from metrics
        # In real system would query role registry
        # For now return empty
        return {}

    def get_persistence_decision(self, role_id: str) -> dict[str, Any] | None:
        """Get latest persistence decision for a role."""
        # Would query persistence_decisions table
        return None