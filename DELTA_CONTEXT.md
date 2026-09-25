# Delta Context

`compute_delta()` + `DeltaContext` in `src/agentos/efficiency.py`. Premium
workers receive the compact baseline ref + delta + task — never the full
repository/history.

## Shape

`DeltaContext.to_dict()`:

- `baselineRef` — ref label (default `baseline@1`)
- `currentSourceState` — label → content hash map (current)
- `changedInputs` — only labels whose hash changed between baseline/current
- `changedFiles` — changed labels that look like files (paths / known
  extensions)
- `invalidatedCacheRefs` — `cache_scope/<label>` for each changed input (only
  when a `cache_scope` is passed)
- `changedFacts` — human facts: `new`, `changed`, `removed` per changed input
- `currentTask`

Unchanged inputs are reused, never re-read. `content_state(files)` builds the
label→hash map from raw file contents via `content_hash`.

## Facts

- `agentos efficiency delta --baseline '{"src/a.py":"h-old","src/b.py":"same"}'
  --current '{"src/a.py":"h-new","src/b.py":"same"}' --cache-scope intcache`
  → `changedInputs={"src/a.py":"h-new"}`, `invalidatedCacheRefs=["intcache/src/a.py"]`,
  `src/b.py` untouched.