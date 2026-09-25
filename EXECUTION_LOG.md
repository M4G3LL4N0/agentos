# AgentOS Execution Log

Chronological record of what was actually built and run. Aspirational
entries are not permitted here.

## 2026-09-17 — Part A foundation build (v0.1.0)

- Scaffolded the AgentOS project from zero (nothing existed).
- Created `.venv` (Python 3.12.13); `pyproject.toml` with zero runtime deps.
- Implemented `models.py` (Objective, Capability, Execution, Event,
  VerificationResult, Failure, Evidence, Report + enums).
- Implemented `store.py` (sqlite, 6 tables, WAL, row↔model translation).
- Implemented `events.py` (persisted EventBus, 19 event types).
- Implemented `adapters/`: `base` (protocol), `shell` (real subprocess),
  `filesystem` (real read/write/exists/list), `echo` (test seam).
- Implemented `registry.py` (native catalog, discover, probe, select,
  configured-CLI registration).
- Implemented `verify.py` (exit_code/contains/file_exists/structural,
  failure classification, retry policy, backoff, next-action).
- Implemented `engine.py` (control loop, recover, verify_latest).
- Implemented `services.py` (AgentOS facade) + `cli.py` (12 commands,
  human + `--json` output).
- Bugs found and fixed during testing (not hidden):
  1. Shell adapter rejected configured operations (`greet.run`) — fixed by
     executing any operation that carries a resolved command.
  2. `--command` option clobbered argparse subparser `dest="command"` —
     fixed with `dest="shell_command"`.
  3. `--json` mode always exited 0 — fixed to propagate failure codes.
- `pip install -e .` clean; `python -m unittest discover -s tests`:
  **86 tests, OK**.
- Runtime verification via real CLI (fresh home, details below).
- Wrote living docs; updated CURRENT_STATE.md + BUILD_MAP.md to actuals.

## 2026-09-17 — Runtime verification transcript (fresh home /tmp/agentos_rt)

Commands executed and outcomes (objective ids abbreviated):

1. `agentos init` → home/db created, capabilities: echo, filesystem, shell.
2. `objective create "Write proof file" --operation fs.write
   --params '{"path":"/tmp/agentos_rt/proof.txt","content":"agentos vertical slice ok"}'`
   → created, READY.
3. `run <id>` → COMPLETED via filesystem, attempt 1; file exists on disk
   with exact content (verified outside the engine with grep).
4. `verify <id>` → VERIFIED, method structural.
5. `inspect <id>` → 1 execution (VERIFIED), 2 verifications (run +
   explicit re-verify), 0 failures, evidence items, 11 events.
6. New process `status` → objective COMPLETED persisted (restart survived).
7. Deliberate failure: `objective create "Fail on purpose" --operation
   shell.run --params '{"command":"exit 7"}'` → `run --max-attempts 2` →
   FAILED after 2 attempts; failure type `exit_code`; events contain
   `recovery.started` + `objective.failed`. Exit code 1, JSON `ok:false`.
8. `doctor` → all checks PASS (db integrity, writable, 3 capabilities OK,
   adapter boundaries).
9. Configured capability: `capabilities add greet --command "echo hello-rt"
   --expect hello-rt` → `run` → COMPLETED; `capabilities remove greet` → removed.
10. Failing-path CLI exit codes confirmed nonzero for run/verify/recover.

## 2026-09-17 — Part B build (v0.2.0)

Environment evidence first: `opencode` (with `run --format json`), `grok`
(with `agent` + `-p` single-turn), `openclaw` (with `agent --message
--json`), `gh` (authenticated) all present; `grok-build` CLI and
`langgraph` package absent; no `XAI_API_KEY`. Integration order and
adapter flags derived from `--help` output, not assumptions.

- Added `policies.py`; hardened shell (argv, duration, policy gate,
  redaction) + fs write gating; verifier redacts adapter errors.
- Extended models/store: 9-state health, Strategy/Role/PlanStep/
  ExecutionPlan/Pattern/GapAnalysis/QualityEvaluation; tables plans,
  patterns, gaps, quality.
- Added `router.py` (9 strategies, scoring, rationale, roles, conflict
  detection, GRAPH fallback).
- Rewrote `engine.py` plan-driven (sequential/parallel/iterative,
  independent verification, quality+revise, alternate-capability recovery,
  unsafe-BLOCKED, gap recording, pattern learning, termination bounds).
- Added adapters: opencode, github (read-only), xai, grok, openclaw,
  langgraph boundary + shared `proc.py`; registry wires config+policy,
  9 native capabilities with honest probes.
