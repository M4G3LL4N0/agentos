# AgentOS Architecture

Living document. Describes the implemented v0.5.0 release surface (710 tests).

## Runtime shape

Lightweight Python processes (stdlib only, no runtime dependencies).
State lives in one embedded sqlite database (`$AGENTOS_HOME/agentos.db`,
default `~/.agentos/`), accessed via per-thread connections (WAL) so the
API server and parallel plan execution are safe. Interfaces: CLI, HTTP API
(`agentos serve`, loopback, no auth in v1), MCP stdio (`agentos mcp`) —
all three call the same `services.AgentOS` core. No containers, no cloud.

```
Interface Layer      CLI (argparse)  ·  HTTP API (api.py)  ·  MCP stdio (mcp_server.py)
        │  thin: every surface delegates to services
        ▼
Control Layer        services.AgentOS  ·  engine.Engine  ·  router.Router
  objective management · plan orchestration · capability-aware routing ·
  lifecycle (states) · policies · approvals (grant/revoke) · delegation
        ▼
Capability Layer     registry.CapabilityRegistry
  registration · discovery (native + detected + configured) · metadata ·
  selection · honest health probes (9-state taxonomy)
        │                 ▲
        ▼                 │ adapter boundary (adapters/base.py)
Execution Layer      shell · filesystem · echo · opencode · github ·
                     xai · grok · openclaw · langgraph-boundary
  agent / model / tool / CLI execution through Adapter protocol
        ▼
State Layer          store.Store (sqlite, per-thread connections)
  objectives · executions · events · capabilities · verification
  results · failures · plans · patterns · gaps · quality
        ▼
Evaluation Layer     verify.Verifier (+ quality) · recovery strategies
  structural / exit_code / contains / file_exists · heuristic quality
  grades · failure classification · retry / alternate-capability /
  revise / replan · next-action derivation · pattern learning
```

## Module map (`src/agentos/`)

| Module | Responsibility |
|---|---|
| `models.py` | Dataclasses + enums: Objective, Capability, Execution, Event, VerificationResult, Failure, Evidence, Report, Strategy, Role, PlanStep, ExecutionPlan, Pattern, GapAnalysis, QualityEvaluation. Explicit `to_dict`/`from_dict`. |
| `store.py` | sqlite persistence, schema, row↔model translation, per-thread connections. Survives restarts. |
| `events.py` | `EventBus`: synchronous persisted emit + queries. |
| `policies.py` | `ExecutionPolicy`: blocked patterns, destructive gating, approvals, write roots, secret redaction. |
| `registry.py` | Native+detected catalog (9), `register`/`remove`, `discover`, honest `probe`, deterministic `select`, configured-CLI registration. Holds adapter instances. |
| `router.py` | `Router.plan`: 9 strategies, capability scoring (health+history+cost/latency), rationale, roles, conflict detection. |
| `adapters/base.py` | `Adapter` protocol, `ExecutionRequest`, `AdapterResult`. The seam future providers implement. |
| `adapters/proc.py` | Shared subprocess runner + binary resolution for CLI-backed adapters. |
| `adapters/shell.py` | Policy-gated subprocess: command/argv, cwd, env (scrubbed), timeout-kill, duration. |
| `adapters/filesystem.py` | Real file ops: `fs.read/write/exists/list`; writes policy-gated. |
| `adapters/echo.py` | Echo op for tests/demos. No external effects. |
| `adapters/opencode.py` | Real `opencode run --format json` delegation (prompt/project/model/agent/files). |
| `adapters/github.py` | Read-only `gh` inspection: repo/branch/file/commit/issue/pr. No writes exist. |
| `adapters/xai.py` | Official OpenAI-compatible chat completions; explicit model required; tools passthrough. |
| `adapters/grok.py` | Single-turn `grok -p` execution; never escalates permissions. |
| `adapters/openclaw.py` | `openclaw agent --message --json` turns; `--deliver` never set. |
| `adapters/langgraph.py` | Reserved boundary; reports UNAVAILABLE until installed. |
| `verify.py` | Method dispatch, real checks, `classify_failure`, `retryable`, `evaluate_quality`, `suggest_next_action`. Part A: 8-stage `run_verify_pipeline` (STATIC/TEST/TYPECHECK/LINT/BUILD/RUNTIME/DIFF_REVIEW/CUSTOM), `DISALLOWED_VERIFY_COMMANDS` fail-closed list, extended failure taxonomy, `bounded_recover` (ABSOLUTE_CAP=5). |
| `engine.py` | Plan-driven control loop (`run`, `recover`, `verify_latest`): sequential/parallel/iterative, independent verification, bounded recovery, pattern learning. Provider-neutral. Part A: mode/authorization enforcement at dispatch, BLOCKED gate for parents/dependencies, execution-record population (mode/auth/summary/normalize pair/change keys), `bounded_recover` wrapper. |
| `safety.py` | Part A: `gate_allows(mode, auth)` + `require_execution_authority` — non-LIVE passes, LIVE needs explicit authorization (fail-closed). |
| `decompose.py` | Part A: `create_child_objectives` + `objective_is_unblocked` — parent/child persistence, dependency gating, unknown `depends_on` raises. |
| `changes.py` | Part A: read-only fs/git change detection (`capture_fs_state`/`diff_fs`/`attribute_changes`, canonical `added`/`modified`/`deleted`); git allow-list is read-only by construction. |
| `task_builder.py` | Part A: pure bounded deterministic `build_task()` rendering 8-section instruction text from real model fields (no I/O). |
| `services.py` | `AgentOS` facade: all of the above + delegate/plans/patterns/gaps/quality/policy/dashboard. Shared by CLI, API, MCP. |
| `api.py` | stdlib HTTP API over services. No duplicated logic. |
| `mcp_server.py` | stdlib MCP stdio server (initialize/tools-list/tools-call, 15 tools) over services. |
| `cli.py` | `agentos` command center: 20 commands; thin wrappers; `--json` preserves exit codes. |

