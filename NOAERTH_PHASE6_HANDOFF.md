# NOAERTH Phase 6 handoff (6A + 6B), 2026-10-21

## One-line status

Phase 6B ran everything that admission permits: interface, gates, team
templates, agent stack, lease layer, observation contract, release audit,
suites — and stopped honestly at the two parts that require real workers
(P1 first worker, P2 parallel worker), which admission still forbids.
**No PASS for an unrun pilot. No bypass. 0 paid calls. 0 pushes.**

## The blocker (as of 2026-10-21T22:25:17Z)

- Disk **89.69%** (92,282,322,944 / 102,888,095,744 B); touched 90.005%
  at 21:24Z (governor `disk_pct >= 90` CRITICAL boundary).
- `governor.classify()` sole live rung: `disk >= 80 → SEVERE,
  allow_new_work=false, cpu_heavy_cap=0`; band PRESSED; janitoros reports
  CRITICAL/CRITICAL; forecast −2.03 GB/day "filling".
- **Bytes to free for <80%: 9,971,846,349 B = 9.29 GiB** (was 5.52 GiB at
  6A entry — growth outran the plan).
- 6A menu package A+B+C+D = 6.13 GiB **no longer sufficient**; full A–F
  ≈9.15 GiB still ≈0.14 GiB short while growth continues ≈1.9–2.0 GiB/day.

### Operator sequence (nothing of this was executed by the agent)

1. **Arrest growth first**: fix/stop the paios.service journal flood
   (~570 lines/min, STATUS=FAILED loop) + install a journald ceiling
   (SystemMaxUse) + address syslog rotation (syslog.1 = 737 MB, +65 MB/day
   class). Growth attribution: journal 2.5 G, syslog family ≈1,186 MB,
   paios tee log >100 MB, REALM tarball duplicates ~2.91 GiB.
2. Re-derive menu bytes at execution time (they have drifted), then approve
   a package that actually clears 9.29 GiB (likely A+B+C+D+E+F + more after
   growth arrest).
3. After reclaim: next `classify()` falls through the disk gate officially
   → band/governor clear → **re-verify with `submit --dry-run`
   (expect accepted path) → submit `/root/phase5b/candidate-manifest.json`
   WITHOUT `--dry-run` as P1 (expected-bytes 536870912)** → observe worker →
   then P2 probe manifest as second parallel worker.
4. Before P1 executes as agentos: **fix the opencode permission wall**
   (see PHASE6B_AGENT_STACK_HEALTH.json) — manifest agent=opencode runs as
   agentos and currently gets EACCES through the /usr/local/bin shim.

## What Phase 6 proved (evidence files in this repo)

- `PHASE6B_HIVE_LIVE_PILOT.md` — verdict table P0–P9, gate stack, dry-runs.
- `PHASE6B_WORKER_EVENTS.json` — 3 timestamped dry-run gate events, full
  P5 lease lifecycle (P5-LEASE-DEMO-01 ACTIVE→RETIRED, reap KEPT grace),
  observed R23-12 verified chain (merged 09e55b85) + R21-12 bounded retry,
  8 live realm-studio workers, serve/port observations, honest UNKNOWNs.
- `PHASE6B_THROUGHPUT.json` — baselines (load/PSI/mem/disk), 1-vs-2
  comparison = **UNKNOWN (not run)**, paid calls 0.
- `PHASE6B_AGENT_STACK_HEALTH.json` — component matrix + 14-entry
  capability catalog + cursor-agent repair record.
- `PHASE6A_*.md/json` — forensics, byte-counted menu A–F, source-safety /
  restic runbook (operator-run), admission-state snapshot.

## Interface cheat-sheet (read-only except submit)

```
# dry-run gate check (safe):
cd /srv/noaerth/autonomousos
python3 -m mesh.hive.cli submit --manifest /root/phase5b/candidate-manifest.json \
  --expected-bytes 536870912 --dry-run

# observation (ephemeral; kill after capture):
python3 -m mesh.hive.cli serve --port <free>      # NOAERTH-Hive/1
curl -s 127.0.0.1:<port>/api/snapshot             # noaerth.hive.snapshot/1
curl -s 127.0.0.1:<port>/api/events               # schema_version 1
python3 -m mesh.hive.cli events | inspect <task>
```

Never leave a serve running; ports 8787 (portfolio_os) and 8791 (AudioOS)
are other services. AgentWorld `/srv/noaerth/autonomousos-ui` is off-limits.

## Release readiness (P8, report-only)

6 releases REVIEW_REQUIRED. 5 rollback targets verified to exist
(publishing 664b20e/e4148ce, app-factory 7a1a58, demo-greeter ec717/991cc).
**REL-realm-e5b5dd7f has NULL rollback → promotion blocked.** Approved: 0.

## Test/coordination state

- Suites green: local agentos 958 / local autonomousos-hive 67 /
  server agentos 1028 / server autonomousos 92.
- Server-only drift: 4 new test files + `src/agentos/*` edits appeared on
  the server between 20:00–22:47 +0200 today from **another active session**
  (execution-ledger / fleet-command / objective-contract / partb work).
  Not touched, not synced — coordinate before merging those directions.
- Local `test_autonomous_loop::test_full_run_safe_echo_objective` is
  PATH-sensitive (needs a provider CLI on PATH); green under login PATH.
- Team templates: local + server (`workforce.py`, `.bak-6b-20261021`).
- Scratch lease evidence: `/var/lib/agentos/scratch-leases.json`
  (P5-LEASE-DEMO-01, RETIRED, KEPT-in-grace), demo dir left on policy under
  `/home/agentos/.cache/noaerth-scratch/p5-lease-demo`.
- Cursor-agent repaired in-phase (tree-sitter install, 25 MB, free registry).
- Single studio/collector confirmed; no reactors; no new daemons; no
  cron/systemd edits; Studio paused=True untouched.
- Bundle: refresh AFTER the 6B commit (currently tip ff25e85 = 6A).
  Push authorization still withheld (all commits local).

## Next phase entry checklist

1. `df -B1 /` < 80% AND `submit --dry-run` returns an accept path.
2. opencode reachable by agentos (health file fix applied by operator).
3. P1 submit (real) → capture worker events → verify deliverable exists.
4. P2 second manifest → parallel window → concurrency evidence.
5. P7 re-run: 1-vs-2 throughput with real workers (replace UNKNOWN).
6. P8: do not promote REL-realm-e5b5dd7f without a rollback artifact.
