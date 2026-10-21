# Phase 6B — Autonomous Company Pilot: Hive live worker demonstration

Date: 2026-10-21 (UTC evidence stamps below). Server: vmi3635094.
Outcome: **PARTIAL — P1/P2 (genuine Hive submissions) BLOCKED by admission, not bypassed.**
No PASS is claimed for an unrun live pilot. Pushes: 0. Paid calls: 0. Operator gates crossed: 0.

## Verdict by mission part

| Part | Status | Evidence |
|---|---|---|
| P0 candidate + admission gates identified | DONE | below "Submission interface" |
| P1 first genuine worker after admission | **BLOCKED** | admission refuses everything; 3 dry-run captures |
| P2 second parallel worker | **BLOCKED / not run** | never eligible while P1 blocked |
| P3 team templates wired to workforce | DONE | local+server code, 6 tests each side, live snapshot |
| P4 agent stack health matrix | DONE | PHASE6B_AGENT_STACK_HEALTH.json |
| P5 scratch-lease crash-recovery layer | DONE (worker-level resume = PARTIAL) | lease registry record P5-LEASE-DEMO-01 |
| P6 versioned Hive interface (observe-only) | DONE | noaerth.hive.observation/1, serves 8799+8792 released |
| P7 throughput 1-vs-2 comparison | **UNKNOWN / not runnable** | baselines only, no synthetic numbers |
| P8 release readiness | DONE (report-only) | 5 rollback targets verified; 1 blocked |
| P9 suites, no scheduler impact, local commits | DONE | 958+67 local / 1028+92 server, all green |

## Submission interface (P0)

Only write command (everything else read-only):

```
cd /srv/noaerth/autonomousos
python3 -m mesh.hive.cli submit --manifest <path> [--expected-bytes N] [--dry-run]
```

Gate order in `mesh/hive/execution.py`: `validate_manifest`
(schema `noaerth.hive.submission/1`, 1..8 tasks, ID regex, unique, deps,
title/prompt, level enum, agent default `opencode`, venture default `realm`,
`cpu_heavy` bool) → `serveros_snapshot_gate` → `resource_admission` →
cpu_heavy budget → `janitor_admission(expected_bytes)`.

Candidates preserved, never submitted for real:

- `/root/phase5b/candidate-manifest.json` — SERVEROS-5B-PILOT, LOW, opencode,
  realm, deps [], cpu_heavy false, prompt writes SERVEROS_HIVE_PILOT.md (<20 lines).
- `/root/phase5b/probe-manifest.json` — MEDIUM probe variant.

Task registry check: 67 tasks, **0 matches** for 5B/PILOT/PROBE ids — the
candidate has never entered the real registry.

## P1/P2: BLOCKED (admission state, no bypass)

Fresh dry-run captures (all identical semantics):

| When (UTC) | Result |
|---|---|
| ~22:15 (session capture) | accepted=false, reason=`serveros_low_priority_deferred`, band=PRESSED, retry_after_s=60, state_age_s=117.7 |
| ~22:20 (session capture) | same, state_age_s=98.8 |
| **2026-10-21T22:25:17Z** | same, state_age_s=76.2, dry_run=true |

Gate stack at 22:25:17Z:

- Disk: 92,282,322,944 / 102,888,095,744 B = **89.69%** (df "90%").
  It touched 90.005% at 21:24Z — at/over the governor's
  `disk_pct >= 90 CRITICAL` rung boundary briefly; hive view reads 89.7%.
- `governor.classify()` (`/srv/noaerth/autonomousos/governor.py:281`):
  `disk_pct >= 80 → SEVERE, allow_new_work=false, cpu_heavy_cap=0` is the sole
  live rung; mem/cpu/io clear (6A re-derivation, unchanged).
- JanitorOS (fresh hive snapshot `resources.janitoros`): health **CRITICAL**,
  band **CRITICAL**, forecast −2,028,401,962 B/day, trend "filling"
  (605 samples, 159 h window).
- Serveros admission band: **PRESSED**, constraints
  `[band_pressed, autonomousos_allow_new_work_false]`.
- LOW priority defers at the band gate (before resource check); MEDIUM+
  would hit `resource_governor_hold`. Either way: refused, honestly.

Bytes needed to reach 80% **now: 9,971,846,349 B = 9.29 GiB**
(Phase 6A entry needed 5.52 GiB — growth outran the plan):

- 6A menu totals: A journal 1.86 + B apt 0.42 + C stale bak 0.94 + D tarball
  dedup 2.91 = **6.13 GiB (A+B+C+D now INSUFFICIENT)**.
- Full menu A–F adds E worktrees 2.3 + F syslog.1 0.72 → ≈9.15 GiB —
  still ≈0.14 GiB short *and* growth continues at ≈1.9–2.0 GiB/day.
