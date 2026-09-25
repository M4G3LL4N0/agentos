# Efficiency Kernel (low-usage)

`src/agentos/efficiency.py` + `src/agentos/intelligence_cache.py` — the
low-usage efficiency kernel: rules-first triage, smallest capable team,
deterministic dedupe, compact delta context, and a shared honest
intelligence cache. Provider-neutral, stdlib-only, tested.

## Why it exists

Low-usage mode must never waste a premium model call on work a cheap rule,
a recorded artifact, or a verified cache entry can already answer. Every
layer is deliberately cheaper-than-first: rules before models, exact before
semantic, cache before reasoning.

## Modules

- `efficiency.py` — `triage`, `plan_team`, `TaskFingerprint`, `compute_delta`,
  `grok_routing_policy`, `project_state_ref`, `content_state`, `equivalent_tasks`.
- `intelligence_cache.py` — shared `IntelligenceCache` over the durable
  `Store`, L0–L6 layers (see `INTELLIGENCE_CACHE.md`).
- `services.py` — `efficiency_*` facade methods the CLI/API/MCP all reuse.
- `cli.py` — `agentos efficiency triage|team|fingerprint|delta|cache-get|
  cache-put|invalidate|stats`.

## Facts (tests + live CLI runs)

- 456 tests pass; `tests/test_efficiency_kernel.py` (23) + `test_efficiency_triage.py` (9) pin the contract.
- CLI smoke (2026-09-23): triage/team/fingerprint/delta/cache-get/cache-put/
  invalidate/stats all dispatch and return honest shapes.

## Honesty rules (non-negotiable)

- A hit returns a previously recorded, scope-compatible, fresh result with
  provenance `intcache://<key>` and `modelCalls = 0`. Never fabricated.
- Every miss returns a reason (`no entry stored`, `security scope not
  dominated`, `entry STALE (fresh reasoning required)`, `cache disabled`).
- HIGHLY_SENSITIVE material and secret-bearing payloads raise `ValueError`
  on build/put. Never cached.
- Dependency invalidation is narrow — only entries listing a changed
  dependency hash; no project-wide blast radius.
- Team planning never fans out without justification (see
  `SMALLEST_CAPABLE_TEAM.md`); zero-agent teams only when fully
  self-contained.