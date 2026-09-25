"""Usage ledger (provider-neutral, durable).

Ingests usage records as *data only* — Cursor plan usage, GrokBot weekly
usage, SuperGrok usage, API budgets, OpenRouter/provider budgets, local
compute constraints, future provider quotas. Nothing here is fabricated:
``save_entry`` persists exactly what the operator feeds it; ``list_entries``
returns those records; summaries never invent totals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentos.models import new_id, utc_now_iso
from agentos.resource_governor import validate_percentage

USAGE_ENTRY_CATEGORIES = (
    "cursor",
    "grokbot_weekly",
    "supergrok",
    "api_budget",
    "openrouter_budget",
    "provider_budget",
    "local_compute",
    "future_quota",
    "manual",
    "other",
)


def normalize_category(value: Any) -> str:
    name = str(value or "manual").strip().lower().replace(" ", "_")
    return name if name in USAGE_ENTRY_CATEGORIES else "other"


@dataclass
class UsageEntry:
    """One durable usage-accounting record (data, never fabricated)."""

    provider: str
    category: str = "manual"
    accountCell: str = ""
    id: str = ""
    amount: float | None = None
    unit: str = ""
    usedPct: float | None = None
    limit: float | None = None
    refillsAt: str | None = None
    source: str = "MANUAL"
    asOf: str | None = None
    note: str = ""
    createdAt: str = field(default_factory=utc_now_iso)

    def __post_init__(self) -> None:
        if not self.id:
            self.id = new_id("ledger")
        self.provider = str(self.provider or "unknown").strip()
        self.category = normalize_category(self.category)
        for name, value, label in (
            ("amount", self.amount, "amount"),
            ("limit", self.limit, "limit"),
        ):
            if value is None:
                continue
            if isinstance(value, bool):
                raise ValueError(f"{label}: boolean is not a valid number")
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{label}: {value!r} is not a valid number") from exc
            if number < 0:
                raise ValueError(f"{label}: {number} cannot be negative")
            setattr(self, name, number)
        self.usedPct = validate_percentage(self.usedPct, "usedPct")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "category": self.category,
            "accountCell": self.accountCell,
            "amount": self.amount,
            "unit": self.unit,
            "usedPct": self.usedPct,
            "limit": self.limit,
            "refillsAt": self.refillsAt,
            "source": str(self.source or "MANUAL"),
            "asOf": self.asOf,
            "note": self.note,
            "createdAt": self.createdAt,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UsageEntry":
        return cls(
            id=str(data.get("id") or ""),
            provider=str(data.get("provider") or "unknown"),
            category=str(data.get("category") or "manual"),
            accountCell=str(data.get("accountCell") or ""),
            amount=data.get("amount"),
            unit=str(data.get("unit") or ""),
            usedPct=data.get("usedPct"),
            limit=data.get("limit"),
            refillsAt=data.get("refillsAt"),
            source=str(data.get("source") or "MANUAL"),
            asOf=data.get("asOf"),
            note=str(data.get("note") or ""),
            createdAt=str(data.get("createdAt") or utc_now_iso()),
        )


def save_entry(store: Any, entry: UsageEntry) -> UsageEntry:
    """Persist one ledger entry (upsert by id). Data in, data out — no math."""
    store.save_usage_entry(entry.to_dict())
    return entry


def list_entries(
    store: Any,
    provider: str | None = None,
    category: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    return store.list_usage_entries(
        provider=provider, category=category, limit=limit
    )


def ledger_summary(store: Any, limit: int = 500) -> dict[str, Any]:
    """Count entries by category/provider and any *recorded* amounts.

    Totals are sums of what the operator actually recorded — never invented.
    Entries without amounts are counted but excluded from totals.
    """
    records = store.list_usage_entries(limit=limit)
    by_category: dict[str, int] = {}
    by_provider: dict[str, int] = {}
    amount_entries = 0
    amount_total = 0.0
    currency_seen: set[str] = set()
    for record in records:
        entry = record.get("entry") or {}
        category = str(entry.get("category", "other") or "other")
        provider = str(entry.get("provider", "unknown") or "unknown")
        by_category[category] = by_category.get(category, 0) + 1
        by_provider[provider] = by_provider.get(provider, 0) + 1
        amount = entry.get("amount")
        if isinstance(amount, (int, float)) and not isinstance(amount, bool):
            amount_entries += 1
            amount_total += float(amount)
            unit = str(entry.get("unit") or "").strip()
            if unit:
                currency_seen.add(unit)
    return {
        "entries": len(records),
        "by_category": by_category,
        "by_provider": by_provider,
        "amount_entries": amount_entries,
        "amount_total": round(amount_total, 4),
        "units_seen": sorted(currency_seen),
        "note": "numbers are only what was recorded as data; no usage is fabricated",
    }