- **Handoff requirement: arrest growth first (paios journal flood + rsyslog),
  then re-derive the menu at execution time.** No menu action was executed
  (all await operator approval); nothing was reclaimed by this phase.

Studio `paused=True` (operator) untouched. No gate, band, or hold was
manually changed. No candidate was ever submitted without `--dry-run`.

## P3: team templates (DONE)

`mesh/hive/workforce.py`: `TEAM_TEMPLATES` (engineering,
quality-assurance, security, design, research, release-engineering,
infrastructure, product), `template(name)`, `assemble(name, project,
open_tasks)` — pure data over existing ROLES, logical-only, no scheduler.

- Local (`autonomousos-hive` repo): implemented + `TeamTemplateTest` (6);
  local suite **67 OK**.
- Server deploy: mirrored byte-identical logic with backup
  `/srv/noaerth/autonomousos/.bak-6b-20261021`; server suite **92 OK** (86+6).
- Live proof: fresh snapshot `teams[0]` shows realm assembled as
  coder/design/integrator/performance/tester roles with real assignments
  (e.g. tester → R21-12), titles from templates.

## P4: agent stack health

Full matrix in `PHASE6B_AGENT_STACK_HEALTH.json`. Highlights:

- opencode v2.0.24 runs for portfolio; **agentos user gets EACCES**
  (`/usr/local/bin/opencode` shim → `/home/portfolio/.opencode/bin/opencode`;
  `/home/portfolio` is 750 portfolio:portfolio, agentos not in group).
  agentos capability: readiness CONFIGURATION_REQUIRED, health MISCONFIGURED.
  Required fix documented (report-only; no permission changes made).
- codex-cli 0.161.0 (auth.json present), claude 2.1.285 — healthy for portfolio.
- cursor-agent **repaired in-phase**: entire `node_modules` was absent
  (bundle requires tree-sitter / tree-sitter-bash). Installed
  tree-sitter 0.25.1 + tree-sitter-bash 0.25.1 via pnpm 12.4.2
  (`allowBuilds` approved, `node-gyp-build` ok) at
  `/home/portfolio/.local/share/node_modules` (25 MB, free public registry).
  Verify: `cursor-agent --version` → `2026.10.14-0f9edde` rc=0.
  `auth status` shows an interactive Workspace-Trust prompt — auth state UNKNOWN.
- hermes: `Hermes Agent v0.22.2 (2026.9.24)` runs via wrapper (portfolio);
  capability catalog still marks hermes UNAVAILABLE for agentos (adapter-side).
- openclaw/langgraph absent; python `langfuse`/`opentelemetry`/`mcp` absent;
  chatbridge present. Capability catalog: 14 entries (3 OK, 2 AUTH_REQUIRED,
  1 UNKNOWN, 7 UNAVAILABLE, 1 MISCONFIGURED).

## P5: scratch-lease crash-recovery layer (DONE; worker-level resume PARTIAL)

Production path proven as agentos against the real default registry
`/var/lib/agentos/scratch-leases.json` (roots: /tmp, /var/tmp,
/srv/noaerth/worktrees, portfolio-os .worktrees/data.worktrees,
/home/agentos/.cache/noaerth-scratch; registration shells to
`janitor space scratch register`; refusal → `lease_state: REFUSED`):

1. acquire → ACTIVE (janitor rc=0, record with monotonic epoch)
2. simulated crash re-register → exactly **1 record, replaced not duplicated**
3. complete → ok, `lease_state: RETIRED`, `completed` recorded (rc=0)
4. reap (disposable) → rc=0, outcome **KEPT, "within grace period after
   completion"** — dir left on policy, not deleted by me.

Record `P5-LEASE-DEMO-01` created 2026-10-21T20:58:10.545154Z, completed
20:58:10.633626Z (full JSON in `PHASE6B_WORKER_EVENTS.json`).
**Worker-level interruption/resume of a real worker was not run** — requires
an admitted worker (P1). Marked PARTIAL.

## P6: versioned observation interface (DONE)

- Contracts: snapshot `noaerth.hive.snapshot/1`, observation
  `noaerth.hive.observation/1`, HTTP header `NOAERTH-Hive/1`, events
  `schema_version: 1` with provenance OBSERVED/INFERRED/VERIFIED and honest
  `provider/model/token_usage/cost_usd: "UNKNOWN"` where unknown.
- Ephemeral serves (read-only): port 8799 earlier this session; final
  capture on port **8792** — `/api/snapshot` 160,727 B, `/api/events`
  288,536 B; log line "hive view on http://127.0.0.1:8792 (read-only)".
  Both ports **verified released** after `timeout`; no hive serve left running.
