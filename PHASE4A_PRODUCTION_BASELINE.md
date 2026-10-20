# PHASE4A — Production Baseline and Release Integrity

Date: 2026-10-20 · Server: `vmi3635094` (`root@100.127.182.126`, Tailscale `noaerth-worker-01`) ·
Repo: `/Users/matador/startups/agentos` (branch `main`) · Result: **PASS with one documented concurrent-actor finding**

Continues the Phase 3 working tree. No work lost; all 23 changed files
committed locally (no push).

---

## 1. Commit SHAs (local `main`, unpushed)

| SHA | Subject |
|---|---|
| `9503527` | (Phase 3 baseline parent) docs: Phase 2 live demo results, incidents, and fixes recorded |
| `2877bdb` | fix(deploy): state-separated production layout and hardened service unit |
| `e84b639` | fix(janitor): fail-closed validation of admission requests |
| `3a5ba5b` | feat(governor): measured-only host resource governor with hysteresis |
| `92a3b88` | feat(deploy): backup/restore tooling and safe deployment pipeline |
| `fb1ac3b` | docs(phase3): runbooks, decisions, module map, and acceptance evidence |
| `d69fca7` | docs(phase4a): production baseline and acceptance record (+ tmp approval manifest) |

Staged with exact-file lists per commit (never `add -A`); worktree clean
after committing; `git diff --check` clean; no tracked deletions; no
pushes; no other branches created or modified.

## 2. Source paths and integrity (Steps 1–2)

- **Authoritative source:** local repo `/Users/matador/startups/agentos`
  → deployed by rsync to `/srv/noaerth/agentos` (server tree has no
  `.git`; git is the local authority).
- **23 files reviewed:** 16 modified + 7 new. Findings:
  - Unrelated changes: **0** · Secrets/credentials: **0** (pattern scan
    across all 23) · Generated artifacts staged: **0** · Temporary
    evidence staged: **0** · Accidental deletions: **0**
  - **Defect found and fixed:** `deploy/noaerth-agentos-backup` and
    `deploy/noaerth-agentos-restore` lacked exec bits (repo + server
    tree) → chmod 755 both, matching git mode `100755`.
- **Concurrent agents:** no git locks, single worktree, **zero repo file
  modifications in 30-min watch window** before commits — the 23 files
  were exclusively Phase 3 work; nothing else's work committed.
  (A concurrent *server-side* actor exists — see §6.)
- Rollback points preserved and verified present:
  `/root/phase3/agentos.service.pre-separation`, `.pre-hardening`,
  `noaerth-agentos.pre-separation`, old tree
  `/srv/noaerth/agentos/{agentos.db,loop_state.json,config.json}`,
  deploy release snapshots in `/var/backups/agentos/releases/` (keep 3).

## 3. Verification results (Step 4)

| Check | Method | Result |
|---|---|---|
| Full test suite | `unittest discover` (local) | **872 / 872 OK (137s)** |
| Focused Phase 3/4A suites | governor + deploy + janitor + paths | **49 / 49 OK** |
| Production source = git state | SHA-256 of all 211 tracked files vs server tree | **0 mismatches, 0 missing → `SOURCE_MATCHES_GIT_STATE`** (7 lagging docs rsynced without `--delete`, no restart) |
| Service definition | live unit vs `deploy/agentos.service` | **`UNIT_MATCHES_REPO`** |
| Service status | `systemctl` + health endpoint | `active/running`, `NRestarts=0`, `{"ok":true,"healthy":true,"version":"0.5.0"}` |
| Scheduled execution identity | crontabs by user | backup → **root** (`17 3 * * *`); janitors/disk-watch/reactor → **portfolio**; **agentos: none** |
| Duplicate cleanup authorities | all cron sources (`crontab`, `/etc/cron.d`, `/etc/crontab`, timers) | AgentOS authorities exactly **1 each** (disk-watch ×1, storage-janitor ×1, agentos-backup ×1); no timers; plus **reactor ×1** — see §6 |
| Runaway processes | `ps` (heredoc probes, bracket patterns, zero self-match) | **janitors 0 · reactors 0 · disk-watches 0 · stuck finds 0 · zombies 0** at 22:27–22:28; transient episode documented in §6 |
| Isolated restore | fresh `dr-20261020T201908Z` → `/root/phase4a/restore-test` | `integrity=ok`, counts **2/2/61**, policy **12**; second run **refused** (`target already contains agentos.db`, no `--force`); prod health unchanged |
| Backup protection | perms scan | `/var/backups/agentos` root:root 755, archives 644 root:root, ledger 644 root:root, **0 world-writable**; 6 archives present; rollback artifacts intact |
| Governor — HEALTHY | `test_host_governor` | ok (`test_healthy_metrics_stay_healthy_and_admit_all`) |
| Governor — PRESSED | live read-only `governor host` | **`band=PRESSED`, `admit=true`** (disk_free_pct=16.58–16.62; history shows HEALTHY→PRESSED transition) |
| Governor — CRITICAL | synthetic metrics, no prod injection | ok (`test_disk_critical_denies_everything`) + fail-closed admission |
| Governor — RECOVERING | synthetic transition sequence | ok (`test_critical_exits_only_into_recovering`, `test_recovering_relapses_immediately`) |
| Governor — hysteresis | dwell/exit tests | ok (`test_pressed_does_not_exit_before_dwell`, `test_pressed_exits_after_dwell_when_clear`) |
| Janitor fail-closed | invalid-input tests | ok (negative/bool/non-int rejected without invoking binary) |

