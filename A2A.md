# A2A.md — Agent-to-Agent (A2A 1.0 boundary)

Status: **IMPLEMENTED + VERIFIED** (loopback, tests).

## Scope

A2A here is a *boundary*, not a transport: AgentOS can expose federation
state as an external-agent catalog and deliberately limits what leaves the
trust domain. There is **no** inbound execution of foreign tasks and no
outbound invocation of external agents.

## What exists

- **Agent catalog endpoint** (loopback HTTP, `A2AHttpServer`) —
  `GET /agents`: discovers executor cells, returns each as an agent card
  (id, provider, tier, latency/data classes, supported operations,
  capability kind, health status) in A2A 1.0-compatible `AgentCard`
  shape. `GET /agent/{id}` returns a single card.
- **Exposure rules**:
  - Health-gated: only cells whose health is AVAILABLE / OK / READY are
    exposed. Existing bridges probed as MISCONFIGURED/DOWN/UNAVAILABLE are
    not advertised.
  - Operation-filtered: every subject/federation-management path
    (`submit`, `route`, `jobs`, `executors`, `approve`, `usage:set`,
    `usage:reset`, ...) is **forbidden** from exposure and dropped from
    advertised agent operations.
  - HS-external guard: any cell that could carry HIGHLY_SENSITIVE data is
    never advertised as an external target.
- **Packets**: TASK/RESULT round-trips are enforced through
  `TaskPacket`/`ResultPacket` (A2A 1.0 message shape, schema-checked).
  Sending side renders minimal, safe, authenticated context fields.

## Status of the broader A2A 1.0 protocol

- Implemented: agent discovery (catalog), message envelope round-trip,
  standard error frame (invalid-message, not-implemented).
- Not implemented: task orchestration across *remote* agents, client
  discovery of remote A2A servers, streaming, push notifications. These
  remain **NOT AVAILABLE** (designed extension points only).

## Honesty markers

- The catalog is loopback-only and unauthenticated by design — bind to
  loopback, trusted networks only (matches the existing API posture).
- "Agents" are executor-cell cards, not generative models. Nothing in
  AgentOS fabric claims a live multi-agent conversation.

## Tests

A2A coverage in `tests/test_federation.py`: agent cards hide forbidden
operations, miss fields are defaulted, missing id rejects, health filters
advertised cells, packet round-trips (TASK/RESULT), server 404/406s.
## A2A v1.0 remote interop (IMPLEMENTED + VERIFIED, 2026-09-22)

- `src/agentos/a2a_v1.py`: well-known card discovery, strict v1 parsing
  (0.x shapes rejected), `A2A-Version: 1.0` negotiation, JSON-RPC
  `SendMessage/GetTask/CancelTask`, typed errors, secret-values-never-sent
  boundary, bounded polling. No streaming (honest UnsupportedOperation).
- Loopback peer serves the v1 surface alongside legacy endpoints (both
  green); remote peers register as UNTRUSTED HS-capped `a2a-remote`
  cells and execute SIMULATED/INSPECT over v1 with bounded timeouts.
- AgentOS card (`a2a card`, API `/agent-card` behind
  `config.a2a.serve_card`, default OFF): echo skill only — no shell, no
  filesystem, no browser internals, no credentials.
- `a2a peers|card|test|register` live; `a2a test` runs the full
  discover→submit→status→artifact loop. `tests/test_a2a_v1.py` (11).