- Port landscape: 8787 = portfolio_os (returns `{"error":"unauthorized"}`),
  8791 = AudioOS (403), 8765 = unrelated pre-existing python service.
  AgentWorld (`/srv/noaerth/autonomousos-ui`) untouched.
- Event projection live: `event_cursor` **1081**, newest event
  2026-10-21T22:06:44 (STAMP_PREFLIGHT for R21-12).

## Observed genuine worker activity (read-only, other actors — not my pilot)

The existing production path (realm-studio scheduler) was observed working
during this phase — real, verified, honestly attributed:

- 8 live execution workers at 22:22:42Z, all scheduler `realm-studio`,
  task R21-12 (routes tool/gate), state TESTING, heartbeats current.
- Event chain 21:11–22:06Z for R23-12: TASK_START → WORKER_START
  claude/sonnet → EXIT_OK (files_changed 8) → ROUTE_RESULT →
  STAMP_PREFLIGHT → VERIFY_RESULT **PASS** → TASK_VERIFIED
  "R23-12 merged as 09e55b85 after independent verification" → TASK_COMPLETE;
  plus R21-12 BOUNDED_RETRY (#2) — timeout/route/retry discipline observed.
- Snapshot task states: 12 VERIFIED_COMPLETE / 9 QUEUED / 1 TESTING (22 total).

This demonstrates the surrounding Hive observes and records genuine
verified work — but **the work was produced by existing Studio workers of
other actors, not by a Phase 6B candidate** (admission never allowed one).

## P7: throughput (UNKNOWN comparison)

`PHASE6B_THROUGHPUT.json`: host baselines captured (load, PSI, memory, disk,
concurrency); **1-worker vs 2-worker throughput comparison NOT RUN —
recorded UNKNOWN**, per the no-synthetic-claims rule. Zero paid calls.

## P8: releases (report-only, nothing approved)

Studio releases table (read-only): all 6 REVIEW_REQUIRED.

- 5 rollback targets **verified to exist** (as portfolio owner):
  publishing `664b20e`, `e4148ce`; app-factory `7a1a58`; demo-greeter
  `ec717`, `991cc`.
- **REL-realm-e5b5dd7f (REALM.app, macos): `rollback` is NULL** — no
  rollback evidence → **promotion blocked** (documented; nothing approved).

## P9: suites, no-impact verification, commits

- Local: agentos **958 tests green** (944 passed + 14 server-only skips + 51
  subtests; run with login PATH — see note), autonomousos-hive **67 green**.
- Server: agentos **1028 green**, autonomousos **92 green**.
- Test-file drift note: server has 4 test files absent locally
  (`test_execution_ledger.py`, `test_fleet_command.py`,
  `test_objective_contract.py`, `test_partb_surfaces.py`; mtimes
  2026-10-21 22:42–22:47 +0200) plus concurrent `src/agentos/*` edits after
  20:00 +0200 — **another session's in-flight work, not touched, not synced**
  (coordination rule). My server run included them (green at run time).
- PATH-env note: `test_autonomous_loop::test_full_run_safe_echo_objective`
  discovers providers via `shutil.which()`; under a stripped tool PATH
  (no opencode/codex/claude/cursor-agent) it fails with "no providers
  available"; under the normal login PATH it passes. Environment-dependent
  test, not a code defect — no code changed.
- No-impact verification: no hive serve (8799/8792 free), no leftover
  pnpm/cursor processes from this phase, single studio (pid 1375070), single
  collector (pid 4132184), zero reactors, no cron/systemd edits, no
  task-DB writes, Studio pause intact. Pre-existing services untouched
  (8787/8791/8765; boot-era `pnpm start` pid 739 predates this phase).

## Disk/growth attribution during 6B (operator handoff facts)

- 24 h drift: 85.82% → 89.69% (≈ +4.0 GiB; brief 90.005% touch at 21:24Z).
- Current `/var/log` = 3.7 G: journal **2.5 G**, syslog family ≈1,186 MB
  (syslog.1 737 M, syslog.2.gz 229 M, syslog 220 M); paios tee log >100 M.
- My footprint this phase: cursor repair node_modules **25 MB** + a few pnpm
  store files. `/srv/noaerth/pnpm` (1.1 G content store) is **pre-existing**
  (created 2026-10-09) — not attributable to this phase.
- Recommended 6A package A+B+C+D (6.13 GiB) **no longer closes the gap
  (needs 9.29 GiB)**; arrest growth first, then re-derive the menu.

## Files

- `PHASE6B_HIVE_LIVE_PILOT.md` (this file)
- `PHASE6B_WORKER_EVENTS.json`
- `PHASE6B_THROUGHPUT.json`
- `PHASE6B_AGENT_STACK_HEALTH.json`
- `NOAERTH_PHASE6_HANDOFF.md`
