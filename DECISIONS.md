# AgentOS Decisions

Living decision log. Each entry: context → decision → consequence.

## D1 — Python 3.11+, standard library only
Context: need fast startup, low resource use, strong typing, testability,
broad agent-ecosystem compatibility.
Decision: Python with dataclasses, `sqlite3`, `argparse`, `unittest`.
Zero runtime dependencies, zero dev dependencies.
Consequence: `pip install -e .` is the whole install; tests run anywhere
with Python 3.11+. Must avoid 3.10-only-incompatible syntax (we use
`X | None` and `StrEnum`, both 3.11+).

## D2 — Embedded sqlite as the state layer
Context: state must survive restarts; no ops burden allowed.
Decision: single sqlite DB with explicit schema + WAL mode; JSON columns
for flexible payloads; typed row↔model translation in one place.
Consequence: no migrations framework yet; schema changes will need a
versioned migration step (see BUILD_MAP FUTURE).

## D3 — One home directory for state
Context: CLI must work from anywhere with one coherent control plane.
Decision: `$AGENTOS_HOME` or `~/.agentos/` holds `agentos.db` + `config.json`;
`--home` overrides per invocation (also the test isolation mechanism).
Consequence: no per-project `.agentos/` scattering in v0.1.0.

## D4 — Services layer shared by CLI and future API
Context: CLI-first now, API/MCP later, without duplicating business logic.
Decision: `services.AgentOS` owns all orchestration-facing operations;
`cli.py` only parses args, formats output, returns exit codes.
Consequence: future API/MCP wrap the same methods; CLI stays thin.

## D5 — Operation-based capability selection
Context: Part A needs real routing without an elaborate planner.
Decision: objectives declare (or imply) one required operation string;
registry selects usable capabilities supporting it, deterministically.
Consequence: routing is data-driven and testable; richer planning can
replace `_select_operation` later without changing the loop.

## D6 — Verification methods travel with requests
Context: verifier must honour capability-declared checks without
provider-specific code in the core.
Decision: engine copies `capability.verification` into the execution
request; verifier resolves method from objective → capability → default.
Consequence: configured capabilities get real verification for free.

## D7 — Adapters return results, never raise (contract)
Context: a provider crash must not crash the control loop.
Decision: `AdapterResult(ok, output, exit_code, error, evidence)`; engine
additionally guards `adapter.execute` with try/except.
Consequence: every failure path produces records, never tracebacks.

## D8 — Shell adapter executes configured operations
Context: configured capabilities have their own operation ids
(`<id>.run`), but execute via the shell adapter.
Decision: shell adapter runs any operation when `params.command` is
present; rejects unknown operations without a command.
Consequence: configured CLI capabilities work end-to-end; native
`shell.run` semantics unchanged.

## D9 — argparse dest collision avoided
Context: `--command` option clobbered the top-level subparser `dest`.
Decision: `--command` uses `dest="shell_command"`.
Consequence: rule for future CLI work — never name an option identically
to a subparser dest.

## D10 — Backoff computed, not slept
Context: bounded retry needs backoff semantics; local retries are instant.
Decision: exponential backoff is computed, logged in `recovery.started`
payloads, but not slept.
Consequence: fast tests and runs; sleep can be added if remote providers
need it (config-gated).

## D11 — JSON mode preserves exit codes
Context: automation needs both structured output and process status.
Decision: `--json` prints JSON but `run`/`verify`/`recover` still return
nonzero on non-success.
Consequence: scriptable and human-friendly.

## D12 — No fake integrations
Context: strong temptation to stub Grok/OpenCode/MCP for appearance.
Decision: extension points + honest docs instead; `agents` command returns
empty until a real agent/model adapter exists; CURRENT_STATE lists
non-implemented items explicitly.

## D13 — Per-thread sqlite connections
Context: API server threads and parallel plan workers crashed on sqlite
thread affinity (`ProgrammingError`), found by failing API tests.
Decision: thread-local connections to the same WAL database (30s busy
timeout) instead of one shared connection or a global lock.
Consequence: genuine concurrency for reads; writes serialize in sqlite.
`Store.close()` closes all tracked connections.

## D14 — Router records rationale on every plan
Context: routing must not be arbitrary (spec requirement).
Decision: `Router.plan` scores capabilities (health + pattern history +
cost/latency), persists the plan, and writes a human-readable rationale
into the plan record and objective.strategy.
Consequence: `agentos plans` shows why each decision was made; history
influences but never dictates (no facts from unverified assumptions).

## D15 — GRAPH falls back, langgraph stays a boundary
Context: langgraph not installed, no provider configured; spec says add it
only where materially useful.
Decision: router assigns GRAPH only on request; without an installed
package it plans SEQUENTIAL with explicit rationale; adapter reports
UNAVAILABLE with remediation.
Consequence: no heavy dependency, no fake graphs, honest UX.

