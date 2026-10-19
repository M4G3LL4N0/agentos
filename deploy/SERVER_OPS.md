# AgentOS Server Operations

Target: `vmi3635094` — `ssh -i ~/.ssh/noaerth_cloud root@100.127.182.126`
(Tailscale `noaerth-worker-01`, Ubuntu 24.04.5, Python 3.12.3)

## Layout

| Path | Purpose |
|---|---|
| `/srv/noaerth/agentos` | code root (`pip3 install -e . --break-system-packages`) |
| `/etc/systemd/system/agentos.service` | unit (from `deploy/agentos.service`) |
| `/usr/local/bin/noaerth` | umbrella wrapper: `noaerth agentos <cmd>` |
| `http://127.0.0.1:8765/health` | API health probe |

## Routine

```bash
systemctl status agentos
systemctl restart agentos
journalctl -u agentos -n 100
noaerth agentos health
noaerth agentos workers
noaerth agentos providers
noaerth agentos logs --limit 50
noaerth agentos tower          # needs a TTY; SSH in and run directly
```

## Deploy

```bash
./deploy/sync-phase2.sh              # rsync + reinstall + test + restart + health
```

## Guarantees (enforced in code, verified in tests)

- Tower cancel requires explicit `y`; without a UI it always aborts.
- Autonomous loop defaults `free_only=true`, hard cycle/time/worker/cost caps;
  a crash at RUNNING is reloaded as INTERRUPTED (resume, never assume success).
- Project contracts block by default: no active contract, expired, out-of-scope,
  paid-under-free_only, cost ceiling, missing evidence → refusal + recorded
  violation.
- No `shell=True` in tower/loop/contracts; verification commands pass the
  fail-closed disallow list (`git push/commit`, `rm -rf`, `git reset --hard`).

## Recovery

- State survives restarts (SQLite WAL at `$AGENTOS_HOME/agentos.db`).
- Loop state: `loop_state.json`, `loop_limits.json`, `contracts/` in home.
- Disk pressure: JanitorOS admission requests fail closed (capacity probe
  before writes); monitor with `df -h /srv`.

## Phase 2 status (2026-10-19)

- Live demo COMPLETE: 837 server-side tests OK; loop demo objective
  `obj_28205e0be7f2` COMPLETED+VERIFIED (all 9 stages); tower rendered a
  live PTY frame; service restart persistence proven.
- Incident: a first `rsync --delete` sync removed live state (`agentos.db`,
  `state/`, `logs/`) and broke ownership; service crash-looped until
  `chown -R agentos:agentos` + recreated dirs. `sync-phase2.sh` no longer
  uses `--delete` and excludes state paths.
- Operator follow-up: ~396 `/tmp/tmp*` agentos test homes (~724M) from test
  runs; disk 87% used. Cleanup command (authorized by operator only):
  `find /tmp -maxdepth 1 -name 'tmp*' -user root -mtime +0 -exec rm -rf {} +`
  — verify each dir contains only `agentos.db`/test state first.
