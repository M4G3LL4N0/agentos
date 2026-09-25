"""Resource Governor (provider-neutral): usage snapshots, source precedence,
freshness, governor routing economics and the four-way separation model.

Everything here is honest by construction:

* Invalid percentages (negative, >100, garbage) are rejected with errors —
  never silently coerced to 0 or capped.
* A snapshot older than ``max_age_seconds`` is STALE and is never presented
  as live (12-hour-old data is not "live").
* An unknown reset time is ``resetsAt=None`` with ``reset_known=False`` —
  never guessed.
* UNKNOWN usage is never converted to 0.
* The winning snapshot in ``resolve_usage`` carries its source and freshness;
  a stale fallback carries an explicit STALE warning and is not live.
* Governor weights are explicit operator configuration; nothing self-optimizes
  silently.

The governor *integrates with* the existing federation router/scarcity
machinery (``federation.resolve_route``/``score_executor_cell`` and
``scarcity.scarcity_profile``) rather than re-implementing a routing engine.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from agentos.models import utc_now_iso

#: A snapshot older than this is STALE_CACHE, never "live".
DEFAULT_MAX_AGE_SECONDS = 12 * 60 * 60  # 12 hours

LIVE = "LIVE"
STALE = "STALE"
UNKNOWN_FRESHNESS = "UNKNOWN"

#: Logical address of the GrokBot Usage & Billing export surface. AgentOS
#: cannot reach that UI directly; this address resolves to a *local exported
#: snapshot file* when one is configured, otherwise UNCONFIGURED/UNKNOWN.
GROK_USAGE_LIVE_ADDRESS = "grok://primary/runtime/usage-live.json"


class UsageSource(StrEnum):
    OFFICIAL_UI = "OFFICIAL_UI"
    OFFICIAL_API = "OFFICIAL_API"
    AUDITED_UNOFFICIAL = "AUDITED_UNOFFICIAL"
    MANUAL = "MANUAL"
    STALE_CACHE = "STALE_CACHE"
    UNKNOWN = "UNKNOWN"


#: Live-source precedence order (lowest index wins).
SOURCE_ORDER: tuple[UsageSource, ...] = (
    UsageSource.OFFICIAL_UI,
    UsageSource.OFFICIAL_API,
    UsageSource.AUDITED_UNOFFICIAL,
    UsageSource.MANUAL,
)

ALL_SOURCES = tuple(UsageSource)


def normalize_source(value: Any) -> str:
    if value is None:
        return "MANUAL"
    name = str(value).strip().upper()
    return name if name in {s.value for s in UsageSource} else "UNKNOWN"


def source_tier(value: Any) -> int:
    name = normalize_source(value)
    try:
        return SOURCE_ORDER.index(UsageSource(name))
    except ValueError:
        return len(SOURCE_ORDER)  # lowest precedence


def source_is_official(value: Any) -> bool:
    return normalize_source(value) in (
        UsageSource.OFFICIAL_UI.value,
        UsageSource.OFFICIAL_API.value,
    )


def source_is_unofficial(value: Any) -> bool:
    return normalize_source(value) == UsageSource.AUDITED_UNOFFICIAL.value


def validate_percentage(value: Any, field_name: str) -> float | None:
    """Coerce a percentage field or raise; empty/absent -> None.

    Negative, >100, booleans and unparseable values raise ValueError —
    they are never silently coerced or clamped.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_name}: boolean is not a valid percentage")
    if isinstance(value, str):
        value = value.strip()
        if not value:
            raise ValueError(f"{field_name}: empty string is not a valid percentage")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name}: {value!r} is not a valid percentage") from exc
    if parsed < 0.0 or parsed > 100.0:
        raise ValueError(f"{field_name}: {parsed} is outside 0..100")
    return round(parsed, 4)


