# AgentOS Build Map

Living document — evolves with discoveries during implementation.
Updated 2026-09-24 (v0.5.0 learning execution lifecycle + low-usage efficiency
kernel, 710 tests; local release gate PASS; publication preparation in progress).

## COMPLETE

Part A (v0.1.0): scaffold, models, sqlite, events, adapters (shell/fs/echo),
registry, verification, engine loop, services, CLI (12 commands), 86 tests,
runtime proof.

Part B (v0.2.0):
- [x] Execution policies + shell/filesystem hardening + secret redaction
- [x] Health taxonomy (9 states) + honest probes everywhere
- [x] Capability-aware router (9 strategies, scoring, rationale, roles,
      conflict detection, GRAPH fallback)
- [x] Engine: plans, sequential/parallel/iterative composites, independent
      verification, quality tier, multi-strategy recovery, termination bounds
- [x] Real adapters: opencode, github (read-only), xai, grok, openclaw,
      langgraph boundary — shim-tested, live-detected
- [x] Learning layer (patterns influence routing) + gap analysis on BLOCKED
- [x] HTTP API (stdlib) + MCP stdio server (stdlib) over the same core
- [x] Thread-safe store (per-thread connections; found by failing tests)
- [x] CLI: dashboard status, delegate, serve, mcp, gaps, learn, plans,
      quality, policy
- [x] 171 tests green (core/capabilities/execution/integration/
      orchestration/safety)
- [x] Runtime proof: live gh read, parallel writes, MCP+API delegate,
      flaky-command recovery, `rm -rf` policy refusal, restart persistence,
      honest availability matrix (no live model spend)

## RELEASE STATUS

- [x] v0.5.0 local release gate PASS: 710 tests, 0 failures, 0 errors, 0 skips;
      post-fix redaction, backup, malformed-state, and learned-routing persistence
      regressions covered.
- [x] Dedicated public remote created, pushed, and verified; no deployment is authorized.

## Learning execution lifecycle (v0.5.0, 710 tests, 2026-09-24)

Existing modules were integrated into `Engine.run`; no replacement
architecture or provider-specific path was added.

- [x] Preflight intelligence: exact cache, safe arithmetic, and state/database
      lookup with zero worker/model/Grok calls
- [x] Workflow reuse from observed/verified/certified evidence with capability,
      input, and approval preconditions; complete workflow/skill persistence
- [x] Verification outcomes drive retry, alternate worker, supervisor, or human
      escalation within explicit attempt bounds
- [x] Coach lessons, workflow maturity, executor metrics, learned routing, fleet
      incidents, routines, and human-attention reasons persist and drive later
      behavior
- [x] Redacted job traces record decisions, failures, actual retries,
      verifications, escalations, cache/workflow changes, and measured usage
- [x] Backup create/inspect/verify/restore/reconstruct with allowlisted tables,
      safe archive extraction, extra-member rejection, secret scan, source-path
      minimization, schema-column validation, and target-aware dry run
- [x] CLI: objective-text `run`, JSON params, `--explain`, learning command
      groups, backup lifecycle, and deterministic `efficiency benchmark`
- [x] 11-class local benchmark: 2 cache hits, 1 workflow reuse, 6 workers,
      10 verifications, 0 retries/model/premium/Grok/human escalations
- [x] Isolated full discovery: **710 OK, 0 failures, 0 errors, 0 skips**
- [x] Editable package + CLI version: **0.5.0**
- [x] Release gate closed: post-fix local audit and 710-test discovery are green;
      publication is a separate remote operation.

## Low-usage efficiency kernel (v0.4.2, 433 → 456 tests, 2026-09-23)

Zero-cost mission; additive; nothing rebuilt. Full detail in
`EFFICIENCY_KERNEL.md`, `INTELLIGENCE_CACHE.md`, `TRIAGE.md`,
`SMALLEST_CAPABLE_TEAM.md`, `DELTA_CONTEXT.md`, `CONTEXT_ECONOMICS.md`,
`GROKBOT_USAGE_POLICY.md`.

- [x] `efficiency.py`: rules-first `triage` (cache > workflow > deterministic >
      cheap > premium > GrokBot-late), `plan_team` (single worker default,
      verifier only on justification), `TaskFingerprint` dedupe, `compute_delta`
      compact context, `grok_routing_policy`, `content_state`, `project_state_ref`
- [x] `intelligence_cache.py`: shared L0–L6 `IntelligenceCache` over the
      durable `Store`; honest hits (provenance + modelCalls=0) / misses with
      reasons; security-scope gating; secret gate (PEM/token/bearer);
      narrow dependency invalidation; `project` + `missReason` in the
      round-trip; durable counters + `stats()`
- [x] `ecosystem_scout.py` (worked-tree seam kept): inert DISCOVERED →
      INSPECTED → TESTING → TESTED → APPROVED lifecycle, static-only cert
