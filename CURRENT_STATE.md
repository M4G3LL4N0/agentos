# AgentOS Current State

Living document — reflects reality as of 2026-10-20 (Phase 3 production
hardening; 872 tests green, local + server).
If this file and behaviour disagree, behaviour wins; fix this file.

## Status: genuinely useful universal Agent Engine (local)

`agentos` installs (`pip install -e .`) and works through **CLI, HTTP API
and MCP stdio** against one shared core. 872 automated tests pass. Runtime
verified end-to-end with real detections, real executions, a deliberate
failure with recovery, a policy refusal, and restart persistence.

## Phase 3: production hardening (2026-10-20, 872 tests)

- **Load incident (root-caused + fixed):** `noaerth-disk-watch` wrote its
  6h re-fire guard *after* launching `noaerth-storage-janitor heavy`, so
  every `*/5` cron tick stacked a new janitor while disk ≥75% — 277
  janitors + 123 disk-watches + 120 stuck `find node_modules` walks drove
  load to ~135 on 6 cores. Fixed: state written before launch + `flock -n`
  single-instance janitor; cron env lacks `USER` (janitor line-58 unbound
  variable killed the `/tmp` cleanup subshell every run) — now
  `${USER:-$(id -un)}`. Verified: 3+ cron ticks post-fix with zero
  respawn, load 135 → 4.18, zombies 0.
- **JanitorOS access repair (service identity):** byte-identical copy of
  the JanitorOS binary at `/usr/local/bin/janitor` (root:root 755) +
  `/home/agentos` (750) for the knowledge store — no broad `/home/portfolio`
  access. Full admission matrix green as `agentos` and under systemd:
  granted, refused (rc 75), threshold-exceeded, unavailable,
  permission-denied, malformed, invalid (negative/bool/non-int rejected at
  the adapter, fail closed).
- **/tmp inventory (dry-run, nothing deleted):** 10,078 entries / 508MB
  categorized in `/root/phase3/tmp-manifest.tsv` — 394 CONFIRMED_DISPOSABLE
  test homes (92MB, our test windows), 3 POSSIBLY_DISPOSABLE, 9,664 UNKNOWN
  (415MB — 9,485 `paios*` + 154 `mee*` other-service workdirs, toolchains),
  17 ACTIVE_PROTECTED (live fds + systemd-private + session).
- **State separation (implemented + rollback-drilled):** production state
  now lives in `/var/lib/agentos` (db, loop_state, governor_state),
  config in `/etc/agentos` (`AGENTOS_CONFIG_DIR`, 12 blocked-pattern
  policy entries), file logs in `/var/log/agentos`; code stays in
  `/srv/noaerth/agentos` (incl. the AutonomousOS exchange `state/` dir).
  Migration used the SQLite backup API; old files kept as rollback
  artifacts; drill proven both directions (old layout live → new layout
  live, counts 2/1/42 both ways).
- **Backup + restore (both layers, isolated-restore tested):**
  `/usr/local/bin/noaerth-agentos-backup` daily 03:17 root cron → DR
  full-file tar (SQLite backup API) + portable secrets-excluded export,
  ledger + 14-archive retention. Restore drill: DR → isolated dir
  (integrity ok, counts 2/1/42, policy 12), portable → fresh db, full
  CLI usable against restored env; guards refuse production target and
  existing-db overwrite.
- **Safe deployment (`deploy/safe-deploy.sh`):** preflight (local suite,
  remote health/disk/JanitorOS storage admission) → release snapshot
  (code tar + SQLite-backup db) → sync with `--delete` restricted to
  `src/`+`tests/` and state excluded → install → health gate → ERR-trap
  rollback. 11 state-deletion regression tests. Pipeline proven live
  (DEPLOY_OK ×4).
- **Host resource governor (new, additive):** `host_governor.py` —
  HEALTHY / PRESSED / CRITICAL / RECOVERING from measured disk/load/mem/
  swap only (unmeasured signals stay None, never invented), entry vs exit
  thresholds + dwell-time hysteresis, CRITICAL exits only via
  RECOVERING, state in `governor_state.json` (bounded history). Wired at
  loop DELEGATE (refuses before janitor), services `host_status` /
  `host_admission`, CLI `governor host` / `governor host-admission`.
  Live: band PRESSED (disk 17.04% free, load 1.52–2.44/cpu); simulated
  CRITICAL denied admission and the loop DELEGATE refused. 18 tests.