def parse_instant(value: Any, field_name: str = "asOf") -> datetime | None:
    """Parse an ISO-8601 timestamp; unparseable values raise, None stays None."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        normalized = text if not text.endswith("Z") else text[:-1] + "+00:00"
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field_name}: {text!r} is not an ISO-8601 timestamp") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def freshness(
    as_of: Any,
    max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
    now: Any | None = None,
) -> tuple[str, float | None]:
    """Classify a snapshot age: LIVE/STALE/UNKNOWN + age seconds.

    A missing/invalid ``asOf`` is UNKNOWN (never assumed fresh). A timestamp
    in the future is UNKNOWN (not trustworthy, not live).
    """
    try:
        asof_dt = parse_instant(as_of)
    except ValueError:
        return UNKNOWN_FRESHNESS, None
    if asof_dt is None:
        return UNKNOWN_FRESHNESS, None
    now_dt = parse_instant(now) if now else datetime.now(timezone.utc)
    age_seconds = (now_dt - asof_dt).total_seconds()
    if age_seconds < 0:
        return UNKNOWN_FRESHNESS, age_seconds
    if age_seconds <= float(max_age_seconds):
        return LIVE, age_seconds
    return STALE, age_seconds


@dataclass
class UsageSnapshot:
    """A validated, provider-neutral usage reading.

    ``source`` is the provenance class; ``freshness`` is LIVE/STALE/UNKNOWN
    computed from ``asOf`` vs the max-age threshold. ``resetsAt`` is None
    whenever the reset time is genuinely unknown (never fabricated).
    """

    provider: str
    accountCell: str = ""
    usedPct: float | None = None
    remainingPct: float | None = None
    resetsAt: str | None = None
    periodStart: str | None = None
    onDemandEnabled: bool | None = None
    onDemandUsed: float | None = None
    onDemandLimit: float | None = None
    source: str = UsageSource.UNKNOWN.value
    asOf: str | None = None
    freshness: str = UNKNOWN_FRESHNESS
    confidence: float | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        self.provider = str(self.provider or "unknown").strip()
        self.accountCell = str(self.accountCell or "").strip()
        self.source = normalize_source(self.source)
        self.freshness = str(self.freshness or UNKNOWN_FRESHNESS).upper()
        if self.freshness not in (LIVE, STALE, UNKNOWN_FRESHNESS):
            self.freshness = UNKNOWN_FRESHNESS
        if self.confidence is not None:
            try:
                confidence = float(self.confidence)
            except (TypeError, ValueError):
                raise ValueError("confidence must be a number 0..1")
            if confidence < 0.0 or confidence > 1.0:
                raise ValueError("confidence must be a number 0..1")
            self.confidence = confidence

    @property
    def reset_known(self) -> bool:
        return self.resetsAt is not None

    @property
    def is_live(self) -> bool:
        return self.freshness == LIVE

    @property
    def is_unknown(self) -> bool:
        return self.source == UsageSource.UNKNOWN.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "accountCell": self.accountCell,
            "usedPct": self.usedPct,
            "remainingPct": self.remainingPct,
            "resetsAt": self.resetsAt,
            "reset_known": self.reset_known,
            "periodStart": self.periodStart,
            "onDemandEnabled": self.onDemandEnabled,
            "onDemandUsed": self.onDemandUsed,
            "onDemandLimit": self.onDemandLimit,
            "source": self.source,
            "asOf": self.asOf,
            "freshness": self.freshness,
            "is_live": self.is_live,
            "confidence": self.confidence,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UsageSnapshot":
        return cls(
            provider=str(data.get("provider", "unknown")),
            accountCell=str(data.get("accountCell", "") or ""),
            usedPct=validate_percentage(data.get("usedPct"), "usedPct"),
            remainingPct=validate_percentage(
                data.get("remainingPct"), "remainingPct"
            ),
            resetsAt=parse_instant(data.get("resetsAt"), "resetsAt"),
            periodStart=parse_instant(data.get("periodStart"), "periodStart"),
            onDemandEnabled=data.get("onDemandEnabled"),
            onDemandUsed=validate_percentage(data.get("onDemandUsed"), "onDemandUsed"),
            onDemandLimit=validate_percentage(data.get("onDemandLimit"), "onDemandLimit"),
            source=normalize_source(data.get("source")),
            asOf=data.get("asOf"),
            freshness=UNKNOWN_FRESHNESS,
            confidence=data.get("confidence"),
            error=data.get("error"),
        )


def parse_snapshot(
    data: dict[str, Any],
    *,
    forced_source: str | None = None,
    max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
    now: Any | None = None,
) -> UsageSnapshot:
    """Validate raw snapshot data (from a JSON file/config) into a snapshot.

    Percentages are validated strictly; a reset time is parsed only when
    present and never invented; freshness is classified from ``asOf``.
    """
    if not isinstance(data, dict):
        raise ValueError("usage snapshot must be a JSON object")
    provider = str(data.get("provider", "") or "unknown").strip()
    account_string = str(data.get("accountCell", "") or "").strip()
    used = validate_percentage(data.get("usedPct"), "usedPct")
    remaining = validate_percentage(data.get("remainingPct"), "remainingPct")
    on_used = validate_percentage(data.get("onDemandUsed"), "onDemandUsed")
    on_limit = validate_percentage(data.get("onDemandLimit"), "onDemandLimit")
    resets_at = parse_instant(data.get("resetsAt"), "resetsAt")
    period_start = parse_instant(data.get("periodStart"), "periodStart")
    source = forced_source or normalize_source(data.get("source"))
    label, _age = freshness(data.get("asOf"), max_age_seconds, now)
    if label == STALE:
        # A snapshot older than the threshold is STALE_CACHE, never live.
        source = UsageSource.STALE_CACHE.value
    confidence = data.get("confidence")
    return UsageSnapshot(
        provider=provider,
        accountCell=account_string,
        usedPct=used,
        remainingPct=remaining,
        resetsAt=resets_at,
        periodStart=period_start,
        onDemandEnabled=data.get("onDemandEnabled"),
        onDemandUsed=on_used,
        onDemandLimit=on_limit,
        source=source,
        asOf=str(data.get("asOf") or "").strip() or None,
        freshness=label,
        confidence=confidence,
        error=(
            "unknown reset time: no resetsAt in snapshot"
            if resets_at is None and data.get("requiresReset") is True
            else None
        ),
    )


def read_snapshot_file(
    path: str | Path,
    *,
    forced_source: str | None = None,
    max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
    now: Any | None = None,
) -> UsageSnapshot:
    """Read + validate a local JSON snapshot file. Honest errors on bad shape."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"usage snapshot not found: {path}")
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read usage snapshot: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"usage snapshot is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("usage snapshot file must contain a JSON object")
    return parse_snapshot(
        data,
        forced_source=forced_source,
        max_age_seconds=max_age_seconds,
        now=now,
    )


