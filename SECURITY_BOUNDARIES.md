# SECURITY_BOUNDARIES.md — Federation trust boundaries

Status: **IMPLEMENTED + VERIFIED** (tests + live enforcement).

## Principle

The federation fabric implements *boundaries*, not *trust*: nothing crosses
a cell boundary until a schema-checked envelope passes hard gates, and
nothing with side effects runs in the federation without explicit,
evidenced approval.

## Boundaries

1. **Data classification** — `HIGHLY_SENSITIVE` (HS) work never routes to
   `external` cells (HS-external guard). Videos/MATERIAL/CAD content stays
   local (PUBLIC/CONFIDENTIAL boundary honored per job `dataClass`).
2. **Latency** — a job's `latencyClass` gates which cells may serve it;
   BATCH-only for the remote bridge, so interactive work never blocks on
   the control plane.
3. **Execution** — inside the federation boundary only SIMULATED/INSPECT
   run by default. LIVE is accepted solely for the read-only `grokbot-office`
   adapter and only with approval evidence (`gate_allows` with
   `live`/`approved`). Every other adapter is REFUSED before dispatch.
4. **Command allowlist** — the bridge maps exactly 8 read-only commands;
   `FORBIDDEN_GROKBOT_COMMANDS` always trips. No shell interpolation,
   no passthrough argv.
5. **Secret containment** — untrusted params and job fields are scanned
   with `contains_secret` (improved unanchored patterns for `sk-*`,
   `gh*_*`, `xox*`, `crsr_*`, `AKIA*`, `AIza*`, plus field-name hints
   `api_key`/key/token/secret/password). Bearing plans envelope checks on
   both input and provenance.
6. **Health honesty** — cells whose probe is MISCONFIGURED/UNAVAILABLE/DOWN
   score zero at routing and are hidden from the A2A catalog. Runtime
   failures surface honestly (failures recorded, no masquerading).
7. **A2A exposure** — the catalog exposes only
   AVAILABLE/OK/READY cells and drops every
   subject/federation-management operation and any cell carrying HS
   capability.

## Verified enforcement points

- `tests/test_federation.py`: credential-in-envelope rejection, malformed
  job rejection, HS-external exclusion at routing, premium hold
  (`executed=False` without approval), LIVE refusal for non-bridge
  adapters, forbidden command guard, A2A forbidden-operation hiding.
- Live: LIVE grokbot-office call without authorization was refused;
  premium submit did not execute without approval.

## Operational caveats (unchanged from Part A)

- Loopback API/MCP/A2A are **unauthenticated** — bind to loopback, trusted
  networks only.
- The repo keeps secrets out by guardrail (patterns), not by secret
  storage; do not put real keys in job params or logs.
## Fabric trust update (IMPLEMENTED, 2026-09-22)

- Trust levels LOCAL_TRUSTED/INTERNAL_TRUSTED/APPROVED_EXTERNAL/UNTRUSTED;
  public A2A/MCP discovery defaults UNTRUSTED; HS never leaves trusted
  local cells; CONFIDENTIAL+ never reaches UNTRUSTED externals.
- Approval classes READ_ONLY…IRREVERSIBLE assigned by task/policy;
  cells declare maxApprovalClass (default READ_ONLY) and cannot reduce it.
- Cell configs with credential-looking values are rejected, never stored.
- MCP approval registers unavailable metadata (transport DEFERRED), never
  a live executor. No secrets in JobEnvelope (rejected at ingest).
