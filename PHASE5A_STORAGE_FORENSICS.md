# PHASE 5A — Storage Forensics

One bounded read-only pass per filesystem (no destructive cleanup, no
unauthorized reclaim). Numbers are from 2026-10-21 02:00–04:00 UTC+2 on
`vmi3635094` unless noted. Root filesystem: 102,888,095,744 B (95.8 GiB)
total, 83.8–84.6% used throughout the window (15.47–16.7 GiB free).

## Disk map (largest first, bounded du depth)

| Path | Size | Owner / actor | Reclaimable? | Risk |
|---|---|---|---|---|
| `/srv/noaerth/friendsos/ops/sprint` | 17,102 M | friendsos venture (active system) | NO without friendsos operator sign-off | UNIQUE — other venture's live data; protected by storage-janitor `*/friendsos/*` |
| `/srv/noaerth/realm-studio/workspace` | 15,973 M | REALM Studio (R23-15/R21-10 running now) | NO — active worktrees/cases | UNIQUE art/builds; partially protected (`workspace/game`, `case-R23-02`, `case-R21-06`) |
| `/home/portfolio` total | 12,141 M | portfolio user | Partial (see below) | Mixed |
| `/home/portfolio/.local` | 9,214 M | tool installs (opencode/godot/bin) | NO — active tooling | Running workers depend on it |
| `/srv/noaerth/startups` | 5,698 M | startup ventures | NO without operators | UNIQUE venture repos |
| `/srv/noaerth/state` (`audioos-music-division`) | 3,223 M | AudioOS | NO | active venture state |
| `/srv/noaerth/realmos` | 2,898 M | realmos | NO | venture data |
| `/var/log/journal` | **2,179 M** | root / systemd | **YES ~1.6 G** (vacuum to 500 M) — OUT OF POLICY | LOW (system logs; current boot retained) — needs explicit operator approval |
| `/srv/noaerth/worktrees` | 2,537 M | worktrees (artblitz 871 M, mira-nav 867 M, kimi-burst 657 M, paios 129 M) | NO — active git worktrees | history corruption if touched |
| `/srv/noaerth/agent-stack` | 2,331 M | agent-stack | NO (protected `*/src/*`, `*/state/*`) | UNIQUE |
| `/tmp/audioos-test-*` | ~1,400 M | AudioOS worker (files created minutes ago) | NO — **active scratch of running worker** | killing it breaks the live task |
| `/home/portfolio/.codex` | 932 M | codex sessions/auth | NO (protected) | sessions/auth |
| `/home/portfolio/.cache` | 731 M | portfolio | Partial — not covered by any current policy pattern | mostly regenerable but UNVERIFIED |
| `/srv/noaerth/pa-ios-runtime` | 650 M | paios | NO (protected `paios-runtime/runtime-*`) | runtime |
| `/srv/noaerth/pnpm` | 478 M | pnpm store | NO (protected `*/pnpm*`) | degrades installs |
| `/var/cache` | 616 M | apt/system | OUT OF POLICY (not janitor roots) | LOW |
| `/srv/noaerth/logs` | 157 M | system logs | OUT OF POLICY | LOW-MED |
| deleted-but-open files >100 M | **0 found** | — | — | — |

## Authorization inventory (what MAY be deleted)

1. **JanitorOS** (`/usr/local/bin/janitor`, agentos user):
   `deletion_autonomy: DISABLED`, `auto_cleanup: false`, mode
   `conservative`, roots **`/tmp` + `/var/tmp` only**, auto category
   `CACHE_PURGE` (moot while auto_cleanup=false). Live `approvals list`:
   *"no recommendations"*. `scan` (02:31, incremental, 4,600 files,
   206 MB seen): **0 approval groups, 0 reclaimable bytes** — and 10,848
   permission errors (agentos cannot read root/portfolio files), so it
   cannot even see most candidates. **Conclusion: zero bytes authorized
   through JanitorOS today.**
2. **Phase-4A tmp manifest**: 394 `CONFIRMED_DISPOSABLE` paths ≈ **92 MB**
   (`PHASE4A_TMP_APPROVAL_MANIFEST.md` / `/root/phase3/tmp-manifest.tsv`)
   — still awaiting operator sign-off. Not deleted, not counted as
   authorized.
3. **storage-janitor tiered policy** (operator-installed, portfolio user,
   thresholds 75 fast / 82 heavy / 88 emergency, protection list for
   friendsos/workspace/db/releases/codex-auth): already runs automatically
   via `noaerth-disk-watch.timer` (every 5 min). Ledger evidence:
   heavy fired 20:59 → **0 B reclaimed**, emergency fired 23:35 →
   `rc=76` min-interval, fast runs → no-op/`-48..-100 MiB` (disk grew
   during them) → fast no-op cooldown until 11:40. Watch log shows every
   later tick suppressed by 6 h same-tier cooldown. **The sanctioned
   policy is exhausted for now: its own candidates are below useful
   threshold.**
4. **Journal vacuum**: 2,179 M, largest single reclaimable block (~1.6 G
   to a 500 M cap) but **outside every cleanup policy** (JanitorOS roots,
   storage-janitor roots, auto-categories). Explicit approval required:
   `journalctl --vacuum-size=500M`.

## Gap to the AOS 80% disk threshold

Free-space must exceed 20% of 95.8 GiB (i.e. <80% used) for AOS
`classify()` to leave SEVERE on disk alone. Measured 04:00: 84.58% used →
**need ≈ 4.0 G reclaimed** (earlier 83.84% reading: 3.68 G;
`to_80pct_need_free_bytes` re-evaluated per publish).
Authorized reclaim today: **0 B**. Journal vacuum (1.6 G) + Phase-4A
manifest (92 M) + `.cache` review would be the minimum operator-approved
set; friendsos/sprint (17 G) is the only in-place pool large enough to
make the host comfortable again, and it is another venture's call.

## Growth measurement (now live)

`resource-growth.json` (bounded, 180 raw + 168 hourly caps) accrues one
sample per publish. 02:28–04:00: 49 raw samples, free 16.73 G → 15.88 G.
Snapshot `disk_growth` now reports the **measured** short rate
(`field_sources.disk_growth = agentos.growth_history`, ≈ −3.3 GB/day at
last read; janitor's own forecast still `insufficient data`), ETA to the
20 GiB warn floor = 0 (already below it: 15.47 G free). UNKNOWN is
reported until ≥3 samples span ≥600 s — never as 0.

## Files written by this phase

- `/var/lib/agentos/resource-growth.json` (0644, accrues via `*/2` cron)
- Server hooks: `mesh/hive/execution.py` (`.bak-5a-20261021`),
  `mesh/health.py` (`.bak-5a-20261021`), `control/collector.py`
  (`.bak-5a-20261021`), `tests/test_phase4b_hive_gate.py` (3 new tests)
- Recovery bundle: `/var/backups/agentos/agentos-full-recovery.bundle`
  (747,082 B, 44 commits, tip `6f1ec2b`, verified by bare clone)