- **systemd hardening (probe-verified):** `StartLimit*` moved to `[Unit]`
  (journal warning gone); `NoNewPrivileges`, `PrivateTmp`,
  `ProtectSystem=strict`, `ProtectHome=read-only`, `ReadWritePaths`
  (`/var/lib/agentos /etc/agentos /home/agentos /var/log/agentos`),
  `ProtectKernel*`, `RestrictSUIDSGID`, `LockPersonality`,
  `CapabilityBoundingSet=`, `RestrictAddressFamilies`. 9-point sandbox
  probe (state/config/home/tmp writes, code read, janitor admit, API
  reach, db read) all green before applying. Syscall filters and
  `MemoryDenyWriteExecute` intentionally NOT applied (worker model spawns
  opencode/node children) — documented in the unit.
- **Operational verification (all green):** 7/7 API endpoints 200;
  restart persistence (counts + loop_state); one full autonomous-loop
  cycle under the separated state (9/9 stages, `obj_76a9ee640521`
  COMPLETED+VERIFIED, loop_state written to `/var/lib` while the old
  rollback copy stayed byte-identical); fresh backup + both restore
  layers round-tripped; pressure simulation via injected thresholds (no
  prod config mutation). 872 tests OK locally (199s) and on server (135s).
- Paid provider calls: 0. Pushes: 0. Deletions of unverified files: 0.

## Phase 2: live tower + autonomous operations (2026-10-19, 830 tests)

- **Live tower** (`tower.py`): curses TUI with SYSTEM / OBJECTIVES / HIVE /
  PROVIDERS / RESOURCES / VERIFICATION / ACTIVITY sections, detail views, and
  interactive controls. Cancel requires explicit `y` confirmation and fails
  closed without UI; no `shell=True` in tower source. CLI: `agentos tower`.
- **New CLI surfaces**: `agentos workers`, `agentos providers`, `agentos logs`.
- **Bounded autonomous loop** (`autonomous_loop.py`): ten explicit stages
  (INSPECT → PLAN → DECOMPOSE → ROUTE → DELEGATE → EXECUTE → VERIFY → REVIEW →
  REPAIR → COMPLETE) with hard cycle/time/worker/cost limits, pause/resume/
  interrupt, crash recovery that marks a RUNNING state INTERRUPTED on restart,
  and free-only provider routing unless paid authorization is explicitly
  recorded. State persists in `loop_state.json` / `loop_limits.json`.
- **Hive intelligence** (`hive_intelligence.py`): evidence-based assignment
  scoring (capability fit, historical success, latency, cost rank, usage
  limits, verification burden, project priority) with free-first tie-breaking,
  measurable-throughput-only parallelism, and balanced chunking that avoids
  excessive prompt splitting.
- **Project contracts** (`project_contract.py`): fail-closed enforcement —
  no active contract, expired contract, out-of-scope operation, paid operation
  under `free_only`, cost-ceiling breach, or missing required evidence all
  block; violations are recorded and flip the contract to VIOLATED.
- **Efficiency measurements** (`measurements.py`): real measured startup,
  import, per-run seconds, DB/home bytes, objective/event counts.
- **Full suite: 830 tests, 0 failures, 0 errors.** Phase 2 security suite
  (`test_phase2_security.py`) locks in fail-closed cancel, free-only defaults,
  bounded state, and no-secret key files.
- Server: Phase 1 deployment (`agentos.service`, `noaerth agentos` wrapper,
  `http://127.0.0.1:8765/health`) verified with the Phase 2 live demo —
  server-side suite 837 OK, loop demo objective COMPLETED+VERIFIED, tower
  rendered a live PTY frame, restart persistence proven. One incident: a
  first sync with `rsync --delete` destroyed Phase 1 server state
  (`agentos.db`, `state/`, `logs/`; record `obj_91c25855be10` unrecoverable,
  no backups existed); service rebuilt fresh after ownership fix, and the
  sync script now forbids `--delete` (see EXECUTION_LOG).

## Learning execution release (LOCAL GATE PASS, v0.5.0, 710 tests)

- `Engine.run` now owns the real lifecycle: job trace → exact cache → deterministic
  or state fast path → eligible workflow reuse → capability-aware plan → actual
  worker execution → verification → cache/coach/workflow/executor learning.
- Exact cache, deterministic calculation, and state lookup complete with zero
  workers and zero model/Grok calls. Reused workflows rebuild bounded execution
  plans and retain hard capability/approval preconditions.
- Advanced verification records real outcomes. Meaningful success/failure runs
  trigger the coach; lessons and workflow maturity remain measured state, never
  assumptions. Router learned modifiers rank only after hard usability gates.
- Fleet signals cluster repeated failures into one incident, suppress redundant
  retries while open, and resolve explicitly. Routines cheaply detect no-change
  and return `NO_ACTION`; changed paths invoke existing workflows.
