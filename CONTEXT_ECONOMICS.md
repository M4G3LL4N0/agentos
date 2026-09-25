# Context Economics

How AgentOS spends context in low-usage mode: cheap layers first, compact
context everywhere, zero re-reading of unchanged inputs.

## Principles

1. **Exact before semantic before reasoning.** L0 (identical normalized
   intent + unchanged deps) is free; L4 (order-independent bag) is still free;
   only L6 escalates to a model. `triage` refuses to spend premium on
   anything a cache hit or a rule can answer.
2. **Delta over history.** Workers get `baselineRef + changedInputs + task`,
   never the whole repo. `compute_delta` diffs hash maps; unchanged inputs are
   reused with zero recompute.
3. **No fabricated provider caching.** L5 (provider/prompt cache) is a
   documented layer only. AgentOS never claims Grok/OpenAI expose native
   prompt-caching metadata it did not measure.
4. **Telemetry is honest counts.** `efficiency stats` (modelCallsAvoided,
   exactHits, semanticHits, hits/misses, hitRate, scopeRejections,
   invalidations) come from durable counters — never inferred from a log.
5. **Deterministic evidence does not call a model.** `TaskFingerprint`
   dedupes identical work across roles; verifier joins only when a quality
   floor or explicit verification burden exists.

## Cost model

- Cache hit: `modelCalls = 0`, provenance recorded, zero token spend.
- Honest miss: `modelCalls = 1` with a reason so callers know *why* the cheap
  path failed.
- Global `requests_total` counter bumps per cache-routed request (see
  `services.efficiency_triage` / `efficiency_worker`).

## Facts

- `modelCallsAvoided`, `exactHits`, `semanticHits` increment synchronously on
  every hit and are visible in `agentos efficiency stats`.