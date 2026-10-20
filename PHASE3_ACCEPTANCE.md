# Phase 3 Acceptance Report — Production Hardening

Date: 2026-10-20 · Target: `vmi3635094` (`root@100.127.182.126`) ·
Code: `/srv/noaerth/agentos` · Local baseline commit: `9503527`
(Phase 1–2; **Phase 3 changes are uncommitted** — commit only on explicit
operator instruction; no pushes were made at any point).

Scope honored: no rebuild of hive/scheduler/router/JanitorOS, no
`rsync --delete` on the live tree root, no broad recursive cleanup, no
unverified deletions, no unrelated process kills, no paid model calls,
no restore tested against production.

---

## 1. Production baseline (evidence)

| Check | Result |
|---|---|
| API health | `{"ok": true, "healthy": true, "version": "0.5.0"}` |
| Service | `ActiveState=active`, `NRestarts=0` |
| Database | integrity `ok`, 39 tables, counts **2 objectives / 1 execution / 42 events** |
| WAL | 2.3MB; disk 87% used |
| Backups | **none existed** — created in §6 |
| Load | **~135** on 6 cores (investigated in §2) |
| Unit config | recorded; `StartLimitIntervalSec` found misplaced in `[Service]` (systemd ignored it) |

## 2. Load investigation + mitigation (evidence)

Root cause (lightweight tools only — `ps`/`uptime`/`vmstat`/`tail`):

- `noaerth-disk-watch` wrote its 6h re-fire guard **after** launching the
  heavy janitor → every `*/5` cron tick with disk ≥75% stacked a new
  janitor: **277 janitors + 123 disk-watches**.
- Each heavy janitor's full-tree `find … node_modules` walk never
  finished under self-contention: **120 stuck finds** (6–13h old),
  `us+sy` 77–82%, run-queue 69–95 on 6 cores.
- Cron env lacks `USER` → `set -u` killed the janitor's `/tmp` cleanup
  subshell every run (`USER: unbound variable`).

Fixes (backed up `*.bak-20261020`, `bash -n` verified): guard state
written **before** launch + `flock -n` single-instance janitor;
`${USER:-$(id -un)}`. Mitigation kill order honored: **finds →
disk-watches (before janitors, they respawn) → janitors.**

| Metric | Before | After |
|---|---|---|
| janitors | 277 | **0** |
| disk-watches | 123 | **0** |
| stuck finds | 120 | **0** |
| zombies | 6 | **0** |
| load | ~135 | **4.18** (settling) |
| cron ticks observed | stacking | **3 ticks (05:50/05:55/06:00), zero respawn; `watch.log` untouched since 23:05Z** |

## 3. JanitorOS repair under service identity (evidence)

- Byte-identical copy of the real binary → `/usr/local/bin/janitor`
  (root:root 755); `/home/agentos` (750) created for the knowledge
  store. **No `/home/portfolio` access.**
- Admission matrix, as `agentos` **and** in true systemd context
  (`SYSTEMD_ADAPTER_OK`): granted (rc 0) · refused (rc 75) ·
  threshold-exceeded · unavailable (binary hidden) · permission-denied
  (EACCES) · malformed (`admission denied`) · invalid request →
  adapter fails closed **before invoking the binary** (negative/bool/
  non-int `bytes_needed` rejected; `no binary returns error`).
- Adapter discovery resolves to `/usr/local/bin/janitor`.

## 4. /tmp inventory — dry-run manifest (evidence)

`/root/phase3/tmp-manifest.tsv` — **10,078 entries / 508MB children**:

| Category | Count | Action |
|---|---|---|
| CONFIRMED_DISPOSABLE | 394 (~92MB) | our agentos test windows — **awaiting explicit operator approval** |
| POSSIBLY_DISPOSABLE | 3 | needs operator review |
| UNKNOWN | 9,664 (~415MB) | 9,485 `paios*` + 154 `mee*` other-service workdirs + toolchains — **out of scope, untouched** |
| ACTIVE_PROTECTED | 17 | live fds / systemd-private / session — **never touched** |

**Zero deletions performed.**

## 5. Durable state separation (evidence)

| Concern | Path |
|---|---|
| code | `/srv/noaerth/agentos` (incl. AutonomousOS exchange `state/`) |
| state | `/var/lib/agentos` (`AGENTOS_HOME`) — db, loop_state, governor_state |
| config | `/etc/agentos` (new `AGENTOS_CONFIG_DIR`, root:agentos 660, 12 blocked patterns) |
| logs | `/var/log/agentos` |

- Migration used the **SQLite backup API** (copies only); old files kept
  as rollback artifacts — hashes stable across the swap
  (`OLD_TREE_STABLE`).
- **Rollback drill green both directions:** `ROLLBACK_OK` (old layout
  serves 2/1/42) and `REAPPLY_OK` (new layout serves 2/1/42).
- Wrapper `noaerth-agentos` now exports both `AGENTOS_HOME` and
  `AGENTOS_CONFIG_DIR`; root CLI verified (`home: /var/lib/agentos`).
- Live proof under real work (§10): loop state written to `/var/lib`
  while the old copy stayed **byte-identical**
  (`ef2662f8890adeccc7…` unchanged).

## 6. Backup + restore (evidence)

- `noaerth-agentos-backup` → DR full-file tar (**SQLite backup API**),
  portable secrets-excluded export, `ledger.csv`, 14-archive retention.
  Root cron `17 3 * * *` → `/var/log/agentos/backup.log`.
