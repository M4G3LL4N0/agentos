# Phase 4B — Governor Integration (ServerOS)

Status: **PASS** (implementation + verification complete 2026-10-20T22:00Z).
Owner: ServerOS. Branch `main` local commits only (never pushed).

## Mission

Connect the existing AgentOS host governor + JanitorOS to AutonomousOS and
Hive Coding so the server can run more productive parallel workers without
repeating CPU saturation, log amplification, or disk incidents — without
creating another scheduler, modifying another agent's active Hive
execution/AgentWorld worktree, or interrupting productive workers.

## Architecture (what was built)

**One published contract, many O(1) readers.** No new scheduler, no new
database, no telemetry daemon.

1. **Snapshot publisher** (`src/agentos/resource_contract.py`):
   `agentos governor snapshot --publish` builds a bounded (~2.3KB) JSON
   document at `/var/lib/agentos/resource-snapshot.json` (atomic tmp+rename,
   chmod 0644 for cross-user readers) from EXISTING authorities only:

   | field | primary source | fallback |
   |---|---|---|
   | cpu_usage, load_averages, available_memory, memory/disk PSI | AutonomousOS `state/resource.json` | /proc, os.getloadavg |
   | disk_free, disk_growth, storage_admission | `janitor space status --json` | governor, statvfs |
   | active_workers, queued_demand | autoscheduler `demands` table (read-only URI) | — |
   | swap_pressure | /proc/meminfo | — |
   | active_processes, stacking/stuck-scan signals | one bounded /proc scan | — |
   | provider cooldowns | REALM Studio `kv provider_wait:*` | — |
   | resource_band | ServerOS host governor (hysteresis lives there) | — |
   | recommended_worker_capacity | derived (policy below) | — |

   Unreadable source ⇒ explicit `"UNKNOWN"` + `_meta.unknown_fields` +
   `_meta.field_sources` provenance. Never fabricated, never optimistic.

2. **Capacity policy** (advice only; never raises an existing cap):
   at most **2 additional heavyweight coding workers**;
   HEALTHY+allow+ALLOW→2, PRESSED/RECOVERING→1 (slowed, hysteresis-held band),
   CRITICAL/UNKNOWN/AOS-hold/storage DENY-or-UNKNOWN/stacking/stuck-scan→0.

3. **Four-question admission** (`src/agentos/worker_admission.py`,
   `agentos governor admit-worker`): `can_start`, `budget`,
   `constraining_resource`, `retry_after_s`. Provider rate limits evaluated
   **independently** of CPU (checked first). Stale (>180s)/missing snapshot
   fails closed. Every decision audited as `resource.admission_decided`
   (new `EventType.RESOURCE_ADMISSION_DECIDED`).

4. **Scratch leases** (`agentos governor lease register|complete|list`)
   wrap the EXISTING JanitorOS mechanism (`janitor space scratch
   register/complete/reap`) and keep the full ServerOS lease record
   (task_id, venture_id, workspace, scratch_root, storage_budget,
   creation_time, owner, lease_state, retention_policy). disposable → reap
   requested on completion; rollback_critical/evidence → retained.

5. **Hive hook** (surgical additive edit, server tree
   `mesh/hive/execution.py`, backup `execution.py.bak-4b-20261020`):
   `serveros_snapshot_gate()` inside `submit_manifest` (before the existing
   governor gate, which is untouched): fresh CRITICAL → refuse
   (`serveros_band_critical`); fresh PRESSED/RECOVERING → defer LOW-level
   manifests only (`serveros_low_priority_deferred`; HIGH/CRITICAL and
   default MEDIUM pass); absent/stale → skip this gate entirely — the
   pre-existing gates stay authoritative either way.

6. **Observability views** (additive, server tree):
   - Control Tower: `"serveros"` panel in `control/state/live.json`
     (collector restart via portfolio user-unit `noaerth-ui-collector`).
   - Fleet doctor: `resource_health` top-level key in `mesh doctor` output.
   - API: `GET /resource` (O(1) read of the published file).
   - CLI: `governor snapshot [--publish|--read]`, bounded summary lines.

7. **Cadence**: root cron `*/2 * * * * sudo -u agentos -H /usr/local/bin/noaerth
   agentos governor snapshot --publish >> /var/log/agentos/resource-snapshot.log 2>&1`
   (~6 bounded lines per fire; ~4KB/day).

## Evidence

- **Tests**: 940/940 green on server (`unittest discover`), 940 local-equivalent
  (932 repo + 8 server-only integration; the 8 skip locally by design).
  New: `tests/test_resource_contract.py` (20), `tests/test_worker_admission.py`
  (20), `tests/test_pressure_response.py` (9 scenarios + bounded-retry matrix),
  `tests/test_phase4b_hive_gate.py` (8, server-only against real
  `mesh.hive.execution`).