## Key flows

**Run:** load objective → READY→RUNNING → discover → resolve operation
(CLI flag → `context.operation` → `operation:` constraint → title heuristics
→ `echo`) → **router plans** (strategy + steps + roles + rationale, persisted)
→ execute plan: DIRECT/DELEGATED single loop; SEQUENTIAL + composites in
order; PARALLEL fan-out (conflict-checked, isolated connections, merged);
ITERATIVE until first verify → per-step attempt loop (execute → persist →
VERIFYING → verify → bounded retry / alternate capability) → independent
verify steps (second capability) → quality evaluation (revise bounded) →
COMPLETED (failures marked recovered) or FAILED/BLOCKED (gap recorded) →
pattern recorded → Report with next action. Termination: complete, blocked,
unsafe (policy), needs-human, or bounds (attempts/steps/revisions/workers).

**Verify:** method resolution order — objective `context.verify` / `verify:`
or `expected:` constraints → capability-declared verification → structural
default. Checks run against real adapter output (exit codes, file existence,
substrings). Re-verifiable at any time via `verify_latest`.

**Recover:** FAILED/BLOCKED objectives re-enter `run` with bounded attempts;
prior failures marked recovered only on verified success.

## Extension points (all exercised by tests, none faked)

- New adapter: implement `execute` + `probe`, call
  `registry.register_adapter(...)`. Core untouched.
- New capability source: persist `Capability` records; `discover()` merges
  them; `select()` ranks them.
- New verification method: add to `Verifier._run_method` dispatch.
- Future API: call `services.AgentOS` methods directly (same core as CLI).
- Future MCP: wrap service methods as tools
  (`engine.inspect/objective/run/delegate/capabilities/verify/recover/state`
  map 1:1 onto existing service methods).

## What is deliberately absent

MCP client, browser/computer use, GitHub writes, streaming events, API
auth/multi-user, execution sandboxing, schema migrations, OrgOS (separate
project). Live model invocations were not run during verification (cost
authorization required). See BUILD_MAP.md.
