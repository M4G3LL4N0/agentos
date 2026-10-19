"""Hive intelligence: evidence-based worker/provider assignment.

Scores candidates by task complexity, model proficiency, provider
availability, historical success, latency, usage limits, resource cost
and verification burden. Free-first when suitable. Prefers substantial
coherent assignments over prompt splitting; requests parallel workers
only when projected throughput improvement is measurable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CandidateScore:
    provider_id: str
    score: float
    reason: str
    free: bool = False
    cost_rank: int = 9


@dataclass
class Assignment:
    provider_id: str
    score: float
    reason: str
    parallelism: int = 1
    chunks: list[str] = field(default_factory=list)


@dataclass
class TaskProfile:
    """What we know about the task before assigning it."""

    capability: str = "coding"
    complexity: int = 1          # 1..5 (5 = hardest)
    expected_seconds: float = 60.0
    verification_required: bool = True
    project_priority: int = 50   # 0..100
    size_bytes: int = 0
    input_count: int = 1


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def score_candidate(
    task: TaskProfile,
    candidate: dict[str, Any],
    history: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
) -> CandidateScore:
    """Score one provider candidate for one task.

    history: {"success_rate": float, "avg_latency_ms": float}
    usage:   {"blocked": bool, "cooldown": bool, "quota_left": float}
    """
    history = history or {}
    usage = usage or {}
    state = str(candidate.get("state", "UNKNOWN"))
    free = bool(candidate.get("free", False))
    cost_rank = int(candidate.get("cost_rank", 9))

    if state != "READY":
        return CandidateScore(
            provider_id=candidate.get("id", "?"),
            score=0.0,
            reason=f"state={state}",
            free=free,
            cost_rank=cost_rank,
        )
    if usage.get("blocked") or usage.get("cooldown") or usage.get("quota_left", 1.0) <= 0.0:
        return CandidateScore(
            provider_id=candidate.get("id", "?"),
            score=0.0,
            reason="usage limit reached",
            free=free,
            cost_rank=cost_rank,
        )

    capabilities = candidate.get("capabilities", [])
    capability_fit = 1.0 if task.capability in capabilities else 0.3

    success_rate = _clamp(float(history.get("success_rate", 0.5)), 0.0, 1.0)
    latency_ms = float(history.get("avg_latency_ms", 3000.0))
    latency_score = _clamp(1.0 - latency_ms / 60000.0, 0.0, 1.0)

    # cost: free providers always preferred when otherwise equal
    cost_score = 1.0 if free else _clamp(1.0 - (cost_rank - 1) / 8.0, 0.1, 1.0)

    # complexity: heavier tasks trust proven providers more
    complexity_weight = 0.1 * (task.complexity - 1)
    proficiency = success_rate + complexity_weight * success_rate

    # verification burden: hard-to-verify tasks need higher success rate
    verify_weight = 0.1 if task.verification_required else 0.0

    # priority boosts score but never overrides state/usage blocks
    priority_bonus = _clamp(task.project_priority / 500.0, 0.0, 0.2)

    score = (
        0.35 * capability_fit
        + 0.30 * _clamp(proficiency, 0.0, 1.0)
        + 0.15 * latency_score
        + 0.20 * cost_score
        + verify_weight * success_rate
        + priority_bonus
    )
    score = round(_clamp(score, 0.0, 1.0), 4)

    reason = (
        f"cap={capability_fit:.1f} succ={success_rate:.2f} "
        f"lat={latency_ms:.0f}ms cost={'free' if free else cost_rank}"
    )
    return CandidateScore(
        provider_id=str(candidate.get("id", "?")),
        score=score,
        reason=reason,
        free=free,
        cost_rank=cost_rank,
    )


def choose_assignment(
    task: TaskProfile,
    candidates: list[dict[str, Any]],
    histories: dict[str, dict[str, Any]] | None = None,
    usages: dict[str, dict[str, Any]] | None = None,
) -> Assignment | None:
    """Pick the best READY candidate; free-first when scores tie."""
    histories = histories or {}
    usages = usages or {}
    scored = [
        score_candidate(task, c, histories.get(str(c.get("id")), None), usages.get(str(c.get("id")), None))
        for c in candidates
    ]
    eligible = [s for s in scored if s.score > 0.0]
    if not eligible:
        return None
    # sort: higher score first; ties broken by free first, then lower cost rank
    eligible.sort(key=lambda s: (-s.score, not s.free, s.cost_rank))
    best = eligible[0]
    return Assignment(provider_id=best.provider_id, score=best.score, reason=best.reason)


def should_parallelize(
    task_count: int,
    avg_task_seconds: float,
    assignment_overhead_seconds: float,
    available_workers: int,
) -> int:
    """Return worker count: >1 only when measurable throughput gain exists.

    Serial duration  = n * avg
    Parallel duration = ceil(n / w) * avg + w * overhead
    Parallel wins only if strictly faster than serial.
    """
    if task_count <= 1 or available_workers <= 1 or avg_task_seconds <= 0:
        return 1
    import math

    serial = task_count * avg_task_seconds
    best_workers = 1
    best_time = serial
    for w in range(2, min(available_workers, task_count) + 1):
        parallel_time = math.ceil(task_count / w) * avg_task_seconds + w * assignment_overhead_seconds
        # require 20% improvement to justify coordination cost
        if parallel_time < best_time * 0.8:
            best_time = parallel_time
            best_workers = w
    return best_workers


def coalesce_tasks(tasks: list[str], max_workers: int) -> list[list[str]]:
    """Bundle tasks into substantial coherent chunks instead of
    excessive prompt splitting. Returns up to ``max_workers`` groups."""
    if not tasks:
        return []
    if len(tasks) <= max_workers:
        return [[t] for t in tasks]
    # balanced chunks: sizes differ by at most 1, so no tiny remainder group
    import math

    base = math.floor(len(tasks) / max_workers)
    remainder = len(tasks) % max_workers
    groups: list[list[str]] = []
    idx = 0
    for w in range(max_workers):
        size = base + (1 if w < remainder else 0)
        if size == 0:
            break
        groups.append(tasks[idx : idx + size])
        idx += size
    return groups
