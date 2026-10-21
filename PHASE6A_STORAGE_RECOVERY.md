# NOAERTH Phase 6A — Storage Recovery

Date: 2026-10-21 (server CEST) · AgentOS tip `6afdf68` · server `vmi3635094`
Role: Principal Linux SRE / storage & release engineering. Read-only
forensics + one authorized-maintenance invocation; **zero unauthorized
deletions, zero hold bypasses, zero paid calls**.

## Outcome

**Net bytes reclaimed by this phase: 0** (nothing eligible under existing
authority). **Precise remaining blocker delivered**: disk 85.76% used,
need **5.52 GiB** freed for <80%; a tiered, byte-counted operator approval
menu (below) totals up to 6.1 GiB and closes the gap. AOS hold stays
untouched; admission remains legitimately BLOCKED.

## P0 — Live forensics

### Filesystem & pressure
| Metric | Value |
|---|---|
| Disk | 102,888,095,744 B total · 88,238,874,624 used (85.76%) · 14,632,443,904 avail |
| Bytes to free for <80% | **5,928,398,029 (5.52 GiB)** |
| Inodes | 11% used — not a constraint |
| Memory | 9.8 GiB available, PSI memory 0 — healthy |
| CPU | load ~5.8 (6 cores), PSI cpu avg10 10.7, iowait bursty (one sample 41%) |
| Swap | 445 MiB of 8 GiB — no thrash |

### Authoritative users / repos / services
- **root**: sys cron (`governor snapshot --publish` */2, `noaerth-agentos-backup` 03:17), journald/rsyslog owners.
- **agentos**: runtime user for `/var/lib/agentos` state + AgentOS suite; publishes snapshot via root cron.
- **portfolio**: REALM Studio loop (pid 977335, `studio.py loop`), Control-Tower collector (pid 4132184, systemd user unit), `paios.service`, `janitor-db-downsample` cron (05:17), `noaerth-cp` cron (6h).
- **Git authority**: local Mac repo (`6afdf68`, 40 unpushed commits) + verified server bundle. `/srv/noaerth/agentos` on server is a **deploy tree, not a git repo**. Venture repos portfolio-owned.
- **Worker execution authority**: REALM Studio `studio.py loop` (single instance). **Admission authority**: AOS governor (`classify()`) + ServerOS snapshot gate — not Studio, not janitor.

### Duplicate-authority check (P2 requirement)
Single collector (1), single studio loop (1), **zero reactor processes**
(the Phase-8 reactor is not running — not duplicated, also not active).
No host_governor daemons. ✓

### Exact SEVERE reason (not assumed)
`/srv/noaerth/autonomousos/governor.py:281 classify()` ladder:
`disk_pct >= 90 → CRITICAL`; **`disk_pct >= 80 → SEVERE, allow=False, cap=0`**.
Live sample: disk 85.76%, mem 9.8 GiB, cpu_busy ~19–65% (psi_cpu 10.7 < 50),
load 5.8 (< cores×1.5×cpu_bound). **Disk is the sole triggered rung**;
`notes=[]`; resource.json history shows continuous SEVERE (80/80 recent
samples). Next rung up is CRITICAL at 90% — 4.24 points away.

### Growth attribution (P2)
- disk_growth store **operational**: 180 raw samples (capped) + 20 hourly; last 6 h window free **+2.07 GiB** (other actors freed space since 16:38 UTC, when free was 12.38 GiB).
- **Dominant growers** (mtime-evidenced, ≥100 M, last 3 d):
  1. REALM Studio `workspace/` — **17 byte-identical 195,130,408 B `REALM-source-*.tgz`** (sha256 verified equal) across case dirs incl. 9 `R21-10-*-preserved-*` snapshots → ~2.91 GiB duplicate content; workspace grew 15.9→19 GiB since Phase 5. Owned by Studio tasks (R21-10 saga, R23-07/12/15).
  2. **`paios.service`** (portfolio user, up 2 d): ~570 log lines/min into user journal (14,983 user@1001 lines/30 min in system journal) + unbounded `tee -a /srv/noaerth/logs/paios-runtime.log` (191 MB, `STATUS=FAILED` loop) → journal rotates 128 MB files every ~3.5–7.5 h ≈ **~0.4 GiB/day**. Excessive logging **has not stopped**.
  3. rsyslog: +~65 MB/day into current syslog (226 MB); weekly `rotate 4` policy in force (syslog.1 772 MB is **in-policy retention**).
  4. `/home/portfolio/.local/state/noaerth-cp/cp.db` 438 MB (6h snapshot rewrite cycle).
- Deleted-but-open: **16.6 MiB total** — negligible, not a reclaim path.

### Infrastructure inventory
Timers: standard Ubuntu set + audioos venture timers (other venture). Crons: as listed above; backup script has **bounded retention** (`RETENTION` prune, P2 ✓). JanitorOS: operator binary `/usr/local/bin/janitor`, **deletion autonomy DISABLED**, approvals empty, anomalies none, fresh read-only scan: 89,807 files/1.7 GiB across its 3 roots, **0 eligible artifacts**. Studio paused (`paused=True` — operator pause, not cleared). Scratch leases: registry absent at default path, `active_leases=0` — **no task currently uses the scratch-lease system** (Studio worktrees predate it). Provider cooldown: UNKNOWN (studio db read busy at sample time).

## P1 — Reclaim accounting (authoritative vs measured vs needed)

