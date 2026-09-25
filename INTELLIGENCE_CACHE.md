# Intelligence Cache

`src/agentos/intelligence_cache.py` — a shared, provider-neutral cache over
the existing durable `Store`. No second storage system, no filesystem forest.

## Contract

- Constructor: `IntelligenceCache(store, config=None, *, project='', requester_scope='PUBLIC')`.
- `build_entry(...)` + `put(entry)`; `get(intent, ...)` (L0 exact),
  `get_semantic(intent, ...)` (L4 bag), `invalidate(deps, also_exact=False)`
  (narrow), `mark_verified(cache_key)`, `list_entries()`, `stats()`.
- Keys: deterministic `intcache_key(intent, output_schema, project)` (L0) and
  order-independent `semantic_key(intent, output_schema, project)` (L4 token
  bag). `content_hash` canonicalizes JSON for dependency faerverification.

## Layers

L0 exact result → L1 artifact reuse → L2 tool result (sourceRefs carry tool
input hash twins) → L3 context intelligence → L4 semantic reuse (intent
shingles/bag, Jaccard overlap) → L5 provider/prompt cache (documented, honest:
never fabricated provider metadata) → L6 fresh reasoning (caller escalates).

## Freshness & status

`effective_status()` = FRESH while `lastVerifiedAt + ttl` is in the future,
otherwise STALE; unparseable timestamps → UNVERIFIED. `ttl <= 0` means the
entry never expires once verified. Stale/UNVERIFIED entries are returned as
honest misses with a reason — never silently presented fresh.

## Security scope

`SecurityScope` (PUBLIC < INTERNAL < CONFIDENTIAL < HIGHLY_SENSITIVE).
`dominates(requester, entry_scope)` gates every lookup; a non-dominating
requester gets an honest miss (`security scope not dominated`) and bumps the
`scope_rejections` counter. HIGHLY_SENSITIVE entries are refused outright —
the cache never stores them.

## Secrets gate

`looks_secret()` rejects payloads whose text contains `SECRET_MARKERS`:
BEGIN PRIVATE KEY, PRIVATE KEY BLOCK, `private key`, session/refresh/access
token, client_secret, authorization, bearer, recovery code, api_key/apikey,
password, passwd, passphrase, auth cookie. This catches PEM blocks
(PEM private-key headers included). Uses substring match on
lowercased text.

## Storage

Rows live in the `intelligence_cache` table (cache_key PK, entry_json,
created_at, hits, ttl_seconds). The semantic alias row is written once on
put (`get_semantic` finds it). `from_dict`/`to_dict` round-trip every field
including `project` and `missReason`.

## Telemetry

`stats()` returns entries, hits, misses, semanticHits, exactHits,
modelCallsAvoided, scopeRejections, staleRejected, invalidations, hitRate,
enabled — from the same durable counters the CLI `efficiency stats` prints.