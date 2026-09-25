# AgentOS — working instructions for agents in this repo

This repo is a venture build: treat it as an operating product, not a
scratchpad. The workspace AGENTS.md applies; this file adds project rules.

## What this is

AgentOS: universal provider-neutral agent execution control plane.
Python 3.11+, stdlib only. CLI-first. sqlite state. Tests prove behaviour.

## How to work here

- Virtualenv: `.venv/` (Python 3.12). Run everything via
  `.venv/bin/python -m ...`.
- Install (editable): `.venv/bin/python -m pip install -e .`
- Tests: `.venv/bin/python -m unittest discover -s tests`
- CLI: `.venv/bin/python -m agentos ...` or `.venv/bin/agentos ...`
- Isolate experiments: `AGENTOS_HOME=$(mktemp -d)` or `--home <dir>`.
  Never run experiments against `~/.agentos` blindly.

## Rules

1. **Inspect first.** Read `CURRENT_STATE.md` and `BUILD_MAP.md` before
   changing anything. They reflect reality — keep them that way.
2. **No fake progress.** Never claim an integration works until a test or a
   real CLI run proves it. No placeholder adapters presented as functional.
3. **Thin CLI, shared core.** New behaviour goes in `services.py`/`engine.py`,
   not in `cli.py`. Future API/MCP must reuse the same methods.
4. **Provider neutrality.** No provider-specific logic in `engine.py`,
   `services.py`, `registry.py`, or `verify.py`. It belongs in `adapters/`.
5. **Verification with evidence.** Any new execution path needs a verifier
   method and a test asserting both pass and fail cases.
6. **Bounded everything.** Retries, timeouts, event limits — no unbounded
   loops, no infinite recursion, no `while True` without an exit proof.
7. **Update the living docs** (`CURRENT_STATE.md`, `BUILD_MAP.md`,
   `EXECUTION_LOG.md`) when behaviour changes. Docs describing nonexistent
   features are defects.
8. **No auto-push.** Never push to GitHub unless explicitly asked.
9. Keep changes additive and incremental; do not replace working systems
   for aesthetics.

## Layout

`src/agentos/`: `models store events policies registry router verify
engine services api mcp_server cli`
`src/agentos/adapters/`: `base proc shell filesystem echo opencode github
xai grok openclaw langgraph`
`tests/`: `test_models test_store test_adapters test_adapters_ext
test_registry_verify test_engine test_router_orchestration test_services_cli
test_policy_safety test_api test_mcp test_learn_gaps`
Docs at repo root: `NORTHSTAR CURRENT_STATE BUILD_MAP ARCHITECTURE
DECISIONS OBJECTIVES CAPABILITIES AGENTS EXECUTION_LOG README`.

## Additional rules (Part B)

10. New adapters go in `adapters/` with honest `probe()`; register the
    capability in `registry.native_capabilities()`. Never claim health you
    did not measure.
11. Routing logic lives in `router.py`; the engine executes plans. Record
    rationale for every selection.
12. API/MCP/CLI share `services.AgentOS` — no business logic outside it.
13. Expensive/side-effecting integrations: shim-test by default; live runs
    only with explicit human authorization, bounded, and recorded.
14. `store.py` connections are per-thread; keep it that way.
