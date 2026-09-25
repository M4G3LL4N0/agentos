"""Conservative federated result cache (Part 2A).

Caching only applies to SIMULATED/INSPECT execution classes. It never
applies to HIGHLY_SENSITIVE data, never to LIVE/PREMIUM orders, never to
secrets or pass-through requests, and never to inherently dynamic
material (news/prices/live availability). A hit is a *cache hit* — the
honest semantics are: no executor ran, provenance is ``cache://<key>``,
cost recorded is exactly zero, usage class is ``cached``.

Keys are deterministic: sha256(capability + deliverable + compactContext +
dataClass + sourceVersion). TTLs come from the material's stability class.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Any

from .executors import stability_class_for

TTL_SECONDS: dict[str, int] = {
    "deterministic_output": 300,
    "repo_facts": 1800,
    "static_extraction": 600,
    "unchanged_docs": 3600,
    "stable_probe": 900,
}

DYNAMIC_HINTS = (
    "news",
    "price",
    "prices",
    "availability",
    "inventory",
    "weather",
    "account",
    "balance",
    "balance",
    "stock",
    "crypto",
    "live",
    "market",
    "rate",
    "ticker",
)

VIRTUAL_CLASSES = ("dynamic",)


def ttl_for(stability: str) -> int:
    return TTL_SECONDS.get(stability, 0)


def cache_class_for(job: dict[str, Any]) -> str:
    """Map a job to its material stability class tag."""
    cap = str(job.get("targetCapability") or "")
    return (
        stability_class_for(cap)
        if cap in __import__("agentos.executors", fromlist=["STABILITY_CLASSES"]).STABILITY_CLASSES
        else "dynamic"
    )


def is_dynamic(job: dict[str, Any]) -> bool:
    deliverable = str(job.get("deliverable") or "").lower()
    compact = str(job.get("compactContext") or "").lower()
    haystack = deliverable + " " + compact
    if any(h in haystack for h in DYNAMIC_HINTS):
        return True
    return cache_class_for(job) == "dynamic"


def cacheable(job: dict[str, Any]) -> bool:
    """Gate: what may even be considered for the cache (before TTL check)."""
    ec = str(job.get("executionClass") or "SIMULATED").upper()
    dc = str(job.get("dataClass") or "PUBLIC").upper()
    if dc == "HIGHLY_SENSITIVE":
        return False
    if ec not in ("SIMULATED", "INSPECT"):
        return False
    body = str(job.get("compactContext") or "") + str(job.get("deliverable") or "")
    if any(s in body for s in ("BEGIN PRIVATE KEY", "session_token=", "refresh_token=")):
        return False
    return True


def cache_key(job: dict[str, Any], source_version: str = "1.0") -> str:
    seed = "\x1f".join(
        [
            str(job.get("targetCapability") or ""),
            str(job.get("deliverable") or ""),
            str(job.get("compactContext") or ""),
            str(job.get("dataClass") or "PUBLIC").upper(),
            source_version,
        ]
    )
    return sha256(seed.encode("utf-8")).hexdigest()


def hit_payload(job: dict[str, Any], stored: dict[str, Any], key: str) -> dict[str, Any]:
    """Wrap a stored result as an honest cache hit envelope."""
    return {
        "usageClass": "cached",
        "actualCost": "0",
        "tokenCount": stored.get("token_count") or "0",
        "executor": stored.get("executor") or "result-cache",
        "note": f"cache hit (class={stored.get('cache_class')}, key={key[:12]})",
        "provenance": f"cache://{key}",
        "filesChanged": [],
    }


class ResultCache:
    """Thin service over the store's ``result_cache`` table."""

    def __init__(self, store: Any, config: dict[str, Any] | None = None) -> None:
        self.store = store
        self.config = dict(config or {})

    @property
    def enabled(self) -> bool:
        return bool((self.config.get("federation") or {}).get("cache", {}).get("enabled", True))

    def get(self, job: dict[str, Any], source_version: str = "1.0") -> dict[str, Any] | None:
        if not self.enabled or not cacheable(job) or is_dynamic(job):
            return None
        key = cache_key(job, source_version)
        row = self.store.get_result_cache(key)
        if row is None:
            return None
        return dict(row)

    def key_for(self, job: dict[str, Any], source_version: str = "1.0") -> str:
        return cache_key(job, source_version)

    def put(self, job: dict[str, Any], result: dict[str, Any], source_version: str = "1.0") -> str | None:
        """Store a run summary only when caching is allowed and the result
        is a usable execution summary (non-error). Returns stored key."""
        if not self.enabled or not cacheable(job) or is_dynamic(job):
            return None
        if not result or result.get("status") in ("failed", "FAILED") or result.get("error"):
            return None
        key = cache_key(job, source_version)
        cls = cache_class_for(job)
        self.store.save_result_cache(
            key,
            {
                "cache_class": cls,
                "capability": str(job.get("targetCapability") or ""),
                "data_class": str(job.get("dataClass") or "PUBLIC").upper(),
                "source_version": source_version,
                "ttl_seconds": ttl_for(cls),
                "payload_json": {},
                "result": result,
            },
        )
        return key

    def invalidate(self, cache_class: str | None = None) -> int:
        return self.store.delete_result_cache(cache_class)

    def stats(self) -> dict[str, Any]:
        return self.store.result_cache_stats()

    def save_to(self, store: Any, limit: int = 1000) -> int:
        """Round-trip this cache's entries into ``store``'s durable
        ``result_cache`` table. Entries must already exist in the store this
        cache is bound to. Returns the number of entries persisted."""
        count = 0
        for row in self.store.list_result_cache_entries(limit=limit):
            store.save_result_cache_entry(
                row["cache_key"],
                row["envelope_json"],
                row["ttl_seconds"],
                row["usage_class"],
                row["stability_class"],
            )
            count += 1
        return count

    @classmethod
    def from_store(
        cls, store: Any, config: dict[str, Any] | None = None
    ) -> "ResultCache":
        """Rebuild a cache service whose entries live in ``store``'s durable
        ``result_cache`` table (round-trips through the store)."""
        return cls(store, config)