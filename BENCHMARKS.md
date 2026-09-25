# Benchmarks

Status: **IMPLEMENTED** (harness + persistence + report + CLI).
Model/browser/persistent workloads **SKIPPED** honestly unless a safe
executor exists — no paid runs, no invented numbers.

Workload classes A–J (`benchmarks.WORKLOAD_CLASSES`): deterministic
transformation, classification, repository inspection, code patch,
research extraction, browser read, browser interaction, multi-step
coding, synthesis/reasoning, persistent/offline-browser requirement.

Only `local-worker` × {A, B, C} executes (real deterministic local
code, measured wall latency, bounded 3 attempts). Every other
executor/workload records `SKIPPED` with the reason (model spend out
of scope, browser unavailable, no safe implementation).

Each record carries only real fields: success/failure, latency_ms,
retries, tool completion, quality signal (`exact`/`mismatch`/error),
usage/cost if exposed (`0` local, else `UNKNOWN`), node state,
date, version. Reports (`benchmark report`, `by_executor` aggregates)
show success_rate/avg_latency over *measured* runs with an explicit
"no cross-executor ranking claimed" note.

Calibration (`services.routing_calibration`): explicit weights
(defaults; operator override via `routing.weights` only — never
self-modified), benchmark aggregates attached for inspection. Scoring
itself is unchanged until an operator enables calibration.

Tests: `tests/test_fabric.py::BenchmarkTests` (4) + live
`benchmark run/report` smoke. **VERIFIED**.