- Backups exclude secret tables, scan archive contents, reject unsafe members,
  unlisted files/tables, row-count mismatches, secret payloads, and unknown
  columns before restore mutation. Manifests omit private source paths, and
  `restore --dry-run` reports target table/column incompatibility. `inspect`,
  `verify`, and `restore --dry-run` are real CLI paths.
- CLI `run` accepts objective text, `--operation`, JSON `--params`, and
  `--explain`. `efficiency benchmark` runs 11 deterministic local task classes.
  Final measured run: 2 cache hits, 1 workflow reuse, 6 workers, 10 verification
  calls, 0 retries, 0 model calls, 0 premium calls, 0 Grok calls, 0 human
  escalations, 143 ms elapsed.
- Full post-fix discovery across bounded isolated batches: **710 tests, 0 failures,
  0 errors, 0 skips**. Editable package and CLI report **0.5.0**. Fresh CLI
  smoke, 11-task benchmark, backup inspect/dry-run, doctor, source, and bytecode
  audits are green. The local release gate is **PASS**. The dedicated public
  remote and CI are verified in `PUBLICATION_AUDIT.md`; the v0.5.0 release tag
  is created separately from the final verified commit. No paid provider,
  GrokBot, deployment, or publication-side execution was used.

## Resource Governor + supervisor operating model (IMPLEMENTED + VERIFIED, 2026-09-22, +26 tests)

Full detail in `RESOURCE_GOVERNOR.md`, `SUPERVISOR_MODEL.md`. Summary:

- Usage snapshot model (`resource_governor.py`): `UsageSource` precedence
  (official → audited-unofficial → manual → stale → unknown), strict
  percentage validation, freshness (LIVE/STALE/UNKNOWN), **no fabricated
  reset dates**, unknown never assumed 0/unlimited; `parse_snapshot`,
  `resolve_usage`, `read_snapshot_file` (local exports only, no network).
- Usage ledger (`usage_ledger.py`): durable `UsageEntry` records du jour
  (RECORD-only; summaries sum only what was recorded); store +
  `usage ledger` CLI.
- Governor routing (`governor_route`): federation safety gates preserved via
  `score_executor_cell`, eligible cells cost-ranked by quotaScarcity/money/
  latency/humanAttention/failureProbability/verificationCost/
  contextTransfer/computerAvailability/sessionAffinity; explicit
  `governor.weights` (negatives rejected); failure rate learned only from
  recorded outcomes; every call persists a `routing_outcome`.
- Four-way separation (`separation_profile`): identity never implies model
  or computer; explicit `identityPersistent`/`preferredRuntimes`/
  `preferredModels`/`computerAffinity`/`requiredCapabilities`/
  `supervisor`/`workerPolicy`/`verificationPolicy` on cells.
- Verification-first task model (`task_verification.py`): UNVERIFIED →
  REQUIRED → PENDING → VERIFIED/FAILED transitions (illegal ones refused);
  verifier/worker separation without waste — deterministic evidence never
  triggers a second model call.
- Supervisor operating model (`supervisor.py`): canonical chain
  USER → PAIOS → AgentOS → ChiefOfStaff → domain supervisor → elastic worker
  fabric → verifier → supervisor → ChiefOfStaff → user; honest per-layer
  status (ACTIVE/IMPLEMENTED/UNCONFIGURED/DEFERRED); GrokBot scoped to
  context/queues/supervision/synthesis, not all worker tasks; `verified_live`
  is `False` honestly until a live loop exists.
- Storage: 4 new tables (`usage_snapshots`, `usage_ledger`,
  `routing_outcomes`, `job_verifications`) + counts/integrity; fixes the
  stale federation comment in `federation.py` that could be misread as live
  code; public documentation omits account-specific posture values.
- New CLI: `usage status|snapshots|resolve|import|refresh|add|ledger`,
  `governor weights|route|outcomes|verify`, `supervisor status`; all
  `--json`-capable. `usage refresh` reads an operator-configured local file
  (`usage.data_dir`), never the network.
- Suite: `python -m unittest discover -s tests` → **411 OK** (385 + 26 new).
  No paid usage, no Grok task execution, no network side effects.

## Federated fabric build (IMPLEMENTED + VERIFIED, 2026-09-22, +57 tests)

Full detail in `FEDERATION.md`, `A2A.md`, `MCP_DISCOVERY.md`,
`MCP_TRUST.md`, `MULTI_CELL.md`, `ROUTING_ECONOMICS.md`,
`RESET_AWARE_ROUTING.md`, `DURABLE_EXECUTION.md`, `BENCHMARKS.md`,
`ADAPTIVE_ROUTING.md`, `APPROVALS.md`, `SECURITY_BOUNDARIES.md`.
Summary:

