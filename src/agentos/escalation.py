"""Bounded escalation across executors (Part 2A).

When a routed executor fails or the routing decision is unsatisfiable, the
federation can escalate to another executor. Escalation is *bounded* (max
hops), *honest* (the reason is a finite vocabulary, surfaced in the result
payload), and *policy aware* (premium executors never get live execution
on escalation unless the job was already approved for them).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

DEFAULT_MAX_HOPS = 2


class EscalationReason(str, Enum):
    CAPABILITY_MISSING = "CAPABILITY_MISSING"
    QUALITY_TOO_LOW = "QUALITY_TOO_LOW"
    TOOL_MISSING = "TOOL_MISSING"
    EXECUTOR_FAILED = "EXECUTOR_FAILED"
    NODE_OFFLINE = "NODE_OFFLINE"
    PERSISTENCE_REQUIRED = "PERSISTENCE_REQUIRED"
    AUTH_SESSION_REQUIRED = "AUTH_SESSION_REQUIRED"
    DATA_BOUNDARY = "DATA_BOUNDARY"
    HUMAN_OVERRIDE = "HUMAN_OVERRIDE"


REASON_LABELS: dict[str, str] = {
    "CAPABILITY_MISSING": "no routed executor offers the required capability",
    "QUALITY_TOO_LOW": "best available executor cannot meet the quality floor",
    "TOOL_MISSING": "the executor lacks tools needed for the deliverable",
    "EXECUTOR_FAILED": "the routed executor errored or returned unusable output",
    "NODE_OFFLINE": "the laptop node is OFFLINE; local executors are gated",
    "PERSISTENCE_REQUIRED": "work must persist across laptop sleep/offline",
    "AUTH_SESSION_REQUIRED": "the task needs an authenticated session no executor has",
    "DATA_BOUNDARY": "no executor permits this data class for the capability",
    "HUMAN_OVERRIDE": "reviewer forced a different executor",
}


def reason_label(reason: str) -> str:
    return REASON_LABELS.get(reason, reason)


def classify_failure(
    job: dict[str, Any] | None,
    decision: dict[str, Any] | None,
    failed: dict[str, Any] | None,
) -> tuple[str, str]:
    """Pick the most truthful escalation reason for a failed attempt.

    Returns ``(reason, description)`` derived only from signals we actually
    observed (executor output, decision structure, node state). Never
    guesses.
    """
    result = (failed or {}).get("result") or {}
    status = str(failed.get("status") or result.get("status") or "").upper()
    error = str(result.get("error") or failed.get("error") or "")
    error_l = error.lower()

    if "unavailable" in error_l or "not configured" in error_l or status == "UNAVAILABLE":
        return ("CAPABILITY_MISSING", reason_label("CAPABILITY_MISSING"))
    if "node" in error_l and "offline" in error_l:
        return ("NODE_OFFLINE", reason_label("NODE_OFFLINE"))
    if "authenticated" in error_l or "session" in error_l or "cookie" in error_l:
        return ("AUTH_SESSION_REQUIRED", reason_label("AUTH_SESSION_REQUIRED"))
    if "data class" in error_l or "data_class" in error_l or "HIGHLY_SENSITIVE" in str(job or {}):
        if "tool" not in error_l:
            return ("DATA_BOUNDARY", reason_label("DATA_BOUNDARY"))
    if "tool" in error_l or "missing tool" in error_l:
        return ("TOOL_MISSING", reason_label("TOOL_MISSING"))
    if "quality" in error_l or "quality floor" in error_l:
        return ("QUALITY_TOO_LOW", reason_label("QUALITY_TOO_LOW"))
    if status == "FAILED" or "error" in error_l or "failed" in error_l:
        return ("EXECUTOR_FAILED", reason_label("EXECUTOR_FAILED"))
    return ("EXECUTOR_FAILED", reason_label("EXECUTOR_FAILED"))


@dataclass
class EscalationPlan:
    reason: str = "EXECUTOR_FAILED"
    description: str = ""
    max_hops: int = DEFAULT_MAX_HOPS
    candidates: list[dict[str, Any]] = field(default_factory=list)
    hops_used: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "description": self.description,
            "maxHops": self.max_hops,
            "candidates": self.candidates,
            "hopsUsed": self.hops_used,
            "notes": self.notes,
        }


def plan_escalation(
    job: dict[str, Any] | None,
    decision: dict[str, Any] | None,
    cells: list[dict[str, Any]],
    failed_id: str | None,
    node_state: str = "ONLINE",
    max_hops: int = DEFAULT_MAX_HOPS,
    data_class: str = "PUBLIC",
) -> EscalationPlan:
    """Plan the bounded escalation ladder for a failed attempt.

    Candidates are other cells that (a) offer the same target capability,
    (b) are node-eligible, (c) are usable, prefer non-premium tier, prefer
    lower tier cost, and never repeat a previously attempted cell.
    """
    plan = EscalationPlan(max_hops=max_hops)
    target = str((job or {}).get("targetCapability") or "")
    tried_ids = set((failed_id or "") and [failed_id])
    for hop in (job or {}).get("escalations") or []:
        if isinstance(hop, dict) and hop.get("cellId"):
            tried_ids.add(hop["cellId"])

    for cell in cells:
        cid = str(cell.get("id") or "")
        if not cid or cid in tried_ids:
            continue
        ops = cell.get("supported_operations") or []
        if target not in ops:
            continue
        if str(cell.get("status") or "").upper() in ("DOWN", "UNAVAILABLE", "DISABLED"):
            continue

        from .node import node_gate

        eligible, note = node_gate(cell, node_state)
        if not eligible:
            continue

        max_dc = str(cell.get("data_class_max") or "HIGHLY_SENSITIVE").upper()
        _rank = {"PUBLIC": 0, "INTERNAL": 1, "CONFIDENTIAL": 2, "HIGHLY_SENSITIVE": 3}
        if _rank.get(str(data_class).upper(), 3) > _rank.get(max_dc, 3):
            continue

        tier = int(cell.get("tier") or 99)
        score = int(cell.get("score") or 0)
        plan.candidates.append(
            {
                "id": cid,
                "tier": tier,
                "score": score,
                "note": note,
                "premium": bool(cell.get("premium")),
            }
        )

    plan.candidates.sort(key=lambda c: (c["premium"], c["tier"], -c["score"], c["id"]))
    plan.candidates = plan.candidates[: max(1, max_hops)]
    plan.notes.append(f"{len(plan.candidates)} escalation candidate(s) found")
    return plan