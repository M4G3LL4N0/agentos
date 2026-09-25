"""Autonomy Model - progressive autonomy states with evidence-based promotion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentos.models import (
    AutonomyState,
    utc_now_iso,
    new_id,
)
from agentos.store import Store


# Promotion thresholds (evidence required for each transition)
AUTONOMY_THRESHOLDS = {
    AutonomyState.DISCOVERED: {
        "next": AutonomyState.OBSERVE,
        "min_successful_verified": 0,
        "max_failures": 100,
        "min_verification_pass_rate": 0.0,
        "max_security_incidents": 100,
        "max_human_corrections": 100,
        "max_scope_violations": 100,
    },
    AutonomyState.OBSERVE: {
        "next": AutonomyState.PROPOSE,
        "min_successful_verified": 3,
        "max_failures": 5,
        "min_verification_pass_rate": 0.5,
        "max_security_incidents": 0,
        "max_human_corrections": 2,
        "max_scope_violations": 0,
    },
    AutonomyState.PROPOSE: {
        "next": AutonomyState.APPROVAL_REQUIRED,
        "min_successful_verified": 10,
        "max_failures": 3,
        "min_verification_pass_rate": 0.7,
        "max_security_incidents": 0,
        "max_human_corrections": 1,
        "max_scope_violations": 0,
    },
    AutonomyState.APPROVAL_REQUIRED: {
        "next": AutonomyState.LIMITED_AUTONOMY,
        "min_successful_verified": 25,
        "max_failures": 2,
        "min_verification_pass_rate": 0.8,
        "max_security_incidents": 0,
        "max_human_corrections": 0,
        "max_scope_violations": 0,
    },
    AutonomyState.LIMITED_AUTONOMY: {
        "next": AutonomyState.CERTIFIED_AUTONOMY,
        "min_successful_verified": 50,
        "max_failures": 1,
        "min_verification_pass_rate": 0.9,
        "max_security_incidents": 0,
        "max_human_corrections": 0,
        "max_scope_violations": 0,
    },
    AutonomyState.CERTIFIED_AUTONOMY: {
        "next": None,
        "min_successful_verified": 100,
        "max_failures": 0,
        "min_verification_pass_rate": 0.95,
        "max_security_incidents": 0,
        "max_human_corrections": 0,
        "max_scope_violations": 0,
    },
}

# Revocation triggers (automatic demotion)
REVOCATION_TRIGGERS = {
    "security_incident": AutonomyState.REVOKED,
    "scope_violation": AutonomyState.SUSPENDED,
    "verification_failure_rate": 0.3,  # If pass rate drops below 30%
    "human_correction_spike": 3,  # 3+ corrections in recent window
}


@dataclass
class AutonomyRecord:
    """Autonomy state record for an entity."""

    id: str
    entity_type: str  # worker, tool, mcp, a2a_peer, workflow, skill, provider, browser_capability
    entity_id: str
    state: AutonomyState = AutonomyState.DISCOVERED
    successful_verified_tasks: int = 0
    failures: int = 0
    security_incidents: int = 0
    human_corrections: int = 0
    scope_violations: int = 0
    verification_pass_rate: float = 0.0
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = utc_now_iso()
        if not self.updated_at:
            self.updated_at = utc_now_iso()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "state": self.state.value,
            "successful_verified_tasks": self.successful_verified_tasks,
            "failures": self.failures,
            "security_incidents": self.security_incidents,
            "human_corrections": self.human_corrections,
            "scope_violations": self.scope_violations,
            "verification_pass_rate": self.verification_pass_rate,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AutonomyRecord":
        return cls(
            id=str(data["id"]),
            entity_type=str(data.get("entity_type", "")),
            entity_id=str(data.get("entity_id", "")),
            state=AutonomyState(str(data.get("state", AutonomyState.DISCOVERED.value))),
            successful_verified_tasks=int(data.get("successful_verified_tasks", 0) or 0),
            failures=int(data.get("failures", 0) or 0),
            security_incidents=int(data.get("security_incidents", 0) or 0),
            human_corrections=int(data.get("human_corrections", 0) or 0),
            scope_violations=int(data.get("scope_violations", 0) or 0),
            verification_pass_rate=float(data.get("verification_pass_rate", 0.0) or 0.0),
            created_at=str(data.get("created_at") or utc_now_iso()),
            updated_at=str(data.get("updated_at") or utc_now_iso()),
        )


class AutonomyModel:
    """Manages autonomy state transitions for entities."""

    def __init__(self, store: Store):
        self.store = store

    def get_record(self, entity_type: str, entity_id: str) -> AutonomyRecord | None:
        """Get autonomy record for entity."""
        data = self.store.get_autonomy_record(entity_type, entity_id)
        return AutonomyRecord.from_dict(data) if data else None

    def create_record(self, entity_type: str, entity_id: str) -> AutonomyRecord:
        """Create new autonomy record."""
        record = AutonomyRecord(
            id=new_id("autonomy"),
            entity_type=entity_type,
            entity_id=entity_id,
            state=AutonomyState.DISCOVERED,
        )
        self.store.save_autonomy_record(record.to_dict())
        return record

    def get_or_create(self, entity_type: str, entity_id: str) -> AutonomyRecord:
        """Get existing or create new record."""
        record = self.get_record(entity_type, entity_id)
        if record:
            return record
        return self.create_record(entity_type, entity_id)

    def record_success(self, entity_type: str, entity_id: str,
                       verification_passed: bool = True) -> AutonomyRecord:
        """Record a successful verified task."""
        record = self.get_or_create(entity_type, entity_id)

        if verification_passed:
            record.successful_verified_tasks += 1
            # Update pass rate
            total = record.successful_verified_tasks + record.failures
            record.verification_pass_rate = record.successful_verified_tasks / total if total > 0 else 1.0
        else:
            record.failures += 1
            total = record.successful_verified_tasks + record.failures
            record.verification_pass_rate = record.successful_verified_tasks / total if total > 0 else 0.0

        record.updated_at = utc_now_iso()
        self._check_promotion(record)
        self._check_revocation(record)
        self.store.save_autonomy_record(record.to_dict())
        return record

    def record_failure(self, entity_type: str, entity_id: str) -> AutonomyRecord:
        """Record a task failure."""
        record = self.get_or_create(entity_type, entity_id)
        record.failures += 1
        total = record.successful_verified_tasks + record.failures
        record.verification_pass_rate = record.successful_verified_tasks / total if total > 0 else 0.0
        record.updated_at = utc_now_iso()
        self._check_revocation(record)
        self.store.save_autonomy_record(record.to_dict())
        return record

    def record_security_incident(self, entity_type: str, entity_id: str) -> AutonomyRecord:
        """Record a security incident - triggers immediate review."""
        record = self.get_or_create(entity_type, entity_id)
        record.security_incidents += 1
        record.updated_at = utc_now_iso()
        self._check_revocation(record)
        self.store.save_autonomy_record(record.to_dict())
        return record

    def record_human_correction(self, entity_type: str, entity_id: str) -> AutonomyRecord:
        """Record a human correction."""
        record = self.get_or_create(entity_type, entity_id)
        record.human_corrections += 1
        record.updated_at = utc_now_iso()
        self._check_revocation(record)
        self.store.save_autonomy_record(record.to_dict())
        return record

    def record_scope_violation(self, entity_type: str, entity_id: str) -> AutonomyRecord:
        """Record a scope violation."""
        record = self.get_or_create(entity_type, entity_id)
        record.scope_violations += 1
        record.updated_at = utc_now_iso()
        self._check_revocation(record)
        self.store.save_autonomy_record(record.to_dict())
        return record

    def _check_promotion(self, record: AutonomyRecord) -> bool:
        """Check if entity should be promoted to next autonomy state."""
        current_thresholds = AUTONOMY_THRESHOLDS.get(record.state)
        if not current_thresholds or current_thresholds["next"] is None:
            return False

        next_state = current_thresholds["next"]
        if (record.successful_verified_tasks >= current_thresholds["min_successful_verified"] and
            record.failures <= current_thresholds["max_failures"] and
            record.verification_pass_rate >= current_thresholds["min_verification_pass_rate"] and
            record.security_incidents <= current_thresholds["max_security_incidents"] and
            record.human_corrections <= current_thresholds["max_human_corrections"] and
            record.scope_violations <= current_thresholds["max_scope_violations"]):

            record.state = next_state
            record.updated_at = utc_now_iso()
            self.store.save_autonomy_record(record.to_dict())
            return True
        return False

    def _check_revocation(self, record: AutonomyRecord) -> bool:
        """Check if entity should be demoted/suspended/revoked."""
        # Security incident -> immediate revocation
        if record.security_incidents > 0 and record.state != AutonomyState.REVOKED:
            record.state = AutonomyState.REVOKED
            record.updated_at = utc_now_iso()
            self.store.save_autonomy_record(record.to_dict())
            return True

        # Scope violation -> suspend
        if record.scope_violations > 0 and record.state not in (AutonomyState.SUSPENDED, AutonomyState.REVOKED):
            record.state = AutonomyState.SUSPENDED
            record.updated_at = utc_now_iso()
            self.store.save_autonomy_record(record.to_dict())
            return True

        # Verification failure rate
        if record.verification_pass_rate < REVOCATION_TRIGGERS["verification_failure_rate"]:
            if record.state not in (AutonomyState.SUSPENDED, AutonomyState.REVOKED):
                record.state = AutonomyState.SUSPENDED
                record.updated_at = utc_now_iso()
                self.store.save_autonomy_record(record.to_dict())
                return True

        # Human correction spike
        if record.human_corrections >= REVOCATION_TRIGGERS["human_correction_spike"]:
            if record.state not in (AutonomyState.SUSPENDED, AutonomyState.REVOKED):
                record.state = AutonomyState.SUSPENDED
                record.updated_at = utc_now_iso()
                self.store.save_autonomy_record(record.to_dict())
                return True

        return False

    def can_act_autonomously(self, entity_type: str, entity_id: str,
                              action_scope: str = "default") -> tuple[bool, str]:
        """Check if entity can act autonomously for given scope."""
        record = self.get_record(entity_type, entity_id)
        if not record:
            return False, "no autonomy record"

        if record.state in (AutonomyState.SUSPENDED, AutonomyState.REVOKED):
            return False, f"autonomy {record.state.value}"

        if record.state == AutonomyState.DISCOVERED:
            return False, "discovered - no evidence"

        if record.state == AutonomyState.OBSERVE:
            return False, "observe only"

        if record.state == AutonomyState.PROPOSE:
            return False, "propose only - requires approval"

        if record.state == AutonomyState.APPROVAL_REQUIRED:
            return False, "approval required"

        if record.state == AutonomyState.LIMITED_AUTONOMY:
            # Limited autonomy - check scope
            if action_scope in ("read", "analyze", "suggest"):
                return True, "limited autonomy - read/analyze/suggest allowed"
            return False, "limited autonomy - scope not permitted"

        if record.state == AutonomyState.CERTIFIED_AUTONOMY:
            return True, "certified autonomy"

        return False, f"unknown state {record.state.value}"

    def promote_to(self, entity_type: str, entity_id: str,
                   target_state: AutonomyState | str) -> AutonomyRecord | None:
        """Manually promote (with evidence verification)."""
        try:
            target_state = AutonomyState(str(target_state))
        except ValueError:
            return None
        record = self.get_or_create(entity_type, entity_id)

        # Demotion targets do not require promotion evidence.
        if target_state in (AutonomyState.SUSPENDED, AutonomyState.REVOKED):
            record.state = target_state
            record.updated_at = utc_now_iso()
            self.store.save_autonomy_record(record.to_dict())
            return record

        thresholds = AUTONOMY_THRESHOLDS.get(target_state)
        if not thresholds:
            return None

        # Check evidence for promotion
        if (record.successful_verified_tasks >= thresholds["min_successful_verified"] and
            record.failures <= thresholds["max_failures"] and
            record.verification_pass_rate >= thresholds["min_verification_pass_rate"] and
            record.security_incidents <= thresholds["max_security_incidents"] and
            record.human_corrections <= thresholds["max_human_corrections"] and
            record.scope_violations <= thresholds["max_scope_violations"]):

            record.state = target_state
            record.updated_at = utc_now_iso()
            self.store.save_autonomy_record(record.to_dict())
            return record

        return None

    def get_autonomy_summary(self) -> dict[str, Any]:
        """Get summary of all autonomy states."""
        # Would query all records - simplified for now
        return {
            "states": {s.value: 0 for s in AutonomyState},
            "total_entities": 0,
            "certified": 0,
            "limited": 0,
            "suspended": 0,
            "revoked": 0,
        }