- A2A v1.0 remote interop (`a2a_v1.py`): well-known card discovery,
  strict v1 parsing (0.x rejected), version negotiation, JSON-RPC
  task lifecycle, typed errors, secret boundary; loopback peer serves
  v1; remote peers are UNTRUSTED HS-capped cells; AgentOS card is
  echo-only, serving OFF by default.
- MCP registry discovery + certification lifecycle (DISCOVERED →
  INSPECTED → TESTING → TESTED → APPROVED, REJECTED/REVOKED);
  static-only certification stated honestly; approval registers
  unavailable metadata (transport DEFERRED); `mcp` CLI full lifecycle.
- Multi-cell federation: config-driven cells, never probed/contacted;
  session specialization bonus; credential rejection at construction.
- Scarcity/reset economics (PRESERVE/NORMAL/HARVEST, all-guards
  harvest), explainable routing (`federate route --explain`,
  `route --job`), explicit calibration, bounded failover + pipelines,
  canonical approval classes, trust levels + gate.
- Benchmarks (local-worker A/B/C measured; rest SKIPPED honestly),
  native durable backend + Temporal DEFERRED with reason, OrgOS
  snapshot bridge (UNCONFIGURED without one), PAIOS snapshot surface.
- New CLI: `route`, `capability find|explain`, `a2a peers|card|test|
  register`, `durable status`, `benchmark run|report`, `usage status`,
  `federate doctor|cells|health`, `federate route --explain`.
- Suite: `python -m unittest discover -s tests` → **385 OK**. No paid
  execution, no Grok task execution, no network side effects.

## Federation build (IMPLEMENTED + VERIFIED, 2026-09-21, 40 tests)

Full detail in `FEDERATION.md`, `A2A.md`, `EXECUTOR_CELLS.md`,
`ROUTING_ECONOMICS.md`, `GROKBOT_PROVIDER.md`, `SECURITY_BOUNDARIES.md`.
Summary:

- Envelope model (`JobEnvelope`/`ResultEnvelope`): schema-checked,
  `targetCapability` required, credentials rejected on ingest; A2A-1.0-shaped
  TASK/RESULT packets round-trip.
- Executor cells T0–T6 (`local-tools` T1, `grokbot-office` T5 external
  CONFIDENTIAL/BATCH), persisted to `federation_cells`, re-hydrated on
  restart.
- Pure routing (`resolve_route`) with data-class, latency, HS-external,
  health and posture gates; lower tiers win ties; reasons recorded.
- Plan-before-premium: T5 jobs pause with a plan/cost/approval object,
  never auto-executed without approval evidence.
- Read-only `grokbot-office` bridge (8 allowlisted commands; forbidden set
  hard-refused). Honest probe on this host → AVAILABLE; SIMULATED/INSPECT
  verified; LIVE refused without authorization.
- A2A loopback agent catalog: healthy cells only, subject/federation
  operations hidden, HS cells never advertised as external.
- Services + CLI (`agentos federate …`) + store tables; live CLI runs
  verified (status/route/submit/packet). LIVE execution restricted to the
  read-only bridge; everything else SIMULATED/INSPECT.
- Suite: `python -m unittest discover -s tests` → **292 OK**.

## IMPLEMENTED + VERIFIED (tests and/or live runs prove it)

- Objective lifecycle with engine-driven states; hierarchical parents supported.
- Capability-aware router: DIRECT / SEQUENTIAL / PARALLEL / DELEGATED /
  ITERATIVE / RESEARCH_THEN_EXECUTE / BUILD_THEN_VERIFY / REVIEW_THEN_REVISE /
  GRAPH (GRAPH falls back to SEQUENTIAL with rationale while langgraph is
  unavailable). Every plan records strategy, steps, roles and rationale.
- Temporary roles (researcher/architect/builder/tester/evaluator/red_team/
  analyst) as plan assignments; topology contracts on completion.
- Parallel execution with write-conflict detection (conflicts serialize),
  thread-local sqlite connections, merged results.
- Verification tiers: EXECUTION RESULT vs VERIFICATION RESULT vs QUALITY
  EVALUATION (heuristic grades pass/needs_review/fail); independent
  verification by a second capability (BUILD_THEN_VERIFY).
- Recovery strategies: retry (bounded), alternate capability, revise on
  quality findings, replan on recover; failures classified + preserved;
  retry-then-success marks failures recovered. No infinite loops anywhere
  (max attempts, max steps=10, max revisions=2, max iterations=5, workers=4).
- Learning layer: patterns recorded per run (class, capability, strategy,
  verification, failure mode, recovery, duration, lesson); router scores
  capabilities by health + history + cost/latency. Patterns are statistics,
  never promoted to facts (a failure never disables a capability).
