# GrokBot Usage Policy

Rules-first, cost-honest routing for the Grok provider under low-usage mode.

## Core policy: Grok is a late escalation, never first

`grok_routing_policy()` / `triage()` in `src/agentos/efficiency.py`:

- **Disfavored** for coding/refactor/repo/test/unit-suite objectives —
  `grok_bias=DISFAVORED`; CHEAP_MODEL or PREMIUM wins unless explicitly
  escalated.
- **Favored** (`grok_bias=FAVORED`) only when the objective uniquely needs a
  persistent cloud computer, authenticated browser, or durable supervisor
  identity — abstraction over the local shell/tools.
- Resolution `GROKBOT` is returned *only* when no cheaper route can satisfy
  the request. Cache/workflow/deterministic routes always short-circuit first.

## Boundary (matches GROKBOT_PROVIDER.md)

- GrokBot is a remote, read-only-by-default bridge: 8 allowlisted commands;
  forbidden operations are hard-refused.
- `verified_live` stays `False` until a live loop is explicitly authorized and
  recorded — no fabricated health.
- Provider/model metadata recorded verbatim from the caller; AgentOS never
  fabricates prompt-cache or model versions.

## CLI surface

- `agentos efficiency triage "<objective>"` prints the resolution and
  `route_matched` — see `agentos efficiency triage --help`.
- No Grok task execution happens automatically; LIVE execution requires an
  explicit authorized objective and a bounded, recorded run.

## Facts

- Triage tests pin: cache-hit > workflow > deterministic > cheap > premium,
  and `GROKBOT` only for the persistent-supervisor use case.