## D16 — No live model spend during verification
Context: opencode/grok/openclaw/xai executions invoke real models (cost,
latency, side effects).
Decision: runtime verification proves *detection + honest health* and
exercises execution paths via shims/mocks; live agent runs require explicit
human authorization (BUILD_MAP NEXT-3).
Consequence: truthful "AVAILABLE (execution path tested, not yet run live)".

## D17 — MCP + API in stdlib, no new dependencies
Context: MCP/API must stay lightweight and reliable; project rule is zero deps.
Decision: stdlib `http.server` REST API and NDJSON JSON-RPC MCP stdio
server, both calling `services.AgentOS` directly.
Consequence: zero new packages; protocol-correct; tested in-process.

## D18 — Smallest-useful policy model
Context: spec asks for policies without an enterprise framework.
Decision: blocked command patterns, destructive gating, approval classes
with grant/revoke, write roots, secret redaction — stored in config.json,
enforced in adapters, surfaced in CLI/doctor/dashboard.
Consequence: `rm -rf` class mistakes are refused before execution; risky
classes stop as BLOCKED with a human next action.

## D19 — GitHub reads only, by construction
Context: GitHub should be context, not an uncontrolled execution target.
Decision: adapter implements six read operations; zero write code paths
exist (no push/merge/mutation possible through it).
Consequence: write support later requires a new adapter surface + policy
design, not a flag flip.

## D20 — Quality as a separate tier with a heuristic defaultContext: verified != good; spec wants EXECUTION vs VERIFICATION vs QUALITY.
Decision: `evaluate_quality` rubric (verification, evidence depth, output
substance, clean stderr, duration telemetry) with an evaluator slot;
REVIEW_THEN_REVISE loops bounded (2 revisions).
Consequence: quality findings can trigger revision today; a model judge
can replace the heuristic without touching the loop.

## D21 — SIMULATED is the default execution mode
Context: gating LIVE-without-auth while keeping LIVE as default would break
real-local-execution paths and force editing existing tests.
Decision: `ExecutionRequest.mode` defaults to SIMULATED; LIVE executes only
with explicit authorization (`safety.require_execution_authority`
fail-closed at engine dispatch).
Consequence: no accidental LIVE runs; follow-on work must pass
`mode=LIVE` + auth explicitly rather than relying on defaults.

## D22 — READY never comes from detection alone
Context: claiming readiness without execution evidence is fake progress.
Decision: `readiness()` maps binary-missing to DETECTED and never returns
READY on a detection path; `evolve_readiness()` promotes only on positive
execution signals (word-boundary matched) and demotes on failure first.
Consequence: honest ledger (`capabilities inspect --readiness`); FAILED
recovers via two evidenced successes (DEGRADED then READY), never one claim.

## D23 — Git access from the control plane is read-only
Context: change detection needs git context without any mutation risk.
Decision: `changes.py` allow-lists only `status`, `ls-files`, `rev-parse`,
`diff`; add/commit/push/reset/clean/checkout are structurally impossible;
verify-stage disallow list (`git commit/push`, `rm -rf`, `git reset --hard`)
is screened before any subprocess call and never executed.
Consequence: attribution and diff review with zero self-sabotage surface.

## D24 — Fail-closed gates everywhere
Context: unknown inputs must block rather than slip through.
Decision: POLICY_BLOCK sorts first in failure classification; disallow-list
matching is substring fail-closed (over-blocking accepted, e.g. `echo git
push`); AUTH_REQUIRED/BINARY_MISSING/CONFIGURATION_ERROR resolve to BLOCKED
with generic reasons (no secret echo); un-run verify stages are
NOT_APPLICABLE, never passed.
Consequence: safe defaults under adversarial or malformed input; explicit
human next actions instead of silent passes.

## D25 — Normalize pair lives in Execution output, not new fields
Context: Task 5 added the taxonomy but Execution has no dedicated columns
for the normalized pair.
Decision: engine stores `result_state`/`verification_state` inside
`Execution.output` plus a `normalized_result` Evidence item; no new field
names invented; SIMULATED-ok maps to PARTIAL×UNVERIFIED (never COMPLETED,
never VERIFIED from normalization alone).
Consequence: persisted records carry the taxonomy without schema churn;
`execution show` surfaces mode/cost/summary honestly (UNKNOWN unless measured).

## D26 — auth legacy kept, AUTH_REQUIRED canonical
Context: existing callers return `auth`; the precise class is AUTH_REQUIRED.
Decision: bare `unauthorized/401/403` still classify as `auth` (no caller
breaks); credential-shaped text classifies AUTH_REQUIRED; `retryable` and
`bounded_recover` handle both; neither is retryable, both BLOCK.
Consequence: backward compatible today, canonical name going forward.