- Capability-gap analysis auto-recorded on BLOCKED (`agentos gaps`).
- Execution policies: blocked command patterns, destructive gating,
  approval classes (grant/revoke), write roots, secret redaction in
  stderr/commands/errors. Proven live: `rm -rf` refused, objective BLOCKED,
  directory intact.
- Hardened shell adapter: command/argv forms, cwd, env (+scrubbed logging),
  timeout-kill, duration_ms, policy gate. Filesystem writes policy-gated.
- Health taxonomy: AVAILABLE/OK/DEGRADED/UNKNOWN/DOWN/UNAVAILABLE/
  AUTH_REQUIRED/MISCONFIGURED/DISABLED; router avoids unusable capabilities.
- Real adapters, all exercised by tests (shims + local HTTP server):
  shell, filesystem, echo, **opencode** (`run --format json`, real flags),
  **github** (gh: repo/branch/file/commit/issue/pr reads; no writes exist),
  **xai** (official OpenAI-compatible endpoint, explicit model required,
  tools passthrough, 401/429/5xx handled), **grok** (`-p` single-turn),
  **openclaw** (`agent --message --json`, `--deliver` never set),
  **langgraph** (boundary reporting UNAVAILABLE).
- Live detections (this machine): opencode AVAILABLE, github AVAILABLE
  (gh authenticated), grok AVAILABLE, openclaw AVAILABLE, xai AUTH_REQUIRED
  (no key), langgraph UNAVAILABLE (not installed). Live `gh` read of
  `cli/cli` COMPLETED.
- HTTP API (stdlib): health/status/dashboard/capabilities/agents/objectives/
  inspect/events/plans/patterns/gaps/quality + create/run/verify/recover/
  cancel/delegate. Live smoke: delegate over HTTP COMPLETED.
- MCP stdio server (stdlib JSON-RPC): initialize/ping/tools-list/15 tools
  incl. delegate/state/gaps/patterns. Live smoke: delegate over MCP COMPLETED.
- CLI dashboard status (active/stuck/strategy/approvals/recent), delegate,
  serve, mcp, gaps, learn, plans, quality, policy commands; `--json`
  preserves failure exit codes.
- Thread-safe store (per-thread connections, WAL) — required by API +
  parallel execution; found by failing tests, fixed for real.

## Part A additions (IMPLEMENTED + RUNTIME VERIFIED, 251 tests, 2026-09-20)

Evidence: `python -m unittest discover -s tests` → 251 OK; bounded SIMULATED
demo runs 2026-09-20 (see EXECUTION_LOG.md Part A entry). No live model
invocations anywhere in Part A.

- Mode-gated execution (Task 1, `b5cb411`): IMPLEMENTED. `safety.py`
  `gate_allows`/`require_execution_authority`; engine enforces before
  dispatch; SIMULATED is the default mode; LIVE needs explicit
  authorization. RUNTIME VERIFIED via tests (15) + demo (`mode: simulated`
  on execution record). Known backlog: direct `adapter.execute()` calls
  bypassing the engine are ungated for shell/filesystem/github/langgraph.
- Objective decomposition (Task 2, `fa9f5cc` + fix `3960a90`): IMPLEMENTED.
  `decompose.py` `create_child_objectives`/`objective_is_unblocked`,
  `services.children`, engine BLOCKED gate with `blocked_reason`. Unknown
  `depends_on` ids now raise (no silent drop). RUNTIME VERIFIED via tests (7+1).
- Capability readiness ledger (Task 3, `6dee306` + fix `541b771`):
  IMPLEMENTED. 10-state `ReadinessState`, `readiness()`/`evolve_readiness()`
  (word-boundary signal matching), `record_execution` counters on
  Capability, `capabilities inspect [--readiness] [id]` AVAILABLE,
  readiness in `doctor`. READY is never reached from detection alone
  (asserted in tests). RUNTIME VERIFIED via tests (14+1) + live CLI below.
- Filesystem + git change detection (Task 4, `958b205`): IMPLEMENTED.
  `changes.py` (`capture_fs_state`/`diff_fs`/`attribute_changes`,
  canonical keys `added`/`modified`/`deleted`); git access is read-only
  (`ls-files`, `status --short`; never commit/push). Engine attaches
  `files_changed` + `git_diff_summary` output keys + `change_footprint`
  evidence (no-op without `project_path`). RUNTIME VERIFIED via tests (5+).
- Normalized result taxonomy (Task 5, `4c81ac0`): IMPLEMENTED.
  `ResultState` (7) / `VerificationState` (4) / `CostPolicy` (5),
  `normalize_result()` in `models.py`; Execution gains 8 defaulted fields
  (old DBs migrate); `execution show <id>` / `executions [--objective
  --limit]` AVAILABLE. Engine population wired by Task 7 (mode,
  authorization, result_summary, flattened `files_changed`,
  `git_diff_summary`, `result_state`/`verification_state` output pair +
  `normalized_result` evidence). Costs UNKNOWN unless measured.
  RUNTIME VERIFIED via tests (11) + demo execution record below.
