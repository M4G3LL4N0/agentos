"""Generic scarcity + reset-aware routing economics (provider-neutral).

Every executor gets a qualitative scarcity profile; no precise money or
token figures are ever fabricated. Scarcity is a routing *penalty*, never
an absolute prohibition: a genuinely-required scarce executor can still win.

Reset modes: PRESERVE (default; scarce quota heavily penalized), NORMAL
(ordinary routing), HARVEST (only with explicit human enablement AND a
known reliable reset AND otherwise-expiring allowance AND queued useful
work — never consume quota merely because it resets).
"""

from __future__ import annotations

from typing import Any

SCARCITY_CLASSES = ("FREE", "LOW", "NORMAL", "HIGH", "SCARCE", "CRITICAL")

SCARCITY_RANK = {name: rank for rank, name in enumerate(SCARCITY_CLASSES)}

RESET_MODES = ("PRESERVE", "NORMAL", "HARVEST")

# Harvest backlog classes: exist as data, disabled unless explicitly authorized.
HARVEST_BACKLOG_CLASSES = (
    "CapabilityScout",
    "GitHubArchaeologist",
    "InternetChangeDetector",
    "UnknownUnknownScout",
    "SerendipityScout",
)

# Additive penalty per scarcity class under PRESERVE routing.
PRESERVE_PENALTY = {
    "FREE": 0.0,
    "LOW": 2.0,
    "NORMAL": 5.0,
    "HIGH": 20.0,
    "SCARCE": 60.0,
    "CRITICAL": 200.0,
}


def normalize_scarcity(value: Any) -> str:
    name = str(value or "NORMAL").upper()
    return name if name in SCARCITY_RANK else "NORMAL"


def normalize_reset_mode(value: Any) -> str:
    mode = str(value or "PRESERVE").upper()
    return mode if mode in RESET_MODES else "PRESERVE"


def scarcity_profile(cell: Any) -> dict[str, Any]:
    """Qualitative scarcity profile for one executor cell (no fabrication).

    ``used_pct`` is reported only when the cell actually carries it
    (measured posture or explicit config); otherwise it is ``None``.
    """
    usage = dict(getattr(cell, "usage", None) or {})
    config = dict(getattr(cell, "config", None) or {})
    cost_hint = dict(getattr(cell, "cost_hint", None) or {})
    scarcity = normalize_scarcity(
        config.get("scarcityClass") or usage.get("scarcityClass") or "NORMAL"
    )
    if getattr(cell, "tier", 0) >= 5 and scarcity == "NORMAL":
        # Scarce premium tiers default to HIGH unless stated otherwise.
        scarcity = "HIGH"
    used = usage.get("used_pct")
    if used is None:
        used = config.get("usedPct")
    try:
        used_pct: float | None = None if used is None else float(used)
    except (TypeError, ValueError):
        used_pct = None
    return {
        "monetaryCostClass": str(
            config.get("monetaryCostClass")
            or ("FREE" if not cost_hint.get("per_run") else "NORMAL")
        ),
        "quotaScarcity": scarcity,
        "resetWindow": config.get("resetWindow") or usage.get("resetWindow"),
        "usedPct": used_pct,
        "remainingPct": (
            None if used_pct is None else round(100.0 - used_pct, 1)
        ),
        "computeScarcity": normalize_scarcity(config.get("computeScarcity", "NORMAL")),
        "latencyClass": str(getattr(cell, "latency_class", "STANDARD") or "STANDARD"),
        "humanAttentionCost": normalize_scarcity(
            config.get("humanAttentionCost", "LOW")
        ),
        "externalDependencyRisk": normalize_scarcity(
            "HIGH" if getattr(cell, "is_external", False) else "LOW"
        ),
    }


def harvest_allowed(config: dict[str, Any]) -> tuple[bool, str]:
    """HARVEST requires ALL of: explicit enable, known reset, expiring
    allowance, queued work. Anything less refuses with a reason."""
    harvest = config.get("harvest") or {}
    if not harvest.get("enabled"):
        return False, "harvest not explicitly enabled (default disabled)"
    if not harvest.get("reset_known"):
        return False, "harvest requires a known reliable reset time"
    if not harvest.get("allowance_expiring"):
        return False, "harvest requires otherwise-expiring allowance"
    if not harvest.get("queued_work"):
        return False, "harvest requires useful queued work"
    return True, "harvest conditions satisfied"


def reset_adjustment(
    cell: Any,
    mode: str = "PRESERVE",
    config: dict[str, Any] | None = None,
) -> tuple[float, str]:
    """Additive score adjustment for a cell under a reset mode.

    Returns ``(penalty, reason)`` where penalty is subtracted from score.
    PRESERVE heavily penalizes scarce quota; NORMAL is neutral; HARVEST
    *reduces* the scarcity penalty only when all harvest guards pass.
    """
    config = dict(config or {})
    mode = normalize_reset_mode(mode)
    profile = scarcity_profile(cell)
    scarcity = profile["quotaScarcity"]
    if mode == "NORMAL":
        return 0.0, f"reset mode NORMAL: no scarcity adjustment ({scarcity})"
    if mode == "HARVEST":
        allowed, reason = harvest_allowed(config)
        if not allowed:
            penalty = PRESERVE_PENALTY[scarcity]
            return penalty, f"harvest refused ({reason}); PRESERVE penalty applies"
        relief = min(PRESERVE_PENALTY[scarcity], 15.0)
        return -relief, f"harvest authorized: scarcity relief -{relief}"
    penalty = PRESERVE_PENALTY[scarcity]
    return penalty, f"PRESERVE: {scarcity} scarcity penalty +{penalty}"
