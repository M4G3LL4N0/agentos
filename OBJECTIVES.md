# AgentOS Objectives

Living document: what the Objective model is, and which objectives this
project itself is tracking. (Runtime test objectives live in the database,
not here — inspect them with `agentos inspect`.)

## The Objective model (`models.Objective`)

An objective carries everything the loop needs:

| Field | Purpose |
|---|---|
| `id` / `title` / `description` | identity + human intent |
| `priority` | LOW / MEDIUM / HIGH / CRITICAL scheduling hint |
| `status` | CREATED → READY → RUNNING → VERIFYING → COMPLETED / FAILED / BLOCKED / CANCELLED. Driven by the engine; never decorative. |
| `constraints` | e.g. `operation:fs.write`, `expected:hello`, `verify:file_exists` |
| `context` | `operation`, `params`, `verify`, `timeout_seconds`, `path`, `command`, … |
| `parent_id` | optional hierarchy (supported, never required) |
| `strategy` | assigned execution strategy string (`operation:X via capability:Y`) |
| `result` | verified output of the final execution |
| `verification_status` | UNVERIFIED / VERIFIED / FAILED |
| `evidence` | discrete proof items from adapters + verifier |
| `next_action` | what should happen next (None when complete) |
| `failure` | preserved failure summary when FAILED |

Operation resolution order: `--operation` flag → `context.operation` →
`operation:` constraint → title/description heuristics → `echo`.

## Project build objectives (Part A)

- [x] Foundational architecture with clean layer boundaries
- [x] Objective system with real state transitions
- [x] Capability registry + discovery (native + locally configured)
- [x] Provider-neutral adapter boundary with working local adapters
- [x] Minimal execution engine proving the vertical slice
- [x] First-class verification with real evidence
- [x] Bounded recovery with classified, preserved failures
- [x] Persisted event system
- [x] CLI command center (human + JSON modes)
- [x] Automated tests exercising actual behaviour (86 tests)
- [x] Runtime verification: real objective → run → verify → restart → failure

## Part B build objectives — all complete (v0.2.0)

- [x] Hardened generic CLI adapter (args, cwd, env, timeout, duration, policy)
- [x] OpenCode adapter as a registered capability (detected AVAILABLE)
- [x] MCP stdio interface over the same core (15 tools, live-verified)
- [x] GitHub read boundary via `gh` (live-verified; writes don't exist)
- [x] xAI adapter on the official interface (AUTH_REQUIRED, honestly reported)
- [x] Grok Build CLI adapter via supported `grok -p` (detected, not run live)
- [x] OpenClaw adapter via supported `agent --message --json` (detected, not run live)
- [x] LangGraph boundary with honest UNAVAILABLE + GRAPH fallback
- [x] Capability-aware router (9 strategies, rationale, roles, conflicts)
- [x] Parallel/sequential/iterative execution, independent verification, quality tier
- [x] Multi-strategy recovery, learning layer, gap analysis, policies, API
- [x] Historical Part B suite: 171 tests; current release suite: 710 tests;
      full runtime verification; docs reflect actual status

## Next candidate objectives (see BUILD_MAP.md NEXT)

1. Daily-use configured capability (repo lint/test via `capabilities add`).
2. Objective decomposition with aggregation.
3. First authorized live agent run (bounded, cost-measured).
4. MCP client adapter. 5. Approvals UX + sandboxing.