- [x] `reusable_workflows.py` seam: usage-led catalog, reuse the only legal
      upgrade path
- [x] Store: `intelligence_cache` table (+ counters), `ecosystem_candidates`,
      `workflow_catalog`, task-fingerprint records
- [x] Services: `efficiency_triage/team/fingerprint/delta/cache_get/cache_put/
      invalidate/stats`, `ecosystem_*`, `workflow_*`
- [x] CLI: `efficiency`, `ecosystem`, `workflow` command groups (from the
      recovered working-tree kernel blocks, re-attached to the healthy HEAD
      cli.py tail)
- [x] Tests: 23 in tests/test_efficiency_kernel.py (+ 9 triage, + ecosystem/
      workflow seams); suite 433 → 456 OK, no regressions
- [x] Runtime proof (isolated homes, zero cost): full efficiency + ecosystem
      + workflow lifecycles via the real CLI; secret-gate + scope + stale +
      narrow-invalidation paths exercised

## Resource Governor + supervisor operating model (v0.4.1, 385 → 411 tests, 2026-09-22)

Zero-cost mission; additive; nothing rebuilt. Full detail in
`RESOURCE_GOVERNOR.md`, `SUPERVISOR_MODEL.md`.

- [x] `resource_governor.py`: UsageSnapshot model, strict validation, no
      fabricated reset dates, freshness (LIVE/STALE/UNKNOWN), source
      precedence resolution, snapshot-key/address mapping, governor weights
      (negatives rejected), cost-factor ranking, four-way separation,
      `governor_route` (federation gates preserved)
- [x] `usage_ledger.py`: durable RECORD-only ledger; sums only what is
      recorded (no fabrication)
- [x] `task_verification.py`: verification-first status machine + verifier
      separation without wasting model calls on deterministic evidence
- [x] `supervisor.py`: canonical operating chain + honest chain-layer status
- [x] Store: `usage_snapshots`, `usage_ledger`, `routing_outcomes`,
      `job_verifications` (+ counts/integrity); federation comment fix
- [x] `federation.py`: ExecutorCell four-way-separation fields +
      JobEnvelope verification fields (additive safe defaults)
- [x] `models.py`: event types VERIFICATION_REQUIRED, GOVERNOR_ROUTE_RECORDED,
      USAGE_SNAPSHOT_IMPORTED, USAGE_ENTRY_RECORDED, USAGE_ADVISORY,
      SUPERVISOR_STATUS
- [x] Services: usage_snapshots/resolve/import/refresh/add/ledger,
      governor_weights/route/outcomes/verify, supervisor_status
- [x] CLI: `usage`, `governor`, `supervisor` subcommands (all `--json`)
- [x] `grokbot_office.py`: official usage-export parser (adapters/, provider
      code stays out of core)
- [x] Tests: 26 in tests/test_resource_governor.py; test_store counts set
      updated additively
- [x] Runtime proof (isolated homes, zero cost): import→resolve→ledger,
      governor route→verify→outcome→learned summary, supervisor status,
      usage refresh from a local export file; no paid usage, no network

## Fabric — Federated agent fabric (v0.4.0, 328 → 385 tests, 2026-09-22)

- [x] A2A v1.0 remote interop (`a2a_v1.py` + peer v1 endpoints):
      well-known card, strict parsing, version negotiation, JSON-RPC
      lifecycle, typed errors, secret boundary; `a2a` CLI
- [x] AgentOS agent card (echo-only, serving OFF; API `/agent-card`
      behind config) + `/federation/snapshot` for PAIOS
- [x] MCP registry discovery + certification lifecycle + `mcp` CLI;
      approval registers unavailable metadata (transport DEFERRED)
- [x] Capability graph (`capability find|explain`)
- [x] Multi-cell federation (config cells, specialization, isolation)
- [x] Scarcity/reset economics (PRESERVE/NORMAL/all-guards HARVEST)
- [x] Explainable routing (`federate route --explain`, `route --job`)
- [x] Calibration (explicit weights), bounded failover, pipelines
- [x] Approval classes + trust levels + gate on new remote families
- [x] Benchmarks (measured local-worker A/B/C; rest SKIPPED) + report
- [x] Durable native backend + Temporal DEFERRED with reason
- [x] OrgOS snapshot bridge (UNCONFIGURED without one), PAIOS snapshot
- [x] `federate doctor|cells|health`, `durable status`,
      `benchmark run|report`, `usage status` CLI
- [x] Tests: 11 A2A-v1 + 7 MCP lifecycle + 38 fabric + 1 honesty test;
      2 env-sensitive federation tests made deterministic (synthetic
      premium cell; unavailable-Grok honesty branch)
