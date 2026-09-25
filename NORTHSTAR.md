# AgentOS North Star

## Mission

AgentOS is a **universal, provider-neutral AI Agent Engine**: a general-purpose
execution and orchestration control plane that lets humans and other AI systems
express *objectives* while AgentOS determines *how* to accomplish them using
whatever useful capabilities are available.

## The fundamental loop

```
OBJECTIVE
   ↓
UNDERSTAND        (inspect objective context, constraints, state)
   ↓
INSPECT CURRENT STATE
   ↓
DISCOVER CAPABILITIES
   ↓
PLAN              (determine the required operation / strategy)
   ↓
SELECT EXECUTION STRATEGY   (capability + adapter)
   ↓
DELEGATE / EXECUTE
   ↓
VERIFY            (real evidence, never assumed success)
   ↓
EVALUATE
   ↓
PERSIST STATE
   ↓
LEARN             (failure records, recovery outcomes, evidence)
   ↓
DETERMINE NEXT ACTION
```

AgentOS expands when a task requires more capabilities (register/discover) and
contracts when they are no longer needed (deregister/disable). It determines
the appropriate topology per objective — no permanent hard-coded hierarchy of
Supervisor → Builder → Researcher → Red Team. Those are possible
configurations, not the architecture.

## The questions AgentOS answers

- What needs to happen?
- What do we already know?
- What capabilities exist?
- What capabilities are required?
- What is the best available way to accomplish this?
- Who/what should execute it?
- Did it actually work?
- What evidence proves that?
- What should happen next?

## Layer separation

- **AgentOS** — the execution/orchestration substrate (this project).
- **OrgOS** (future, separate) — organizational strategy, priorities,
  governance, roles, organizational context. NOT implemented inside AgentOS.
- **Applications** (e.g. TrillionX, LegalOne) — consume AgentOS directly,
  or through OrgOS once it exists.

## Principles

1. **Working vertical slice over speculative breadth.** Every component must
   earn its place by serving the real loop.
2. **Verification is first-class.** An execution is not successful because an
   adapter returned text. Requested ≠ attempted ≠ executed ≠ verified.
3. **Failures are preserved, never hidden.** Errors are classified, recorded
   with evidence, retried only within bounds, and surfaced honestly.
4. **Provider neutrality.** The core contains zero provider-specific logic.
   Every provider integrates behind the adapter boundary.
5. **Inspect-first, additive, incremental.** No rebuilds of working systems,
   no premature distributed architecture, no undocumented claims.
6. **Lightweight by default.** Local-first, embedded persistence, CLI-first,
   fast startup, low resource usage. No Docker/Kafka/Redis/cloud until real
   load proves necessity.

## Success criteria (Part A — foundation)

A runnable project where a user can express an objective, watch AgentOS
inspect state, discover capabilities, select and execute through an adapter,
verify with real evidence, persist results across restarts, and recover from
a deliberate failure — all exercised by automated tests, not claimed.

## Non-goals (explicit)

- Not a chatbot, not a fixed multi-agent framework, not a project manager.
- Not an OrgOS implementation.
- Not Grok-only, not an OpenCode wrapper, not a LangGraph app, not merely
  an MCP server — though it is designed to interoperate with all of them
  through adapters when those integrations are genuinely built.