- Task builder (Task 6, `39efff9`): IMPLEMENTED. Pure `task_builder.py`
  `build_task()` (8 `== HEADER ==` sections, verbatim instruction line,
  bounded <8000 chars with `[truncated]`, deterministic, no I/O).
  RUNTIME VERIFIED via tests (5).
- Staged verification pipeline (Task 7, `219b595`): IMPLEMENTED.
  `VerifyStage` (8 stages) + `VERIFY_STAGES` order +
  `DISALLOWED_VERIFY_COMMANDS` (fail-closed, never executed) +
  `run_verify_pipeline()` (overall VERIFIED only when every requested
  stage passes; un-run stages NOT_APPLICABLE, never passed). `verify`
  prints a per-stage section; `project verify <path> [--objective ID]
  [--stages csv]` AVAILABLE. RUNTIME VERIFIED via tests (10) + live
  `project verify` demo below (SIMULATED only).
- Failure taxonomy + bounded recovery (Task 8, `4daa80d`): IMPLEMENTED.
  8 new `classify_failure` classes (POLICY_BLOCK first, BINARY_MISSING
  before not_found, legacy order intact); `bounded_recover()` with
  `ABSOLUTE_CAP = 5`, computed-not-slept backoff; AUTH_REQUIRED →
  BLOCKED (1 attempt, zero executions, generic reason, no secret echo);
  POLICY_BLOCK → reject; RATE_LIMIT bounded; TEST/BUILD/TYPECHECK_FAILURE
  → correct; CONFLICT → report (no auto-merge). RUNTIME VERIFIED via
  tests (11).

## Detection honesty (this shell, 2026-09-20)

- opencode binary DETECTED in a user-configured location on one prior shell
  (not on the isolated test PATH); probes in that shell report UNAVAILABLE.
- Probes in this shell report: shell/filesystem/echo AVAILABLE (OK),
  xai AUTH_REQUIRED, opencode/github/grok/openclaw/langgraph UNAVAILABLE
  (`gh`/`grok`/`openclaw` not on PATH here). Health is environment/PATH
  dependent — the Part B AVAILABLE readings came from a different PATH.
- opencode AVAILABLE only where probed. **opencode LIVE never executed
  (no authorization)** — no model spend authorized in Part A, none occurred.

## AVAILABLE BUT UNCONFIGURED

- xai: code path real, needs `XAI_API_KEY` + explicit model per call.
- opencode/grok/openclaw execution: real code paths, invoked only via
  explicit objectives (never ran at runtime: no model spend authorized).
- langgraph: needs `pip install langgraph` + provider; adapter + GRAPH
  fallback ready.
- grokbot-office: bridge AVAILABLE + read-only verified (routing/telemetry);
  LIVE execution requires explicit authorization and is refused otherwise.

## DESIGNED (extension points exist, no implementation)

- MCP *client* adapter (calling external MCP servers), OrgOS consumption
  (API/MCP/CLI surface ready), schema migrations, execution sandboxing,
  API auth (currently loopback-only, unauthenticated).

## FUTURE

Per BUILD_MAP.md: federation layer done (Part B). Next is a daily-use
configured capability, a first external write-capable adapter behind
approvals, MCP client, approvals UX, sandboxing, and (Part 2) deeper
federation economics/negotiation across remote cells.

## Known limitations

- Heuristic operation resolution without explicit operation (safe default).
- Quality evaluator is heuristic (no model judge wired; evaluator slot exists).
- Backoff computed, not slept. API has no auth — bind loopback, trusted networks only.
- No live model calls were made during verification (cost/latency unmeasured).
- Learned routing returns no executor when no measured candidate profile exists; the hard-gated router remains authoritative rather than inventing a score.

## Efficiency triage short-circuit (2026-09-23)

- `efficiency.py` import fixed (`intent_bag as intent_shingles`).
- Single-token hints use word boundaries (`compute` no longer matches `computer`).
- `tests/test_efficiency_triage.py` — 9/9 unittest OK (cache/workflow/deterministic/cheap/GrokBot/team).
- Status: **CODE_VERIFIED** for rules kernel; CoS front-door wiring still **PROPOSAL**.

## Low-usage efficiency kernel (IMPLEMENTED + VERIFIED, 2026-09-23, +23 tests → 456 OK)

Full detail in `EFFICIENCY_KERNEL.md`, `INTELLIGENCE_CACHE.md`, `TRIAGE.md`,
`SMALLEST_CAPABLE_TEAM.md`, `DELTA_CONTEXT.md`, `CONTEXT_ECONOMICS.md`,
`GROKBOT_USAGE_POLICY.md`. Summary:

- Shared `IntelligenceCache` (L0 exact → L4 semantic bag → L5 documented-only →
  L6 fresh reasoning) over the durable `Store`, provider-neutral, stdlib-only.
  Honest contract: hits carry `intcache://` provenance and `modelCalls=0`;
  every miss carries a reason (`no entry stored`, `security scope not
  dominated`, `entry STALE (fresh reasoning required)`, `cache disabled`).
- Security: `SecurityScope.dominates` gates every lookup; HIGHLY_SENSITIVE
  entries and secret-bearing payloads (PEM private keys incl.
  `BEGIN RSA PRIVATE KEY`, tokens, bearer, recovery codes, api keys) raise
  `ValueError` at build/put. Narrow dependency invalidation only — no
  project-wide blast radius.
- Freshness: `effective_status` = FRESH while `lastVerifiedAt + ttl` is in the
  future, else STALE/UNVERIFIED; stale is a miss, never silently fresh.
  `mark_verified` restores FRESH with a new verification timestamp.
- Project scoping: `intcache_key`/`semantic_key` bind intent + schema +
  project; `project` and `missReason` round-trip through the store.
- Rules-first `triage` (cache > workflow > deterministic > cheap > premium >
  GrokBot-late), smallest-capable-team `plan_team` (single worker default;
  verifier only on VERIFIED floor), `TaskFingerprint` dedupe, `compute_delta`
  compact context, `content_state` hashing.
- CLI restored + extended: HEAD-`cli.py` tail (gaps/learn/plans/quality/policy/
  governor/supervisor/executions/route/capability/a2a/durable/benchmark/usage)
  recovered verbatim and merged back with the working-tree kernel blocks —
  `efficiency` (8 subcommands), `ecosystem`, `workflow`. All 35 dispatch
  targets resolve; full lifecycle CLI runs green.
- Store: `intelligence_cache` table + durable counters, `ecosystem_candidates`,
  `workflow_catalog`, task-fingerprint records — all additive.
- Bugs found and fixed (not hidden): `invalidate()` fed a `CacheEntry` back
  through `from_dict()`; `to_dict` dropped `project` (cost cross-scope
  confusion on reload); CLI signature drift vs `services()` resolved
  (found via dispatch smoke, fixed in place); `ecosystem_scout.test_static`
  renamed-call → `certify_static`.
- Suite: `.venv/bin/python -m unittest discover -s tests` → **456 OK**
  (433 baseline + 23 kernel). No paid usage, no Grok task execution, no
  network side effects.

## Phase 4B — NOAERTH governor integration (2026-10-20, PASS)

ServerOS now publishes a bounded resource contract snapshot
(`/var/lib/agentos/resource-snapshot.json`, 0644, every 2 min via cron)
from existing authorities only (AOS governor + scheduler demands,
JanitorOS, REALM Studio, host governor, kernel). New modules
`resource_contract.py` + `worker_admission.py`: four-question admission
(can_start/budget/constraining_resource/retry_after_s) with provider
cooldowns independent of CPU, fail-closed on stale/UNKNOWN, capacity
ceiling of 2 additional heavyweight coding workers, and scratch leases
over the existing JanitorOS scratch registry. Server-side hooks (backed
up `.bak-4b-20261020`): Hive `submit_manifest` snapshot gate (CRITICAL
refuses, PRESSED defers LOW only), Control Tower `serveros` panel,
fleet-doctor `resource_health` key, `GET /resource`. 940/940 tests green
local + server; live admission correctly defers to the AOS
allow_new_work=false hold. Details: `PHASE4B_GOVERNOR_INTEGRATION.md`,
`PHASE4B_ACCEPTANCE.json`, `NOAERTH_RESOURCE_CONTRACT.json`.

## Phase 5A — storage forensics + admission readiness (2026-10-21, DONE)

Disk growth is now measured, not borrowed: `resource-growth.json` accrues
one (epoch, free) sample per publish (caps 180 raw / 168 hourly, atomic
0644) and `disk_growth` reports the measured short rate
(`agentos.growth_history`) ahead of the JanitorOS forecast, UNKNOWN until
≥3 samples span ≥600 s (never 0), with ETA to the 20 GiB warn floor only
from a supported negative rate. Collector.py module-level loop moved
behind `main()` + `__main__` guard (hermetic timeout-based regression
test). AOS hold ownership proven resource-generated (governor
`classify()` disk≥80 lane, notes=[], no expiry; cpu>97 lane self-cleared)
— refusals now carry `triggers{reasons[]}` in Hive and the fleet doctor
exposes `aos_mode/allow/constraints/growth-eta`; 3 tests prove
restrictive policy survives every snapshot state. Forensics: authorized
reclaim = 0 B today (janitor autonomy DISABLED + empty approvals +
tiered-policy exhausted); ~4.45 G must free for disk<80%; journal 2.1 G
awaits operator approval. Offline recovery bundle (44 commits, tip
`6f1ec2b`) verified at `/var/backups/agentos/`. 952 local / 946 server /
86 autonomousos tests OK. Live worker admission: BLOCKED, not bypassed.

