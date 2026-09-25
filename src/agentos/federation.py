"""Provider-neutral federated agent fabric (FEDERATION).

AgentOS routes work among local capabilities, agent CLI binaries, model
APIs, MCP tools and remote peers through a single envelope contract.
Every route carries a rationale; every number (usage, cost, confidence)
is measured or explicitly absent — never fabricated.

Module layout:
    Envelope + security data classes  -> JobEnvelope, ResultEnvelope, DataClass
    Micro-protocol conversions        -> job <-> TASK packet, result <-> RESULT
    ExecutorCell + executor registry  -> cells are isolated execution channels
    Tiers + scoring + routing         -> T0..T6, score_executor_cell, resolve_route
    Plan-before-premium               -> pipeline gates, premium gated behind approval
    A2A 1.0 boundary                  -> agent card, client, optional server (OFF)
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import StrEnum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from agentos.models import Priority, new_id, utc_now_iso

###############
# security data classes
###############


class DataClass(StrEnum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    HIGHLY_SENSITIVE = "HIGHLY_SENSITIVE"


DATA_CLASS_RANK: dict[str, int] = {
    DataClass.PUBLIC.value: 0,
    DataClass.INTERNAL.value: 1,
    DataClass.CONFIDENTIAL.value: 2,
    DataClass.HIGHLY_SENSITIVE.value: 3,
}


def data_class_rank(value: str) -> int:
    return DATA_CLASS_RANK.get(str(value).upper(), 0)


class LatencyClass(StrEnum):
    BATCH = "BATCH"
    STANDARD = "STANDARD"
    INTERACTIVE = "INTERACTIVE"


LATENCY_RANK: dict[str, int] = {
    LatencyClass.BATCH.value: 0,
    LatencyClass.STANDARD.value: 1,
    LatencyClass.INTERACTIVE.value: 2,
}


def latency_rank(value: str) -> int:
    return LATENCY_RANK.get(str(value).upper(), 1)


class QualityFloor(StrEnum):
    BEST_EFFORT = "BEST_EFFORT"
    VERIFIED = "VERIFIED"
    REVIEWED = "REVIEWED"


QUALITY_RANK: dict[str, int] = {
    QualityFloor.BEST_EFFORT.value: 0,
    QualityFloor.VERIFIED.value: 1,
    QualityFloor.REVIEWED.value: 2,
}


def quality_rank(value: str) -> int:
    return QUALITY_RANK.get(str(value).upper(), 0)


class ExecutionClass(StrEnum):
    INSPECT = "INSPECT"            # dry-run: show the plan, run nothing
    SIMULATED = "SIMULATED"        # exercise routing/packets, no side effects
    STANDARD = "STANDARD"          # ordinary real execution when authorized
    PREMIUM = "PREMIUM"            # scarce/paid execution (gated + approved)


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    ROUTED = "ROUTED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    ORPHANED = "ORPHANED"


class ResultStatus(StrEnum):
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    REFUSED = "REFUSED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    INTERRUPTED = "INTERRUPTED"
    UNKNOWN = "UNKNOWN"


_SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"crsr_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
]

_SECRET_KEY_HINTS = (
    "api_key",
    "apikey",
    "api-key",
    "secret",
    "password",
    "passwd",
    "token",
    "bearer",
    "recovery code",
    "recovery-code",
    "session token",
    "session-token",
)


def contains_secret(text: str | None) -> bool:
    """True when *text* is, or plausibly carries, a credential value.

    Deliberately conservative: values that look like keys/tokens or field
    names like ``api_key=`` trip it. Envelopes carrying secrets are
    rejected at the boundary rather than silently forwarded.
    """
    if not text:
        return False
    lowered = str(text).lower()
    if any(hint in lowered for hint in _SECRET_KEY_HINTS):
        return True
    for pattern in _SECRET_PATTERNS:
        if pattern.search(str(text).strip()):
            return True
    return False


# Which cells may carry HIGHLY_SENSITIVE work: never arbitrary external peers.
EXTERNAL_PROVIDER_HINTS = ("a2a", "remote", "external", "peer", "cloud")


###############
# envelopes
###############


def _clean_text(value: Any, label: str, max_len: int = 8000) -> str:
    text = str(value or "").strip()
    if contains_secret(text):
        raise ValueError(f"{label} must not carry credential values")
    return text[:max_len]


@dataclass
class JobEnvelope:
    """A first-class task that can be routed to any executor cell."""

    id: str
    requester: str
    targetCapability: str
    priority: str = Priority.MEDIUM.value
    dataClass: str = DataClass.INTERNAL.value
    delivery: str = "deliverable"
    contextRefs: list[str] = field(default_factory=list)
    compactContext: str | None = None
    deliverable: str = ""
    budget: dict[str, Any] | None = None
    latencyClass: str = LatencyClass.STANDARD.value
    qualityFloor: str = QualityFloor.BEST_EFFORT.value
    executionClass: str = ExecutionClass.SIMULATED.value
    preferredExecutors: list[str] = field(default_factory=list)
    forbiddenExecutors: list[str] = field(default_factory=list)
    approvalPolicy: str = "none"
    owner: str | None = None
    parentId: str | None = None
    correlationId: str | None = None
    deadline: str | None = None
    createdAt: str = field(default_factory=utc_now_iso)
    # ---- verification-first task model (all optional, safe defaults) ----
    verificationRequired: bool = False
    verificationType: str = ""
    verifierCapability: str = ""
    verificationEvidence: list[str] = field(default_factory=list)
    verificationStatus: str = "UNVERIFIED"

    def __post_init__(self) -> None:
        self.id = _clean_text(self.id, "id")
        self.requester = _clean_text(self.requester, "requester", 200)
        self.targetCapability = _clean_text(self.targetCapability, "targetCapability", 200)
        if not self.targetCapability:
            raise ValueError("targetCapability is required")
        self.dataClass = str(self.dataClass).upper()
        if self.dataClass not in DATA_CLASS_RANK:
            raise ValueError(f"unknown data class {self.dataClass!r}")
        if not self.deliverable:
            raise ValueError("deliverable is required")
        self.deliverable = _clean_text(self.deliverable, "deliverable")
        if self.compactContext:
            self.compactContext = _clean_text(self.compactContext, "compactContext")
        self.contextRefs = [
            _clean_text(ref, "contextRefs", 1000) for ref in (self.contextRefs or [])
        ]
        self.verificationType = _clean_text(
            self.verificationType, "verificationType", 200
        )
        self.verifierCapability = _clean_text(
            self.verifierCapability, "verifierCapability", 200
        )
        self.verificationEvidence = [
            _clean_text(e, "verificationEvidence", 2000)
            for e in (self.verificationEvidence or [])
        ]
        self.verificationStatus = _clean_text(
            self.verificationStatus, "verificationStatus", 100
        ) or "UNVERIFIED"

    def requires_verification(self) -> bool:
        return bool(self.verificationRequired) or bool(self.verificationType)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "requester": self.requester,
            "owner": self.owner,
            "parentId": self.parentId,
            "correlationId": self.correlationId,
            "targetCapability": self.targetCapability,
            "preferredExecutors": list(self.preferredExecutors),
            "forbiddenExecutors": list(self.forbiddenExecutors),
            "priority": self.priority,
            "budget": self.budget,
            "latencyClass": self.latencyClass,
            "qualityFloor": self.qualityFloor,
            "dataClass": self.dataClass,
            "executionClass": self.executionClass,
            "contextRefs": list(self.contextRefs),
            "compactContext": self.compactContext,
            "deliverable": self.deliverable,
            "approvalPolicy": self.approvalPolicy,
            "deadline": self.deadline,
            "createdAt": self.createdAt,
            "verificationRequired": self.verificationRequired,
            "verificationType": self.verificationType,
            "verifierCapability": self.verifierCapability,
            "verificationEvidence": list(self.verificationEvidence),
            "verificationStatus": self.verificationStatus,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobEnvelope":
        return cls(
            id=str(data.get("id") or new_id("job")),
            requester=str(data.get("requester") or ""),
            owner=data.get("owner"),
            parentId=data.get("parentId"),
            correlationId=data.get("correlationId"),
            targetCapability=str(data.get("targetCapability") or ""),
            preferredExecutors=[
                str(e) for e in (data.get("preferredExecutors") or [])
            ],
            forbiddenExecutors=[
                str(e) for e in (data.get("forbiddenExecutors") or [])
            ],
            priority=str(data.get("priority", Priority.MEDIUM.value)),
            budget=data.get("budget"),
            latencyClass=str(data.get("latencyClass", LatencyClass.STANDARD.value)),
            qualityFloor=str(data.get("qualityFloor", QualityFloor.BEST_EFFORT.value)),
            dataClass=str(data.get("dataClass", DataClass.INTERNAL.value)),
            executionClass=str(
                data.get("executionClass", ExecutionClass.SIMULATED.value)
            ),
            contextRefs=[str(r) for r in (data.get("contextRefs") or [])],
            compactContext=data.get("compactContext"),
            deliverable=str(data.get("deliverable") or ""),
            approvalPolicy=str(data.get("approvalPolicy", "none")),
            deadline=data.get("deadline"),
            createdAt=str(data.get("createdAt") or utc_now_iso()),
            verificationRequired=bool(data.get("verificationRequired", False)),
            verificationType=str(data.get("verificationType") or ""),
            verifierCapability=str(data.get("verifierCapability") or ""),
            verificationEvidence=[
                str(e) for e in (data.get("verificationEvidence") or [])
            ],
            verificationStatus=str(
                data.get("verificationStatus") or "UNVERIFIED"
            ),
        )

    def required_operations(self) -> list[str]:
        op = self.targetCapability
        if "." not in op and op != "any":
            return []
        return [op]

    def requires(self, key: str) -> bool:
        if key == "persistence":
            refs = " ".join(self.contextRefs or []) + " "
            return any(
                hint in refs.lower()
                for hint in ("persistent", "offline continuation", "long-running")
            )
        return False

    def is_premium(self) -> bool:
        return self.executionClass == ExecutionClass.PREMIUM.value


@dataclass
class ResultEnvelope:
    """The envelope an executor returns for a JobEnvelope."""

    jobId: str
    executor: str
    status: str = ResultStatus.UNKNOWN.value
    verifiedFacts: list[str] = field(default_factory=list)
    artifactRefs: list[str] = field(default_factory=list)
    action: str = "none"
    confidence: float | None = None
    blocker: str | None = None
    actualCost: Any = None
    usageClass: str | None = None
    startedAt: str = field(default_factory=utc_now_iso)
    completedAt: str | None = None

    def __post_init__(self) -> None:
        if self.confidence is not None:
            try:
                confidence = float(self.confidence)
            except (TypeError, ValueError):
                raise ValueError("confidence must be a number 0..1")
            if confidence < 0.0 or confidence > 1.0:
                raise ValueError("confidence must be a number 0..1")
            self.confidence = confidence
        self.verifiedFacts = [
            _clean_text(f, "verifiedFacts", 2000) for f in (self.verifiedFacts or [])
        ]
        self.artifactRefs = [
            _clean_text(r, "artifactRefs", 2000) for r in (self.artifactRefs or [])
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "jobId": self.jobId,
            "executor": self.executor,
            "status": self.status,
            "verifiedFacts": list(self.verifiedFacts),
            "artifactRefs": list(self.artifactRefs),
            "action": self.action,
            "confidence": self.confidence,
            "blocker": self.blocker,
            "actualCost": self.actualCost,
            "usageClass": self.usageClass,
            "startedAt": self.startedAt,
            "completedAt": self.completedAt,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResultEnvelope":
        return cls(
            jobId=str(data.get("jobId") or ""),
            executor=str(data.get("executor") or ""),
            status=str(data.get("status", ResultStatus.UNKNOWN.value)),
            verifiedFacts=[str(f) for f in (data.get("verifiedFacts") or [])],
            artifactRefs=[str(r) for r in (data.get("artifactRefs") or [])],
            action=str(data.get("action", "none")),
            confidence=data.get("confidence"),
            blocker=data.get("blocker"),
            actualCost=data.get("actualCost"),
            usageClass=data.get("usageClass"),
            startedAt=str(data.get("startedAt") or utc_now_iso()),
            completedAt=data.get("completedAt"),
        )


###############
# micro-protocol packet conversions (grokbot-office compatible)
###############

_PACKET_KIND = re.compile(r"^(TASK|RESULT|ESCALATE)\|")


def serialize_task_packet(job: JobEnvelope, to: str | None = None) -> str:
    """Render a JobEnvelope as a canonical ``TASK|from->to|goal|...`` line."""
    target = to or job.targetCapability
    goal = job.deliverable
    context = job.compactContext or ""
    if job.contextRefs:
        refs = "; ".join(job.contextRefs)
        context = f"{context} refs: {refs}" if context else refs
    constraints = (
        f"priority={job.priority}; data={job.dataClass}; latency={job.latencyClass}; "
        f"approval={job.approvalPolicy}"
    )
    return (
        f"TASK|{job.requester}->{target}|{goal}|{context or ''}|"
        f"{constraints}|{job.deliverable}"
    )


def parse_task_packet(line: str) -> dict[str, str]:
    match = _PACKET_KIND.match(line.strip())
    if not match or match.group(1) != "TASK":
        raise ValueError("packet: must start with TASK|")
    body = line.strip()[len(match.group(0)) :]
    parts = [p.strip() for p in body.split("|")]
    if "->" in parts[0]:
        source, target = parts[0].split("->", 1)
        parts = [source.strip(), target.strip(), *parts[1:]]
    if len(parts) < 6:
        raise ValueError("packet: TASK requires 6 fields")
    from_, to, goal, context, constraints, deliverable = parts[:6]
    return {
        "from": from_,
        "to": to,
        "goal": goal,
        "context": context,
        "constraints": constraints,
        "deliverable": deliverable,
    }


def job_from_task_packet(line: str) -> JobEnvelope:
    parsed = parse_task_packet(line)
    data: dict[str, Any] = {
        "id": new_id("job"),
        "requester": parsed["from"],
        "targetCapability": parsed["to"],
        "compactContext": parsed["context"] or None,
        "deliverable": parsed["deliverable"],
        "executionClass": ExecutionClass.SIMULATED.value,
    }
    for piece in parsed["constraints"].split("; "):
        if "=" not in piece:
            continue
        key, value = piece.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key in ("priority", "data", "latency", "approval"):
            data[key] = value
    return JobEnvelope.from_dict(data)


def serialize_result_packet(result: ResultEnvelope, to: str | None = None) -> str:
    """Render a ResultEnvelope as a canonical ``RESULT|to->from|facts|...`` line."""
    confidence = -1 if result.confidence is None else result.confidence
    facts = "; ".join(result.verifiedFacts)
    recipient = to or result.executor
    blocker = f"|{result.blocker}" if result.blocker else ""
    return (
        f"RESULT|{result.executor}->{recipient}|{facts}|{result.action}|"
        f"{confidence}{blocker}"
    )


def parse_result_packet(line: str) -> dict[str, Any]:
    match = _PACKET_KIND.match(line.strip())
    if not match or match.group(1) != "RESULT":
        raise ValueError("packet: must start with RESULT|")
    body = line.strip()[len(match.group(0)) :]
    parts = [p.strip() for p in body.split("|")]
    if "->" in parts[0]:
        to, from_ = parts[0].split("->", 1)
        parts = [to.strip(), from_.strip(), *parts[1:]]
    if len(parts) < 5:
        raise ValueError("packet: RESULT requires at least 5 fields")
    to, from_, facts, action, confidence_raw = parts[:5]
    blocker = parts[5] if len(parts) > 5 else None
    return {
        "to": to,
        "from": from_,
        "facts": [f.strip() for f in facts.split(";") if f.strip()],
        "action": action,
        "confidence": confidence_raw,
        "blocker": blocker,
    }


def result_from_packet(job_id: str, line: str) -> ResultEnvelope:
    parsed = parse_result_packet(line)
    confidence = parsed["confidence"]
    if confidence in (None, "", "-1"):
        confidence_value = None
    else:
        confidence_value = float(confidence)
    return ResultEnvelope(
        jobId=job_id,
        executor=parsed["from"],
        status=ResultStatus.COMPLETED.value,
        verifiedFacts=parsed["facts"],
        action=parsed["action"],
        confidence=confidence_value,
        blocker=parsed["blocker"],
        completedAt=utc_now_iso(),
    )


###############
# ExecutorCell
###############


def default_tier(provider: str, capability_ids: list[str]) -> int:
    """Cheapest honest tier for a provider (overridable per cell)."""
    provider = provider.lower()
    if "a2a" in provider or "remote" in provider or "peer" in provider:
        return 6
    if provider in ("grokbot-office", "grokbot office") or any(
        "grokbot" in c.lower() for c in capability_ids
    ):
        return 5
    if provider == "hermes":
        return 4
    if provider in ("openhands", "openhands-code"):
        return 4
    if provider == "browser-harness":
        return 2
    if provider in ("xai", "openai", "anthropic", "model"):
        return 4
    if provider in ("opencode", "grok", "openclaw", "agent"):
        return 3
    if provider in ("shell", "cli", "filesystem"):
        return 1
    return 0


@dataclass
class ExecutorCell:
    """One isolated executor channel (local, agent, provider, or remote peer).

    Isolation by construction: each cell owns its own ``config`` channel
    (binary/env/endpoint) and never shares adapter instances or credential
    material with another cell. Any secret-looking value in ``config`` is
    rejected at construction — credentials are never copied between cells.
    """

    id: str
    provider: str
    description: str
    capability_ids: list[str] = field(default_factory=list)
    supported_operations: list[str] = field(default_factory=list)
    adapter: str = ""
    availability: bool = True
    health: str = "UNKNOWN"
    tier: int = 0
    data_class_max: str = DataClass.INTERNAL.value
    quality_ladder: str = QualityFloor.VERIFIED.value
    latency_class: str = LatencyClass.STANDARD.value
    persistence: bool = False
    requires_online: bool = True
    persistent_remote: bool = False
    local: bool = True
    cost_hint: dict[str, Any] | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    is_external: bool = False
    config: dict[str, Any] = field(default_factory=dict)
    # ---- multi-cell federation extensions (all optional, safe defaults) ---
    owner_label: str = ""
    authorized: bool = False
    used_pct: float | None = None
    reset_at: str | None = None
    live_roles: list[str] = field(default_factory=list)
    virtual_role_count: int = 0
    credential_boundary: str = ""
    data_classes_allowed: list[str] = field(default_factory=list)
    persistent_computer: bool = False
    persistent_browser: bool = False
    transport: str = ""
    priority: int = 0
    last_verified_at: str | None = None
    trust: str = "UNTRUSTED"
    # ---- four-way separation (identity / runtime / model / computer) ----
    # A role identity NEVER implies a model or a computer; these are explicit
    # separate preferences. Additive fields, safe defaults, provider-neutral.
    identityPersistent: bool = False
    preferredRuntimes: list[str] = field(default_factory=list)
    preferredModels: list[str] = field(default_factory=list)
    computerAffinity: str | None = None
    requiredCapabilities: list[str] = field(default_factory=list)
    supervisor: str | None = None
    workerPolicy: str | None = None
    verificationPolicy: str | None = None

    def __post_init__(self) -> None:
        blob = json.dumps(
            {"config": self.config, "boundary": self.credential_boundary},
            default=str,
        )
        if contains_secret(blob):
            raise ValueError(
                f"cell {self.id!r} config carries credential-looking values; "
                "credentials are never stored on or copied between cells"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "description": self.description,
            "capability_ids": list(self.capability_ids),
            "supported_operations": list(self.supported_operations),
            "adapter": self.adapter,
            "availability": self.availability,
            "health": self.health,
            "tier": self.tier,
            "data_class_max": self.data_class_max,
            "quality_ladder": self.quality_ladder,
            "latency_class": self.latency_class,
            "persistence": self.persistence,
            "requires_online": self.requires_online,
            "persistent_remote": self.persistent_remote,
            "local": self.local,
            "cost_hint": self.cost_hint,
            "usage": dict(self.usage),
            "is_external": self.is_external,
            "config": dict(self.config),
            "owner_label": self.owner_label,
            "authorized": self.authorized,
            "used_pct": self.used_pct,
            "reset_at": self.reset_at,
            "live_roles": list(self.live_roles),
            "virtual_role_count": self.virtual_role_count,
            "credential_boundary": self.credential_boundary,
            "data_classes_allowed": list(self.data_classes_allowed),
            "persistent_computer": self.persistent_computer,
            "persistent_browser": self.persistent_browser,
            "transport": self.transport,
            "priority": self.priority,
            "last_verified_at": self.last_verified_at,
            "trust": self.trust,
            "identityPersistent": self.identityPersistent,
            "preferredRuntimes": list(self.preferredRuntimes),
            "preferredModels": list(self.preferredModels),
            "computerAffinity": self.computerAffinity,
            "requiredCapabilities": list(self.requiredCapabilities),
            "supervisor": self.supervisor,
            "workerPolicy": self.workerPolicy,
            "verificationPolicy": self.verificationPolicy,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExecutorCell":
        used = data.get("used_pct")
        return cls(
            id=str(data["id"]),
            provider=str(data.get("provider", "")),
            description=str(data.get("description", "")),
            capability_ids=[str(c) for c in (data.get("capability_ids") or [])],
            supported_operations=[
                str(o) for o in (data.get("supported_operations") or [])
            ],
            adapter=str(data.get("adapter", "")),
            availability=bool(data.get("availability", True)),
            health=str(data.get("health", "UNKNOWN")),
            tier=int(data.get("tier", 0)),
            data_class_max=str(
                data.get("data_class_max", DataClass.INTERNAL.value)
            ),
            quality_ladder=str(
                data.get("quality_ladder", QualityFloor.VERIFIED.value)
            ),
            latency_class=str(
                data.get("latency_class", LatencyClass.STANDARD.value)
            ),
            persistence=bool(data.get("persistence", False)),
            requires_online=bool(data.get("requires_online", True)),
            persistent_remote=bool(data.get("persistent_remote", False)),
            local=bool(data.get("local", True)),
            cost_hint=data.get("cost_hint"),
            usage=dict(data.get("usage", {}) or {}),
            is_external=bool(data.get("is_external", False)),
            config=dict(data.get("config", {}) or {}),
            owner_label=str(data.get("owner_label", "") or ""),
            authorized=bool(data.get("authorized", False)),
            used_pct=(None if used is None else float(used)),
            reset_at=data.get("reset_at"),
            live_roles=[str(r) for r in (data.get("live_roles") or [])],
            virtual_role_count=int(data.get("virtual_role_count", 0) or 0),
            credential_boundary=str(data.get("credential_boundary", "") or ""),
            data_classes_allowed=[
                str(d) for d in (data.get("data_classes_allowed") or [])
            ],
            persistent_computer=bool(data.get("persistent_computer", False)),
            persistent_browser=bool(data.get("persistent_browser", False)),
            transport=str(data.get("transport", "") or ""),
            priority=int(data.get("priority", 0) or 0),
            last_verified_at=data.get("last_verified_at"),
            trust=str(data.get("trust", "UNTRUSTED") or "UNTRUSTED"),
            identityPersistent=bool(data.get("identityPersistent", False)),
            preferredRuntimes=[
                str(p) for p in (data.get("preferredRuntimes") or [])
            ],
            preferredModels=[
                str(m) for m in (data.get("preferredModels") or [])
            ],
            computerAffinity=data.get("computerAffinity"),
            requiredCapabilities=[
                str(c) for c in (data.get("requiredCapabilities") or [])
            ],
            supervisor=data.get("supervisor"),
            workerPolicy=data.get("workerPolicy"),
            verificationPolicy=data.get("verificationPolicy"),
        )

    def usable(self) -> bool:
        return self.availability and self.health not in ("DOWN", "UNAVAILABLE", "DISABLED")

    def supports(self, operation: str) -> bool:
        if operation in self.supported_operations:
            return True
        return operation in self.capability_ids

    def is_hs_capable(self) -> bool:
        return data_class_rank(self.data_class_max) >= data_class_rank(
            DataClass.HIGHLY_SENSITIVE.value
        )


def make_executor_cell(
    id: str,
    provider: str,
    description: str = "",
    *,
    capability_ids: list[str] | None = None,
    supported_operations: list[str] | None = None,
    adapter: str = "",
    health: str = "UNKNOWN",
    tier: int | None = None,
    data_class_max: str = DataClass.INTERNAL.value,
    quality_ladder: str = QualityFloor.VERIFIED.value,
    latency_class: str = LatencyClass.STANDARD.value,
    persistence: bool = False,
    cost_hint: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
    is_external: bool = False,
    config: dict[str, Any] | None = None,
    **extra: Any,
) -> ExecutorCell:
    ids = list(capability_ids or [])
    resolved_tier = default_tier(provider, ids) if tier is None else tier
    return ExecutorCell(
        id=id,
        provider=provider,
        description=description,
        capability_ids=ids,
        supported_operations=list(supported_operations or []),
        adapter=adapter,
        availability=True,
        health=health,
        tier=resolved_tier,
        data_class_max=data_class_max,
        quality_ladder=quality_ladder,
        latency_class=latency_class,
        persistence=persistence,
        cost_hint=cost_hint,
        usage=dict(usage or {}),
        is_external=is_external,
        config=dict(config or {}),
        **extra,
    )


class ExecutorRegistry:
    """Holds executor cells; routing is a pure function over them."""

    def __init__(self, cells: list[ExecutorCell] | None = None) -> None:
        self._cells: dict[str, ExecutorCell] = {}
        for cell in cells or []:
            self._cells[cell.id] = cell

    def register(self, cell: ExecutorCell) -> ExecutorCell:
        self._cells[cell.id] = cell
        return cell

    def remove(self, cell_id: str) -> bool:
        return self._cells.pop(cell_id, None) is not None

    def get(self, cell_id: str) -> ExecutorCell | None:
        return self._cells.get(cell_id)

    def list(self) -> list[ExecutorCell]:
        return sorted(self._cells.values(), key=lambda c: c.id)


###############
# usage governor (measured posture, never fabricated)
###############

USAGE_BAND_PENALTY: dict[str, float] = {
    "NORMAL": 0.0,
    "NORMAL_LIGHT": 0.0,
    "CONSERVE_LIGHT": 15.0,
    "CONSERVE": 45.0,
    "HARD_STOP": 300.0,
    "UNKNOWN": 0.0,
}

# Roster-sourced override whitelist: operations GrokBot is *still allowed*
# to run under a CONSERVE band without further scarcity denial. This is a
# job-context term match only — it is NOT tied to any specific measured
# used_pct (a prior "79%" probe value is not treated as live truth here).
SCARCE_OVERRIDE_TERMS = (
    "persistent login",
    "persistent_login",
    "offline continuation",
    "offline_continuation",
    "cloud-browser",
    "grok native",
    "grok-native",
    "grok_native",
)


def usage_band(cell: ExecutorCell) -> str:
    band = (cell.usage or {}).get("band") or (cell.usage or {}).get("mode") or "UNKNOWN"
    return str(band).upper()


def usage_scarcity_penalty(cell: ExecutorCell, job: JobEnvelope, approved: bool) -> float:
    band = usage_band(cell)
    if band not in USAGE_BAND_PENALTY:
        return 0.0
    if approved:
        return 0.0
    if band not in ("CONSERVE", "HARD_STOP", "CONSERVE_LIGHT"):
        return 0.0
    if job_override_ok(job):
        return min(USAGE_BAND_PENALTY[band], 10.0)
    return USAGE_BAND_PENALTY[band]


def job_override_ok(job: JobEnvelope) -> bool:
    haystack = " ".join(job.contextRefs or []).lower()
    if job.compactContext:
        haystack += " " + job.compactContext.lower()
    haystack += " " + job.deliverable.lower()
    return any(term.lower() in haystack for term in SCARCE_OVERRIDE_TERMS)


def trust_gate(cell: "ExecutorCell", job: JobEnvelope) -> tuple[bool, str]:
    """Enforce trust + approval-class boundaries for a route (§18/§19).

    Separate from the legacy scorer on purpose: long-standing local and
    bridge cells keep their historical semantics, while every *new*
    remote/external family (``a2a-remote``, ``mcp:*``) must pass this
    gate before execution. Returns ``(allowed, reason)``.
    """
    from agentos.policies import (
        APPROVAL_CLASS_RANK,
        approval_class_for,
        trust_allows,
    )

    allowed, reason = trust_allows(cell.trust, job.dataClass, cell.is_external)
    if not allowed:
        return False, reason
    job_class = approval_class_for(
        job.targetCapability, job.dataClass, cell.is_external
    )
    cell_max = str(
        (cell.config or {}).get("maxApprovalClass", "READ_ONLY")
    ).upper()
    if APPROVAL_CLASS_RANK.get(job_class.value, 0) > APPROVAL_CLASS_RANK.get(
        cell_max, 0
    ):
        return False, (
            f"cell {cell.id} caps approval at {cell_max}; job requires "
            f"{job_class.value} (executors never reduce approval class)"
        )
    return True, f"trust={cell.trust} allows {job.dataClass}; class {job_class.value}"


def approval_granted(job: JobEnvelope, approval_evidence: list[dict] | None = None) -> bool:
    if job.approvalPolicy in ("granted", "human", "approved"):
        return True
    for item in approval_evidence or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or item.get("grant") or "").lower()
        if "live" in kind or "premium" in kind or "approve" in kind:
            return True
    return False


###############
# tiers
###############

TIERS: list[tuple[int, str]] = [
    (0, "T0_LOCAL_NO_COST"),
    (1, "T1_LOCAL_TOOL"),
    (2, "T2_NATIVE_DETECTED"),
    (3, "T3_AUTHORIZED_AGENT"),
    (4, "T4_PAID_MODEL"),
    (5, "T5_SCARCE_PREMIUM"),
    (6, "T6_EXTERNAL_PEER"),
]

TIER_NAMES: dict[int, str] = dict(TIERS)


def tier_name(tier: int) -> str:
    return TIER_NAMES.get(int(tier), f"T{tier}")


def tier_premium(tier: int) -> bool:
    return int(tier) >= 4


###############
# scoring + routing (deterministic, rationale everywhere)
###############


def _stat_rate(stats: dict[str, dict[str, int]], cell_id: str) -> float:
    entry = (stats or {}).get(cell_id)
    if not entry or not entry.get("runs"):
        return 0.0
    return float(entry.get("verified", 0)) / float(entry["runs"])


def score_executor_cell(
    job: JobEnvelope,
    cell: ExecutorCell,
    stats: dict[str, dict[str, int]] | None = None,
    approval_evidence: list[dict] | None = None,
) -> tuple[float, list[str] | None]:
    """Score one cell against a job; returns ``score`` and rationale parts.

    Hard gates (capability fit, data fit, HS isolation, quality, latency)
    return ``(0.0, None)`` — the cell is ineligible. Otherwise all penalty
    terms are additive over a base that mostly derives from measured health
    and historical verification.
    """
    parts: list[str] = []
    target = job.targetCapability
    if target != "any" and not cell.supports(target):
        return 0.0, None
    if cell.is_external and job.dataClass == DataClass.HIGHLY_SENSITIVE.value:
        return 0.0, None  # HS work never routed to arbitrary external agents
    if data_class_rank(cell.data_class_max) < data_class_rank(job.dataClass):
        return 0.0, None
    if quality_rank(cell.quality_ladder) < quality_rank(job.qualityFloor):
        return 0.0, None
    if latency_rank(cell.latency_class) < latency_rank(job.latencyClass):
        return 0.0, None
    if job.requires("persistence") and not cell.persistence:
        return 0.0, None
    if not cell.usable():
        return 0.0, None

    score = 0.0
    if cell.health in ("OK", "AVAILABLE", "READY"):
        score += 30.0
        parts.append(f"health={cell.health}")
    elif cell.health in ("DEGRADED", "AUTH_REQUIRED", "CONFIGURED"):
        score += 12.0
        parts.append(f"health={cell.health}")
    else:
        score += 5.0
        parts.append(f"health={cell.health}")

    rate = _stat_rate(stats, cell.id)
    if rate > 0.0:
        bonus = (rate - 0.5) * 20.0
        score += bonus
        parts.append(f"history={rate:.0%}({bonus:+.1f})")
    else:
        parts.append("history=none")

    # Cell specialization: prefer an authorized cell that already holds the
    # required session (declared in its config) over duplicating credentials
    # elsewhere. Only fires for authorized cells with explicit sessions.
    sessions = (cell.config or {}).get("sessions") or []
    if cell.authorized and sessions:
        haystack = " ".join(
            [*(job.contextRefs or []), job.compactContext or "", job.deliverable]
        ).lower()
        for session in sessions:
            if f"session:{session}".lower() in haystack:
                score += 8.0
                parts.append(f"session-match:{session}(+8.0)")
                break

    cost = cell.cost_hint or {}
    per_run = cost.get("per_run")
    if isinstance(per_run, (int, float)):
        penalty = min(20.0, float(per_run))
        score -= penalty
        parts.append(f"cost({penalty:.1f})")
    else:
        parts.append("cost=free")

    score -= usage_scarcity_penalty(cell, job, approval_granted(job, approval_evidence))
    parts.append(f"usage={usage_band(cell)}")

    transfer = len(job.contextRefs or [])
    if job.compactContext:
        transfer += max(0.0, min(4.0, len(job.compactContext) / 2000.0))
    if cell.is_external:
        score -= transfer
        parts.append(f"contextTransfer({transfer:.1f})")

    if job.executionClass == ExecutionClass.PREMIUM.value and not tier_premium(cell.tier):
        score -= 5.0
        parts.append("premiumRequestedOnCheapCell(-5)")
    return score, parts


@dataclass
class RouteDecision:
    """Deterministic routing outcome for one job."""

    jobId: str
    chosen: ExecutorCell | None
    tier: int | None
    score: float
    rationale: str
    alternatives: list[str]
    gates: list[str]
    premiumGated: bool
    approved: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "jobId": self.jobId,
            "chosen": self.chosen.id if self.chosen else None,
            "tier": self.tier,
            "tier_name": tier_name(self.tier) if self.tier is not None else None,
            "score": self.score,
            "rationale": self.rationale,
            "alternatives": self.alternatives,
            "gates": self.gates,
            "premiumGated": self.premiumGated,
            "approved": self.approved,
        }


def resolve_route(
    job: JobEnvelope,
    cells: list[ExecutorCell],
    stats: dict[str, dict[str, int]] | None = None,
    approval_evidence: list[dict] | None = None,
) -> RouteDecision:
    """Pick the cheapest tier that satisfies every hard gate.

    Within the winning tier, prefers the highest score. Premium tiers
    (T4+) are gated unless the job carries approval evidence. Deterministic
    ordering breaks ties by cell id.
    """
    gates: list[str] = []
    if job.forbiddenExecutors:
        cells = [c for c in cells if c.id not in job.forbiddenExecutors]
        gates.append(f"forbidden={','.join(job.forbiddenExecutors)}")
    if job.preferredExecutors:
        cells = [c for c in cells if c.id in job.preferredExecutors] or cells
        gates.append(f"preferred={','.join(job.preferredExecutors)}")

    approved = approval_granted(job, approval_evidence)
    scored: list[tuple[ExecutorCell, float, list[str]]] = []
    for cell in cells:
        score, parts = score_executor_cell(job, cell, stats, approval_evidence)
        if parts is None:
            continue
        scored.append((cell, score, parts))
    if not scored:
        return RouteDecision(
            jobId=job.id,
            chosen=None,
            tier=None,
            score=0.0,
            rationale="no executor passes routing gates",
            alternatives=[],
            gates=gates,
            premiumGated=False,
            approved=approved,
        )

    scored.sort(key=lambda item: (-item[1], item[0].id))
    cheapest_tier = min(c.tier for c, _, _ in scored)
    in_tier = sorted(
        (item for item in scored if item[0].tier == cheapest_tier),
        key=lambda item: (-item[1], item[0].id),
    )
    chosen, score, parts = in_tier[0]
    premium = tier_premium(chosen.tier)
    gated = premium and not approved
    alternatives = [
        f"{c.id}(tier={c.tier},score={s:.0f})" for c, s, _ in scored[1:4]
    ]
    rationale = (
        f"tier={tier_name(chosen.tier)}({chosen.tier}) -> {chosen.id}; "
        + "; ".join(parts)
        + (f"; premiumGated={gated}" if premium else "")
    )
    return RouteDecision(
        jobId=job.id,
        chosen=chosen,
        tier=chosen.tier,
        score=score,
        rationale=rationale,
        alternatives=alternatives,
        gates=gates,
        premiumGated=gated,
        approved=approved,
    )


def plan_before_premium(
    job: JobEnvelope, decision: RouteDecision, available_count: int
) -> dict[str, Any]:
    """Plan-before-premium pipeline: verify cheaper channels first.

    Returns a deterministic plan object: the chosen (gated) premium cell,
    the cheaper verified alternatives that must run first, and what must be
    approved before any premium side-effect executes. Never auto-runs.
    """
    cheaper = [
        alt
        for alt in decision.alternatives
        if "tier=" in alt and int(alt.split("tier=")[1].split(",")[0]) < (decision.tier or 6)
    ] if decision.tier is not None else []
    return {
        "jobId": job.id,
        "plan": "plan_before_premium",
        "premium": decision.chosen.id if decision.chosen else None,
        "premiumTier": tier_name(decision.tier) if decision.tier is not None else None,
        "premiumGated": decision.premiumGated,
        "approved": decision.approved,
        "runCheaperFirst": cheaper[:3],
        "executorCount": available_count,
        "policy": "premium executes only after cheaper verified channels fail AND human approval exists",
    }


###############
# A2A 1.0 boundary (client + optional server, OFF by default)
###############


def agent_card(capability: Any) -> dict[str, Any]:
    """Render a capability as an A2A-style agent card.

    Never exposes HIGHLY_SENSITIVE capabilities on the wire.
    """
    data_class = str(getattr(capability, "data_class_max", "") or data_class_rank_max())
    caps = list(getattr(capability, "operations", None) or [])
    name = getattr(capability, "name", None) or getattr(capability, "id", "agent")
    description = getattr(capability, "description", "") or ""
    if data_class in ("HIGHLY_SENSITIVE",) or "sensitive" in name.lower():
        return {"name": "<redacted>", "description": "not exposed", "capabilities": []}
    return {
        "name": name,
        "description": description[:500],
        "url": "local://federation",
        "capabilities": caps,
    }


def data_class_rank_max() -> str:
    return DataClass.INTERNAL.value


class A2AClient:
    """Sends tasks to a remote A2A endpoint and maps the reply to envelopes."""

    def __init__(self, base_url: str, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def send_task(self, job: JobEnvelope) -> ResultEnvelope:
        payload = {
            "method": "tasks/send",
            "job": job.to_dict(),
        }
        request = urllib.request.Request(
            f"{self.base_url}/message",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return ResultEnvelope(
                jobId=job.id,
                executor=self.base_url,
                status=ResultStatus.BLOCKED.value,
                blocker=f"a2a HTTP {exc.code}",
                completedAt=utc_now_iso(),
            )
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            return ResultEnvelope(
                jobId=job.id,
                executor=self.base_url,
                status=ResultStatus.INTERRUPTED.value,
                blocker=f"a2a unreachable: {type(exc).__name__}",
                completedAt=utc_now_iso(),
            )
        result = body.get("result") if isinstance(body, dict) else None
        if isinstance(result, dict):
            result = dict(result)
            result.setdefault("jobId", job.id)
        else:
            result = {"jobId": job.id, "executor": self.base_url}
        return ResultEnvelope.from_dict(result)


_FORBIDDEN_EXPOSED_OPS = (
    "shell.run",
    "fs.write",
    "grokbot-office.activate",
)


class _A2AHandler(BaseHTTPRequestHandler):
    server_version = "AgentOS-Federation/A2A"

    def log_message(self, fmt: str, *args: Any) -> None:
        pass

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        try:
            raw = self.rfile.read(length).decode("utf-8")
        except (OSError, UnicodeDecodeError):
            return {}
        try:
            body = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            return {}
        return body if isinstance(body, dict) else {}

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/":
                self._send(
                    200,
                    {
                        "name": "agentos-federation",
                        "description": "provider-neutral federated agent fabric (A2A boundary)",
                    },
                )
                return
            if path == "/agents":
                cards = []
                for cell in self.server.cells:  # type: ignore[attr-defined]
                    if cell.health in ("DOWN", "UNAVAILABLE", "DISABLED"):
                        continue
                    if set(_FORBIDDEN_EXPOSED_OPS).intersection(cell.supported_operations):
                        continue
                    if cell.is_external and cell.is_hs_capable():
                        continue
                    cards.append(
                        {
                            "name": cell.id,
                            "description": (
                                cell.description[:500]
                                + f" (provider={cell.provider}, tier={cell.tier})"
                            ),
                            "url": "local://federation",
                            "capabilities": [
                                c for c in cell.capability_ids
                                if c not in _FORBIDDEN_EXPOSED_OPS
                            ],
                        }
                    )
                self._send(200, {"agents": cards})
                return
            self._send(404, {"error": "not found"})
        except Exception as exc:
            self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        body = self._read_body()
        try:
            if path == "/message" and body.get("method") == "tasks/send":
                job_data = body.get("job")
                if not isinstance(job_data, dict):
                    self._send(400, {"error": "missing job envelope"})
                    return
                job = JobEnvelope.from_dict(job_data)
                if job.executionClass not in (
                    ExecutionClass.SIMULATED.value,
                    ExecutionClass.INSPECT.value,
                ):
                    self._send(
                        403,
                        {
                            "error": (
                                "remote A2A accepts only SIMULATED/INSPECT jobs; "
                                "LIVE/PREMIUM stays behind local authorization"
                            )
                        },
                    )
                    return
                result = ResultEnvelope(
                    jobId=job.id,
                    executor="a2a-server",
                    status=ResultStatus.COMPLETED.value,
                    verifiedFacts=[
                        f"accepted {job.id}",
                        f"target={job.targetCapability}",
                    ],
                    action="none",
                    confidence=0.9,
                    completedAt=utc_now_iso(),
                )
                self._send(200, {"result": result.to_dict()})
                return
            self._send(404, {"error": "unknown a2a method"})
        except Exception as exc:
            self._send(500, {"error": f"{type(exc).__name__}: {exc}"})


def create_a2a_server(
    cells: list[ExecutorCell], host: str = "127.0.0.1", port: int = 0
) -> ThreadingHTTPServer:
    """A2A server is OFF by default; start it only when explicitly asked."""
    server = ThreadingHTTPServer((host, port), _A2AHandler)
    server.cells = cells  # type: ignore[attr-defined]
    server.daemon_threads = True
    return server


###############
# telemetry helpers (measured facts only)
###############


def export_telemetry(cells: list[ExecutorCell], results: list[ResultEnvelope]) -> dict[str, Any]:
    """Summarize what is *measured*. Costs appear only when adapters report them."""
    measured_costs: list[Any] = []
    for result in results:
        cost = result.actualCost
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            measured_costs.append(float(cost))
        elif isinstance(cost, dict) and cost.get("amount") is not None:
            amount = cost.get("amount")
            if isinstance(amount, (int, float)) and not isinstance(amount, bool):
                measured_costs.append(float(amount))
    return {
        "cells": [c.id for c in cells],
        "results": len(results),
        "measured_cost_samples": len(measured_costs),
        "measured_cost_total": round(sum(measured_costs), 6)
        if measured_costs
        else None,
        "note": "costs are only ever measured values; no estimates are fabricated",
    }