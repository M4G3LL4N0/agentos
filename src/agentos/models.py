"""Core domain models for AgentOS.

Every model is a dataclass with explicit ``to_dict``/``from_dict`` so the
objects survive serialization to JSON and back through the sqlite store.
No record here implies more than what exists: models only carry fields the
system actually reads or writes.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return [value] if value else []
    return [str(item) for item in (value or [])]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def utc_now_iso() -> str:
    return now()


class ObjectiveState(StrEnum):
    """Actual execution states of an objective.

    Transitions are driven by the engine; labels track real execution state,
    not aspirational metadata.
    """

    CREATED = "CREATED"
    READY = "READY"
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class Priority(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class VerificationStatus(StrEnum):
    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"


class ExecutionStatus(StrEnum):
    REQUESTED = "REQUESTED"
    ATTEMPTED = "ATTEMPTED"
    EXECUTED = "EXECUTED"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"


class ExecutionMode(StrEnum):
    """Execution mode for agent capabilities."""
    INSPECT = "inspect"      # Dry-run: show what would be done without executing
    SIMULATED = "simulated"  # Run with mocked/fake responses (no API calls)
    LIVE = "live"            # Actual execution with real API calls


class ResultState(StrEnum):
    """Normalized outcome of a single execution attempt.

    COMPLETED is only reachable from real (LIVE) execution; dry-run and
    simulated modes are capped at PARTIAL/UNKNOWN so AgentOS never claims
    execution it did not perform.
    """

    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    REFUSED = "REFUSED"
    INTERRUPTED = "INTERRUPTED"
    UNKNOWN = "UNKNOWN"


class VerificationState(StrEnum):
    """Normalized verification posture of a single execution attempt.

    VERIFIED is never pre-claimed: a fresh COMPLETED result lands in
    VERIFYING (pending) until a verifier actually runs.
    """

    UNVERIFIED = "UNVERIFIED"
    VERIFYING = "VERIFYING"
    VERIFIED = "VERIFIED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"


class CostPolicy(StrEnum):
    """Cost posture guiding provider/model selection (never a cost claim)."""

    FREE_FIRST = "FREE_FIRST"
    LOW_COST = "LOW_COST"
    BALANCED = "BALANCED"
    QUALITY_FIRST = "QUALITY_FIRST"
    EXPLICIT_PROVIDER = "EXPLICIT_PROVIDER"


def cost_status(amount: Any = None) -> str:
    """Honest cost status for an execution: "UNKNOWN" unless measured.

    ``amount`` is a measured charge (number or non-empty cost string).
    Anything else — None, missing, or the "UNKNOWN" sentinel — returns
    "UNKNOWN". AgentOS never fabricates costs.
    """
    if amount is None:
        return "UNKNOWN"
    if isinstance(amount, bool):
        return "UNKNOWN"
    if isinstance(amount, (int, float)):
        return "RECORDED"
    text = str(amount).strip()
    if not text or text.upper() == "UNKNOWN":
        return "UNKNOWN"
    return "RECORDED"


def _mode_value(mode: Any) -> str:
    return str(getattr(mode, "value", mode) or "").lower()


def _coerce_mode(mode: Any) -> str:
    """Coerce a stored mode value to its string form; default SIMULATED."""
    if mode is None or (isinstance(mode, str) and not mode.strip()):
        return ExecutionMode.SIMULATED.value
    return str(getattr(mode, "value", mode))


def _timed_out(adapter_result: Any) -> bool:
    output = getattr(adapter_result, "output", None)
    if isinstance(output, dict) and output.get("timed_out"):
        return True
    error = str(getattr(adapter_result, "error", None) or "").lower()
    return "timed out" in error or "timed_out" in error or "timeout" in error


def _policy_blocked(adapter_result: Any) -> bool:
    error = str(getattr(adapter_result, "error", None) or "").lower()
    if "policy_block" in error or "policy block" in error:
        return True
    evidence = getattr(adapter_result, "evidence", None) or []
    for item in evidence:
        kind = getattr(item, "kind", "")
        if isinstance(item, dict):
            kind = item.get("kind", "")
        if str(kind) == "policy_block":
            return True
    return False


def normalize_result(
    adapter_result: Any, mode: Any
) -> tuple[ResultState, VerificationState]:
    """Normalize an adapter outcome into a result x verification pair.

    Mapping (checked in order):

    | signal                              | result       | verification |
    |-------------------------------------|--------------|--------------|
    | timed out (output flag / err text)  | INTERRUPTED  | UNVERIFIED   |
    | policy_block (err text / evidence)  | REFUSED      | UNVERIFIED   |
    | blocked / approval-required error   | BLOCKED      | UNVERIFIED   |
    | ok=False                            | FAILED       | UNVERIFIED   |
    | ok=True + LIVE                      | COMPLETED    | VERIFYING    |
    | ok=True + SIMULATED                 | PARTIAL      | UNVERIFIED   |
    | ok=True + INSPECT (or anything else)| UNKNOWN      | UNVERIFIED   |

    INSPECT/SIMULATED never yield COMPLETED: a dry run or a fake run is
    not execution. VERIFIED is never returned here — verification pending
    (VERIFYING) must be resolved by an actual verifier run.
    """
    if _timed_out(adapter_result):
        return ResultState.INTERRUPTED, VerificationState.UNVERIFIED
    if _policy_blocked(adapter_result):
        return ResultState.REFUSED, VerificationState.UNVERIFIED
    error = str(getattr(adapter_result, "error", None) or "").lower()
    if "blocked" in error or "approval" in error:
        return ResultState.BLOCKED, VerificationState.UNVERIFIED
    if not bool(getattr(adapter_result, "ok", False)):
        return ResultState.FAILED, VerificationState.UNVERIFIED
    if _mode_value(mode) == ExecutionMode.LIVE.value:
        return ResultState.COMPLETED, VerificationState.VERIFYING
    if _mode_value(mode) == ExecutionMode.SIMULATED.value:
        return ResultState.PARTIAL, VerificationState.UNVERIFIED
    return ResultState.UNKNOWN, VerificationState.UNVERIFIED


class CapabilityType(StrEnum):
    AGENT = "agent"
    MODEL = "model"
    CLI = "cli"
    API = "api"
    MCP = "mcp"
    BROWSER = "browser"
    FILESYSTEM = "filesystem"
    REPOSITORY = "repository"
    WORKFLOW = "workflow"
    COMPUTER = "computer"
    TOOL = "tool"


class HealthStatus(StrEnum):
    UNKNOWN = "UNKNOWN"
    AVAILABLE = "AVAILABLE"
    OK = "OK"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"
    UNAVAILABLE = "UNAVAILABLE"
    CONFIGURED = "CONFIGURED"  # registered but not yet probed against real provider
    READY = "READY"          # configured AND verified usable for real execution
    DETECTED = "DETECTED"    # binary/source present but not yet configured/usable
    AUTH_REQUIRED = "AUTH_REQUIRED"
    MISCONFIGURED = "MISCONFIGURED"
    DISABLED = "DISABLED"


class ReadinessState(StrEnum):
    """Honest execution-readiness of a capability.

    Health says what a probe measured; readiness says what AgentOS may
    claim. DETECTED (source found, not executable) is never READY — only
    an executable/configured signal or real execution evidence promotes
    a capability to READY.
    """

    UNKNOWN = "UNKNOWN"
    UNAVAILABLE = "UNAVAILABLE"
    DETECTED = "DETECTED"
    AVAILABLE = "AVAILABLE"
    CONFIGURATION_REQUIRED = "CONFIGURATION_REQUIRED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    CONFIGURED = "CONFIGURED"
    READY = "READY"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"


#: Tuple of valid readiness values (same set as ReadinessState).
READINESS_STATES: tuple[str, ...] = tuple(s.value for s in ReadinessState)


def _coerce_health(health: Any) -> HealthStatus:
    try:
        return HealthStatus(str(getattr(health, "value", health)))
    except ValueError:
        return HealthStatus.UNKNOWN


def _adapter_binary_missing(adapter: Any) -> bool:
    """True when the adapter names a binary that is not executable.

    Adapters without a ``binary()`` method (in-process adapters such as
    shell/filesystem/echo) have no external binary to check: never missing.
    """
    resolver = getattr(adapter, "binary", None)
    if not callable(resolver):
        return False
    try:
        binary = resolver()
    except Exception:
        return True
    if not binary:
        return True
    try:
        return shutil.which(str(binary)) is None
    except Exception:
        return True


def readiness(adapter: Any, health: Any) -> str:
    """Derive honest readiness from a measured probe outcome + executability.

    ``health`` is the measured probe outcome supplied by the caller
    (``registry.probe``); this function never probes itself, so discovery
    stays cheap and single-probed. ``adapter`` is consulted only for
    binary executability: a probe-ok adapter whose binary is absent is
    DETECTED (source found, not executable) — never READY.
    """
    status = _coerce_health(health)
    if status in (
        HealthStatus.DOWN,
        HealthStatus.UNAVAILABLE,
        HealthStatus.DISABLED,
    ):
        return ReadinessState.UNAVAILABLE.value
    if adapter is not None and _adapter_binary_missing(adapter):
        return ReadinessState.DETECTED.value
    if status == HealthStatus.AUTH_REQUIRED:
        return ReadinessState.AUTH_REQUIRED.value
    if status == HealthStatus.MISCONFIGURED:
        return ReadinessState.CONFIGURATION_REQUIRED.value
    if status == HealthStatus.DEGRADED:
        return ReadinessState.DEGRADED.value
    if status == HealthStatus.CONFIGURED:
        return ReadinessState.CONFIGURED.value
    if status == HealthStatus.READY:
        return ReadinessState.READY.value
    if status in (HealthStatus.AVAILABLE, HealthStatus.OK):
        return ReadinessState.AVAILABLE.value
    if status == HealthStatus.DETECTED:
        return ReadinessState.DETECTED.value
    return ReadinessState.UNKNOWN.value


def evolve_readiness(current: str, signal: str) -> str:
    """Evolve a readiness state on a new signal. Never invents READY.

    Only an executable/configured/success signal promotes toward READY;
    anything else leaves the state unchanged or demotes it. Unknown
    current values are treated as UNKNOWN.
    """
    state = str(current or ReadinessState.UNKNOWN.value).upper()
    if state not in READINESS_STATES:
        state = ReadinessState.UNKNOWN.value
    text = str(signal or "").lower()
    if any(
        key in text
        for key in (
            "not executable",
            "not found",
            "missing",
            "no binary",
            "misconfigured",
            "not configured",
        )
    ):
        if state in (
            ReadinessState.DETECTED.value,
            ReadinessState.AVAILABLE.value,
            ReadinessState.UNKNOWN.value,
        ):
            return ReadinessState.CONFIGURATION_REQUIRED.value
        return state
    if any(
        key in text for key in ("auth", "unauthorized", "401", "forbidden", "login")
    ):
        return ReadinessState.AUTH_REQUIRED.value
    if any(
        key in text
        for key in ("fail", "error", "down", "unavailable", "timeout", "refused")
    ):
        if state in (
            ReadinessState.READY.value,
            ReadinessState.AVAILABLE.value,
            ReadinessState.CONFIGURED.value,
        ):
            return ReadinessState.DEGRADED.value
        if state == ReadinessState.DEGRADED.value:
            return ReadinessState.FAILED.value
        return state
    # Promotion requires genuine executability/configured evidence. The bare
    # "ok" substring over-matched neutral words ("broken", "token", "smoke"),
    # so it only counts as a standalone token (word boundary); longer signals
    # such as "executed ok" / "probe ok" still promote via "executed" /
    # "executable" or the token match. Branch order is unchanged: config-miss,
    # auth, and failure signals all run first, so record_execution's
    # "failed: ..." path still demotes before this branch is reached.
    if (
        any(
            key in text
            for key in (
                "executable",
                "executed",
                "success",
                "verified",
                "configured",
                "available",
            )
        )
        or re.search(r"\bok\b", text) is not None
    ):
        if "not " in text or "missing" in text or "fail" in text:
            return state
        if state == ReadinessState.FAILED.value:
            # Stickiness: one success moves FAILED to DEGRADED (recovering);
            # a second consecutive success promotes to READY. Recovery is
            # bounded and evidence-driven, never a single leap.
            return ReadinessState.DEGRADED.value
        if state in (
            ReadinessState.DETECTED.value,
            ReadinessState.AVAILABLE.value,
            ReadinessState.CONFIGURED.value,
            ReadinessState.DEGRADED.value,
            ReadinessState.UNKNOWN.value,
            ReadinessState.CONFIGURATION_REQUIRED.value,
        ):
            return ReadinessState.READY.value
        return state
    return state


class EventType(StrEnum):
    OBJECTIVE_CREATED = "objective.created"
    OBJECTIVE_READY = "objective.ready"
    OBJECTIVE_STARTED = "objective.started"
    OBJECTIVE_COMPLETED = "objective.completed"
    OBJECTIVE_FAILED = "objective.failed"
    OBJECTIVE_CANCELLED = "objective.cancelled"
    CAPABILITY_REGISTERED = "capability.registered"
    CAPABILITY_REMOVED = "capability.removed"
    CAPABILITY_DISCOVERED = "capability.discovered"
    CAPABILITY_SELECTED = "capability.selected"
    CAPABILITY_MISSING = "capability.missing"
    EXECUTION_STARTED = "execution.started"
    EXECUTION_COMPLETED = "execution.completed"
    EXECUTION_FAILED = "execution.failed"
    VERIFICATION_STARTED = "verification.started"
    VERIFICATION_PASSED = "verification.passed"
    VERIFICATION_FAILED = "verification.failed"
    RECOVERY_STARTED = "recovery.started"
    RECOVERY_COMPLETED = "recovery.completed"
    FEDERATION_JOB_ROUTED = "federation.job_routed"
    FEDERATION_JOB_SUBMITTED = "federation.job_submitted"
    FEDERATION_RESULT_RECEIVED = "federation.result_received"
    VERIFICATION_REQUIRED = "verification.required"
    GOVERNOR_ROUTE_RECORDED = "governor.route_recorded"
    USAGE_SNAPSHOT_IMPORTED = "usage.snapshot_imported"
    USAGE_ENTRY_RECORDED = "usage.entry_recorded"
    USAGE_ADVISORY = "usage.advisory"
    SUPERVISOR_STATUS = "supervisor.status"


@dataclass
class Evidence:
    """A discrete piece of recorded proof for an objective."""

    kind: str
    detail: str
    source: str = ""
    at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "detail": self.detail,
            "source": self.source,
            "at": self.at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Evidence":
        return cls(
            kind=str(data.get("kind", "")),
            detail=str(data.get("detail", "")),
            source=str(data.get("source", "")),
            at=str(data.get("at") or utc_now_iso()),
        )


@dataclass
class Objective:
    """A first-class objective.

    Hierarchical objectives are supported via ``parent_id`` but never required;
    a flat objective is fully functional.
    """

    id: str
    title: str
    description: str
    status: ObjectiveState = ObjectiveState.CREATED
    priority: Priority = Priority.MEDIUM
    constraints: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    parent_id: str | None = None
    parents_data: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    strategy: str | None = None
    result: dict[str, Any] | None = None
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    evidence: list[Evidence] = field(default_factory=list)
    next_action: str | None = None
    failure: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": self.status.value,
            "priority": self.priority.value,
            "constraints": list(self.constraints),
            "context": self.context,
            "parent_id": self.parent_id,
            "parents_data": dict(self.parents_data),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "strategy": self.strategy,
            "result": self.result,
            "verification_status": self.verification_status.value,
            "evidence": [e.to_dict() for e in self.evidence],
            "next_action": self.next_action,
            "failure": self.failure,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Objective":
        return cls(
            id=str(data["id"]),
            title=str(data["title"]),
            description=str(data.get("description", "")),
            status=ObjectiveState(str(data.get("status", ObjectiveState.CREATED.value))),
            priority=Priority(str(data.get("priority", Priority.MEDIUM.value))),
            constraints=list(data.get("constraints", []) or []),
            context=dict(data.get("context", {}) or {}),
            parent_id=data.get("parent_id"),
            parents_data=dict(data.get("parents_data", {}) or {}),
            created_at=str(data.get("created_at") or utc_now_iso()),
            updated_at=str(data.get("updated_at") or utc_now_iso()),
            strategy=data.get("strategy"),
            result=data.get("result"),
            verification_status=VerificationStatus(
                str(data.get("verification_status", VerificationStatus.UNVERIFIED.value))
            ),
            evidence=[
                Evidence.from_dict(e)
                for e in (data.get("evidence") or [])
                if isinstance(e, dict)
            ],
            next_action=data.get("next_action"),
            failure=data.get("failure"),
        )

    def mark_updated(self) -> None:
        self.updated_at = utc_now_iso()

    def add_evidence(self, evidence: Evidence) -> None:
        self.evidence.append(evidence)
        self.mark_updated()


@dataclass
class Capability:
    """Something AgentOS can use to accomplish an objective.

    Not every field is required for every capability; optional fields are
    ``None``/empty when unknown.
    """

    id: str
    name: str
    type: CapabilityType
    description: str
    operations: list[str] = field(default_factory=list)
    adapter: str = ""
    availability: bool = True
    health: HealthStatus = HealthStatus.UNKNOWN
    source: str = "native"
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    cost: dict[str, Any] | None = None
    latency: dict[str, Any] | None = None
    permissions: dict[str, Any] | None = None
    environment: dict[str, Any] | None = None
    persistence: dict[str, Any] | None = None
    constraints: dict[str, Any] | None = None
    verification: dict[str, Any] | None = None
    config: dict[str, Any] = field(default_factory=dict)
    # Honest readiness + execution evidence ledger (single source of truth).
    # Readiness is derived from probe outcome + binary executability
    # (see readiness()/evolve_readiness()); the counters below are the
    # persisted per-capability execution ledger updated by
    # registry.record_execution(). DETECTED is never READY.
    readiness: str = ReadinessState.UNKNOWN.value
    last_probe: str | None = None
    last_success: str | None = None
    last_failed: str | None = None
    execution_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    avg_duration_ms: float | None = None
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type.value,
            "description": self.description,
            "operations": list(self.operations),
            "adapter": self.adapter,
            "availability": self.availability,
            "health": self.health.value,
            "source": self.source,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "cost": self.cost,
            "latency": self.latency,
            "permissions": self.permissions,
            "environment": self.environment,
            "persistence": self.persistence,
            "constraints": self.constraints,
            "verification": self.verification,
            "config": self.config,
            "readiness": self.readiness,
            "last_probe": self.last_probe,
            "last_success": self.last_success,
            "last_failed": self.last_failed,
            "execution_count": self.execution_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "avg_duration_ms": self.avg_duration_ms,
            "last_error": self.last_error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Capability":
        readiness_value = str(
            data.get("readiness", ReadinessState.UNKNOWN.value)
        ).upper()
        if readiness_value not in READINESS_STATES:
            readiness_value = ReadinessState.UNKNOWN.value
        avg = data.get("avg_duration_ms")
        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            type=CapabilityType(str(data["type"])),
            description=str(data.get("description", "")),
            operations=list(data.get("operations", []) or []),
            adapter=str(data.get("adapter", "")),
            availability=bool(data.get("availability", True)),
            health=HealthStatus(str(data.get("health", HealthStatus.UNKNOWN.value))),
            source=str(data.get("source", "configured")),
            inputs=dict(data.get("inputs", {}) or {}),
            outputs=dict(data.get("outputs", {}) or {}),
            cost=data.get("cost"),
            latency=data.get("latency"),
            permissions=data.get("permissions"),
            environment=data.get("environment"),
            persistence=data.get("persistence"),
            constraints=data.get("constraints"),
            verification=data.get("verification") or {},
            config=dict(data.get("config", {}) or {}),
            readiness=readiness_value,
            last_probe=(
                str(data["last_probe"]) if data.get("last_probe") is not None else None
            ),
            last_success=(
                str(data["last_success"])
                if data.get("last_success") is not None
                else None
            ),
            last_failed=(
                str(data["last_failed"]) if data.get("last_failed") is not None else None
            ),
            execution_count=int(data.get("execution_count", 0) or 0),
            success_count=int(data.get("success_count", 0) or 0),
            failure_count=int(data.get("failure_count", 0) or 0),
            avg_duration_ms=float(avg) if avg is not None else None,
            last_error=(
                str(data["last_error"]) if data.get("last_error") is not None else None
            ),
        )

    def supports(self, operation: str) -> bool:
        return operation in self.operations

    def is_usable(self) -> bool:
        return self.availability and self.health not in (
            HealthStatus.DOWN,
            HealthStatus.UNAVAILABLE,
            HealthStatus.DISABLED,
        )


@dataclass
class Execution:
    """A single attempt to execute an objective through a capability."""

    id: str
    objective_id: str
    capability_id: str
    operation: str
    status: ExecutionStatus = ExecutionStatus.REQUESTED
    request: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    attempt: int = 1
    started_at: str = field(default_factory=utc_now_iso)
    finished_at: str | None = None
    evidence: list[Evidence] = field(default_factory=list)
    # Normalized record fields (all defaulted; old dicts load with defaults).
    mode: str = ExecutionMode.SIMULATED.value
    files_changed: list[str] = field(default_factory=list)
    git_diff_summary: str = ""
    cost_status: str = "UNKNOWN"
    cost_amount: Any = "UNKNOWN"
    parent_objective_id: str | None = None
    authorization: list[Any] = field(default_factory=list)
    result_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "objective_id": self.objective_id,
            "capability_id": self.capability_id,
            "operation": self.operation,
            "status": self.status.value,
            "request": self.request,
            "output": self.output,
            "error": self.error,
            "attempt": self.attempt,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "evidence": [e.to_dict() for e in self.evidence],
            "mode": self.mode,
            "files_changed": list(self.files_changed),
            "git_diff_summary": self.git_diff_summary,
            "cost_status": self.cost_status,
            "cost_amount": self.cost_amount,
            "parent_objective_id": self.parent_objective_id,
            "authorization": list(self.authorization),
            "result_summary": self.result_summary,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Execution":
        return cls(
            id=str(data["id"]),
            objective_id=str(data["objective_id"]),
            capability_id=str(data["capability_id"]),
            operation=str(data.get("operation", "")),
            status=ExecutionStatus(str(data.get("status", ExecutionStatus.REQUESTED.value))),
            request=dict(data.get("request", {}) or {}),
            output=dict(data.get("output", {}) or {}),
            error=data.get("error"),
            attempt=int(data.get("attempt", 1)),
            started_at=str(data.get("started_at") or utc_now_iso()),
            finished_at=data.get("finished_at"),
            evidence=[
                Evidence.from_dict(e)
                for e in (data.get("evidence") or [])
                if isinstance(e, dict)
            ],
            mode=_coerce_mode(data.get("mode")),
            files_changed=[str(f) for f in (data.get("files_changed", []) or [])],
            git_diff_summary=str(data.get("git_diff_summary", "") or ""),
            cost_status=str(data.get("cost_status", None) or "UNKNOWN"),
            cost_amount=data.get("cost_amount", "UNKNOWN")
            if data.get("cost_amount", "UNKNOWN") is not None
            else "UNKNOWN",
            parent_objective_id=data.get("parent_objective_id"),
            authorization=list(data.get("authorization", []) or []),
            result_summary=str(data.get("result_summary", "") or ""),
        )

    def finish(self, status: ExecutionStatus) -> None:
        self.status = status
        self.finished_at = utc_now_iso()


@dataclass
class VerificationResult:
    """Outcome of verifying an execution of an objective."""

    id: str
    objective_id: str
    execution_id: str
    verified: bool
    method: str
    evidence: list[Evidence]
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "objective_id": self.objective_id,
            "execution_id": self.execution_id,
            "verified": self.verified,
            "method": self.method,
            "evidence": [e.to_dict() for e in self.evidence],
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VerificationResult":
        return cls(
            id=str(data["id"]),
            objective_id=str(data["objective_id"]),
            execution_id=str(data["execution_id"]),
            verified=bool(data.get("verified", False)),
            method=str(data.get("method", "")),
            evidence=[
                Evidence.from_dict(e)
                for e in (data.get("evidence") or [])
                if isinstance(e, dict)
            ],
            created_at=str(data.get("created_at") or utc_now_iso()),
        )


@dataclass
class Event:
    """A persisted event record."""

    id: str
    event_type: EventType
    objective_id: str | None = None
    execution_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "event_type": self.event_type.value,
            "objective_id": self.objective_id,
            "execution_id": self.execution_id,
            "payload": self.payload,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Event":
        return cls(
            id=str(data["id"]),
            event_type=EventType(str(data["event_type"])),
            objective_id=data.get("objective_id"),
            execution_id=data.get("execution_id"),
            payload=dict(data.get("payload", {}) or {}),
            created_at=str(data.get("created_at") or utc_now_iso()),
        )


@dataclass
class Failure:
    """A recorded failure with preserved evidence (never converted to success)."""

    id: str
    objective_id: str
    execution_id: str | None
    failure_type: str
    detail: str
    attempt: int = 1
    recovered: bool = False
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "objective_id": self.objective_id,
            "execution_id": self.execution_id,
            "failure_type": self.failure_type,
            "detail": self.detail,
            "attempt": self.attempt,
            "recovered": self.recovered,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Failure":
        return cls(
            id=str(data["id"]),
            objective_id=str(data["objective_id"]),
            execution_id=data.get("execution_id"),
            failure_type=str(data.get("failure_type", "unknown")),
            detail=str(data.get("detail", "")),
            attempt=int(data.get("attempt", 1)),
            recovered=bool(data.get("recovered", False)),
            created_at=str(data.get("created_at") or utc_now_iso()),
        )


@dataclass
class Report:
    """Human- and machine-readable outcome of an engine pass."""

    objective_id: str
    state: ObjectiveState
    verification_status: VerificationStatus | None
    capability_id: str | None = None
    execution_id: str | None = None
    attempt: int = 0
    next_action: str | None = None
    result: dict[str, Any] | None = None
    failure: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective_id": self.objective_id,
            "state": self.state.value,
            "verification_status": (
                self.verification_status.value if self.verification_status else None
            ),
            "capability_id": self.capability_id,
            "execution_id": self.execution_id,
            "attempt": self.attempt,
            "next_action": self.next_action,
            "result": self.result,
            "failure": self.failure,
        }


class Strategy(StrEnum):
    """Execution strategies the router can assign to an objective."""

    DIRECT = "DIRECT"
    SEQUENTIAL = "SEQUENTIAL"
    PARALLEL = "PARALLEL"
    DELEGATED = "DELEGATED"
    ITERATIVE = "ITERATIVE"
    DECOMPOSE = "DECOMPOSE"
    DECOMPOSE_THEN_EXECUTE = "DECOMPOSE_THEN_EXECUTE"
    RESEARCH_THEN_EXECUTE = "RESEARCH_THEN_EXECUTE"
    BUILD_THEN_VERIFY = "BUILD_THEN_VERIFY"
    REVIEW_THEN_REVISE = "REVIEW_THEN_REVISE"
    GRAPH = "GRAPH"


class Role(StrEnum):
    """Temporary roles: capability assignments around an objective.

    Roles are not persistent agents; they exist inside a plan and the
    topology contracts when the objective completes.
    """

    RESEARCHER = "researcher"
    ARCHITECT = "architect"
    BUILDER = "builder"
    TESTER = "tester"
    EVALUATOR = "evaluator"
    RED_TEAM = "red_team"
    ANALYST = "analyst"


@dataclass
class PlanStep:
    """One step of an execution plan."""

    id: str
    kind: str  # operation | verify
    operation: str
    capability_id: str | None = None
    role: Role | None = None
    params: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: float = 120.0
    depends_on: list[str] = field(default_factory=list)
    parallelizable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "operation": self.operation,
            "capability_id": self.capability_id,
            "role": self.role.value if self.role else None,
            "params": self.params,
            "timeout_seconds": self.timeout_seconds,
            "depends_on": list(self.depends_on),
            "parallelizable": bool(self.parallelizable),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlanStep":
        role = data.get("role")
        return cls(
            id=str(data.get("id") or new_id("step")),
            kind=str(data.get("kind", "operation")),
            operation=str(data.get("operation", "")),
            capability_id=data.get("capability_id"),
            role=Role(str(role)) if role else None,
            params=dict(data.get("params", {}) or {}),
            timeout_seconds=float(data.get("timeout_seconds", 120.0)),
            depends_on=[
                str(d) for d in (data.get("depends_on") or []) if d
            ],
            parallelizable=bool(data.get("parallelizable", False)),
        )


@dataclass
class ExecutionPlan:
    """A capability-aware execution strategy with recorded rationale."""

    id: str
    objective_id: str
    strategy: Strategy
    steps: list[PlanStep] = field(default_factory=list)
    rationale: str = ""
    roles: list[Role] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "objective_id": self.objective_id,
            "strategy": self.strategy.value,
            "steps": [s.to_dict() for s in self.steps],
            "rationale": self.rationale,
            "roles": [r.value for r in self.roles],
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExecutionPlan":
        return cls(
            id=str(data["id"]),
            objective_id=str(data["objective_id"]),
            strategy=Strategy(str(data.get("strategy", Strategy.DIRECT.value))),
            steps=[
                PlanStep.from_dict(s)
                for s in (data.get("steps") or [])
                if isinstance(s, dict)
            ],
            rationale=str(data.get("rationale", "")),
            roles=[Role(str(r)) for r in (data.get("roles") or [])],
            created_at=str(data.get("created_at") or utc_now_iso()),
        )


@dataclass
class Pattern:
    """One recorded execution pattern for the learning layer.

    Lessons are statistics over verified outcomes, never unverified
    assumptions promoted to facts.
    """

    id: str
    objective_class: str
    operation: str
    strategy: str
    capability_id: str
    verified: bool
    failure_type: str | None = None
    recovery: str | None = None
    duration_ms: int | None = None
    lesson: str = ""
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "objective_class": self.objective_class,
            "operation": self.operation,
            "strategy": self.strategy,
            "capability_id": self.capability_id,
            "verified": self.verified,
            "failure_type": self.failure_type,
            "recovery": self.recovery,
            "duration_ms": self.duration_ms,
            "lesson": self.lesson,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Pattern":
        return cls(
            id=str(data["id"]),
            objective_class=str(data.get("objective_class", "")),
            operation=str(data.get("operation", "")),
            strategy=str(data.get("strategy", "")),
            capability_id=str(data.get("capability_id", "")),
            verified=bool(data.get("verified", False)),
            failure_type=data.get("failure_type"),
            recovery=data.get("recovery"),
            duration_ms=data.get("duration_ms"),
            lesson=str(data.get("lesson", "")),
            created_at=str(data.get("created_at") or utc_now_iso()),
        )


@dataclass
class GapAnalysis:
    """Structured capability-gap analysis for an unsupported objective."""

    id: str
    objective_id: str
    operation: str
    required_capability: str
    why_required: str
    alternatives: list[str] = field(default_factory=list)
    missing_interface: str = ""
    implementation_paths: list[str] = field(default_factory=list)
    risk: str = ""
    verification_plan: str = ""
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "objective_id": self.objective_id,
            "operation": self.operation,
            "required_capability": self.required_capability,
            "why_required": self.why_required,
            "alternatives": list(self.alternatives),
            "missing_interface": self.missing_interface,
            "implementation_paths": list(self.implementation_paths),
            "risk": self.risk,
            "verification_plan": self.verification_plan,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GapAnalysis":
        return cls(
            id=str(data["id"]),
            objective_id=str(data["objective_id"]),
            operation=str(data.get("operation", "")),
            required_capability=str(data.get("required_capability", "")),
            why_required=str(data.get("why_required", "")),
            alternatives=[str(a) for a in (data.get("alternatives") or [])],
            missing_interface=str(data.get("missing_interface", "")),
            implementation_paths=[str(p) for p in (data.get("implementation_paths") or [])],
            risk=str(data.get("risk", "")),
            verification_plan=str(data.get("verification_plan", "")),
            created_at=str(data.get("created_at") or utc_now_iso()),
        )


@dataclass
class QualityEvaluation:
    """Quality tier above verification: executed and verified, but is it good?"""

    id: str
    objective_id: str
    execution_id: str
    score: int
    grade: str  # pass | needs_review | fail
    findings: list[str] = field(default_factory=list)
    evaluator: str = "heuristic"
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "objective_id": self.objective_id,
            "execution_id": self.execution_id,
            "score": self.score,
            "grade": self.grade,
            "findings": list(self.findings),
            "evaluator": self.evaluator,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QualityEvaluation":
        return cls(
            id=str(data["id"]),
            objective_id=str(data["objective_id"]),
            execution_id=str(data["execution_id"]),
            score=int(data.get("score", 0)),
            grade=str(data.get("grade", "needs_review")),
            findings=[str(f) for f in (data.get("findings") or [])],
            evaluator=str(data.get("evaluator", "heuristic")),
            created_at=str(data.get("created_at") or utc_now_iso()),
        )


# =============================================================================
# LEARNING EXECUTION SYSTEM MODELS
# =============================================================================


class TaskState(StrEnum):
    """Canonical task lifecycle states."""

    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    REVIEW = "REVIEW"
    VERIFICATION = "VERIFICATION"
    VERIFIED_COMPLETE = "VERIFIED_COMPLETE"
    CANCELLED = "CANCELLED"


class VerificationType(StrEnum):
    """Types of verification - cheapest sufficient first."""

    TEST = "TEST"
    BUILD = "BUILD"
    RUNTIME = "RUNTIME"
    DIFF = "DIFF"
    API = "API"
    DATABASE = "DATABASE"
    SOURCE = "SOURCE"
    SCREENSHOT = "SCREENSHOT"
    EXTERNAL_CONFIRMATION = "EXTERNAL_CONFIRMATION"
    INDEPENDENT_REVIEW = "INDEPENDENT_REVIEW"
    CUSTOM = "CUSTOM"


class VerificationOutcome(StrEnum):
    """Outcomes of a verification attempt."""

    ACCEPT = "ACCEPT"
    RETRY_SAME_WORKER = "RETRY_SAME_WORKER"
    RETRY_WITH_FEEDBACK = "RETRY_WITH_FEEDBACK"
    ESCALATE_WORKER = "ESCALATE_WORKER"
    ESCALATE_SUPERVISOR = "ESCALATE_SUPERVISOR"
    ESCALATE_HUMAN = "ESCALATE_HUMAN"
    REJECT = "REJECT"


class LessonStatus(StrEnum):
    """Lesson promotion lifecycle."""

    CANDIDATE = "CANDIDATE"
    VALIDATED = "VALIDATED"
    ADOPTED = "ADOPTED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"
    REVOKED = "REVOKED"


class WorkflowStatus(StrEnum):
    """Workflow maturity lifecycle."""

    DRAFT = "DRAFT"
    OBSERVED = "OBSERVED"
    VERIFIED = "VERIFIED"
    CERTIFIED = "CERTIFIED"
    DEPRECATED = "DEPRECATED"
    REVOKED = "REVOKED"


class WorkflowMaturity(StrEnum):
    """Workflow execution maturity."""

    FIRST_RUN = "FIRST_RUN"
    REPEAT = "REPEAT"
    MATURE = "MATURE"
    EXCEPTION = "EXCEPTION"


class RoleForm(StrEnum):
    """Classification of role materialization."""

    PERSISTENT_BOT = "PERSISTENT_BOT"
    SKILL = "SKILL"
    WORKFLOW = "WORKFLOW"
    CAPABILITY = "CAPABILITY"
    EPHEMERAL_WORKER = "EPHEMERAL_WORKER"
    DETERMINISTIC_TOOL = "DETERMINISTIC_TOOL"
    PERSONA_ONLY = "PERSONA_ONLY"


class AutonomyState(StrEnum):
    """Progressive autonomy states."""

    DISCOVERED = "DISCOVERED"
    OBSERVE = "OBSERVE"
    PROPOSE = "PROPOSE"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    LIMITED_AUTONOMY = "LIMITED_AUTONOMY"
    CERTIFIED_AUTONOMY = "CERTIFIED_AUTONOMY"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"


class PersistenceDecision(StrEnum):
    """Bot materialization decisions."""

    CREATE_PERSISTENT = "CREATE_PERSISTENT"
    KEEP_VIRTUAL = "KEEP_VIRTUAL"
    CONVERT_SKILL = "CONVERT_SKILL"
    CONVERT_WORKFLOW = "CONVERT_WORKFLOW"
    EPHEMERAL = "EPHEMERAL"
    TOOL = "TOOL"
    RETIRE_IF_UNUSED = "RETIRE_IF_UNUSED"


@dataclass
class VerificationRecord:
    """A single verification attempt record."""

    id: str
    task_id: str
    verification_type: VerificationType
    verifier: str
    evidence_refs: list[str] = field(default_factory=list)
    result: VerificationOutcome = VerificationOutcome.REJECT
    confidence: float = 0.0
    started_at: str = field(default_factory=utc_now_iso)
    completed_at: str | None = None
    failure_reason: str | None = None
    recommendation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "verification_type": self.verification_type.value,
            "verifier": self.verifier,
            "evidence_refs": list(self.evidence_refs),
            "result": self.result.value,
            "confidence": self.confidence,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "failure_reason": self.failure_reason,
            "recommendation": self.recommendation,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VerificationRecord":
        return cls(
            id=str(data["id"]),
            task_id=str(data["task_id"]),
            verification_type=VerificationType(str(data.get("verification_type", VerificationType.CUSTOM.value))),
            verifier=str(data.get("verifier", "")),
            evidence_refs=_string_list(
                data.get("evidence_refs") or data.get("evidence_refs_json")
            ),
            result=VerificationOutcome(str(data.get("result", VerificationOutcome.REJECT.value))),
            confidence=float(data.get("confidence", 0.0)),
            started_at=str(data.get("started_at") or utc_now_iso()),
            completed_at=data.get("completed_at"),
            failure_reason=data.get("failure_reason"),
            recommendation=data.get("recommendation"),
        )


@dataclass
class LessonCandidate:
    """A candidate lesson from execution experience."""

    id: str
    task_class: str
    scope: str
    symptom: str
    root_cause: str
    lesson: str
    evidence_refs: list[str] = field(default_factory=list)
    confidence: float = 0.0
    recommended_change: str = ""
    status: LessonStatus = LessonStatus.CANDIDATE
    created_at: str = field(default_factory=utc_now_iso)
    last_validated_at: str | None = None
    times_observed: int = 1
    times_applied: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_class": self.task_class,
            "scope": self.scope,
            "symptom": self.symptom,
            "root_cause": self.root_cause,
            "lesson": self.lesson,
            "evidence_refs": list(self.evidence_refs),
            "confidence": self.confidence,
            "recommended_change": self.recommended_change,
            "status": self.status.value,
            "created_at": self.created_at,
            "last_validated_at": self.last_validated_at,
            "times_observed": self.times_observed,
            "times_applied": self.times_applied,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LessonCandidate":
        return cls(
            id=str(data["id"]),
            task_class=str(data.get("task_class", "")),
            scope=str(data.get("scope", "")),
            symptom=str(data.get("symptom", "")),
            root_cause=str(data.get("root_cause", "")),
            lesson=str(data.get("lesson", "")),
            evidence_refs=_string_list(
                data.get("evidence_refs") or data.get("evidence_refs_json")
            ),
            confidence=float(data.get("confidence", 0.0)),
            recommended_change=str(data.get("recommended_change", "")),
            status=LessonStatus(str(data.get("status", LessonStatus.CANDIDATE.value))),
            created_at=str(data.get("created_at") or utc_now_iso()),
            last_validated_at=data.get("last_validated_at"),
            times_observed=int(data.get("times_observed", 1) or 1),
            times_applied=int(data.get("times_applied", 0) or 0),
        )


@dataclass
class WorkflowDefinition:
    """A compiled reusable workflow from repeated successful execution."""

    id: str
    name: str
    task_class: str
    version: int = 1
    inputs: dict[str, Any] = field(default_factory=dict)
    preconditions: list[str] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    capability_requirements: list[str] = field(default_factory=list)
    preferred_executors: list[str] = field(default_factory=list)
    fallback_executors: list[str] = field(default_factory=list)
    cache_policy: str = "exact"
    approval_policy: str = "none"
    verification_policy: str = "test"
    outputs: dict[str, Any] = field(default_factory=dict)
    success_evidence: list[str] = field(default_factory=list)
    failure_handling: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    status: WorkflowStatus = WorkflowStatus.DRAFT
    maturity: WorkflowMaturity = WorkflowMaturity.FIRST_RUN
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "task_class": self.task_class,
            "version": self.version,
            "inputs": self.inputs,
            "preconditions": list(self.preconditions),
            "steps": list(self.steps),
            "capability_requirements": list(self.capability_requirements),
            "preferred_executors": list(self.preferred_executors),
            "fallback_executors": list(self.fallback_executors),
            "cache_policy": self.cache_policy,
            "approval_policy": self.approval_policy,
            "verification_policy": self.verification_policy,
            "outputs": self.outputs,
            "success_evidence": list(self.success_evidence),
            "failure_handling": self.failure_handling,
            "metrics": self.metrics,
            "status": self.status.value,
            "maturity": self.maturity.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkflowDefinition":
        # Handle both direct keys and _json suffix keys from database
        def get_json(key: str, default):
            val = data.get(key)
            if val is None:
                val = data.get(key + "_json")
            if val is None:
                return default
            if isinstance(val, str):
                try:
                    return json.loads(val)
                except (json.JSONDecodeError, TypeError) as exc:
                    raise ValueError("invalid persisted workflow JSON") from exc
            return val

        return cls(
            id=str(data["id"]),
            name=str(data.get("name", "")),
            task_class=str(data.get("task_class", "")),
            version=int(data.get("version", 1) or 1),
            inputs=get_json("inputs", {}),
            preconditions=get_json("preconditions", []),
            steps=[dict(s) for s in (get_json("steps", []) or []) if isinstance(s, dict)],
            capability_requirements=[str(c) for c in (get_json("capability_requirements", []) or [])],
            preferred_executors=[str(e) for e in (get_json("preferred_executors", []) or [])],
            fallback_executors=[str(e) for e in (get_json("fallback_executors", []) or [])],
            cache_policy=str(data.get("cache_policy", "exact")),
            approval_policy=str(data.get("approval_policy", "none")),
            verification_policy=str(data.get("verification_policy", "test")),
            outputs=dict(get_json("outputs", {}) or {}),
            success_evidence=[str(e) for e in (get_json("success_evidence", []) or [])],
            failure_handling=dict(get_json("failure_handling", {}) or {}),
            metrics=get_json("metrics", {}),
            status=WorkflowStatus(str(data.get("status", WorkflowStatus.DRAFT.value))),
            maturity=WorkflowMaturity(str(data.get("maturity", WorkflowMaturity.FIRST_RUN.value))),
            created_at=str(data.get("created_at") or utc_now_iso()),
            updated_at=str(data.get("updated_at") or utc_now_iso()),
        )


@dataclass
class SkillDefinition:
    """A compact structured skill from repeated supervisor procedures."""

    id: str
    objective: str
    inputs: dict[str, Any] = field(default_factory=dict)
    constraints: list[str] = field(default_factory=list)
    decision_rules: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    outputs: dict[str, Any] = field(default_factory=dict)
    verification: str = ""
    approval_boundary: str = ""
    failure_handling: dict[str, Any] = field(default_factory=dict)
    context_refs: list[str] = field(default_factory=list)
    version: int = 1
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "objective": self.objective,
            "inputs": self.inputs,
            "constraints": list(self.constraints),
            "decision_rules": list(self.decision_rules),
            "tools": list(self.tools),
            "outputs": self.outputs,
            "verification": self.verification,
            "approval_boundary": self.approval_boundary,
            "failure_handling": self.failure_handling,
            "context_refs": list(self.context_refs),
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SkillDefinition":
        def get_json(key: str, default):
            value = data.get(key)
            if value is None:
                value = data.get(key + "_json")
            if isinstance(value, str):
                try:
                    return json.loads(value)
                except (json.JSONDecodeError, TypeError) as exc:
                    raise ValueError("invalid persisted skill JSON") from exc
            return default if value is None else value

        return cls(
            id=str(data["id"]),
            objective=str(data.get("objective", "")),
            inputs=dict(get_json("inputs", {}) or {}),
            constraints=[str(c) for c in (get_json("constraints", []) or [])],
            decision_rules=[str(r) for r in (get_json("decision_rules", []) or [])],
            tools=[str(t) for t in (get_json("tools", []) or [])],
            outputs=dict(get_json("outputs", {}) or {}),
            verification=str(data.get("verification", "")),
            approval_boundary=str(data.get("approval_boundary", "")),
            failure_handling=dict(get_json("failure_handling", {}) or {}),
            context_refs=[str(r) for r in (get_json("context_refs", []) or [])],
            version=int(data.get("version", 1) or 1),
            created_at=str(data.get("created_at") or utc_now_iso()),
            updated_at=str(data.get("updated_at") or utc_now_iso()),
        )


@dataclass
class IncidentRecord:
    """Clustered incident from repeated failures."""

    id: str
    signature: str
    affected_jobs: list[str] = field(default_factory=list)
    affected_executors: list[str] = field(default_factory=list)
    first_seen: str = field(default_factory=utc_now_iso)
    last_seen: str = field(default_factory=utc_now_iso)
    probable_cause: str = ""
    confidence: float = 0.0
    status: str = "OPEN"
    mitigation: str = ""
    resolution: str = ""
    lesson_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "signature": self.signature,
            "affected_jobs": list(self.affected_jobs),
            "affected_executors": list(self.affected_executors),
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "probable_cause": self.probable_cause,
            "confidence": self.confidence,
            "status": self.status,
            "mitigation": self.mitigation,
            "resolution": self.resolution,
            "lesson_ref": self.lesson_ref,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IncidentRecord":
        affected_jobs = data.get("affected_jobs")
        if affected_jobs is None:
            affected_jobs = data.get("affected_jobs_json")
        if isinstance(affected_jobs, str):
            try:
                affected_jobs = json.loads(affected_jobs)
            except (json.JSONDecodeError, TypeError) as exc:
                raise ValueError("invalid persisted incident JSON") from exc
        affected_executors = data.get("affected_executors")
        if affected_executors is None:
            affected_executors = data.get("affected_executors_json")
        if isinstance(affected_executors, str):
            try:
                affected_executors = json.loads(affected_executors)
            except (json.JSONDecodeError, TypeError) as exc:
                raise ValueError("invalid persisted incident JSON") from exc
        return cls(
            id=str(data["id"]),
            signature=str(data.get("signature", "")),
            affected_jobs=[str(j) for j in (affected_jobs or [])],
            affected_executors=[str(e) for e in (affected_executors or [])],
            first_seen=str(data.get("first_seen") or utc_now_iso()),
            last_seen=str(data.get("last_seen") or utc_now_iso()),
            probable_cause=str(data.get("probable_cause", "")),
            confidence=float(data.get("confidence", 0.0)),
            status=str(data.get("status", "OPEN")),
            mitigation=str(data.get("mitigation", "")),
            resolution=str(data.get("resolution", "")),
            lesson_ref=data.get("lesson_ref"),
        )


@dataclass
class LowUsageMetrics:
    """Low-usage efficiency metrics (only measurable fields)."""

    requests_total: int = 0
    zero_agent_resolutions: int = 0
    cache_hits: int = 0
    artifact_reuse: int = 0
    workflow_uses: int = 0
    skill_uses: int = 0
    model_calls: int = 0
    premium_calls: int = 0
    grok_calls: int = 0
    average_team_size: float = 1.0
    verification_required_rate: float = 0.0
    verification_pass_rate: float = 0.0
    retries: int = 0
    duplicate_tasks_prevented: int = 0
    human_escalations: int = 0
    lessons_created: int = 0
    lessons_adopted: int = 0
    workflow_compilations: int = 0
    autonomy_promotions: int = 0
    incident_clusters: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "requests_total": self.requests_total,
            "zero_agent_resolutions": self.zero_agent_resolutions,
            "cache_hits": self.cache_hits,
            "artifact_reuse": self.artifact_reuse,
            "workflow_uses": self.workflow_uses,
            "skill_uses": self.skill_uses,
            "model_calls": self.model_calls,
            "premium_calls": self.premium_calls,
            "grok_calls": self.grok_calls,
            "average_team_size": self.average_team_size,
            "verification_required_rate": self.verification_required_rate,
            "verification_pass_rate": self.verification_pass_rate,
            "retries": self.retries,
            "duplicate_tasks_prevented": self.duplicate_tasks_prevented,
            "human_escalations": self.human_escalations,
            "lessons_created": self.lessons_created,
            "lessons_adopted": self.lessons_adopted,
            "workflow_compilations": self.workflow_compilations,
            "autonomy_promotions": self.autonomy_promotions,
            "incident_clusters": self.incident_clusters,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LowUsageMetrics":
        return cls(
            requests_total=int(data.get("requests_total", 0) or 0),
            zero_agent_resolutions=int(data.get("zero_agent_resolutions", 0) or 0),
            cache_hits=int(data.get("cache_hits", 0) or 0),
            artifact_reuse=int(data.get("artifact_reuse", 0) or 0),
            workflow_uses=int(data.get("workflow_uses", 0) or 0),
            skill_uses=int(data.get("skill_uses", 0) or 0),
            model_calls=int(data.get("model_calls", 0) or 0),
            premium_calls=int(data.get("premium_calls", 0) or 0),
            grok_calls=int(data.get("grok_calls", 0) or 0),
            average_team_size=float(data.get("average_team_size", 1.0) or 1.0),
            verification_required_rate=float(data.get("verification_required_rate", 0.0) or 0.0),
            verification_pass_rate=float(data.get("verification_pass_rate", 0.0) or 0.0),
            retries=int(data.get("retries", 0) or 0),
            duplicate_tasks_prevented=int(data.get("duplicate_tasks_prevented", 0) or 0),
            human_escalations=int(data.get("human_escalations", 0) or 0),
            lessons_created=int(data.get("lessons_created", 0) or 0),
            lessons_adopted=int(data.get("lessons_adopted", 0) or 0),
            workflow_compilations=int(data.get("workflow_compilations", 0) or 0),
            autonomy_promotions=int(data.get("autonomy_promotions", 0) or 0),
            incident_clusters=int(data.get("incident_clusters", 0) or 0),
        )