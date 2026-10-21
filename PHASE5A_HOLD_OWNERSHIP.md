# PHASE 5A — AOS Hold Ownership

Question: is the admission-blocking hold an operator decision (must not be
touched) or a resource-generated state (must not be bypassed, but must be
understood and made visible)? Answer, proven from source and live state:
**resource-generated.**

## Origin (source: `autonomousos/governor.py`, `classify()`)

The ladder, in evaluation order:

| Condition | Result |
|---|---|
| `disk_pct ≥ 90` | CRITICAL / allow=False |
| **`disk_pct ≥ 80`** | **SEVERE / allow=False / cpu cap 0** |
| `mm_avail < 0.75` or (`load1 > cores×4` and cpu-bound) | CRITICAL |
| `mm_avail < 1.5` or (`load1 > cores×2.5` and cpu-bound) or **`cpu_busy > 97`** | **SEVERE** |
| below those | PRESSURE / GUARDED / HEALTHY (allow=True) |

No expiration field, no TTL, no operator override key: the state clears
only when the measured resources recover (disk < 80% **and** CPU/mem below
the SEVERE thresholds).

## Ownership evidence

- `resource.json`: `mode: SEVERE`, `allow_new_work: false`,
  **`notes: []`** (no operator annotation), `ts` fresh (< 30 s).
- `scale.recommendation`: `HOLD` with reason
  *"current host can still be managed adaptively"* — advisory resource
  text, not a human order.
- Triggers measured at 04:04: `disk_used_pct = 84.58` (≥ 80 lane),
  `cpu = 56.7%` (below 97 lane — the earlier 97.4 % CPU lane has
  self-cleared with the previous AudioOS worker exiting), `mm` comfortable.
  → **sole live trigger: disk ≥ 80 %**.
- Therefore: an *operator-visible* hold would carry `notes`/annotation; a
  *resource* hold carries thresholds. This is the latter. Per standing
  rules: never override it; surface its reason instead.

## What was done to make the reason visible (never bypass)

1. **Hive admission refusals** (`mesh/hive/execution.py`): both
   `resource_governor_hold` and `disk_warning_hold` now include
   `triggers: {mode, allow_new_work, disk_used_pct, cpu_usage, reasons[],
   state_ts}` — reasons are coded (`allow_new_work_false`,
   `mode_severe`, `disk_used_pct_80plus`, `disk_used_pct_90plus`,
   `cpu_usage_above_97`). Verified live:
   `reasons: [allow_new_work_false, mode_severe, disk_used_pct_80plus]`.
   The Hive CLI `submit` prints this JSON as before (exit code 3).
2. **Fleet doctor** (`mesh/health.py resource_contract_health()`): now
   exposes `aos_mode`, `aos_allow_new_work`, `capacity_constraints`,
   `disk_growth_bytes_per_day`, `disk_eta_to_warn_floor_s` alongside the
   existing band/storage fields.
3. **Snapshot `_meta.autonomousos` + `_meta.growth`** already carried
   mode/allow and the measured growth/ETA (Phase 4B/5A contract).
4. **Correctness tests** (3 added to `tests/test_phase4b_hive_gate.py`,
   server-only, 11 total green):
   - `test_restrictive_policy_survives_every_snapshot_state` — with AOS
     restrictive, absent / stale-CRITICAL / fresh-HEALTHY snapshots all
     still refuse (`resource_governor_hold`) and the registry stays empty:
     a snapshot can never be read as permission to override the hold.
   - `test_hold_refusal_carries_measured_triggers`
   - `test_fresh_critical_snapshot_refuses_even_when_resource_allows`
     (both gates fail closed).

## Clearing path (unblock real Hive worker admission)

- Requires disk < 80 % → **≈ 4.0 G free** at last measure. Zero bytes are
  authorized for reclaim today (see `PHASE5A_STORAGE_FORENSICS.md`).
- Operator choices, smallest blast radius first:
  1. `journalctl --vacuum-size=500M` (≈ 1.6 G, LOW risk, out of policy →
     needs approval),
  2. sign the Phase-4A 394-path manifest (~92 M),
  3. authorize a `.cache`/`/var/cache` review (~1.3 G combined, mixed),
  4. friendsos `ops/sprint` review (17 G — friendsos operator's call).
- Do **not** expect the tiered storage-janitor to do it: it already ran
  its heavy/emergency tiers within policy and reclaimed ~0 B.

Until then: every live submission is refused with the concrete triggers
above; Phase 5B live-worker steps report **BLOCKED**, not PASS, and never
bypass the gate.
