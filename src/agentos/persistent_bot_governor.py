"""Persistent Bot Governor - materialization decision framework."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentos.models import PersistenceDecision, utc_now_iso, new_id
from agentos.store import Store
from agentos.role_classifier import RoleFactors, calculate_factors_for_role, classify_persistence


@dataclass
class MaterializationPlan:
    """Plan for bot materialization."""
    role_id: str
    decision: PersistenceDecision
    factors: RoleFactors
    estimated_cost: str = "UNKNOWN"
    required_resources: list[str] = None
    timeline: str = ""
    approval_required: bool = False
    created_at: str = ""

    def __post_init__(self):
        if self.required_resources is None:
            self.required_resources = []
        if not self.created_at:
            self.created_at = utc_now_iso()

    def to_dict(self) -> dict[str, Any]:
        return {
            "role_id": self.role_id,
            "decision": self.decision.value,
            "factors": self.factors.to_dict() if self.factors is not None else {},
            "estimated_cost": self.estimated_cost,
            "required_resources": list(self.required_resources or []),
            "timeline": self.timeline,
            "approval_required": self.approval_required,
            "created_at": self.created_at,
        }


class PersistentBotGovernor:
    """Governor for persistent bot materialization decisions."""

    def __init__(self, store: Store):
        self.store = store
        self.pending_decisions: list[MaterializationPlan] = []

    def evaluate_virtual_role(self, role_id: str) -> MaterializationPlan:
        """Evaluate a virtual role for materialization."""
        factors = calculate_factors_for_role(role_id, self.store)
        decision = classify_persistence(factors)

        plan = MaterializationPlan(
            role_id=role_id,
            decision=decision,
            factors=factors,
            estimated_cost=self._estimate_cost(decision, factors),
            required_resources=self._required_resources(decision, factors),
            timeline=self._estimate_timeline(decision),
            approval_required=decision == PersistenceDecision.CREATE_PERSISTENT,
        )

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

        if plan.approval_required:
            self.pending_decisions.append(plan)

        return plan

    def _estimate_cost(self, decision: PersistenceDecision, factors: RoleFactors) -> str:
        """Estimate materialization cost."""
        if decision == PersistenceDecision.CREATE_PERSISTENT:
            if factors.browser_session_affinity > 0.7:
                return "HIGH (cloud browser + Grok subscription)"
            if factors.computer_affinity > 0.7:
                return "MEDIUM (dedicated computer)"
            return "MEDIUM (persistent runtime)"
        if decision in (PersistenceDecision.CONVERT_WORKFLOW, PersistenceDecision.CONVERT_SKILL):
            return "LOW (compile from existing executions)"
        if decision == PersistenceDecision.EPHEMERAL:
            return "NEGLIGIBLE (on-demand worker)"
        if decision == PersistenceDecision.TOOL:
            return "NEGLIGIBLE (deterministic function)"
        return "ZERO (remains virtual)"

    def _required_resources(self, decision: PersistenceDecision, factors: RoleFactors) -> list[str]:
        """List required resources for materialization."""
        resources = []

        if decision == PersistenceDecision.CREATE_PERSISTENT:
            if factors.browser_session_affinity > 0.7:
                resources.extend(["Grok subscription", "Cloud browser", "Auth session"])
            if factors.computer_affinity > 0.7:
                resources.extend(["Dedicated computer", "Persistent storage"])
            if factors.unique_permissions >= 2:
                resources.append("Credential isolation")

        if decision in (PersistenceDecision.CONVERT_WORKFLOW, PersistenceDecision.CONVERT_SKILL):
            resources.append("Execution history for compilation")

        if decision == PersistenceDecision.TOOL:
            resources.append("Deterministic function implementation")

        return resources

    def _estimate_timeline(self, decision: PersistenceDecision) -> str:
        if decision == PersistenceDecision.CREATE_PERSISTENT:
            return "1-2 weeks (provisioning + approval)"
        if decision in (PersistenceDecision.CONVERT_WORKFLOW, PersistenceDecision.CONVERT_SKILL):
            return "1-3 days (compile from history)"
        return "Immediate"

    def approve_materialization(self, role_id: str, approved: bool,
                                approver: str = "") -> MaterializationPlan | None:
        """Approve or reject a pending materialization."""
        for i, plan in enumerate(self.pending_decisions):
            if plan.role_id == role_id:
                if approved:
                    plan.decision = PersistenceDecision.CREATE_PERSISTENT
                    plan.approval_required = False
                    self.pending_decisions.pop(i)
                    self._save_plan_decision(plan, "APPROVED", approver)
                    return plan
                else:
                    plan.decision = PersistenceDecision.KEEP_VIRTUAL
                    plan.approval_required = False
                    self.pending_decisions.pop(i)
                    self._save_plan_decision(plan, "REJECTED", approver)
                    return plan
        return None

    def _save_plan_decision(
        self,
        plan: MaterializationPlan,
        approval_status: str,
        approver: str,
    ) -> None:
        factors = plan.factors
        self.store.save_persistence_decision({
            "role_id": plan.role_id,
            "task_frequency": factors.task_frequency if factors else 0,
            "persistent_context_value": factors.persistent_context_value if factors else 0.0,
            "computer_affinity": factors.computer_affinity if factors else 0.0,
            "browser_session_affinity": factors.browser_session_affinity if factors else 0.0,
            "unique_permissions": factors.unique_permissions if factors else 0,
            "supervisory_value": factors.supervisory_value if factors else 0.0,
            "workflow_repeatability": factors.workflow_repeatability if factors else 0.0,
            "can_skill_replace": int(factors.can_skill_replace) if factors else 0,
            "can_workflow_replace": int(factors.can_workflow_replace) if factors else 0,
            "can_ephemeral_worker_replace": int(factors.can_ephemeral_worker_replace) if factors else 0,
            "measured_usage_cost": factors.measured_usage_cost if factors else "UNKNOWN",
            "decision": plan.decision.value,
            "approval_status": approval_status,
            "approved_by": approver,
        })

    def get_pending_approvals(self) -> list[MaterializationPlan]:
        return self.pending_decisions

    def evaluate_all_virtual_roles(self) -> list[MaterializationPlan]:
        """Evaluate all known virtual roles."""
        # In real system, would query role registry for virtual roles
        # For now return empty
        return []

    def get_materialization_summary(self) -> dict[str, Any]:
        """Get summary of materialization decisions."""
        # Would query persistence_decisions table
        return {
            "pending_approvals": len(self.pending_decisions),
            "decisions_by_type": {},
            "total_evaluated": 0,
        }