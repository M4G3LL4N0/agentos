"""IntelligenceCache — durable, provider-neutral intelligence reuse with
honest freshness, scope gating, narrow dependency invalidation and honest
L0..L6 layering. Thin over the existing durable ``Store`` (no second
storage system, no fabricated provider/model metadata).

Honesty contract
----------------
* A cache HIT is served only when the entry's *measured* freshness
  (verified-at + ttl vs now) is FRESH **and** the requester's security
  scope dominates the entry's scope. Never a guessed freshness.
* STALE / PARTIAL / INVALID / UNVERIFIED entries are served as an honest
  MISS with an explicit reason — never laundered into a silent HIT, never
  reported as "provider prompt cache" on the provider's behalf.
* Secrets and HIGHLY_SENSITIVE material are never cached; secret-bearing
  payloads raise ``ValueError`` on write. No fabricated provider metadata:
  provider/model/modelVersion/promptVersion come verbatim from the caller.
* Invalidation is *narrow* — only entries whose recorded dependencies list a
  changed dependency key are invalidated. No project-wide blast radius.
* L4/L5 reuse is honest: ``semantic_key`` matches an order-independent intent
  bag (documented), and ``L5_PROVIDER_PROMPT_CACHE`` is only recorded when
  the caller explicitly reports a provider-managed prompt cache.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

# ---------------------------------------------------------------------------
# security scope
# ---------------------------------------------------------------------------


class SecurityScope:
    public = "PUBLIC"
    internal = "INTERNAL"
    confidential = "CONFIDENTIAL"
    highly_sensitive = "HIGHLY_SENSITIVE"
    order = (public, internal, confidential, highly_sensitive)
    _rank = {s: i for i, s in enumerate(order)}

    @classmethod
    def dominates(cls, requester: str, entry_scope: str) -> bool:
        r = cls._rank.get(str(requester or "").upper(), -1)
        e = cls._rank.get(str(entry_scope or "").upper(), -1)
        return r >= 0 and e >= 0 and r >= e


# ---------------------------------------------------------------------------
# cache layers / states / entry types
# ---------------------------------------------------------------------------


class CacheLevel:
    L0 = "L0_EXACT_RESULT"
    L1 = "L1_ARTIFACT_REUSE"
    L2 = "L2_TOOL_RESULT"
    L3 = "L3_CONTEXT_INTELLIGENCE"
    L4 = "L4_SEMANTIC_REUSE"
    L5 = "L5_PROVIDER_PROMPT_CACHE"
    L6 = "L6_FRESH_REASONING"


LAYERS: tuple[str, ...] = (
    CacheLevel.L0,
    CacheLevel.L1,
    CacheLevel.L2,
    CacheLevel.L3,
    CacheLevel.L4,
    CacheLevel.L5,
    CacheLevel.L6,
)


class CacheState:
    FRESH = "FRESH"
    STALE = "STALE"
    PARTIAL = "PARTIAL"
    INVALID = "INVALID"
    UNVERIFIED = "UNVERIFIED"


CACHE_STATES: tuple[str, ...] = (
    CacheState.FRESH,
    CacheState.STALE,
    CacheState.PARTIAL,
    CacheState.INVALID,
    CacheState.UNVERIFIED,
)


class CacheEntryType:
    exact = "exact"
    artifacts = "artifacts"
    tool = "tool"
    research = "research"
    web = "web"
    repos = "repos"
    files = "files"
    context = "context"
    summaries = "summaries"
    decisions = "decisions"
    tasks = "tasks"
    prompts = "prompts"
    model_results = "model_results"
    project_state = "project_state"


CATEGORIES: tuple[str, ...] = (
    CacheEntryType.exact,
    CacheEntryType.artifacts,
    CacheEntryType.tool,
    CacheEntryType.research,
    CacheEntryType.web,
    CacheEntryType.repos,
    CacheEntryType.files,
    CacheEntryType.context,
    CacheEntryType.summaries,
    CacheEntryType.decisions,
    CacheEntryType.tasks,
    CacheEntryType.prompts,
    CacheEntryType.model_results,
    CacheEntryType.project_state,
)

VALID_CATEGORIES: frozenset[str] = frozenset(
    {
        *CATEGORIES,
        "exact_result",
        "artifact_reuse",
        "tool_result",
        "context_intelligence",
        "semantic_reuse",
        "provider_prompt_cache",
        "fresh_reasoning",
    }
)

#: default TTL (seconds) per category — conservative, honest.
DEFAULT_TTL_SECONDS: dict[str, int] = {
    "exact": 7 * 24 * 3600,
    "artifacts": 30 * 24 * 3600,
    "tool": 6 * 3600,
    "research": 6 * 3600,
    "web": 4 * 3600,
    "repos": 7 * 24 * 3600,
    "files": 24 * 3600,
    "context": 12 * 3600,
    "summaries": 12 * 3600,
    "decisions": 30 * 24 * 3600,
    "tasks": 7 * 24 * 3600,
    "prompts": 4 * 3600,
    "model_results": 24 * 3600,
    "project_state": 24 * 3600,
    "default": 6 * 3600,
}

SECRET_MARKERS: tuple[str, ...] = (
    "BEGIN PRIVATE KEY",
    "PRIVATE KEY BLOCK",
    "private key",
    "session_token",
    "refresh_token",
    "access_token",
    "client_secret",
    "authorization",
    "bearer ",
    "recovery code",
    "recovery_code",
    "api_key",
    "apikey",
    "password",
    "passwd",
    "passphrase",
    "auth cookie",
)

_STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
        "how", "i", "in", "into", "is", "it", "of", "on", "or", "please",
        "the", "to", "we", "what", "will", "with", "you", "your",
    }
)

_TOKEN_RE: re.Pattern[str] = re.compile(r"[a-z0-9]+")


def _norm_text(value: Any) -> str:
    return " ".join(str(value or "").lower().split())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_now_iso() -> str:
    """Honest UTC timestamp (the single clock the cache measures against)."""
    return _now_iso()


def content_hash(value: Any) -> str:
    """Deterministic canonical content hash (order-stable JSON)."""
    if value is None:
        text = "null"
    else:
        try:
            text = json.dumps(value, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError):
            text = str(value)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def looks_secret(value: Any) -> bool:
    """Honest cheap heuristic: never cache obvious secret-bearing material."""

    if isinstance(value, dict):
        haystack = " ".join(str(k) for k in value.keys()) + " " + str(value)
    elif isinstance(value, (list, tuple)):
        haystack = " ".join(str(v) for v in value)
    else:
        haystack = str(value)
    lowered = haystack.lower()
    return any(marker.lower() in lowered for marker in SECRET_MARKERS)


#: canonical exact cache-key for L0 exact reuse (never semantic guessing).
def intcache_key(intent: str, output_schema: str = "", project: str = "") -> str:
    seed = "\x1f".join(
        [_norm_text(intent), _norm_text(output_schema), _norm_text(project)]
    )
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def intent_bag(intent: str) -> tuple[str, ...]:
    """Order-independent intent token bag (honest L4 fingerprint)."""
    toks = tuple(
        sorted(
            {
                t
                for t in _TOKEN_RE.findall(_norm_text(intent))
                if t not in _STOPWORDS
            }
        )
    )
    return toks


def intent_shingles(intent: str, width: int = 3) -> tuple[str, ...]:
    """Order-independent n-gram shingles of the intent bag (L4 matching)."""
    toks = intent_bag(intent)
    if len(toks) < width:
        return toks
    return tuple(
        "-".join(toks[i : i + width]) for i in range(len(toks) - width + 1)
    )


def semantic_key(intent: str, output_schema: str = "", project: str = "") -> str:
    """L4 semantic (order-independent intent bag) key — honest reuse."""
    seed = "\x1f".join(
        [
            ",".join(intent_bag(intent)),
            _norm_text(output_schema),
            _norm_text(project),
        ]
    )
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def bag_overlap(a: Iterable[str], b: Iterable[str]) -> float:
    """Jaccard overlap of two intent bags (0..1)."""
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _effective_status(
    created_at: str,
    last_verified_at: str,
    ttl: int,
    explicit: str,
) -> str:
    """Freshness *measured* from verification timestamps — never asserted."""
    if str(explicit or "").upper() in (
        CacheState.INVALID,
        CacheState.UNVERIFIED,
    ):
        return str(explicit).upper()
    base = last_verified_at or created_at or ""
    if not base:
        return CacheState.UNVERIFIED
    try:
        dt = datetime.fromisoformat(base)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - dt).total_seconds()
    except (TypeError, ValueError):
        return CacheState.UNVERIFIED
    if int(ttl) <= 0:
        return CacheState.FRESH
    return CacheState.FRESH if age <= int(ttl) else CacheState.STALE


# ---------------------------------------------------------------------------
# CacheEntry
# ---------------------------------------------------------------------------


@dataclass
class CacheEntry:
    """One durable intelligence-cache row (mission shape)."""

    id: str
    type: str
    cacheKey: str
    intent: str
    intentBag: tuple[str, ...] = field(default_factory=tuple)
    outputSchema: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    createdAt: str = ""
    lastVerifiedAt: str = ""
    ttl: int = 0
    confidence: float = 0.0
    level: str = CacheLevel.L0
    securityScope: str = SecurityScope.public
    dependencies: dict[str, str] = field(default_factory=dict)
    sourceRefs: list[dict[str, Any]] = field(default_factory=list)
    artifactRef: str = ""
    provider: str = ""
    model: str = ""
    modelVersion: str = ""
    promptVersion: str = ""
    status: str = CacheState.FRESH
    category: str = ""
    project: str = ""
    missReason: str = ""

    def __post_init__(self) -> None:
        self.type = str(self.type or CacheEntryType.exact).lower()
        self.category = str(self.category or self.type).lower()
        self.status = str(self.status or CacheState.FRESH).upper()
        self.level = str(self.level or CacheLevel.L0)
        self.securityScope = str(
            self.securityScope or SecurityScope.public
        ).upper()
        if not self.intentBag:
            self.intentBag = intent_bag(self.intent)
        if not self.createdAt:
            self.createdAt = utc_now_iso()
        if not self.lastVerifiedAt and self.status == CacheState.FRESH:
            self.lastVerifiedAt = self.createdAt
        if not self.cacheKey:
            self.cacheKey = intcache_key(self.intent)
        if not self.ttl:
            self.ttl = int(
                DEFAULT_TTL_SECONDS.get(self.category, DEFAULT_TTL_SECONDS["default"])
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "category": self.category,
            "cacheKey": self.cacheKey,
            "intent": self.intent,
            "intentBag": list(self.intentBag),
            "outputSchema": self.outputSchema,
            "payload": dict(self.payload),
            "createdAt": self.createdAt,
            "lastVerifiedAt": self.lastVerifiedAt,
            "ttl": int(self.ttl),
            "confidence": float(self.confidence),
            "level": self.level,
            "securityScope": self.securityScope,
            "project": self.project,
            "dependencies": dict(self.dependencies),
            "sourceRefs": [dict(r) for r in self.sourceRefs],
            "artifactRef": self.artifactRef,
            "provider": self.provider,
            "model": self.model,
            "modelVersion": self.modelVersion,
            "promptVersion": self.promptVersion,
            "status": self.status,
            "missReason": self.missReason,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CacheEntry":
        return cls(
            id=str(data.get("id") or ""),
            type=str(data.get("type") or CacheEntryType.exact),
            cacheKey=str(data.get("cacheKey") or ""),
            intent=str(data.get("intent") or ""),
            intentBag=tuple(str(s) for s in (data.get("intentBag") or [])),
            outputSchema=str(data.get("outputSchema") or ""),
            payload=dict(data.get("payload") or {}),
            createdAt=str(data.get("createdAt") or ""),
            lastVerifiedAt=str(data.get("lastVerifiedAt") or ""),
            ttl=int(data.get("ttl") or 0),
            confidence=float(data.get("confidence") or 0.0),
            level=str(data.get("level") or CacheLevel.L0),
            securityScope=str(data.get("securityScope") or SecurityScope.public),
            project=str(data.get("project") or ""),
            dependencies={
                str(k): str(v) for k, v in (data.get("dependencies") or {}).items()
            },
            sourceRefs=[dict(r) for r in (data.get("sourceRefs") or [])],
            artifactRef=str(data.get("artifactRef") or ""),
            provider=str(data.get("provider") or ""),
            model=str(data.get("model") or ""),
            modelVersion=str(data.get("modelVersion") or ""),
            promptVersion=str(data.get("promptVersion") or ""),
            status=str(data.get("status") or CacheState.FRESH),
            category=str(data.get("category") or data.get("type") or CacheEntryType.exact),
            missReason=str(data.get("missReason") or ""),
        )

    def effective_status(self) -> str:
        return _effective_status(
            self.createdAt, self.lastVerifiedAt, self.ttl, self.status
        )

    def to_dict_for_store(self) -> dict[str, Any]:
        return self.to_dict()


class IntelligenceCache:
    """Shared, provider-neutral intelligence cache over the durable Store.

    Provider/model metadata is recorded verbatim from the caller; we never
    fabricate it)Skip-freehorn honest cache reuse and honest miss paths."
    """

    def __init__(
        self,
        store: Any,
        config: dict[str, Any] | None = None,
        *,
        project: str = "",
        requester_scope: str = SecurityScope.public,
    ) -> None:
        self.store = store
        self.config = dict(config or {})
        self.project = str(project or "")
        self.requester_scope = str(requester_scope or SecurityScope.public).upper()
        eff_cfg = (self.config.get("efficiency") or {}).get("cache") or {}
        self.enabled = bool(eff_cfg.get("enabled", True))

    # -- build ---------------------------------------------------------------
    def build_entry(
        self,
        intent: str,
        *,
        category: str = CacheEntryType.exact,
        output_schema: str = "",
        source_refs: list[dict[str, Any]] | None = None,
        dependencies: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
        artifact_ref: str = "",
        security_scope: str = SecurityScope.public,
        confidence: float = 1.0,
        provider: str = "",
        status: str = CacheState.FRESH,
        project: str = "",
        level: str = CacheLevel.L0,
        ttl: int | None = None,
    ) -> CacheEntry:
        key = intcache_key(
            intent, output_schema, project or self.project
        )
        cat = str(category or CacheEntryType.exact).lower()
        scope = str(security_scope or SecurityScope.public).upper()
        if scope == SecurityScope.highly_sensitive:
            raise ValueError("HIGHLY_SENSITIVE material is never cached")
        if looks_secret(payload):
            raise ValueError("secret-bearing payload is never cached")
        entry = CacheEntry(
            id=key,
            type=cat,
            cacheKey=key,
            intent=str(intent or ""),
            outputSchema=str(output_schema or ""),
            payload=dict(payload or {}),
            createdAt=utc_now_iso(),
            lastVerifiedAt=(
                utc_now_iso() if str(status).upper() == CacheState.FRESH else ""
            ),
            ttl=int(ttl if ttl is not None
                    else DEFAULT_TTL_SECONDS.get(cat, DEFAULT_TTL_SECONDS["default"])),
            confidence=float(confidence),
            level=str(level or CacheLevel.L0),
            securityScope=scope,
            dependencies={
                str(k): str(v) for k, v in (dependencies or {}).items()
            },
            sourceRefs=[dict(r) for r in (source_refs or [])],
            artifactRef=str(artifact_ref or ""),
            provider=str(provider or ""),
            status=str(status or CacheState.FRESH).upper(),
            category=cat,
        )
        entry.project = str(project or self.project)
        return entry

    def put(self, entry: CacheEntry) -> CacheEntry:
        if not self.enabled:
            return entry
        if entry.securityScope == SecurityScope.highly_sensitive:
            raise ValueError("HIGHLY_SENSITIVE material is never cached")
        if looks_secret(entry.payload):
            raise ValueError("secret-bearing payload is never cached")
        self.store.save_intelligence_entry(entry.to_dict())
        skey = semantic_key(entry.intent, entry.outputSchema, entry.project)
        alias = entry.to_dict()
        alias["cacheKey"] = skey
        alias["level"] = CacheLevel.L4
        alias["id"] = skey
        self.store.save_intelligence_entry(alias)
        return entry

    # -- access --------------------------------------------------------------
    def get(
        self, intent: str, *, output_schema: str = "", project: str = ""
    ) -> dict[str, Any]:
        """L0 exact lookup with scope gating. Returns hit or miss dict."""
        if not self.enabled:
            return self._miss(
                intcache_key(intent, output_schema, project or self.project),
                "cache disabled",
            )
        key = intcache_key(intent, output_schema, project or self.project)
        return self._lookup_key(key, reason_prefix="exact")

    def get_semantic(
        self, intent: str, *, output_schema: str = "", project: str = ""
    ) -> dict[str, Any]:
        """L4 semantic lookup (order-independent intent bag)."""
        if not self.enabled:
            return self._miss(
                semantic_key(intent, output_schema, project or self.project),
                "cache disabled",
            )
        key = semantic_key(intent, output_schema, project or self.project)
        return self._lookup_key(key, reason_prefix="semantic")

    def _lookup_key(
        self, key: str, *, reason_prefix: str
    ) -> dict[str, Any]:
        if not self.enabled:
            return self._miss(key, "cache disabled")
        try:
            row = self.store.get_intelligence_entry(key)
        except AttributeError:
            row = None
        if row is None:
            self._bump("cache_misses", 1)
            return self._miss(key, f"{reason_prefix}: no entry stored")
        try:
            entry = CacheEntry.from_dict(row)
        except (TypeError, ValueError):
            self._bump("cache_misses", 1)
            return self._miss(key, f"{reason_prefix}: stored row unparseable")
        if not SecurityScope.dominates(self.requester_scope, entry.securityScope):
            self._bump("scope_rejections", 1)
            return self._miss(key, f"{reason_prefix}: security scope not dominated")
        status = entry.effective_status()
        if status != CacheState.FRESH:
            self._bump("stale_rejected", 1)
            return self._miss(key, f"{reason_prefix}: entry {status} (fresh reasoning required)")
        self._bump("cache_hits", 1)
        self._bump("semantic_hits" if reason_prefix == "semantic" else "exact_hits", 1)
        self._bump("model_calls_avoided", 1)
        return {
            "hit": True,
            "cacheKey": entry.cacheKey,
            "status": status,
            "level": entry.level,
            "reason": (
                f"{reason_prefix}: exact match" if reason_prefix == "exact"
                else f"{reason_prefix}: semantic (bag) match"
            ),
            "payload": entry.payload,
            "confidence": entry.confidence,
            "provenance": f"intcache://{entry.cacheKey[:12]}",
            "sourceRefs": list(entry.sourceRefs),
            "artifactRef": entry.artifactRef,
            "dependencies": dict(entry.dependencies),
            "modelCalls": 0,
        }

    def _miss(self, key: str, reason: str) -> dict[str, Any]:
        return {
            "hit": False,
            "cacheKey": key,
            "reason": reason,
            "modelCalls": 1,
        }

    def _bump(self, name: str, amount: int = 1) -> None:
        bump = getattr(self.store, "bump_intelligence_counter", None)
        if callable(bump):
            try:
                bump(name, amount)
            except (TypeError, ValueError):
                pass

    def stats(self) -> dict[str, Any]:
        counters = {}
        getter = getattr(self.store, "intelligence_counters", None)
        if callable(getter):
            try:
                counters = dict(getter())
            except (TypeError, ValueError):
                pass
        hits = int(counters.get("cache_hits", 0))
        misses = int(counters.get("cache_misses", 0))
        total = hits + misses
        return {
            "entries": int(self._count()),
            "hits": hits,
            "misses": misses,
            "semanticHits": int(counters.get("semantic_hits", 0)),
            "exactHits": int(counters.get("exact_hits", 0)),
            "modelCallsAvoided": int(counters.get("model_calls_avoided", 0)),
            "scopeRejections": int(counters.get("scope_rejections", 0)),
            "staleRejected": int(counters.get("stale_rejected", 0)),
            "invalidations": int(counters.get("invalidations", 0)),
            "hitRate": round(hits / total, 4) if total else 0.0,
            "enabled": self.enabled,
        }

    def _count(self) -> int:
        counter = getattr(self.store, "count_intelligence_entries", None)
        if callable(counter):
            try:
                return int(counter())
            except (TypeError, ValueError):
                pass
        try:
            return len(self.list_entries())
        except (TypeError, ValueError):
            return 0

    def invalidate(
        self, dependencies: dict[str, str], *, also_exact: bool = False
    ) -> dict[str, Any]:
        """Narrow dependency invalidation — never a project-wide blast radius."""
        changed = {str(k): str(v) for k, v in (dependencies or {}).items()}
        invalidated: list[str] = []
        for row in self.list_entries():
            if isinstance(row, CacheEntry):
                entry = row
            else:
                try:
                    entry = CacheEntry.from_dict(row)
                except (TypeError, ValueError):
                    continue
            deps = entry.dependencies or {}
            hit = also_exact or any(
                dep_key in deps and deps[dep_key] != new_hash
                for dep_key, new_hash in changed.items()
            )
            if hit:
                entry.status = CacheState.INVALID
                entry.lastVerifiedAt = ""
                self.store.save_intelligence_entry(entry.to_dict())
                invalidated.append(entry.cacheKey)
        self._bump("invalidations", len(invalidated))
        return {
            "invalidated": invalidated,
            "reason": f"{len(invalidated)} entries invalidated (narrow deps)",
        }

    def mark_verified(self, cache_key: str) -> bool:
        try:
            row = self.store.get_intelligence_entry(cache_key)
        except AttributeError:
            return False
        if row is None:
            return False
        entry = CacheEntry.from_dict(row)
        if entry.status == CacheState.INVALID:
            return False
        entry.status = CacheState.FRESH
        entry.lastVerifiedAt = utc_now_iso()
        self.store.save_intelligence_entry(entry.to_dict())
        return True

    def list_entries(self, limit: int = 500) -> list[CacheEntry]:
        lister = getattr(self.store, "list_intelligence_entries", None)
        if not callable(lister):
            return []
        try:
            rows = lister(limit=max(0, int(limit)))
        except (TypeError, ValueError):
            return []
        out: list[CacheEntry] = []
        for row in rows:
            try:
                out.append(CacheEntry.from_dict(row))
            except (TypeError, ValueError):
                continue
        return out
