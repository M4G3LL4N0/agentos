# FEDERATION.md — Federated Agent Fabric

Status: **VERIFIED** (tests + live runs, 2026-09-21).

AgentOS v0.3.0 adds a provider-neutral federation layer that routes work
between local capabilities (T0–T4) and remote control planes (T5) using a
single concept: the **executor cell**.

## Core concepts

- **Envelope** — `JobEnvelope` (driver + target + SLA/classification) and
  `ResultEnvelope` (facts, provenance, guard traces) are the only units that
  cross cell boundaries. No raw tool calls, no model output — just
  serialized, schema-checked packets.
- **Executor cell** — a named provider with a tier, an adapter, a
  `supported_operations` set, an invited/observable/isolated policy level,
  latency/data classes, and measured posture.
- **Tiers T0–T6** — LOCAL (T0–T4: platform, engine-owned, credential-owned,
  delegated, deployment boundary) and EXTERNAL (T5–T6: premium/tier-gated /
  fully remote). Routing prefers the cheapest tier that passes the gates.
- **Routing** — `resolve_route(job, cells)` scores every cell against
  capability match, gates (data class, latency, HS-external guard,
  postures), and policy level, then returns the best cell with a
  deterministic reason.
- **Plan-before-premium** — when the best cell is a premium T5, nothing is
  executed; a `plan_before_premium` object (plan, cost model, telemetry,
  approval requirements) is returned and execution waits for approval
  evidence.
- **A2A boundary** — a loopback A2A 1.0-style HTTP agent-catalog endpoint
  exposes federation capabilities, hides all
  subject/federation-management operations, and drops any capability whose
  health is not AVAILABLE/OK/READY.

## What is and is not executed

- `federate_route` = pure routing. `federate_submit` = persist + route +
  record. Both recorded in `federation_jobs` / `federation_results`.
- Execution inside the federation boundary runs `SIMULATED`/`INSPECT` only.
  `LIVE` is reachable **only** through the read-only `grokbot-office`
  bridge and only with approval evidence (`live`/`approved` grant via
  `gate_allows`). Everything else is `REFUSED` at the cell executor.
- No paid model calls, no quota use, no deployment, no writes to external
  providers — by construction and by enforcement.

## Surfaces

- Services: `federation_executors`, `federation_status`,
  `federate_route`, `federate_submit`, `federation_jobs`,
  `federation_telemetry`, `federation_packet`.
- CLI: `agentos federate {status|executors|route|submit|jobs|packet|telemetry}`.
- Storage: `federation_jobs`, `federation_results`, `federation_cells`
  (auto-migrated on connect).

## Verification evidence

- 40 `tests/test_federation.py` cases: envelopes, packets, tiers, scoring,
  routing, premium gating, HS-external guard, storage round-trips, A2A
  agent catalog, telemetry, CLI.
- Live CLI run: `federate status` shows `local-tools` (T1) and
  `grokbot-office` (T5, external, CONFIDENTIAL/BATCH, `CONSERVE` posture,
  runtime-probed AVAILABLE); `federate route --job echo` routes to
  `local-tools`; `federate submit` of an echo job → COMPLETED result with
  provenance.
## Federated fabric extension (IMPLEMENTED + VERIFIED, 2026-09-22, +57 tests)

- Multi-cell `ExecutorCell` extension (owner/authorized/usage/reset/
  roles/virtual counts/boundaries/persistence flags/transport/priority/
  verification/trust); credential-bearing configs rejected at
  construction; `federation.cells` loads as metadata only, never probed.
- `federate doctor|cells|health`, `federate route --explain`, top-level
  `route --job …` (explain default on), `capability find|explain`,
  `durable status`, `benchmark run|report`, `usage status` — all live.
- Trust gate on new remote families; premium/LIVE remote needs evidence.
- Suite: 385 tests green (`discover -s tests`), incl. restart persistence.
