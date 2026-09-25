# AgentOS Changelog

## 0.5.0 — 2026-09-24

### Added

- End-to-end learning execution lifecycle in `Engine.run`: traces, exact-cache and zero-worker fast paths, deterministic/state resolution, verified workflow reuse, advanced verification, coaching, lessons, workflow maturity, executor metrics, learned routing, fleet incidents, routines, and bounded escalation.
- CLI surfaces for learning execution, backup/reconstruction, objective-text `run`, JSON operation parameters, job-trace explanations, and an 11-class deterministic local benchmark.
- Durable stores for verification records, lessons, workflows, skills, incidents, executor metrics, autonomy records, routines, persistence decisions, and job traces.
- Hardened portable backup inspection and restore with table/column allowlists, archive safety checks, row-count validation, secret scanning, dry-run support, and transactional rollback.

### Changed

- Learned routing now modifies rank only after capability health, security, node, and approval gates remain authoritative.
- Workflow and skill persistence now round-trip every field on update; workflow compilation and matching use verified plan history and real capability preconditions.
- Retry telemetry now distinguishes failed attempts from actual recovery retries.
- Secret redaction now covers nested key variants and raw learning records; malformed persisted JSON fails closed.
- Backup validation now rejects unlisted archive members, hides private source paths, and reports target schema incompatibility during dry run.
- Learned-routing decision and explanation persistence now round-trips through the store.
- Editable package, CLI, MCP, and local A2A agent version now report `0.5.0`.

### Verification

- Full isolated discovery: 710 tests, 710 passed, 0 failures, 0 errors, 0 skips.
- Final local benchmark: 11 tasks, 2 cache hits, 1 workflow reuse, 6 workers, 10 verification calls, 0 retries, 0 model calls, 0 premium calls, 0 Grok calls, 0 human escalations, 143 ms elapsed.
- Real CLI smoke passed for init, run/explain, deterministic resolution, benchmark, fleet, routines, backup create/inspect, backup restore dry-run, and doctor (16 checks).
- Recursive/raw-field secret redaction, fail-closed persisted JSON, extra archive-member rejection, source-path minimization, target-aware backup dry-run, and learned-routing decision persistence are covered by regression tests.
- No paid provider, GrokBot, or deployment was used.
- Release status: **LOCAL GATE PASS**; publication is a separate dedicated-remote operation.