- [x] Runtime proof (isolated homes, zero cost): init 13 caps, honest
      UNKNOWN probes, local-tools routing, OFFLINE gating, simulated
      multi-cell, UNCONFIGURED registry, benchmark/usage/durable live;
      no paid usage, no Grok task execution

## Part B — Federation (v0.3.0, 251 → 292 tests, 2026-09-21)

- [x] Envelope model: JobEnvelope/ResultEnvelope, required targetCapability,
      credential rejection on ingest; TASK/RESULT packets (A2A-1.0 shape)
- [x] Executor cells T0–T6 + tier map; `federation_cells` persistence +
      re-hydration on restart
- [x] Pure routing (`resolve_route`): data-class/latency/HS-external/health/
      posture gates; lower-tier preference; reasons recorded
- [x] Plan-before-premium: T5 jobs pause with plan/cost/approval object;
      never auto-executed without approval evidence
- [x] Storage: federation_jobs/results/cells tables + methods; counts()
- [x] grokbot-office adapter: read-only bridge, 8 allowlisted ops,
      forbidden-command guard, honest probe, SIMULATED/INSPECT/LIVE
- [x] Registry + native capability `grokbot-office` (kind AGENT); 3 new
      event types
- [x] Services: federation_executors/status/route/submit/jobs/telemetry/packet;
      capability-driven cell dispatch; LIVE restricted to read-only bridge
- [x] CLI: `agentos federate {status|executors|route|submit|jobs|packet|telemetry}`
- [x] A2A loopback agent catalog: healthy cells only, management ops hidden,
      no HS cells advertised external
- [x] Tests: 40 in tests/test_federation.py; existing assertions updated
      for `grokbot-office` + new counts keys
- [x] Runtime proof: status (grokbot-office adapter health measured),
      echo→local-tools route, premium hold (executed=False), LIVE refusal
      without auth, packet round-trip; no paid usage

## Part A (v0.3.0-track, 8 tasks, 171 → 251 tests, all reviewed clean)

- [x] Task 1 mode-gated execution (`b5cb411`): SIMULATED default, LIVE needs
      explicit auth; 15 tests
- [x] Task 2 decomposition (`fa9f5cc` + `3960a90` unknown-depends_on raise); 8 tests
- [x] Task 3 readiness ledger (`6dee306` + `541b771` word-boundary fix),
      `capabilities inspect --readiness`; 15 tests
- [x] Task 4 change detection (`958b205`), git read-only; 5 tests
- [x] Task 5 normalization (`4c81ac0`), `execution show`/`executions list`; 11 tests
- [x] Task 6 task builder (`39efff9`), bounded prompt text; 5 tests
- [x] Task 7 verify pipeline (`219b595`), `project verify`, closes Task 5
      engine-wiring debt; 10 tests
- [x] Task 8 failure taxonomy + bounded recovery (`4daa80d`,
      ABSOLUTE_CAP=5); 11 tests
- [x] Runtime proof (isolated homes, SIMULATED only): fs.write objective →
      COMPLETED + VERIFIED; `project verify` stage table; `doctor` all PASS.
      opencode LIVE never executed; no paid spend.
- [x] Carried minors (non-blocking, see task reviews): ungated direct
      adapter calls outside engine; `executions list` positional superset;
      `diff_fs` `removed` alias (consume `deleted`); `change_summary`
      assert-narrowing; over-broad taxonomy substrings; `bounded_recover`
      name shadowing; verify stdout unredacted; `_has_tests` walk uncapped.

## NEXT (highest leverage first)

1. **Part 2 planning** — federation economics/negotiation across remote
   cells, A2A task orchestration with remote agents, nested planning tiers
   (per NORTHSTAR). Do not start until Part 2 is explicitly requested.
2. **Daily-use configured capability** — register a repo lint/test command,
   drive real fixes through objectives. Zero core changes.
3. **Objective decomposition** — parent spawning verifiable children with
   aggregation. Engine + services + CLI flags.
4. **First authenticated live agent run (explicitly authorized)** — one
   bounded `opencode.run` or `grok -p` objective to measure real
   cost/latency/verification behaviour; record pattern. Requires human go-ahead.
5. **MCP client adapter** — call external MCP servers as capabilities
   (AgentOS already speaks MCP; now let it consume MCP too).
6. **Approvals UX + sandboxing** — human gate for destructive/production
   classes beyond CLI grant; path/network sandbox for shell.

## BLOCKED

- xai live use: needs `XAI_API_KEY` (not present; adapter ready).
- Live agent model runs: need explicit cost authorization (adapters ready).
- LangGraph execution: needs package + provider (fallback in place).

## FUTURE (reserved, not started)

- Schema migrations, event search/streaming, API auth/multi-user.
- Additional adapters: browser, computer, external APIs, GitHub write
  (behind approvals + policy design first).
- Model-based quality judge (evaluator slot exists).
- OrgOS integration (separate project; consumes AgentOS via API/MCP/CLI).
