"""Unified capability graph: one ranked view over every executor source.

Sources: native deterministic tools, OpenCode/OpenHands/Hermes/OpenClaw,
BrowserHarness, GrokBot cells (configured metadata only, never contacted),
A2A peers (approved or explicitly listed), approved MCP servers (metadata
only; transport DEFERRED), model/API workers (capability records only).

Every candidate reports availability, health, quality class, cost/scarcity,
local/remote, persistence, browser/computer availability, data-class
limits, approval requirements and historical success — each marked
measured or unknown, never invented.
"""

from __future__ import annotations

from typing import Any


def _success_rate(stats: dict[str, Any], key: str) -> float | None:
    entry = (stats or {}).get(key) or {}
    runs = entry.get("runs") or 0
    if not runs:
        return None
    return round(float(entry.get("verified", 0)) / float(runs), 3)


def rank_candidates(
    need: str,
    capabilities: list[Any],
    cells: list[Any],
    stats: dict[str, Any] | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Rank executor candidates for a free-text *need* (deterministic).

    Matching is substring-based over ids, names, operations and
    descriptions — no model, no invention. Unmatched needs return [].
    """
    needle = str(need or "").strip().lower()
    if not needle:
        return []
    stats = stats or {}
    cell_by_op: dict[str, Any] = {}
    for cell in cells:
        for op in list(getattr(cell, "supported_operations", []) or []) + list(
            getattr(cell, "capability_ids", []) or []
        ):
            cell_by_op.setdefault(str(op).lower(), cell)
    scored: list[tuple[float, dict[str, Any]]] = []
    for cap in capabilities:
        cap_id = str(getattr(cap, "id", ""))
        name = str(getattr(cap, "name", "") or "")
        operations = [str(o) for o in (getattr(cap, "operations", None) or [])]
        description = str(getattr(cap, "description", "") or "")
        haystack = f"{cap_id} {name} {' '.join(operations)} {description}".lower()
        if needle not in haystack:
            continue
        health = getattr(getattr(cap, "health", ""), "value", getattr(cap, "health", ""))
        cell = cell_by_op.get(cap_id.lower())
        for op in operations:
            if cell is None:
                cell = cell_by_op.get(op.lower())
        score = 10.0 if needle in cap_id.lower() else 5.0
        if needle in " ".join(operations).lower():
            score += 4.0
        usable = bool(getattr(cap, "is_usable", lambda: False)())
        if not usable:
            score -= 6.0
        candidate: dict[str, Any] = {
            "executor": cap_id,
            "availability": str(getattr(cap, "availability", "")),
            "health": str(health or "UNKNOWN"),
            "usable": usable,
            "quality_class": str(
                getattr(cap, "readiness", "") or "UNKNOWN"
            ),
            "cost_scarcity": _cost_of(cap),
            "local_remote": (
                "remote"
                if cell is not None and bool(getattr(cell, "is_external", False))
                else "local"
            ),
            "persistence": bool(cell.persistence) if cell is not None else False,
            "browser": _has_browser(cap, cell),
            "computer": _has_computer(cap, cell),
            "data_class_max": (
                str(cell.data_class_max) if cell is not None else "UNKNOWN"
            ),
            "approval_required": _approval_of(cap, cell),
            "success_rate": _success_rate(stats, cap_id),
            "source": str(getattr(cap, "source", "") or "unknown"),
            "operations": operations,
        }
        scored.append((score, candidate))
    scored.sort(key=lambda item: (-item[0], item[1]["executor"]))
    return [candidate for _, candidate in scored[: max(1, limit)]]


def _cost_of(cap: Any) -> str:
    cost = getattr(cap, "cost", None) or {}
    per_run = cost.get("per_run") if isinstance(cost, dict) else None
    if isinstance(per_run, (int, float)):
        return "NORMAL" if per_run else "FREE"
    return "UNKNOWN"


def _has_browser(cap: Any, cell: Any) -> str:
    text = f"{getattr(cap, 'id', '')} {getattr(cell, 'provider', '') if cell else ''}".lower()
    if "browser" in text:
        return "available" if cell is None or cell.usable() else "unavailable"
    return "none"


def _has_computer(cap: Any, cell: Any) -> str:
    if cell is not None and (
        getattr(cell, "persistent_computer", False)
        or getattr(cell, "persistence", False)
    ):
        return "persistent" if cell.usable() else "unavailable"
    return "none"


def _approval_of(cap: Any, cell: Any) -> str:
    if cell is not None and int(getattr(cell, "tier", 0) or 0) >= 4:
        return "premium approval required"
    return "none"