No disruptive load injected into production at any point.

## 4. Production state snapshot (2026-10-20T20:28Z)

| Metric | Value |
|---|---|
| Load (1/5/15 min) | **3.53 / 3.82 / 4.22** on 6 CPUs (≈0.59/core) — Phase 3 mitigation held (was ~135) |
| CPU (vmstat 3s) | idle **47%**, user 20%, sys 32%, iowait 1% |
| Memory | 10,669 MiB available of 11,960 MiB (89.2%); swap 566 MiB of 8,191 MiB used |
| Disk `/` | 85,987,303,424 / 102,888,095,744 bytes → **83.6% used**, 16,884,015,104 B (15.7 GiB) free |
| Service | `agentos.service` active, `NRestarts=0`, health OK |
| Active workers | **0** (`workers: []`; 1 agentos process = API service) |
| Governor band | **PRESSED** — small work admitted, heavy/big refused (max 104,857,600 B) |
| Backups | 6 archives + ledger (latest `dr-20261020T201908Z`); cron next 03:17 UTC+? → `17 3 * * *` root |
| Cron status | root: 1 active (backup) · portfolio: 5 active (fast-janitor, disk-watch, cp-snapshot, downsample, **reactor** — §6) · agentos: none |
| /tmp candidates | 394 approved, 92.0 MiB — **not deleted** (manifest §5) |

## 5. /tmp approval manifest (Step 5)

`PHASE4A_TMP_APPROVAL_MANIFEST.md` — 394 exact paths, owner `root`,
mtime (UTC), bytes, **fresh `/proc` scan: no live process refs
(cwd/exe/fd)**, confidence HIGH, per-row cleanup policy + 6-point
execution policy. Expected reclaim: **96,518,144 B (92.0 MiB)**.
Explicitly rejected: 17 ACTIVE_PROTECTED, 3 POSSIBLY_DISPOSABLE,
9,664 UNKNOWN. **No deletion performed in Phase 4A.**

## 6. Concurrent-actor finding (preserved, not interfered with)

- Actor: active `portfolio` sessions from **100.80.236.70** (since 21:36).
- Timeline: wrote `noaerth-reactor.py`/`noaerth-reactor` ("JanitorOS
  Reactor — central orchestration (Phase 7)") at **22:10**; manually
  started **two duplicate instances** at 22:13/22:14 → each spawned
  `noaerth-storage-janitor heavy` chains; load rose 3.4 → 7.7.
- Disposition per Phase 3 rules ("never kill unrelated processes";
  ownership clear → preserve): **no kill, no file changes, no repo
  overlap** (reactor lives in `/home/portfolio/.local`, outside repo).
- The actor self-resolved: manual duplicates exited by 22:26 and added a
  **single** `*/15` cron entry for the reactor (one authority, state-rate
  limited via its own `state.json`: last_heavy/last_reactor_run).
- Verified after settling (22:27:47, clean probe): janitors **0**,
  reactors **0**, zombies **0**, load **3.53**.
- Watch item: observe the reactor's `*/15` cron + the disk-watch `*/5`
  interplay over the next day (both now have single-instance guards /
  rate limits); incident response for stacking remains in
  `deploy/SERVER_OPS.md`.

## 7. Rollback instructions

- **State separation (drill-proven):** restore
  `/root/phase3/agentos.service.pre-separation` +
  `noaerth-agentos.pre-separation`, `daemon-reload`, restart; old tree
  serves counts 2/1/42. Scripts: `/root/phase3/{migrate,rollback}-state-separation.sh`.
- **Unit hardening:** restore `/root/phase3/agentos.service.pre-hardening`.
- **Deploy:** `/usr/local/bin/noaerth-agentos-restore --target
  <isolated> /var/backups/agentos/releases/<ts>.code.tar.gz` + db
  snapshot; or restore a `dr-*.tar.gz` per `deploy/SERVER_OPS.md`
  (production requires `--confirm-production` + stopped service).
- **Code:** `git revert` any of `2877bdb e84b639 3a5ba5b 92a3b88 fb1ac3b`
  (reverts are local; push only on instruction).
- **Load incident:** kill order + root cause in `deploy/SERVER_OPS.md`
  (finds → disk-watches → janitors; exact argv only).

## 8. Compliance ledger

Pushes **0** · Commits: **5 + this record** (local `main` only) ·
Paid calls **0** · Deletions of unverified files **0** · /tmp candidates
deleted **0** · Restore-over-prod **0** · Disruptive load injected **0** ·
Other actor's processes killed **0** · Other actor's files modified **0**.

## 9. Open items

1. Operator sign-off to execute the /tmp approval manifest (394 paths, 92 MiB).
2. Watch the new reactor `*/15` cron interplay with disk-watch `*/5` (concurrent actor's Phase 7 work).
3. Push `main` (25 commits ahead of `origin`) only when explicitly instructed.
4. Deferred from Phase 3: syscall-filter/MDWE re-probe when the worker model changes; JanitorOS binary upgrades must replace both copies in lockstep.

**Phase 4B: ready to start when briefed.**