@dataclass
class ResolvedUsage:
    """Outcome of source-precedence resolution over one account's snapshots."""

    source: str = UsageSource.UNKNOWN.value
    original_source: str = UsageSource.UNKNOWN.value
    freshness: str = UNKNOWN_FRESHNESS
    is_live: bool = False
    unofficial: bool = False
    stale: bool = False
    stale_warning: str | None = None
    warnings: list[str] = field(default_factory=list)
    snapshot: UsageSnapshot | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "original_source": self.original_source,
            "freshness": self.freshness,
            "is_live": self.is_live,
            "unofficial": self.unofficial,
            "stale": self.stale,
            "stale_warning": self.stale_warning,
            "warnings": list(self.warnings),
            "snapshot": self.snapshot.to_dict() if self.snapshot else None,
        }


def resolve_usage(
    snapshots: list[Any],
    *,
    max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
    now: Any | None = None,
) -> ResolvedUsage:
    """Pick the winning snapshot by source precedence.

    Fresh official -> fresh audited-unofficial -> fresh manual ->
    stale-known (with an explicit STALE warning) -> UNKNOWN. Freshness
    dominates source: a fresh manual snapshot beats a stale official one,
    and a stale fallback is never presented as live.
    Accepts ``UsageSnapshot`` objects or plain dicts (validated
    on read); nothing here fabricates usage.
    """
    normalized: list[UsageSnapshot] = []
    for snap in snapshots:
        if isinstance(snap, UsageSnapshot):
            normalized.append(snap)
        elif isinstance(snap, dict):
            normalized.append(parse_snapshot(snap, now=now))
    if not normalized:
        return ResolvedUsage(
            source=UsageSource.UNKNOWN.value,
            freshness=UNKNOWN_FRESHNESS,
            is_live=False,
            warnings=[
                "no usage snapshot is configured or imported; state is UNCONFIGURED/UNKNOWN",
                "usage is never assumed to be 0 or unlimited",
            ],
        )

    evaluated: list[dict[str, Any]] = []
    for snap in normalized:
        label, _age = freshness(snap.asOf, max_age_seconds, now)
        evaluated.append(
            {
                "snapshot": snap,
                "label": label or UNKNOWN_FRESHNESS,
                "tier": source_tier(snap.source),
                "original": snap.source,
            }
        )

    fresh = sorted(
        [e for e in evaluated if e["label"] == LIVE],
        key=lambda e: (e["tier"], str(getattr(e["snapshot"], "asOf", "") or "")),
    )
    if fresh:
        winner = fresh[0]
        snap = winner["snapshot"]
        return ResolvedUsage(
            source=snap.source,
            original_source=snap.source,
            freshness=LIVE,
            is_live=True,
            unofficial=source_is_unofficial(snap.source),
            stale=False,
            snapshot=snap,
            warnings=(
                ["unofficial source (AUDITED_UNOFFICIAL): not an official reading"]
                if source_is_unofficial(snap.source)
                else []
            ),
        )

    stale = sorted(
        [e for e in evaluated if e["label"] == STALE],
        key=lambda e: (e["tier"], str(e["snapshot"].asOf or "")),
    )
    if stale:
        winner = stale[0]
        snap = winner["snapshot"]
        return ResolvedUsage(
            source=UsageSource.STALE_CACHE.value,
            original_source=snap.source,
            freshness=STALE,
            is_live=False,
            unofficial=source_is_unofficial(snap.source),
            stale=True,
            stale_warning=(
                f"snapshot is stale (asOf={snap.asOf}); shown as STALE_CACHE, "
                "not presented as live"
            ),
            warnings=[
                f"stale usage ({snap.asOf}): STALE_CACHE, never reported as live"
            ],
            snapshot=snap,
        )

    unknown_age = sorted(
        [e for e in evaluated if e["label"] == UNKNOWN_FRESHNESS],
        key=lambda e: e["tier"],
    )
    if unknown_age:
        winner = unknown_age[0]
        snap = winner["snapshot"]
        return ResolvedUsage(
            source=UsageSource.UNKNOWN.value,
            original_source=winner["original"],
            freshness=UNKNOWN_FRESHNESS,
            is_live=False,
            unofficial=source_is_unofficial(snap.source),
            stale=False,
            warnings=[
                f"asOf unknown/unusable for {snap.provider}: freshness "
                "cannot be confirmed; not presented as live"
            ],
            snapshot=snap,
        )

    return ResolvedUsage(
        source=UsageSource.UNKNOWN.value,
        freshness=UNKNOWN_FRESHNESS,
        is_live=False,
        warnings=["no usable usage snapshot; state is UNKNOWN"],
    )