- **Live first publish**: 14/15 contract fields populated from real sources;
  only `disk_growth` UNKNOWN (janitor forecast: insufficient data — honest).
- **Live admission** under the real host state: `autonomousos_hold`
  (their primary brake, allow_new_work=false) → capacity additional=0,
  refused with bounded retry 60s. ServerOS never overrides their hold.
- **Integration**: LOW deferred (absent from registry), MEDIUM submitted
  once, fresh CRITICAL refused with registry digest unchanged, duplicate
  refusal unchanged, no subprocess side effects on any admission path.
- **Before/after** (P7 before 20:40:45Z → after 21:58:27Z):

  | metric | before | after |
  |---|---|---|
  | load 1/5/15 | 3.01 / 3.36 / 3.77 | 2.45 / 2.25 / 3.60 |
  | processes | 201 | 194 |
  | zombies / stuck finds | 0 / 0 | 0 / 0 |
  | active leases | 0 | 0 |
  | studio RUNNING | 0 | 0 |
  | governor band | PRESSED | PRESSED (hysteresis held, no oscillation) |
  | agentos service | ok, NRestarts=0 | ok, NRestarts=0 |
  | disk free | 16.0 GB | 16.8 GB |

  No worker was started or killed by this phase; the scheduler and Studio
  registry are unchanged apart from test fixtures in throwaway temp dirs.

## Honest findings

- AOS governor currently reports **SEVERE / allow_new_work=false** —
  the primary brake; capacity 0 and admission refusals are that hold
  working as designed, not a ServerOS failure.
- `disk_growth` stays UNKNOWN until JanitorOS forecast has ≥2 samples.
- My verification test run wrote `/srv/noaerth/autonomousos/state/admission-backoff.json`
  (failures=1, advisory only; clears on next successful admission). Documented, not hidden.
- Importing `control/collector.py` executes its main loop (module-level
  `while True`) — a duplicate live.json writer was spawned during an inline
  test, identified by pid start-time, killed within ~3 min; the real
  portfolio systemd unit was then restarted cleanly. No live.json damage
  (atomic writes). Do not import collector.py casually.
- Sub-second-fresh snapshots were initially skipped by the gate (int-clock
  truncation ⇒ negative age). Found by the live integration test, fixed
  (float clock), covered by regression tests.
- Storage janitor cron runs as `portfolio` (existing authority, pre-dates
  this phase; not changed — noted for the standing janitor-identity review).
- Root docs `ARCHITECTURE.md`/`BUILD_MAP.md`/`CURRENT_STATE.md`/`DECISIONS.md`
  on the server tree were touched by another actor (~17:44–22:18 local);
  this phase did not edit them. Pre-deploy drift check confirmed 147/156
  tracked files identical to git HEAD; the 9 diffs were exactly this
  phase's own un-deployed changes.

## Files

- Local repo (git): `src/agentos/resource_contract.py`,
  `src/agentos/worker_admission.py`, additive edits in `models.py`
  (EventType), `services.py` (3 wrappers), `cli.py` (4 verbs),
  `api.py` (`/resource`), tests (4 files), `NOAERTH_RESOURCE_CONTRACT.json`.
- Server tree (backed up `.bak-4b-20261020`): `mesh/hive/execution.py`,
  `mesh/health.py`, `control/collector.py`.
- Runtime: `/var/lib/agentos/resource-snapshot.json` (644),
  `/var/lib/agentos/scratch-leases.json`,
  `/var/log/agentos/resource-snapshot.log`, root crontab line.
- Releases: safe-deploy `20261020T210429Z`, `20261020T210909Z`,
  `20261020T214518Z`, `20261020T215130Z` (code tar + db copy each).

## Rollback

- Server hooks: restore any `*.bak-4b-20261020` over the live file and
  restart the affected unit (`noaerth-ui-collector` for collector; hive
  gate is import-per-invocation so no restart needed).
- AgentOS code: `./deploy/safe-deploy.sh` auto-rollback triggers on failed
  health gate; manual rollback via release tarball + db snapshot under
  `/var/backups/agentos/releases/`.
- Cron: remove the one crontab line. Snapshot file is inert data.

## Rules honored

No new scheduler or task DB; no paid inference; no push; no destructive
cleanup; no worker started/stopped/killed; no other agent's active task or
worktree touched; bounded outputs everywhere; UNKNOWN stated explicitly;
existing authorities (AOS governor, JanitorOS, Studio, mesh caps) reused,
never replaced.
