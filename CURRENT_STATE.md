# AgentOS Current State

Living document — reflects reality as of 2026-09-25 (v0.5.0 release gate PASS; 710 tests green; public remote and CI verified).
If this file and behaviour disagree, behaviour wins; fix this file.

## Status: genuinely useful universal Agent Engine (local)

`agentos` installs (`pip install -e .`) and works through **CLI, HTTP API
and MCP stdio** against one shared core. 710 automated tests pass. Runtime
verified end-to-end with real detections, real executions, a deliberate
failure with recovery, a policy refusal, and restart persistence.

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
