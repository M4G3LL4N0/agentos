# AgentOS Server Operations

Target: `vmi3635094` — `ssh -i ~/.ssh/noaerth_cloud root@100.127.182.126`
(Tailscale `noaerth-worker-01`, Ubuntu 24.04.5, Python 3.12.3)

## Layout (since Phase 3 state separation, 2026-10-20)

| Path | Purpose |
|---|---|
| `/srv/noaerth/agentos` | code root (`pip3 install -e . --break-system-packages`) |
| `/var/lib/agentos` | production state: `agentos.db`, `loop_state.json`, `loop_limits.json`, `contracts/`, `governor_state.json` (env `AGENTOS_HOME`) |
| `/etc/agentos` | config: `config.json` (policy, root:agentos 660; env `AGENTOS_CONFIG_DIR`) |
| `/var/log/agentos` | file logs (backup cron output) |
| `/var/backups/agentos` | DR + portable backups, release snapshots, `ledger.csv` |
| `/usr/local/bin/janitor` | shared JanitorOS admission binary (service identity) |
| `/etc/systemd/system/agentos.service` | hardened unit (from `deploy/agentos.service`) |
| `/usr/local/bin/noaerth` | umbrella wrapper: `noaerth agentos <cmd>` |
| `http://127.0.0.1:8765/health` | API health probe |
| `/root/phase3/` | Phase 3 evidence: manifests, migration scripts, probes, unit backups |

The old copies under `/srv/noaerth/agentos` (`agentos.db*`,
`loop_state.json`, `config.json`) are **rollback artifacts — do not use,
do not delete without operator approval.**

## Routine

```bash
systemctl status agentos
systemctl restart agentos
journalctl -u agentos -n 100
noaerth agentos health
noaerth agentos --json status
noaerth agentos governor host        # host resource band (measured only)
noaerth agentos workers
noaerth agentos providers
noaerth agentos logs --limit 50
noaerth agentos tower          # needs a TTY; SSH in and run directly
```

## Deploy

```bash
./deploy/safe-deploy.sh                # preflight (tests) → snapshot → sync → health gate
./deploy/safe-deploy.sh --dry-run      # preflight only, no mutation
./deploy/safe-deploy.sh --skip-tests --allow-dirty
```

Guarantees: release snapshot (code tar + SQLite-backup db) before any
sync; `--delete` only within `src/` and `tests/`; state paths excluded;
JanitorOS storage admission gate; ERR-trap rollback to the snapshot;
health gate after restart. Regression-tested by `tests/test_deploy_safety.py`
(11 tests). `deploy/sync-phase2.sh` remains for reference only (no `--delete`).

## Backup & restore

```bash
/usr/local/bin/noaerth-agentos-backup      # manual run; cron: 17 3 * * * root
# -> /var/backups/agentos/dr-<ts>.tar.gz            (full-file DR: SQLite backup API)
# -> /var/backups/agentos/agentos_backup_<ts>...tar.gz (portable, secrets excluded)
# -> ledger + 14-archive retention

# ISOLATED restore (safe, default):
/usr/local/bin/noaerth-agentos-restore --target /root/phase3/restore-test dr-<ts>.tar.gz
# Production restore (explicit, service must be stopped first):
systemctl stop agentos
/usr/local/bin/noaerth-agentos-restore --target /var/lib/agentos --confirm-production dr-<ts>.tar.gz
systemctl start agentos

# Portable restore (isolated target only — never prod):
python3 -c "from agentos.store import Store; from agentos.backup_reconstruction import restore_backup; Store('/tmp/t.db'); restore_backup('<archive>', '/tmp/t.db')"
```

Never restore on top of the live database: the restore script refuses a
target that already contains `agentos.db` without `--force`, and refuses
the production dir without `--confirm-production`.

## State-separation rollback (drill-proven)

```bash
cp -a /root/phase3/agentos.service.pre-separation /etc/systemd/system/agentos.service
cp -a /root/phase3/noaerth-agentos.pre-separation /usr/local/bin/noaerth-agentos
systemctl daemon-reload && systemctl restart agentos
# verify old layout: AGENTOS_HOME=/srv/noaerth/agentos serves counts 2/1/42
```
Scripts: `/root/phase3/migrate-state-separation.sh`, `rollback-state-separation.sh`.

## Guarantees (enforced in code, verified in tests)

- Tower cancel requires explicit `y`; without a UI it always aborts.
- Autonomous loop defaults `free_only=true`, hard cycle/time/worker/cost caps;
  a crash at RUNNING is reloaded as INTERRUPTED (resume, never assume success).
- Project contracts block by default: no active contract, expired, out-of-scope,
  paid-under-free_only, cost ceiling, missing evidence → refusal + recorded
  violation.
- No `shell=True` in tower/loop/contracts; verification commands pass the
  fail-closed disallow list (`git push/commit`, `rm -rf`, `git reset --hard`).
- Host governor: PRESSED admits small non-heavy work only; CRITICAL denies
  all new work; upgrades need exit thresholds + dwell (default 300s);
  CRITICAL exits only via RECOVERING. Unmeasured signals are never assumed
  healthy or unhealthy.
- JanitorOS admission fails closed (missing binary, permission denied,
  malformed/unrecognised payload, non-zero exit, invalid request).

## Incident response: janitor/disk-watch stacking (2026-10-20)

Symptom: load climbing toward ~135, hundreds of `noaerth-storage-janitor`
and `find … -name node_modules` processes, zombies.

```bash
ps -eo pid,etime,args | grep -c noaerth-storage-janitor
tail -20 /home/portfolio/.local/state/noaerth-storage-janitor/watch.log
uptime && vmstat 1 3
# mitigation order (kills ONLY exact argv matches, never self):
#   1. SIGTERM the stuck `find /home/portfolio /srv/noaerth -type d -name node_modules`
#   2. SIGTERM disk-watches FIRST (they respawn janitors after child death)
#   3. SIGTERM janitors
```
Root cause is fixed in `/home/portfolio/.local/bin/noaerth-disk-watch` (state
before launch + `flock -n`) and `noaerth-storage-janitor` (`${USER:-…}`).
Backups of both: `/home/portfolio/.local/bin/*.bak-20261020`.

## Phase 3 status (2026-10-20)

Full evidence: `PHASE3_ACCEPTANCE.md` in the repo. Highlights: 872 tests
local+server OK; load root-caused and fixed (135 → 4, zero respawn across
cron ticks); state separation rollback-drilled; both backup layers
restore-tested in isolation; safe deploy proven live; governor denies
under simulated CRITICAL; hardened unit probe-verified. /tmp inventory
manifest at `/root/phase3/tmp-manifest.tsv` (394 confirmed test homes —
deletion pending explicit operator approval). Paid calls: 0. Pushes: 0.