| Quantity | Value |
|---|---|
| **GiB reclaimed this phase** | **0** |
| GiB currently available | 13.63 GiB |
| **GiB needed by admission policy** | **5.52 GiB** (disk <80%) |

Actions considered and their authority:
- **Journal vacuum**: journald config is **all defaults — no `SystemMaxUse`, no `MaxRetentionSec`** → current 2.4 GiB (archived+active) is *within* the 10%-of-fs default ceiling (9.6 GiB). Vacuum = **new cleanup → authorization required**. Active-vs-accounted: `--vacuum-size` only removes archived journals; a `--rotate` first rolls the active file; ~128 MB active remains. Prepared command below.
- **syslog.1 (772 MB)**: inside `rotate 4 weekly` policy → deleting early **violates retention** → only with explicit waiver.
- **JanitorOS**: no eligible approved cleanup exists (scan verified) → nothing to run.
- **Accidental invocation (disclosed)**: `janitor-db-downsample --help` — the script has no help guard and executed its **daily-authorized compaction** (same command as portfolio cron 05:17) against `portfolio ~/.local/state/janitoros/janitor.db`: kept 96,136 latest-per-subject rows, final DB=66 MiB (file measured 70 MB, healthy). Idempotent maintenance op; **no data loss, 0 net bytes** (db was already compacted by this morning's cron).
- Protected (never touched): active Studio cases R21-12/R23-07 + `game/`, friendsos 17 GiB, audioos state 3.2 GiB, ventures source, all databases, releases, backups, preserved evidence, `/tmp` active scratch.

### Operator approval menu (exact commands, byte-counted)

| # | Operation | Frees | Authority needed |
|---|---|---|---|
| A | `journalctl --rotate && journalctl --vacuum-size=400M` | **~1.86 GiB** | Administrator (no configured ceiling exists) |
| B | `apt-get clean` | **~0.42 GiB** | Administrator (regenerable cache) |
| C | `rm /home/portfolio/.local/state/janitoros/janitor.db.pre-downsample.bak` | **~0.94 GiB** | Administrator (superseded backup; current db 70 MB verified) |
| D | REALM tarball hardlink-dedup: for each `find … -name REALM-source-*.tgz -links 1` keep newest per content-hash, `ln -f` others to it (paths preserved, evidence intact) | **~2.91 GiB** | REALM Studio owner + operator (other-venture workspace) |
| E | Retire signed-off worktrees `realm-artblitz-candidate` (871 M) + `realm-mira-nav` (867 M) + `kimi-burst` (657 M) — lsof-verified idle | ~2.3 GiB | Venture owners (evidence) |
| F | Early-delete syslog.1 | 0.72 GiB | Retention waiver (policy breach) |

**A+B+C+D = ~6.13 GiB → closes the 5.52 GiB gap with margin** (recommended).
A+B+C alone = 3.22 GiB — **insufficient**. Note: even after A, journals
regrow ~0.4 GiB/day while `paios.service` floods — see P2 fix list.

## P2 — Permanent growth control (no new scheduler created)

Verified: disk_growth reporting operational (fields wired into collector
panel); backups bounded (RETENTION prune); test/tmp dirs small; worktree
retirement evidence exists (preserved-* dirs); no duplicate reactor/
collector/studio; janitor single-instance lock present.

**Gaps (proposals only — configs are operator-owned):**
1. journald: set `SystemMaxUse=400M` (+ existing built-in rotation) — this is journald's own control, not a new scheduler; stops unbounded journal growth permanently.
2. `paios.service`: venture-owned — fix failing loop (`STATUS=FAILED`, cycles=0) and bound `paios-runtime.log` (logrotate entry). ~0.4 GiB/day + 65 MB/day rsyslog pressure trace back here.
3. REALM Studio: stop embedding full source tarballs per case dir (dedup or build-once artifact store) — 2.91 GiB duplicate today, grows per task.
4. `cp.db` 438 MB diurnal cycle: confirm noaerth-cp's own retention (6h snapshots rewrite full db).

## P3 — Admission verification

Snapshot fresh at read (age ~1.4 s, publisher cron */2 working). Ladder +
live inputs re-derived by hand: **SEVERE from disk alone; allow_new_work=false
correct; cap 0**. Studio `paused=True` (operator pause — not cleared).
Provider cooldown UNKNOWN (honest). Task ownership: R21-12 (opencode
free route) + R23-07 (prepare) under paused dispatch. **No manual band
change, no allow_new_work override, no hold clear performed.** The hold is
resource-generated: the only transition path is disk <80% → next
`classify()` rung falls through to GUARDED/PRESSURE (mem 9.8 GiB, load
fine) → `allow_new_work=true`, provable from code + measured inputs.
Until menu items are executed: **transition cannot be verified live**.

## P5 — Gate

Focused governance suites (local): resource_contract + worker_admission +
resource_governor = **89 OK**. Full server suites run at phase close
(recorded in commit). No governance code changed by 6A — ops-only phase.

## Success criteria mapping

- Measured recovery **or precise blocker**: blocker precise (5.52 GiB, menu A–F). ✓
- Stable resource policy: untouched, ladder documented. ✓
- No duplicate authorities: verified single instances. ✓
- No lost source/work: zero deletions. ✓
- Verified admission decision: re-derived from code + live inputs. ✓
- No unauthorized cleanup: 0 bytes; the one invoked op was the already-authorized daily compaction. ✓
- No paid API calls: janitor scan `ai_calls=0`; all probes local. ✓
