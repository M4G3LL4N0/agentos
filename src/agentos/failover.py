"""Bounded failover and pipeline routing (provider-neutral).

Failover walks an ordered candidate list with hard bounds
(``max_attempts`` / ``max_executor_switches`` / ``max_tier_escalations`` /
``max_scarcity_escalation``); every fallback records its reason and the
walk always terminates — no infinite retries, no silent escalation.

Pipelines run bounded DAG-like stage chains (max 5 stages): each stage is
one ``federate_submit``-shaped call through an injected runner, so every
stage produces a result/artifact reference or a recorded failure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class FailoverPolicy:
    max_attempts: int = 3
    max_executor_switches: int = 2
    max_tier_escalations: int = 1
    max_scarcity_escalation: int = 1

    def __post_init__(self) -> None:
        for name in (
            "max_attempts",
            "max_executor_switches",
            "max_tier_escalations",
            "max_scarcity_escalation",
        ):
            value = int(getattr(self, name))
            if value < 1:
                raise ValueError(f"{name} must be >= 1")
            setattr(self, name, value)


@dataclass
class FailoverAttempt:
    executor: str
    ok: bool
    reason: str


@dataclass
class FailoverOutcome:
    ok: bool
    executor: str | None
    result: Any | None
    attempts: list[FailoverAttempt] = field(default_factory=list)
    fallback_reasons: list[str] = field(default_factory=list)


def run_with_failover(
    candidates: list[dict[str, Any]],
    attempt: Callable[[dict[str, Any]], tuple[bool, Any, str]],
    policy: FailoverPolicy | None = None,
) -> FailoverOutcome:
    """Try candidates in order through ``attempt`` until one succeeds.

    ``attempt(candidate)`` returns ``(ok, result, reason)``. Bounds are
    enforced on attempts, executor switches, tier jumps and scarcity
    jumps; every switch records its reason.
    """
    policy = policy or FailoverPolicy()
    outcome = FailoverOutcome(ok=False, executor=None, result=None)
    last_tier: int | None = None
    last_scarcity = 0
    switches = 0
    tier_jumps = 0
    scarcity_jumps = 0
    tried = 0
    previous: str | None = None
    for candidate in candidates:
        if tried >= policy.max_attempts:
            outcome.fallback_reasons.append(
                f"stopped: max_attempts={policy.max_attempts} reached"
            )
            break
        tier = int(candidate.get("tier", 0) or 0)
        scarcity = int(candidate.get("scarcity_rank", 0) or 0)
        if previous is not None and candidate.get("executor") != previous:
            switches += 1
            if switches > policy.max_executor_switches:
                outcome.fallback_reasons.append(
                    "stopped: max_executor_switches="
                    f"{policy.max_executor_switches} reached"
                )
                break
        if last_tier is not None and tier > last_tier:
            tier_jumps += 1
            if tier_jumps > policy.max_tier_escalations:
                outcome.fallback_reasons.append(
                    "stopped: max_tier_escalations="
                    f"{policy.max_tier_escalations} reached"
                )
                break
        if scarcity > last_scarcity:
            scarcity_jumps += 1
            if scarcity_jumps > policy.max_scarcity_escalation:
                outcome.fallback_reasons.append(
                    "stopped: max_scarcity_escalation="
                    f"{policy.max_scarcity_escalation} reached"
                )
                break
        tried += 1
        ok, result, reason = attempt(candidate)
        outcome.attempts.append(
            FailoverAttempt(
                executor=str(candidate.get("executor", "?")), ok=ok, reason=reason
            )
        )
        if ok:
            outcome.ok = True
            outcome.executor = str(candidate.get("executor", ""))
            outcome.result = result
            return outcome
        outcome.fallback_reasons.append(
            f"{candidate.get('executor', '?')}: {reason}"
        )
        previous = str(candidate.get("executor", ""))
        last_tier = tier
        last_scarcity = scarcity
    return outcome


# ---------------------------------------------------------------------------
# Pipelines: bounded stage chains.
# ---------------------------------------------------------------------------

MAX_PIPELINE_STAGES = 5


def validate_pipeline(stages: list[dict[str, Any]]) -> list[str]:
    """Return a list of problems (empty == valid). Bounded and explicit."""
    problems: list[str] = []
    if not isinstance(stages, list) or not stages:
        return ["pipeline needs at least one stage"]
    if len(stages) > MAX_PIPELINE_STAGES:
        return [f"pipeline exceeds {MAX_PIPELINE_STAGES} stages"]
    for index, stage in enumerate(stages):
        if not isinstance(stage, dict):
            problems.append(f"stage {index} is not an object")
            continue
        if not str(stage.get("targetCapability", "")).strip():
            problems.append(f"stage {index} is missing targetCapability")
        if not str(stage.get("deliverable", "")).strip():
            problems.append(f"stage {index} is missing deliverable")
    return problems


def run_pipeline(
    stages: list[dict[str, Any]],
    submit: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    """Run stages in order through ``submit`` (a federate_submit-shaped fn).

    Each stage receives prior artifact references in
    ``contextRefs``. Stops at the first non-executed/failed stage. Every
    stage outcome (result or recorded failure) is returned.
    """
    problems = validate_pipeline(stages)
    if problems:
        raise ValueError("; ".join(problems))
    artifacts: list[str] = []
    stage_results: list[dict[str, Any]] = []
    for index, stage in enumerate(stages):
        job = dict(stage)
        refs = list(job.get("contextRefs") or [])
        refs.extend(artifacts)
        job["contextRefs"] = refs
        try:
            outcome = submit(job)
        except Exception as exc:
            outcome = {"executed": False, "error": f"{type(exc).__name__}: {exc}"}
        record = {"stage": index, "target": job.get("targetCapability"), "outcome": outcome}
        stage_results.append(record)
        result = (outcome or {}).get("result") or {}
        facts = result.get("verifiedFacts") or []
        if (outcome or {}).get("executed") and facts:
            artifacts.append(f"stage:{index}:{job.get('targetCapability')}")
        else:
            return {
                "ok": False,
                "stages": stage_results,
                "artifacts": artifacts,
                "stopped_at": index,
            }
    return {"ok": True, "stages": stage_results, "artifacts": artifacts}
