"""Low-usage efficiency kernel: TaskFingerprint, TriageDecision, TeamPlan,
DeltaContext and the provider-neutral GrokBot usage policy (rules-first).

Everything here is pure/deterministic: no I/O, no subprocesses, no
timestamps, no provider-specific logic. It computes *decisions*; executing
them stays in the engine/services/adapters.

Design stance (North star): MAXIMIZE VERIFIED USEFUL OUTPUT / TOTAL
RESOURCE COST. Grok is a *late escalation*, not the first reflex; team size
defaults to the smallest capable team (no fan-out unless justified).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
from typing import Any, Iterable

from agentos.intelligence_cache import content_hash, intent_bag as intent_shingles

DETERMINISTIC_HINTS = (
    "calculate",
    "compute",
    "sum",
    "count",
    "format",
    "convert",
    "parse",
    "validate",
    "diff",
    "sort",
    "aggregate",
    "totals",
    "query",
    "lookup",
    "formula",
    "regex",
    "transform",
)

DATABASE_HINTS = ("select", "insert", "update", "delete", "schema", "migration")

API_HINTS = ("api call", "rest endpoint", "http", "curl", "webhook", "post request")

MCP_HINTS = ("mcp", "tool candidate", "connector")

SCRIPT_HINTS = ("script", "shell", "bash", "cli", "command", "pipeline")

CHEAP_MODEL_HINTS = (
    "summarize",
    "rename",
    "classify",
    "categorize",
    "extract",
    "suggest title",
    "short response",
)

SPECIALIST_HINTS = ("verify", "audit", "security review", "code review")

# -- GrokBot usage policy -----------------------------------------------------

GROKBOT_FAVORED = (
    "persistent cloud computer",
    "authenticated browser",
    "offline continuation",
    "durable supervisor identity",
    "durable context",
    "cross-app coordination",
    "exception handling",
    "final judgment",
    "long-running remote",
    "session ownership",
)

GROKBOT_DISFAVORED = (
    "large repo inspection",
    "repeated debugging",
    "deterministic calculation",
    "unchanged-state review",
    "bulk research collection",
    "verbose internal reporting",
    "bot-to-bot discussion",
    "coding",
    "refactor",
    "unit test",
)

#: strong external reasoning capabilities (provider-neutral).
REASONING_CAPABILITIES: tuple[str, ...] = (
    "research",
    "architecture",
    "planning",
    "critique",
    "synthesis",
    "compression",
    "decision analysis",
)


# ---------------------------------------------------------------------------
# TaskFingerprint (dedupe across everyone)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskFingerprint:
    """Deterministic fingerprint of a normalized task.

    Two tasks with identical intent tokens, input hashes, output schema and
    constraints collide — so identical/equivalent work can be deduplicated
    across ChiefOfStaff, ProjectsChief, IntelligenceChief, temporary roles
    and AgentOS workers.
    """

    intent: str
    input_refs: tuple[dict[str, str], ...] = ()
    output_schema: str = ""
    constraints: tuple[str, ...] = ()
    role: str = ""

    def fingerprint(self) -> str:
        seed = "\x1f".join(
            [
                ",".join(sorted(intent_shingles(self.intent)))
                or self.intent.lower(),
                ",".join(
                    f"{r.get('ref') or ''}={r.get('hash') or ''}"
                    for r in sorted(self.input_refs, key=lambda r: str(r.get("ref") or ""))
                ),
                str(self.output_schema or "").lower(),
                ",".join(sorted(str(c).lower() for c in self.constraints)),
            ]
        )
        return sha256(seed.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "input_refs": [dict(r) for r in self.input_refs],
            "output_schema": self.output_schema,
            "constraints": list(self.constraints),
            "role": self.role,
            "fingerprint": self.fingerprint(),
        }


def equivalent_tasks(a: str, b: str, *, output_schema: str = "") -> bool:
    """Cheap semantic-equivalence test via L4 shingles (no model involved)."""
    fa = TaskFingerprint(intent=a, output_schema=output_schema)
    fb = TaskFingerprint(intent=b, output_schema=output_schema)
    return fa.fingerprint() == fb.fingerprint()


# ---------------------------------------------------------------------------
# TriageDecision
# ---------------------------------------------------------------------------


class Resolution(StrEnum):
    CACHE_RETURN = "CACHE_RETURN"
    ARTIFACT_RETURN = "ARTIFACT_RETURN"
    KNOWN_WORKFLOW = "KNOWN_WORKFLOW"
    DETERMINISTIC = "DETERMINISTIC"
    DATABASE = "DATABASE"
    API = "API"
    MCP = "MCP"
    SCRIPT = "SCRIPT"
    CHEAP_MODEL = "CHEAP_MODEL"
    SPECIALIST = "SPECIALIST"
    PREMIUM = "PREMIUM"
    GROKBOT = "GROKBOT"


@dataclass
class TriageDecision:
    """Cheap, rules-first routing decision. Resolutions ordered cheapest up."""

    resolution: Resolution
    objective: str
    reason: str
    route_matched: str = "rules"
    grok_bias: str = "NEUTRAL"
    match: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolution": self.resolution.value,
            "objective": self.objective,
            "reason": self.reason,
            "route_matched": self.route_matched,
            "grok_bias": self.grok_bias,
            "match": list(self.match),
        }


_SUB = re.compile(r"[^a-z0-9 ]+")


def _haystack(*texts: Any) -> str:
    return " " + _SUB.sub(" ", " ".join(str(t or "") for t in texts).lower()) + " "


def _has_hint(text: str, hint: str) -> bool:
    """Multi-word hints stay substring; single tokens require word boundaries.

    Prevents ``compute`` matching inside ``computer`` (GrokBot-favored path).
    """
    h = str(hint or "").lower().strip()
    if not h:
        return False
    if " " in h:
        return h in text
    return re.search(rf"\b{re.escape(h)}\b", text) is not None


def grokbots_favored(objective: str) -> bool:
    return any(_has_hint(_haystack(objective), h) for h in GROKBOT_FAVORED)


def grokbots_disfavored(objective: str) -> bool:
    return any(_has_hint(_haystack(objective), h) for h in GROKBOT_DISFAVORED)


def triage(
    objective: str,
    *,
    exact_cache_hit: bool = False,
    artifact_hit: bool = False,
    known_workflow: bool = False,
    target_capability: str = "",
    provider: str = "",
) -> TriageDecision:
    """Rules-first classification: deterministic over model, cheap over premium.

    The order is deliberately cheapest-to-expensive:
    CACHE_RETURN -> ARTIFACT_RETURN -> KNOWN_WORKFLOW -> DETERMINISTIC ->
    DATABASE -> API -> MCP -> SCRIPT -> CHEAP_MODEL -> SPECIALIST ->
    PREMIUM -> GROKBOT. Premium/Groks are never the first reflex.
    """
    text = _haystack(objective, target_capability)

    if exact_cache_hit:
        return TriageDecision(
            Resolution.CACHE_RETURN,
            objective,
            "exact result cached; zero model call",
        )
    if artifact_hit:
        return TriageDecision(
            Resolution.ARTIFACT_RETURN,
            objective,
            "fresh artifact exists; reuse without recomputation",
        )
    if known_workflow:
        return TriageDecision(
            Resolution.KNOWN_WORKFLOW,
            objective,
            "registered reusable workflow; execute known steps",
        )

    if any(_has_hint(text, h) for h in DETERMINISTIC_HINTS):
        return TriageDecision(
            Resolution.DETERMINISTIC,
            objective,
            "deterministic computation; no model required",
            match=[h for h in DETERMINISTIC_HINTS if _has_hint(text, h)],
        )
    if any(_has_hint(text, h) for h in DATABASE_HINTS):
        return TriageDecision(
            Resolution.DATABASE,
            objective,
            "database operation; SQL not model",
            match=[h for h in DATABASE_HINTS if _has_hint(text, h)],
        )
    if any(_has_hint(text, h) for h in API_HINTS):
        return TriageDecision(
            Resolution.API,
            objective,
            "direct API call; no model",
            match=[h for h in API_HINTS if _has_hint(text, h)],
        )
    if any(_has_hint(text, h) for h in MCP_HINTS):
        return TriageDecision(
            Resolution.MCP,
            objective,
            "MCP connector available",
            match=[h for h in MCP_HINTS if _has_hint(text, h)],
        )
    if any(_has_hint(text, h) for h in SCRIPT_HINTS):
        return TriageDecision(
            Resolution.SCRIPT,
            objective,
            "scriptable deterministic operation",
            match=[h for h in SCRIPT_HINTS if _has_hint(text, h)],
        )
    if any(_has_hint(text, h) for h in CHEAP_MODEL_HINTS):
        return TriageDecision(
            Resolution.CHEAP_MODEL,
            objective,
            "cheap model sufficient (classification/extraction)",
            match=[h for h in CHEAP_MODEL_HINTS if _has_hint(text, h)],
        )
    if any(_has_hint(text, h) for h in SPECIALIST_HINTS):
        return TriageDecision(
            Resolution.SPECIALIST,
            objective,
            "specialist verifier/auditor required",
            match=[h for h in SPECIALIST_HINTS if _has_hint(text, h)],
        )

    favored = grokbots_favored(objective)
    disfavored = grokbots_disfavored(objective)
    if favored and not disfavored:
        return TriageDecision(
            Resolution.GROKBOT,
            objective,
            "unique GrokBot value (persistent cloud/browser/durable context)",
            grok_bias="FAVORED",
        )
    if disfavored and not favored:
        return TriageDecision(
            Resolution.PREMIUM,
            objective,
            "GrokBot disfavored; premium provider reasoning instead",
            grok_bias="DISFAVORED",
        )
    return TriageDecision(
        Resolution.PREMIUM,
        objective,
        "no cheaper rule matched; premium reasoning required",
        grok_bias="NEUTRAL",
    )


def grok_routing_policy(objective: str) -> dict[str, Any]:
    """Provider-neutral GrokBot usage policy summary for an objective."""
    favored = [h for h in GROKBOT_FAVORED if _has_hint(_haystack(objective), h)]
    disfavored = [h for h in GROKBOT_DISFAVORED if _has_hint(_haystack(objective), h)]
    return {
        "favored": favored,
        "disfavored": disfavored,
        "decision": (
            "GROKBOT" if favored and not disfavored else
            "AVOID_GROKBOT" if disfavored and not favored else
            "NEUTRAL"
        ),
    }


# ---------------------------------------------------------------------------
# TeamPlan (smallest capable team)
# ---------------------------------------------------------------------------

#: a verifier is added only above a strict quality floor, not by default.
VERIFIER_QUALITY_FLOOR = "VERIFIED"


@dataclass
class TeamPlan:
    """Minimum team that satisfies requirements. Default: no fan-out."""

    size: int
    roles: list[str] = field(default_factory=list)
    rationale: str = ""
    parallel: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "size": self.size,
            "roles": list(self.roles),
            "rationale": self.rationale,
            "parallel": int(self.parallel),
        }


def plan_team(
    *,
    quality_floor: str = "",
    risk: str = "low",
    decomposable: bool = False,
    independent_subtasks: int = 1,
    verification_burden: bool = False,
    latency_class: str = "interactive",
    security: str = "internal",
    capabilities: Iterable[str] = (),
) -> TeamPlan:
    """Smallest team that satisfies the request.

    * 0 workers: only when the request is fully self-contained (no team).
    * 1 worker: the overwhelming default. No fan-out without justification.
    * supervisor + worker: independent subtasks or meaningful decomposition.
    * + verifier: only on a high quality floor or explicit verification need.
    * fan-out: only with independent subtasks plus a verified quality floor;
      large fan-out always requires explicit economic justification.
    """
    security = str(security or "internal").upper()
    high_security = security in ("CONFIDENTIAL", "HIGHLY_SENSITIVE")
    quality = str(quality_floor or "").upper()
    verified = quality == VERIFIER_QUALITY_FLOOR or verification_burden

    if not decomposable or independent_subtasks <= 1:
        return TeamPlan(
            1,
            ["worker"],
            "single worker; no fan-out without justification",
            parallel=1,
        )

    roles = ["supervisor", "worker"]
    parallel = min(int(independent_subtasks), 4)
    if verified or high_security:
        roles.append("verifier")
        parallel = min(int(independent_subtasks) + 1, 5)
        return TeamPlan(
            len(roles),
            roles,
            f"independent subtasks + verifier threshold ({quality or security}); "
            f"parallel={parallel}",
            parallel=parallel,
        )
    return TeamPlan(
        len(roles),
        roles,
        f"supervisor+worker for {independent_subtasks} independent subtasks",
        parallel=parallel,
    )


# ---------------------------------------------------------------------------
# DeltaContext
# ---------------------------------------------------------------------------


@dataclass
class DeltaContext:
    """Compact context: baseline ref + changes + task. No full history.

    Premium workers normally receive BASELINE REF + DELTA + TASK, never the
    entire repository/history.
    """

    baselineRef: str
    current_source_state: dict[str, str]
    changed_inputs: dict[str, str]
    changed_files: list[str]
    invalidated_cache_refs: list[str]
    currentTask: str
    changed_facts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "baselineRef": self.baselineRef,
            "currentSourceState": dict(self.current_source_state),
            "changedInputs": dict(self.changed_inputs),
            "changedFiles": list(self.changed_files),
            "invalidatedCacheRefs": list(self.invalidated_cache_refs),
            "changedFacts": list(self.changed_facts),
            "currentTask": self.currentTask,
        }


def compute_delta(
    baseline: dict[str, str],
    current: dict[str, str],
    task: str,
    *,
    baseline_ref: str = "baseline@1",
    cache_scope: str = "",
) -> DeltaContext:
    """Diff a baseline hash map against the current source state.

    ``baseline``/``current`` map a label (e.g. file path, commit, provider
    version) to a content hash. Only changed entries appear in the delta —
    unchanged inputs are re-used, never re-read.
    """
    changed_inputs: dict[str, str] = {}
    baseline_keys = set(str(k) for k in baseline)
    current_keys = set(str(k) for k in current)
    for key in sorted(baseline_keys | current_keys):
        old = str(baseline.get(key) or "")
        new = str(current.get(key) or "")
        if old != new:
            changed_inputs[key] = new
    invalidated = [
        f"{cache_scope.rstrip('/')}/{k}"
        for k in sorted(changed_inputs)
        if cache_scope
    ]
    changed_facts: list[str] = []
    for key, h in changed_inputs.items():
        if not h:
            changed_facts.append(f"{key}: removed")
        elif key not in baseline or not baseline.get(key):
            changed_facts.append(f"{key}: new ({h[:8]})")
        else:
            changed_facts.append(f"{key}: changed ({h[:8]})")
    return DeltaContext(
        baselineRef=baseline_ref,
        current_source_state=dict(current),
        changed_inputs=changed_inputs,
        changed_files=[k for k in changed_inputs if "/" in k or k.endswith((".py", ".md", ".js", ".ts"))],
        invalidated_cache_refs=invalidated,
        currentTask=task,
        changed_facts=changed_facts,
    )


def project_state_ref(source_revision: str, active_work: str = "") -> dict[str, Any]:
    """Compact project state reference (PROJECT_STATE.category entry)."""
    return {
        "PROJECT": "",
        "STATE_VERSION": "1",
        "SOURCE_REVISION": source_revision,
        "ARCHITECTURE": "",
        "CAPABILITIES": [],
        "ACTIVE_WORK": active_work,
        "KNOWN_PROBLEMS": [],
        "CONSTRAINTS": [],
        "DECISIONS": [],
        "DEPENDENCIES": {},
        "NEXT_ACTIONS": [],
    }


def content_state(files: dict[str, str]) -> dict[str, str]:
    """Map of label -> canonical content hash for a set of inputs."""
    return {str(k): content_hash(v) for k, v in files.items()}