- `noaerth-agentos-restore`: isolated `--target` default; refuses
  production without `--confirm-production` (service must be stopped);
  refuses existing target without `--force`.
- **Isolated drill green:** DR restore → integrity `ok`, counts
  **2/1/42**, **12** policy patterns; portable restore into a fresh
  `Store` → counts match; full-cycle CLI usable against the restored
  env; both guards fired; production untouched.
- **Fresh round-trip after all changes** (`dr-20261020T154021Z`):
  DR → 2/2/61 + 12 patterns, portable → 2/2/61, prod still healthy.

## 7. Safe deployment (evidence)

`deploy/safe-deploy.sh`: preflight (local suite unless `--skip-tests`;
remote health / disk / JanitorOS **storage admission** gate) → release
snapshot (code tar + backup-API db, keep 3) → sync with `--delete`
**only** inside `src/` and `tests/`, state excluded → install →
health gate (15×2s) → `rollback()` on ERR + explicit rollback on gate
failure.

- `tests/test_deploy_safety.py`: **11/11 green** — encodes the Phase 2
  `rsync --delete` incident as permanent regression guards
  (state-deletion tests must stay).
- Live runs: **`DEPLOY_OK` ×4**, final
  `ts=20261020T153646Z release=…/releases/20261020T153646Z.code.tar.gz`.
  (Earlier failures fixed honestly: PEP 668 `--break-system-packages`
  placement, `-e` flag order, health-grep JSON space.)

## 8. Host resource governor (evidence)

- `src/agentos/host_governor.py`: bands **HEALTHY / PRESSED / CRITICAL /
  RECOVERING**; measured-only disk/load/mem/swap — unmeasured signals
  stay `None` (excluded, never invented); immediate degradation, exit
  thresholds + 300s dwell for upgrades, **CRITICAL → RECOVERING only**,
  immediate relapse; bounded history (50) in `governor_state.json`;
  config overrides under `config["hostGovernor"]`.
- Wired at loop **DELEGATE** (host gate before janitor), services
  `host_status()`/`host_admission()`, CLI `governor host` /
  `governor host-admission` (exit 1 on refusal). **+18 tests.**
- **Live:** `band=PRESSED (disk_free_pct=17.04, load1_per_cpu=2.44)`,
  state persisted, small work admitted.
- **Pressure simulation (injected thresholds, prod config untouched):**
  `band=CRITICAL, admit=False, "host CRITICAL (disk_free_pct=17.04 …)"`;
  loop DELEGATE → `False | host governor refused: host CRITICAL …`.

## 9. systemd hardening (evidence)

- Applied: `StartLimit*` **moved to `[Unit]`** (journal warning gone),
  `StateDirectory=agentos`, `ReadWritePaths=/var/lib/agentos
  /etc/agentos /home/agentos /var/log/agentos`, `NoNewPrivileges`,
  `PrivateTmp`, `ProtectSystem=strict`, `ProtectHome=read-only`,
  `ProtectKernel{Tunables,Modules,Logs,ControlGroups}`,
  `RestrictSUIDSGID`, `LockPersonality`, `CapabilityBoundingSet=`,
  `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6`.
- **9-point sandbox probe GREEN before applying** (`SANDBOX_PROBE_OK`):
  state write, config read + in-place `W_OK`, home write, tmp write,
  code read, janitor admit, API reachable, db read.
- Deliberately **not** applied (documented in the unit): syscall
  filters, `MemoryDenyWriteExecute` — child worker model spawns
  opencode/node.
- After apply: `systemd-analyze verify` clean, no journal warnings,
  `NRestarts=0`, health OK, governor works.
  Backup: `/root/phase3/agentos.service.pre-hardening`.

## 10. Operational verification (evidence)

| Check | Result |
|---|---|
| API sweep | `/health /status /capabilities /objectives /events /plans /gaps` → **7/7 × 200** |
| Restart persistence | counts **2/2/61**, `loop_state` intact after `systemctl restart` |
| Autonomous loop under separated state | **9/9 stages ✓**, `obj_76a9ee640521` **COMPLETED+VERIFIED**, `loop_state.json` written to `/var/lib/agentos`, old rollback copy **sha-identical** |
| Governor after loop | `band=PRESSED, admit=True` |
| Fresh backup + both restore layers | round-tripped, counts match, prod healthy |
| Pressure sim | CRITICAL denies + loop refuses (§8) |
| Service | `ActiveState=active NRestarts=0`, health ok |
| Final suite — local | **872 tests OK (199s)** |
| Final suite — server | **872 tests OK (135s)** |

## Compliance ledger

- Paid provider/model calls: **0** · Pushes: **0** · Commits: **0**
  (Phase 3 uncommitted, awaiting operator instruction).
- Unverified deletions: **0** · /tmp entries deleted: **0** ·
  Unrelated processes killed: **0** · Restore-over-prod: **0**.
- Kill activity (load mitigation) targeted exact argv matches only,
  ordered disk-watches before janitors.

## Follow-ups for the operator

1. Approve deletion of the **394 CONFIRMED_DISPOSABLE** `/tmp` test
   homes from the manifest (92MB; UNKNOWN/PASS categories remain).
2. Decide when to commit Phase 3 (local branch, no push).
3. Postpone-until-asked: syscall-filter/MDWE re-probe when the worker
   model changes; JanitorOS binary upgrade must replace both copies.