def snapshot_key(snapshot: UsageSnapshot | dict[str, Any]) -> str:
    if isinstance(snapshot, UsageSnapshot):
        return f"{snapshot.provider}|{snapshot.accountCell or 'default'}"
    return (
        f"{snapshot.get('provider', '') or 'unknown'}|"
        f"{snapshot.get('accountCell', '') or 'default'}"
    )


def grok_address_to_path(
    address: str, configured_path: str | None = None
) -> str | None:
    """Map the logical ``grok://primary/runtime/usage-live.json`` address to a
    configured local file, or None (UNCONFIGURED).

    Provider-neutral: the address is a label; resolution is explicit config.
    When ``configured_path`` names a directory, the address's own filename
    (``usage-live.json``) is appended.
    """
    address = str(address or "").strip()
    if address != GROK_USAGE_LIVE_ADDRESS and not address.startswith("grok://"):
        return None
    if not configured_path:
        return None
    path = Path(str(configured_path))
    if path.is_dir() or not path.suffix:
        filesystem_name = address.rstrip("/").rsplit("/", 1)[-1] or "usage-live.json"
        path = path / filesystem_name
    return str(path)


# ---------------------------------------------------------------------------
# Governor routing economics (integrated with federation/scarcity, additive)
# ---------------------------------------------------------------------------