## Phase 5B end-state (2026-10-21)

Phase 5B work is complete; outcome is BLOCKED at first controlled worker —
AOS SEVERE hold (disk 84.3%, allow_new_work=false) refused both genuine Hive
submissions pre-registry with full trigger evidence (not bypassed, registry
untouched). P6 crash-recovery tests added (3). Control Tower serveros panel
now carries aos_mode/allow_new_work/capacity_constraints/active_leases/
provider_cooldown/disk_growth. Concurrency benchmark: 40-case pure matrix,
fail-closed rules verified + live host admission (all profiles →
autonomousos_hold, retry 60 s; cpu_heavy cap 0/running 2). Tests: 955 local
(11 server-only skips) / 949 server OK. Release readiness: 6
REVIEW_REQUIRED reported-only. Next: operator frees ≥4.8 GiB (journal
vacuum first), re-run classify(), resubmit /root/phase5b/candidate
(-manifest) as first genuine worker, then P3 second worker. Bundle refresh
required at session end (stale by the new commits). Pushes: 0.

## Phase 6A end-state (2026-10-21)

Admission still BLOCKED — disk 85.76% (need 5.52 GiB for <80%); AOS
SEVERE from disk rung alone; hold untouched. Zero bytes reclaimed (nothing
eligible under existing authority; JanitorOS scan 0 eligible). Operator
approval menu delivered (A journal 1.86G / B apt 0.42G / C stale janitor
backup 0.94G / D REALM tarball hardlink-dedup 2.91G / E idle worktrees
2.3G / F syslog.1 waiver 0.72G); recommended A+B+C+D = 6.13G closes the
gap. Growth drivers: identical 195MB REALM tarballs per Studio case dir,
paios.service log flood (~0.4 GiB/day journals). Studio paused (operator,
untouched). No off-host backup exists — restic runbook in
PHASE6A_SOURCE_SAFETY.md. Bundle verified at 6afdf68. Suites: 958+86 OK.
Next: operator executes approved menu subset -> governor self-clears ->
re-verify classify() -> Phase 6B eligible. Tip 6afdf68, 40 unpushed.

## Phase 6B end-state (2026-10-21)

Pilot PARTIAL: every admission-independent part done (P0 interface/gates,
P3 team templates local+server, P4 agent-stack matrix, P5 lease layer
worker-level resume PARTIAL, P6 observation contract serves, P7 baselines
only, P8 release audit, P9 suites), P1/P2 BLOCKED and never bypassed -
three dry-run captures end at 2026-10-21T22:25:17Z (accepted=false,
serveros_low_priority_deferred, band PRESSED, dry_run=true), registry still
0 candidate matches. Disk worsened 85.82% -> 89.69% (+4.0 GiB/24h, brief
90.005% touch); bytes to <80% now 9.29 GiB -> 6A package A+B+C+D (6.13)
no longer suffices; arrest growth (paios journal flood, syslog) then
re-derive menu - nothing reclaimed, all approvals pending. Opencode runs
for portfolio but EACCES for agentos (home 750) - fix required before P1
executes; cursor-agent repaired in-phase (tree-sitter install, 25 MB).
Lease evidence P5-LEASE-DEMO-01 RETIRED/KEPT-in-grace on the production
registry; hive serves 8799/8792 captured then released (event_cursor 1081;
noaerth.hive.observation/1). Observed other-actor production: 8
realm-studio workers TESTING, R23-12 merged 09e55b85 after verification.
REL-realm-e5b5dd7f (no rollback) promotion blocked; 0 approvals.
Suites: local 958 + 67 OK; server 1028 + 92 OK (server has 4 test files +
src edits from a concurrent session 20:00-22:47 +0200 - untouched).
Deliverables: PHASE6B_HIVE_LIVE_PILOT.md, PHASE6B_WORKER_EVENTS.json,
PHASE6B_THROUGHPUT.json (1-vs-2 = UNKNOWN, not run),
PHASE6B_AGENT_STACK_HEALTH.json, NOAERTH_PHASE6_HANDOFF.md. Bundle refresh
required after this commit (tip ff25e85 = 6A). Paid calls: 0. Pushes: 0.
