# PHASE 5A — Test Report

## Suites

| Where | Command | Result |
|---|---|---|
| local (`/Users/matador/startups/agentos`, macOS) | `python3 -m unittest discover -s tests` | **952 ran, OK, 11 skipped** (8 `test_phase4b_hive_gate` + 3 `test_collector_importable` are server-only by design) |
| server (`/srv/noaerth/agentos`, sudo -u agentos, `AGENTOS_HOME=/var/lib/agentos`) | same | **946 ran, OK** |
| server autonomousos | `python3 -m unittest discover -s tests` (in `/srv/noaerth/autonomousos`) | **86 ran, OK** |

## New tests added in Phase 5A

### `tests/test_resource_contract.py` (+9, local + server)

`TestGrowthHistory`:
- `test_record_and_measure_short_rate` — 6 real samples shrinking 1 GiB/10 min
  → short rate ≈ −144 GiB/day (delta-tolerant), positive ETA to warn floor.
- `test_insufficient_samples_is_unknown_not_zero` — no history, janitor
  forecast `insufficient data` → `disk_growth == "UNKNOWN"` and listed in
  `_meta.unknown_fields` (never 0).
- `test_zero_growth_is_distinct_from_unknown` — flat real samples →
  `disk_growth == 0.0` while `eta_to_warn_floor_s is None`; not UNKNOWN.
- `test_measured_rate_precedes_janitor_forecast` — with history present,
  `field_sources.disk_growth == "agentos.growth_history"` and the value
  differs from the injected forecast.
- `test_raw_and_hourly_caps_hold` — 230 inserts over 20 days stay within
  `GROWTH_RAW_CAP=180` / `GROWTH_HOURLY_CAP=168`.
- `test_unknown_disk_free_records_nothing` — all disk sources dead
  (janitor None, governor empty, statvfs raising) → `disk_free == UNKNOWN`,
  publish records **no** growth sample (`history == {}`).
- `test_publish_records_sample` — one publish → exactly one
  `[epoch, free]` row.
- `test_eta_zero_when_already_below_floor` — free < warn floor with a
  supported negative rate → `eta == 0.0`.
- `test_corrupt_history_tolerated` — corrupt `resource-growth.json` reads
  as `{}` and the next record recovers cleanly.

### `tests/test_collector_importable.py` (+3, server-only)

- `test_import_returns_promptly_in_subprocess` — child imports
  `control/collector.py` with a 15 s timeout; a module-level loop
  regression **fails** (`subprocess.TimeoutExpired` → assertion) instead of
  hanging the suite; asserts `has_main=True`.
- `test_plain_import_spawns_no_collector_process` — in-process import
  returns < 10 s, `/proc` count of `collector.py` processes grows by ≤ 1
  (the systemd instance).
- `test_main_guard_present_in_source` — `def main():` + `__main__` guard
  strings present.

Note: the first draft asserted `live.json` mtime equality and failed
against the **live** collector's own 2 s writes (test defect, not product
defect); rewritten hermetically in `6f1ec2b`. Prompt-return is the
authoritative signal — the loop body is the only writer.

### `tests/test_phase4b_hive_gate.py` (+3, server-only, 8 → 11)

- `test_restrictive_policy_survives_every_snapshot_state` — AOS
  `allow=false/SEVERE` + {absent snapshot, stale CRITICAL snapshot, fresh
  HEALTHY snapshot} → all three refuse `resource_governor_hold`; Studio
  registry rows remain `[]`. **The override rule under test: a snapshot can
  never be read as permission when policy is restrictive.**
- `test_hold_refusal_carries_measured_triggers` — refusal payload carries
  `triggers.reasons` incl. `disk_used_pct_80plus`, `mode_severe`, and
  `disk_used_pct >= 80`.
- `test_fresh_critical_snapshot_refuses_even_when_resource_allows` —
  fresh CRITICAL snapshot + permissive resource → `serveros_band_critical`,
  registry untouched (both gates fail closed).

## Live verification (not simulated)

- Growth store accruing via the existing `*/2` root cron: 51 samples by
  02:09, `disk_growth` measured (−13.1 GB/day over the active-worker
  churn window), `field_sources.disk_growth = agentos.growth_history`,
  `eta_to_warn_floor_s = 0.0` (free already below the 20 GiB floor).
- Hive refusal live-rendered: `triggers.reasons =
  [allow_new_work_false, mode_severe, disk_used_pct_80plus]` at
  disk 83.46%.
- Fleet doctor live: `aos_mode: SEVERE`, `aos_allow_new_work: false`,
  `capacity_constraints: [band_pressed, autonomousos_allow_new_work_false]`.
- Collector: import 0.04 s, service restarted (PID 3485820), `live.json`
  fresh at restart+4 s.
- Recovery bundle: bare-clone verified (44 commits reachable, tip
  `6f1ec2b` == local HEAD) then clone removed.

## Known limitations (explicit)

- `disk_growth` remains UNKNOWN for the first ~4 minutes after a store
  wipe (by design: <3 samples).
- Janitor forecast stays `insufficient data` — its own sampler has 0
  samples; our store does not feed JanitorOS (no cross-system write).
- Growth ETA only ever covers the janitor warn floor (20 GiB); it does not
  model the AOS 80% disk lane (that threshold is in
  `PHASE5A_HOLD_OWNERSHIP.md` as a byte gap, not a time estimate, because
  reclaim events are discrete, not trended).
- 11 local skips are structural (server-only files), not failures.