DEFAULT_GOVERNOR_WEIGHTS: dict[str, float] = {
    "quotaScarcity": 1.0,
    "money": 1.0,
    "latency": 1.0,
    "humanAttention": 0.5,
    "failureProbability": 2.0,
    "verificationCost": 1.0,
    "contextTransfer": 1.0,
    "computerAvailability": 1.0,
    "sessionAffinity": 1.0,
}

#: Scarcity class -> qualitative penalty used as a quota-scarcity cost.
QUOTA_COST: dict[str, float] = {
    "FREE": 0.0,
    "LOW": 1.0,
    "NORMAL": 3.0,
    "HIGH": 10.0,
    "SCARCE": 25.0,
    "CRITICAL": 60.0,
}

HUMAN_ATTENTION_COST: dict[str, float] = {
    "FREE": 0.0,
    "LOW": 0.5,
    "NORMAL": 2.0,
    "HIGH": 8.0,
    "SCARCE": 15.0,
    "CRITICAL": 30.0,
}


def governor_weights(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Explicit per-factor weights; operator config wins, never self-tuned.

    Negative weights are rejected loudly (no silent coercion). Returns the
    merged map plus its provenance.
    """
    cfg = dict((config or {}).get("governor", {}) or {})
    raw = cfg.get("weights") or {}
    merged = dict(DEFAULT_GOVERNOR_WEIGHTS)
    source = "built-in defaults (no operator override)"
    if raw and isinstance(raw, dict):
        for key, value in raw.items():
            if key not in merged:
                continue
            try:
                factor = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"governor.weights.{key} must be a number"
                ) from exc
            if factor < 0:
                raise ValueError(
                    f"governor.weights.{key} must be >= 0 (negative weight rejected)"
                )
            merged[key] = factor
        source = "operator config (governor.weights)"
    return {
        "weights": merged,
        "weights_source": source,
        "weights_explicit": bool(raw and isinstance(raw, dict) and raw),
    }


def _verified_rate(
    stats: dict[str, dict[str, int]] | None, cell_id: str
) -> float | None:
    entry = (stats or {}).get(cell_id)
    if not entry or not entry.get("runs"):
        return None
    return float(entry.get("verified", 0)) / float(entry["runs"])


@dataclass
class CostFactors:
    """Total-resource-cost factors for one routed candidate."""

    executor: str
    quotaScarcity: str
    money: float
    latencyRank: int
    humanAttention: str
    failureProbability: float | None
    expectedFailureCost: float
    verificationRequired: bool
    contextTransfer: float
    computerAvailable: str
    sessionAffinity: bool
    totalResourceCost: float
    detail: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "executor": self.executor,
            "quotaScarcity": self.quotaScarcity,
            "money": self.money,
            "latencyRank": self.latencyRank,
            "humanAttention": self.humanAttention,
            "failureProbability": self.failureProbability,
            "expectedFailureCost": round(self.expectedFailureCost, 4),
            "verificationRequired": self.verificationRequired,
            "contextTransfer": round(self.contextTransfer, 3),
            "computerAvailable": self.computerAvailable,
            "sessionAffinity": self.sessionAffinity,
            "totalResourceCost": round(self.totalResourceCost, 4),
            "detail": list(self.detail),
        }


def _computer_requirement(job: Any) -> bool:
    haystack = " ".join(
        [*(job.contextRefs or []), job.compactContext or "", job.deliverable]
    ).lower()
    return any(token in haystack for token in ("computer", "gui", "browser app", "persistent computer"))


def governor_cost_factors(
    job: Any,
    cell: Any,
    stats: dict[str, dict[str, int]] | None = None,
    weights: dict[str, float] | None = None,
) -> CostFactors:
    """Estimates (not fabricated expenditures) of total-resource cost.

    All terms derive from measured/declared state: cell config, measured
    failure probability (from past outcomes), declared cost hints. Nothing
    is invented; missing signals contribute 0 with an explanatory detail.
    """
    from agentos.scarcity import scarcity_profile

    from agentos.federation import latency_rank, quality_rank, QualityFloor

    weights = dict(weights or DEFAULT_GOVERNOR_WEIGHTS)
    profile = scarcity_profile(cell)
    detail: list[str] = []

    money = 0.0
    per_run = (cell.cost_hint or {}).get("per_run")
    if isinstance(per_run, (int, float)) and not isinstance(per_run, bool):
        money = float(per_run)
        detail.append(f"money={money:.2f}")
    else:
        detail.append("money=0 (no per_run cost declared)")

    scarcity = profile["quotaScarcity"]
    scarcity_cost = QUOTA_COST.get(scarcity, 0.0)

    attention = profile["humanAttentionCost"]
    attention_cost = HUMAN_ATTENTION_COST.get(attention, 0.0)

    rate = _verified_rate(stats, cell.id)
    failure_prob = None if rate is None else round(1.0 - rate, 4)
    if failure_prob is None:
        expected_failure_cost = 0.0
        detail.append("failureProbability=unknown (no previous outcomes)")
    else:
        expected_failure_cost = (money + 1.0) * failure_prob
        detail.append(
            f"failureProbability={failure_prob:.2f} expectedFailureCost={expected_failure_cost:.2f}"
        )

    qr = quality_rank(job.qualityFloor)
    verification_required = qr >= 1
    if verification_required:
        detail.append(f"verification required (quality_floor={job.qualityFloor})")

    transfer = float(len(job.contextRefs or []))
    if job.compactContext:
        transfer += max(0.0, min(4.0, len(job.compactContext) / 2000.0))
    detail.append(f"contextTransfer={transfer:.1f}")

    computer_req = _computer_requirement(job)
    if computer_req:
        computer_available = "YES" if getattr(cell, "persistent_computer", False) or cell.local else "NO"
    else:
        computer_available = "N/A"
    if computer_req and computer_available == "NO":
        detail.append("computer required but cell exposes no persistent computer")

    affinity = False
    sessions = (cell.config or {}).get("sessions") or []
    if cell.authorized and sessions:
        haystack = " ".join(
            [*(job.contextRefs or []), job.compactContext or "", job.deliverable]
        ).lower()
        for session in sessions:
            if f"session:{session}".lower() in haystack:
                affinity = True
                detail.append(f"sessionAffinity={session}")
                break
    if affinity:
        detail.append("authenticated-session affinity present")

    total = 0.0
    total += weights.get("quotaScarcity", 1.0) * scarcity_cost
    total += weights.get("money", 1.0) * money
    total += weights.get("latency", 1.0) * float(latency_rank(cell.latency_class))
    total += weights.get("humanAttention", 0.5) * attention_cost
    total += weights.get("failureProbability", 2.0) * expected_failure_cost
    total += weights.get("verificationCost", 1.0) * (4.0 if verification_required else 0.0)
    total += weights.get("contextTransfer", 1.0) * transfer
    total += weights.get("computerAvailability", 1.0) * (8.0 if computer_req and computer_available == "NO" else 0.0)
    total -= weights.get("sessionAffinity", 1.0) * (3.0 if affinity else 0.0)
    total = max(0.0, total)

    return CostFactors(
        executor=cell.id,
        quotaScarcity=scarcity,
        money=money,
        latencyRank=latency_rank(cell.latency_class),
        humanAttention=attention,
        failureProbability=failure_prob,
        expectedFailureCost=expected_failure_cost,
        verificationRequired=verification_required,
        contextTransfer=transfer,
        computerAvailable=computer_available,
        sessionAffinity=affinity,
        totalResourceCost=total,
        detail=detail,
    )


def governor_route(
    job: Any,
    cells: list[Any],
    stats: dict[str, dict[str, int]] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Govern resource-cost routing over the federation engine's candidates.

    Hard gates come from the existing ``score_executor_cell`` (capability,
    data class, quality floor, latency, HS isolation, health). Among the
    *eligible* cells the governor ranks by total-resource-cost estimate
    (quota scarcity, money, latency, human attention, failure probability,
    verification cost, context transfer, computer availability,
    authenticated-session affinity). Cheap-but-failing-repeatedly loses to a
    stronger worker because expected failure cost scales their price.
    """
    from agentos.federation import score_executor_cell

    weights_info = governor_weights(config)
    weights = weights_info["weights"]
    base_decision = None
    try:
        from agentos.federation import resolve_route

        base_decision = resolve_route(job, cells).to_dict()
    except Exception:
        base_decision = None

    eligible: list[tuple[Any, CostFactors]] = []
    for cell in cells:
        _score, parts = score_executor_cell(job, cell, stats)
        if parts is None:
            continue
        factors = governor_cost_factors(job, cell, stats, weights)
        eligible.append((cell, factors))

    ranked = sorted(
        eligible, key=lambda item: (item[1].totalResourceCost, str(item[0].id))
    )
    candidates = [
        {
            "executor": cell.id,
            "tier": cell.tier,
            "health": cell.health,
            "governed_score": round(factors.totalResourceCost, 4),
            "cost_factors": factors.to_dict(),
        }
        for cell, factors in ranked
    ]
    chosen = ranked[0] if ranked else None
    rationale = (
        "no executor passes the routing gates"
        if chosen is None
        else (
            f"governor chose {chosen[0].id} at total cost "
            f"{chosen[1].totalResourceCost:.2f} (cost-ranked among eligible "
            "cells scoring through federation gates)"
        )
    )
    return {
        "jobId": job.id,
        "targetCapability": job.targetCapability,
        "governed_choice": chosen[0].id if chosen else None,
        "governed_rationale": rationale,
        "candidates": candidates,
        "candidate_count": len(candidates),
        "weights": weights,
        "weights_source": weights_info["weights_source"],
        "weights_explicit": weights_info["weights_explicit"],
        "base_decision": base_decision,
    }


# ---------------------------------------------------------------------------
# Four-way separation (identity / runtime / model / computer) — additive
# ---------------------------------------------------------------------------


def separation_profile(cell: Any) -> dict[str, Any]:
    """Report whether a role/identity cell implies model or computer.

    A persistent identity NEVER implies a model inference backend or a
    computer: those are separate, explicitly configured preferences.
    ``grokInferenceImplied``/``grokComputerImplied`` reflect *disclosed
    preferences*, not identity locks.
    """
    models = list(getattr(cell, "preferredModels", None) or [])
    runtimes = list(getattr(cell, "preferredRuntimes", None) or [])
    affinity = str(getattr(cell, "computerAffinity", None) or "").strip().lower()
    identity = bool(getattr(cell, "identityPersistent", False))
    grok_inference = any(
        "grok" in str(m).lower() for m in models
    )
    grok_computer = affinity in ("grok", "grokbot", "grok computer", "grok-computer")
    return {
        "identityPersistent": identity,
        "identityImpliesModel": False,
        "identityImpliesComputer": False,
        "preferredRuntimes": runtimes,
        "preferredModels": models,
        "preferredModelsDisclosed": bool(models),
        "computerAffinity": getattr(cell, "computerAffinity", None),
        "computerDisclosed": getattr(cell, "computerAffinity", None) is not None,
        "requiredCapabilities": list(getattr(cell, "requiredCapabilities", None) or []),
        "supervisor": getattr(cell, "supervisor", None),
        "workerPolicy": getattr(cell, "workerPolicy", None),
        "verificationPolicy": getattr(cell, "verificationPolicy", None),
        "grokInferenceDisclosed": grok_inference,
        "grokComputerDisclosed": grok_computer,
        "separation": "MAINTAINED",
        "note": (
            "identity never implies model or computer; runtimes/models/"
            "computer are explicit separate preferences"
        ),
    }