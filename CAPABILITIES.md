# AgentOS Capabilities

Living registry documentation. Only capabilities with **working adapters**
are listed as available. Anything else belongs in BUILD_MAP.md FUTURE.

## Capability record schema

`id, name, type, description, operations, adapter, availability, health,
source, inputs, outputs, cost?, latency?, permissions?, environment?,
persistence?, constraints?, verification?, config?`

Optional metadata stays `None`/empty when unknown — never invented.

## Native local capabilities (always present after `init`)

### `shell` — Local shell (type: `cli`, adapter: `shell`)
Policy-gated command execution: command/argv forms, cwd, env (secrets
scrubbed from logs), timeout-kill, duration telemetry.
- operations: `shell.run` · verification: `exit_code` · probe: `true`

### `filesystem` — Local filesystem (type: `filesystem`, adapter: `filesystem`)
- operations: `fs.read`, `fs.write`, `fs.exists`, `fs.list`
- verification: `structural` (override per objective, e.g. `file_exists`)
- writes gated by policy write-roots · probe: temp-dir round-trip

### `echo` — Echo (type: `tool`, adapter: `echo`)
Input returned unchanged. Tests, demos, safe default. No external effects.
- operations: `echo` · probe: always OK

## Detected capabilities (registered always, health probed honestly)

### `opencode` — OpenCode agent (type: `agent`, adapter: `opencode`)
`opencode run --format json --dir <project> [--model --agent --title -f]
<prompt>`. Router DELEGATED strategy for implementation-class work.
Detection honesty (2026-09-20, one shell): a user-configured OpenCode binary
was detected outside the isolated test PATH, so probes in that shell report
UNAVAILABLE; AVAILABLE only where a probe actually resolves it.
**opencode LIVE never executed (no authorization).**

### `github` — GitHub context (type: `repository`, adapter: `github`)
Read-only `gh` inspection: `github.repo/branch/file/commit/issue/pr`.
**No write operations exist in this adapter by construction.**
Detection honesty (2026-09-20, this shell): `gh` not on PATH here, so
probes report UNAVAILABLE; AVAILABLE only where probed (Part B verified
live: `github.repo` on `cli/cli` COMPLETED under a PATH with
authenticated `gh`).

### `xai` — xAI Grok model (type: `model`, adapter: `xai`)
Official OpenAI-compatible `chat/completions`; model must be explicit per
call; tools passthrough; 401/429/5xx classified honestly.
Health on this machine: **AUTH_REQUIRED** (no `XAI_API_KEY`). Real code
path tested against a local HTTP server; no live calls made.

### `grok` — Grok Build CLI (type: `agent`, adapter: `grok`)
Single-turn `grok -p <prompt>`; permission modes never escalated.
Detection honesty (2026-09-20, this shell): binary not on PATH, probes
report UNAVAILABLE; AVAILABLE only where probed.
Execution path shim-tested; never invoked live (no model spend authorized).

### `openclaw` — OpenClaw agent (type: `agent`, adapter: `openclaw`)
`openclaw agent --message <text> --json`; `--deliver` never set.
Bot identities sharing an environment are NOT security isolation.
Detection honesty (2026-09-20, this shell): binary not on PATH, probes
report UNAVAILABLE; AVAILABLE only where probed.
Execution path shim-tested; never invoked live.

### `langgraph` — LangGraph orchestration (type: `workflow`, adapter: `langgraph`)
Reserved boundary. Health on this machine: **UNAVAILABLE** (package not
installed). Router falls back GRAPH→SEQUENTIAL with rationale.

## Configured capabilities (source: `configured`)

`agentos capabilities add <id> --command "..."`: real shell execution,
`{placeholders}` + `AGENTOS_PARAM_*` env, exit_code/contains verification,
removable (natives protected).

## Selection

Router scores usable capabilities (health + historical success rate +
cost/latency) and records rationale on every plan; deterministic order.
Unusable (DOWN/UNAVAILABLE/DISABLED/AUTH_REQUIRED-without-key at execution)
capabilities are avoided; unknown operations BLOCK with gap analysis.