- Added `api.py` (stdlib REST) + `mcp_server.py` (stdlib MCP stdio, 15 tools).
- Extended services (delegate/plans/patterns/gaps/quality/policy/dashboard)
  + CLI (delegate/serve/mcp/gaps/learn/plans/quality/policy, dashboard status).
- Test-found bugs fixed (not hidden): parallel id re-save collision,
  GRAPH-without-probe, iterative no-early-stop, double gap records,
  argparse-safe, sqlite thread affinity → per-thread connections,
  configured-missing binaries → UNAVAILABLE.
- `pip install -e .` clean; `python -m unittest discover -s tests`:
  **171 tests, OK**.

Runtime verification transcript (fresh home /tmp/agentos_pb):
1. `init` → 9 capabilities; health: shell/filesystem/echo OK,
   github/grok/openclaw/opencode AVAILABLE, xai AUTH_REQUIRED,
   langgraph UNAVAILABLE.
2. `delegate "Inspect cli/cli repo" --operation github.repo` → COMPLETED
   via live authenticated `gh` read (cli/cli, "GitHub's official command
   line tool"); strategy DELEGATED with recorded rationale.
3. `delegate "Write three proof files" --operation fs.write --items [...]`
   → COMPLETED, PARALLEL 3 items, all three files byte-exact on disk.
4. `plans` → both plans with strategies/rationale/roles shown.
5. MCP stdio: initialize + `delegate` → COMPLETED (same core as CLI).
6. HTTP API on :18701: /health ok; /delegate → COMPLETED; 4 COMPLETED listed.
7. Flaky shell command → COMPLETED on attempt 2 (bounded recovery);
   patterns recorded (5 lessons).
8. `rm -rf /tmp/agentos_pb` objective → policy-refused, BLOCKED with
   approval next action, directory intact, exit 1.
9. New process `status` → 6 objectives, 10 executions, 93 events persisted;
   dashboard shows stuck work + pending approvals.
10. `agents` → grok/openclaw/opencode AVAILABLE, xai AUTH_REQUIRED.
11. `teleport.run` objective → BLOCKED + structured gap analysis recorded.
No live model invocations (opencode/grok/openclaw/xai executions never
called; detection + shim-tested paths only). No pushes made.

## 2026-09-19 — Task 7: staged verification pipeline + Task 5 engine wiring

- `verify.py`: `VerifyStage` (8 stages) + `VERIFY_STAGES` order +
  `DISALLOWED_VERIFY_COMMANDS` (git commit/push, rm -rf, git reset --hard,
  fail-closed, never executed) + `stage_commands(stage, project_type)` +
  `Verifier.run_verify_pipeline(objective, project, stages=None)` accepting
  a ProjectContext or plain path, aggregating per-stage evidence into one
  VerificationResult. Overall VERIFIED only when every requested stage
  passes; un-run stages record NOT_APPLICABLE, never passed.
- `engine.py` (Task 5 debt): every adapter attempt now populates
  `mode`/`authorization` (from request), `result_summary` (short human
  line), `files_changed` (flattened Task 4 keys, pre-existing excluded),
  `git_diff_summary`, `cost_status` (UNKNOWN unless measured), and the
  `normalize_result` pair in output (`result_state`/`verification_state` —
  Execution has no dedicated fields for the pair) + normalized_result
  evidence.
- `cli.py`/`services.py`: `verify` prints a per-stage section;
  new `project verify <path> [--objective ID] [--stages csv]`.
- Proven: `tests/test_verify_pipeline.py` (10 tests, incl. subprocess-spy
  policy test); full suite **240 tests, OK** at that commit; live CLI runs
  — empty dir yields STATIC passed + TEST NOT_APPLICABLE (exit 1); repo
  root yields STATIC + DIFF_REVIEW VERIFIED (50 files parsed). SIMULATED only.

## 2026-09-20 — Part A: real-agent control plane (Tasks 1–8, 171 → 251 tests)

- Task 1 (`b5cb411`): `safety.py` mode gate, SIMULATED default, engine
  enforcement; 15 tests. Task 2 (`fa9f5cc` + `3960a90`): `decompose.py`,
  children/blocked gates, unknown-depends_on raises; 8 tests. Task 3
  (`6dee306` + `541b771`): readiness ledger + `capabilities inspect
  --readiness`; 15 tests. Task 4 (`958b205`): `changes.py` read-only
  detection, canonical added/modified/deleted; 5 tests. Task 5 (`4c81ac0`):
  ResultState/VerificationState/CostPolicy + `execution show`/`executions
  list`; 11 tests. Task 6 (`39efff9`): pure bounded `task_builder.py`;
  5 tests. Task 7 (`219b595`): 8-stage verify pipeline + `project verify`,
  closes Task 5 engine-wiring debt; 10 tests. Task 8 (`4daa80d`): failure
  taxonomy + `bounded_recover` (ABSOLUTE_CAP=5); 11 tests.
- `pip install -e .` clean; `python -m unittest discover -s tests`:
  **251 tests, OK**.
- Runtime demo transcript (isolated home $AGENTOS_HOME=/tmp/tmp.V662aPIr9d,
  SIMULATED default, no LIVE, no network providers):
  1. `init` → 9 capabilities (echo/filesystem/shell OK; xai AUTH_REQUIRED;
     opencode/github/grok/openclaw/langgraph UNAVAILABLE in this PATH).
  2. `capabilities inspect --readiness` → readiness table (see above).
  3. `doctor` → all checks PASS (db integrity/writable, per-capability
     health+readiness, adapter boundaries).
  4. `objective create "Write proof file" --operation fs.write` → READY
     (obj_479ef95459ce); `run` → COMPLETED via filesystem attempt 1
     (exec_407949aae715, mode simulated, result_state PARTIAL ×
     verification_state UNVERIFIED at attempt level); `verify` →
     VERIFIED (structural); `execution show` → mode/cost UNKNOWN/summary
     populated; file byte-exact on disk (`part-a task9 demo`).
  5. `project verify $AGENTOS_HOME` → STATIC passed (0 python files),
     TEST/TYPECHECK/LINT/BUILD/RUNTIME/DIFF_REVIEW/CUSTOM NOT_APPLICABLE,
     overall FAILED (1/8) — honest, no fabrication.
- NOT run: opencode LIVE (no authorization, binary not on PATH here);
  no network provider calls (xai/grok/openclaw executions never invoked);
  no pushes; `~/.agentos` never touched (AGENTOS_HOME isolation every time).
- Paid usage: none.

## 2026-09-21 — Federation build (v0.3.0, Part B, 252 → 292 tests)

Built the federated agent fabric. All claims below were tested or run live.

- `federation.py` (new, ~1190 lines): JobEnvelope/ResultEnvelope (schema
  checked, `targetCapability` required, credential rejection via improved
  unanchored secret patterns), TaskPacket/ResultPacket (A2A 1.0 shape,
  whitespace-stripped facts), ExecutorCell, tier map T0–T6, scoring,
  `resolve_route` (pure), plan-before-premium, HS-external guard, A2A
  loopback HTTP catalog server, federation telemetry.
- `store.py`: `federation_jobs` / `federation_results` / `federation_cells`
  tables + methods; `counts()` includes them.
- `adapters/grokbot_office.py` (new): read-only bridge to the grokbot-office
  CLI; `READ_ONLY_COMMANDS` (8 ops), `FORBIDDEN_GROKBOT_COMMANDS` guard,
  honest probe (A3 repo + tsx + `validate` exit 0 → AVAILABLE on this host),
  SIMULATED/INSPECT/LIVE modes with `gate_allows` + `contains_secret`.
- `registry.py`: registered the adapter + `grokbot-office` native capability
  (kind AGENT). `models.py`: 3 federation event types.
- `services.py`: `_federation_cells` (local-tools T1 + grokbot-office T5
  external CONFIDENTIAL/BATCH + configured peers, persisted, posture
  parsed from a local `mode` result),
  `federation_executors`, `federation_status`, `federate_route` (pure),
  `federate_submit` (persist + route + conservative execute + premium
  hold), `_execute_cell` (capability→adapter dispatch; LIVE only for the
  read-only bridge), `federation_jobs/telemetry/packet`.
- `cli.py`: `agentos federate {status|executors|route|submit|jobs|packet|telemetry}`.
- Bugs found and fixed during federation test bring-up (not hidden):
  1. A2A `/agents` crashed (tuple `.intersection`) → fixed with
     `set(_FORBIDDEN_EXPOSED_OPS).intersection(...)`.
  2. Invalid job routed from CLI crashed uncaught → `federate_route` /
     `federate_submit` wrap `JobEnvelope` errors in `AgentOSError`
     (clean CLI/API error path).
  3. `_execute_cell` couldn't dispatch capability-targeted jobs from
     multi-op cells (cell.adapter ≠ desired adapter) → dispatched by the
     target capability instead. Live echo submit → COMPLETED.
- Existing tests updated for the new native capability + counts keys
  (asserted sets now include `grokbot-office`; capabilities 9 → 10;
  counts include federation tables). GROKBOT-FEDERATION tests:
  40 in `tests/test_federation.py`, all green.
- Full suite: `python -m unittest discover -s tests` → **292 OK**
  (252 baseline + 40 federation).
- Live verification (CLI, isolated home):
  1. `federate status` → executors `grokbot-office` (AVAILABLE) +
      `local-tools` (T1); grokbot-office posture reported without publishing
      account-specific usage values.
  2. `federate route --job echo …` → local-tools.
  3. `federate submit` premium grokbot job without approval →
     `premiumGated=True`, plan produced, `executed=False`. Echo job →
     COMPLETED result with provenance.
  4. LIVE grokbot-office call without authorization → refused.
  5. `federate packet` TASK/RESULT round-trip parse OK.
- NOT run: LIVE execution of any non-bridge adapter (engine `run`/`delegate`
  path unchanged, SIMULATED by default); no paid actions, no usage writes,
  no account creation, no deployment.
- Paid usage: none.

## 2026-09-22 — Resource Governor + supervisor operating model (v0.4.1, 385 → 411 tests)

Zero-cost mission. Additive; existing federation untouched except two
touchpoints. No paid execution, no Grok task execution, no accounts, no
deployment, no network side effects.

- Baseline 385 tests green *before* any change (re-run to confirm).
- Added: `resource_governor.py` (UsageSnapshot, strict validation, freshness,
  source precedence, governor weights + cost routing, four-way separation),
  `usage_ledger.py`, `task_verification.py`, `supervisor.py`.
- Store: 4 new tables (`usage_snapshots`, `usage_ledger`,
  `routing_outcomes`, `job_verifications`); `counts()`/integrity updated;
  `test_store.py` exact-set updated additively.
- `federation.py`: ExecutorCell separation fields + JobEnvelope
  verification fields (additive, safe defaults in to_dict/from_dict); fixed
  the stale posture comment (historical probe values are not live code truth).
- `models.py`: 6 new event types.
- Services + CLI: `usage`, `governor`, `supervisor` (all `--json`).
- `grokbot_office.py`: official usage-export parser (provider code stays in
  adapters).
- New tests: tests/test_resource_governor.py (26). Full suite → **411 OK**.
- Bugs found and fixed during the build (not hidden):
  1. `resolve_usage` dict input crashed (`snap.asOf` on dict) — accept
     UsageSnapshot or dict, normalize on read.
  2. `unknown_age` branch used `snap.original` (unknown attr) — now
     `winner["original"]`.
  3. `governor_weights`/`governor_route` expected the full config shape
     (`config["governor"]["weights"]`) — callers fixed to pass `self.config`.
  4. `grok_address_to_path` returned the data dir, not the file inside it —
     appends the address's filename.
  5. `usage refresh` received a UsageSnapshot object into a dict-API —
     converted via `.to_dict()`.
- Runtime smokes (isolated temp homes): usage import→resolve→ledger/summary;
  governor route (echo→local-tools at cost 5.25) → verify VERIFIED with
  sandbox+reporter evidence → outcomes summary learned (runs=1 verified=1);
  supervisor status (chain, per-layer statuses, separation MAINTAINED);
  usage refresh from a local export file (data_dir, no network).
- Docs: `RESOURCE_GOVERNOR.md`, `SUPERVISOR_MODEL.md` (new) + CURRENT_STATE/
  BUILD_MAP updated. Statuses use IMPLEMENTED/VERIFIED/UNCONFIGURED/
  DEFERRED exactly; `verified_live` stays `False` honestly.
- Paid usage: none. GrokBot tasks executed: none. Supervisor/ChiefOfStaff:
  reported UNCONFIGURED/DEFERRED, not claimed live.

## 2026-09-22 — Federated fabric build (v0.4.0, 328 → 385 tests)

Zero-cost mission. No paid execution, no Grok task execution, no
accounts, no deployment, no network side effects.

- Baseline was red (2 env-sensitive federation tests assumed
  grokbot-office routable; this shell probes it UNAVAILABLE). Fixed
  honestly: deterministic synthetic premium cell + unavailable-Grok
  honesty branch. No availability fabricated.
- Added: `a2a_v1.py` (+ peer v1 endpoints), `mcp_discovery.py` (+
  `mcp_candidates` table), `capability_graph.py`, `scarcity.py`,
  `failover.py` (failover + pipelines), `benchmarks.py` (+
  `benchmark_runs` table), `durable.py`, `bridges.py`; extended
  `ExecutorCell` (multi-cell fields, credential rejection),
  `policies.py` (approval classes, trust levels), services, CLI, API.
- New tests: test_a2a_v1 (11), test_mcp_lifecycle (7), test_fabric (38).
- Full suite: `python -m unittest discover -s tests` → 385 OK.
- Runtime smokes (isolated temp homes): init 13 caps; honest UNKNOWN
  probes; local-tools routing; OFFLINE gates local-tools INELIGIBLE;
  simulated grok-research cell loads UNKNOWN uncontacted; registry
  UNCONFIGURED; benchmark/usage/durable/route-explain/a2a-card live.
- Docs: 8 new (MCP_DISCOVERY/MCP_TRUST/MULTI_CELL/RESET_AWARE_ROUTING/
  DURABLE_EXECUTION/BENCHMARKS/ADAPTIVE_ROUTING/APPROVALS) + updates to
  FEDERATION/A2A/ROUTING_ECONOMICS/SECURITY_BOUNDARIES/CURRENT_STATE/
  BUILD_MAP. Statuses use VERIFIED/IMPLEMENTED/SIMULATED/ADAPTER_ONLY/
  UNCONFIGURED/UNAVAILABLE/DEFERRED exactly.
- Paid usage: none. GrokBot tasks executed: none.

## 2026-09-23 — Resource Governor + supervisor operating model (v0.4.1:, +26 tests)

- Governor consumes transport-neutral validated usage snapshots (import seam
  accepts grok://primary/runtime/usage-live.json-shaped local files; source
  classes OFFICIAL_UI/OFFICIAL_API/AUDITED_UNOFFICIAL/MANUAL/STALE_CACHE/UNKNOWN;
  UNKNOWN never coerced to 0; stale labeled STALE_CACHE, never "live").
- Supervisor model + four-way separation (identity/runtime/model/computer)
  implemented additively; GrokBots = supervisor-grade roles (persistent
  context, queues, browser/session ownership, monitoring, escalation,
  synthesis), never implied every-worker executors.
- Verification-first task model: WORK + EVIDENCE + VERIFICATION with
  verifier/worker separation where meaningful.
- Suite: 411 tests OK (385 + 26). Zero paid usage; zero GrokBot activation
  during build; grokbot-office registry reconciled to 134 roles (133/134
  registered, NOT activated).

## 2026-09-23 — Governor reference-clock hardening (424 tests OK)

- `resolve_usage` now re-parses dict snapshots with the same caller-supplied
  `now` (resource_governor.py), so freshness/STALE classification is stable
  instead of silently defaulting to the real wall clock.
- Services gained a pinned reference clock: `AgentOS._usage_now()` uses
  `config["usage"]["now"]` when set, else wall-clock UTC; threaded through
  `usage_import` and `usage_resolve` so integration fixtures stay LIVE.
- Fixed latent `usage_resolve` honesty-note NameError (`warnings` was
  undefined; now `resolved.warnings`).
- Test fixture pinned to `FIXED_NOW` (parse + integration clock). Full
  suite: 424 tests OK (411 + 13 ecosystem/workflow). Zero paid usage.

## 2026-09-23 — LOW-USAGE EFFICIENCY KERNEL + CLI RESTORE (456 tests OK)

Recovered and shipped the shared IntelligenceCache + efficiency kernel,
and repaired cli.py (recorded verbatim — see CURRENT_STATE.md):

- `src/agentos/cli.py` was missing its post-mcp tail (cmd_gaps…cmd_execution).
  Rebuilt from `git show HEAD:src/agentos/cli.py` (lost subtree) into the
  intact file, then re-attached the working-tree-only kernel command groups
  (`efficiency` 8 subcommands, `ecosystem`, `workflow`) as parsers, dispatch
  and command definitions. Verified: all 35 dispatched `cmd_*` resolve; full
  lifecycle CLI runs green (discover→inspect→test→approve, register→state→
  use, triage/team/fingerprint/delta/cache-get/cache-put/invalidate/stats).
- `src/agentos/intelligence_cache.py` (reconstructed, compiles, 708 lines):
  honest hit/miss contract, SecurityScope gate, secret gate (now catches
  `BEGIN RSA PRIVATE KEY` PEM blocks), narrow dependency invalidation,
  `mark_verified`, durable counters, `stats()`. Fixed real bugs found by
  smoke/test: `invalidate()` re-wrapping `CacheEntry` through `from_dict`;
  `to_dict` dropping `project`; `stats` missing `invalidations`; service/CLI
  signature drift (exact_cache_hit, requested_permissions, found_useful,
  teamPlan shape, workflow/ecosystem candidate unwrap).
- Healthiest ground-truth twin was read through a separate local Python 3.14
  environment; its `SECRET_PATTERNS` and `to_dict` field set were used to close
  the PEM and project gaps.
- New tests: `tests/test_efficiency_kernel.py` (23) covering cache contract,
  scopes, secrets, staleness, narrow invalidation, mark_verified, services
  paths, triage/team/delta. Suite: 456 tests OK (433 + 23). Zero paid usage.

## 2026-09-24 — Learning execution lifecycle release (v0.5.0, 674 tests OK)

- Release baseline for this work: 639 tests; 626 passed, 2 failed, 11 errored,
  0 skipped. Failures were fixed at their source rather than excluded.
- Integrated the existing efficiency/learning components into the real
  `Engine.run` lifecycle: redacted trace, exact cache, deterministic/state fast
  paths, workflow reuse, verification outcomes, coach/lessons, executor metrics,
  learned routing, fleet incidents, routines, and bounded escalation.
- Hardened persistence and matching: workflow/skill updates round-trip every
  field; workflow compile reads verified plan history; observed/verified/
  certified workflows are eligible only when real preconditions pass.
- Hardened backup restore: allowlisted tables/columns, safe tar extraction,
  row-count and secret validation, target-schema checks, and rollback before
  mutation. Added inspect/verify/dry-run runtime evidence.
- Corrected measured telemetry: failures and actual retries are separate;
  one-attempt failures report zero retries. Added JSON `--params` to CLI run.
- Fixed two test HTTP servers missing `server_close()`; the final full run has
  no socket ResourceWarnings.
- Final isolated discovery: **674 tests, 674 passed, 0 failures, 0 errors,
  0 skips** in 25.759s.
- Final deterministic benchmark: 11 tasks; 2 cache hits; 1 workflow reuse;
  6 workers; 10 verification calls; 0 retries/model/premium/Grok/human
  escalations; 2 duplicates prevented; 53 ms elapsed.
- Real CLI smoke passed: init, run/explain, deterministic run, efficiency stats,
  efficiency benchmark, fleet health, routine list/run, backup create/inspect/
  verify, and restore dry-run.
- Editable install rebuilt as `agentos-0.5.0`; `agentos --version` returned
  `agentos 0.5.0`.
- Bytecode audit: 69 first-party `.py` sources, 69 current CPython 3.12 caches,
  29 ignored cross-version CPython 3.14 caches, 0 source-less `.pyc`. Source is
  authoritative. Source and test files are included in the release candidate.
- Paid provider calls: 0. GrokBot tasks: 0. Deployment/publication/push: 0.
- Independent review immediately afterward reproduced release-blocking defects in
  secret redaction/backups, cache/policy behavior, workflow truncation, and reset
  state. The v0.5.0 release gate was therefore **BLOCKED** at that checkpoint.

## 2026-09-24 — Post-fix release verification (v0.5.0, 710 tests OK)

- Closed the concrete local review findings: nested/raw-field secret redaction,
  persisted malformed-JSON fail-closed behavior, extra archive-member rejection,
  source-path minimization, target-aware backup dry-run, and learned-routing
  decision/explanation persistence.
- Full discovery completed in isolated bounded batches: **710 tests, 710 passed,
  0 failures, 0 errors, 0 skips**.
- Fresh CLI smoke: init, echo delegate, deterministic calculation, fleet health,
  routine list, backup create/inspect, backup restore dry-run, and doctor all
  passed; doctor reported 16 checks.
- Fresh benchmark: 11 tasks; 2 cache hits; 1 workflow reuse; 6 workers; 10
  verification calls; 0 retries/model/premium/Grok/human escalations; 2 duplicate
  requests prevented; 143 ms elapsed.
- Editable CLI version: `agentos 0.5.0`. Bytecode audit: 69 first-party sources,
  69 current CPython 3.12 caches, 29 cross-version CPython 3.14 caches, 0
  source-less `.pyc`.
- Local release gate: **PASS**. Paid provider calls, GrokBot tasks, deployment,
  publication, and push: 0. Publication preparation remains separate from
  local verification.
