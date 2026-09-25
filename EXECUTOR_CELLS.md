# EXECUTOR_CELLS.md — Cell model and fleet

Status: **IMPLEMENTED + VERIFIED**.

## Cell anatomy

An `ExecutorCell` is a plain dataclass. Fields:

- `id`, `name`, `provider` — stable identifiers and capability provider key.
- `tier` — T0..T6 (see tiers map in `federation.py`).
- `kind` — TOOL / AGENT / GRAPH / SERVICE.
- `adapter` — adapter registry key that executes the cell's operations.
- `supported_operations` — capability ids the cell can serve.
- `policy_level` — INVITED / OBSERVABLE / ISOLATED.
- `external` — bool; True ⇒ T5/T6 boundary cell.
- `latency_class` — LOW / MEDIUM / BATCH.
- `data_class` — PUBLIC / CONFIDENTIAL / HIGHLY_SENSITIVE.
- `health` — `HealthStatus` (AVAILABLE/OK/READY/MISCONFIGURED/UNAVAILABLE/DOWN).
- `posture` — measured usage posture without publishing account-specific values.
- `balance`, `last_probe_at`.

## Fleet on the reference host (measured, 2026-09-21)

| cell | tier | kind | adapter | external | latency | data class | health | posture |
|------|------|------|---------|----------|---------|------------|--------|---------|
| local-tools | T1 | TOOL | (capability-resolved) | no | MEDIUM | CONFIDENTIAL | OK | n/a |
| grokbot-office | T5 | AGENT | grokbot-office | yes | BATCH | CONFIDENTIAL | AVAILABLE | measured posture; value omitted |

- Configured peers from `federation.peers` are appended on refresh.
- Cells persist to `federation_cells` and are re-hydrated on startup
  (so the fleet survives restarts without re-probing).

## Health probing

- `_probe_cell_health` runs each cell's adapter probe honestly:
  - `local-tools` is OK trivially (engine-owned adapters present).
  - `grokbot-office` probe = repo present + `tsx` present + read-only
    `validate` exits 0 ⇒ AVAILABLE; partial ⇒ MISCONFIGURED.
- Posture for grokbot-office is read via the read-only `mode` command and
  parsed into `cell.posture` when `read_posture` is enabled
  (config `federation.grokbot_office.enabled` / `read_posture`, both
  default True).

## Execution dispatch

`_execute_cell` resolves the adapter by the *target capability* (capability
→ registry adapter), never by the cell's `adapter` label. LIVE is accepted
only for the read-only `grokbot-office` bridge behind approval evidence;
every other cell/operation is REFUSED inside the federation boundary.