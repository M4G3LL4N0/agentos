# NOAERTH Phase 5B — Verified Parallel Hive Production

Date: 2026-10-21 · Branch `main` (local only, not pushed) · Server `vmi3635094` (Contabo, UTC+2)

## Outcome

**BLOCKED at first controlled worker admission — by design, with evidence.**
No hold was bypassed, no registry written by me, no paid inference, no push.
All other phases (P0, P1, P4–P9) completed with read-only observations and
hermetic tests. This document is the honest record: the phase is *not* PASS
and never claims one.

## P0 — Admission gate state (live)

- AOS governor `classify()`: **SEVERE**, `allow_new_work=false`, sole trigger
  `disk_used_pct_80plus` (disk ≈84.3–84.65%, floor 80%). CPU lane self-cleared
  (cpu 29–57%). `notes=[]`, no expiry; clears only when disk < 80%.
- Bytes to free for <80%: **~4.45–4.77 GiB**. Authorized reclaim: **0 B**
  (JanitorOS `deletion_autonomy: DISABLED`, approvals empty, scan = 0 B).
- Published snapshot `/var/lib/agentos/resource-snapshot.json` fresh;
  growth samples accruing in `resource-growth.json` (51+ samples, capped).

## P1 — Production invocation path (traced, no edits)

Real chain in `/srv/noaerth/realm-studio/studio-src/studio.py`:

1. `submit_manifest()` → registry insert + `HIVE_TASK_SUBMITTED` event
2. `execute_one()` (line 372): `resource_ok()` → `eligible()` → QUEUED→RUNNING
   → worktree add/detach → `run_agent` → verify → events
3. `run_agent` routes via `_run_agent_raw` (line 184): cursor / claude / codex /
   opencode
4. `godot_verify()` (271) / `integrate_existing()` (340)
5. Crash recovery: `recover()` (519) RUNNING→BLOCKED + `WORKER_CRASH`;
   `auto_retry()` (527) max 3 attempts, 1800 s cooldown, skips permanent faults

## P2 — First controlled worker: BLOCKED (evidence)

Two Hive submissions, both refused *before* any registry write:

| # | Manifest | Level | Gate | Result | Exit |
|---|----------|-------|------|--------|------|
| 1 | `candidate-manifest.json` (SERVEROS-5B-PILOT) | LOW | serveros_snapshot_gate | `serveros_low_priority_deferred` (band PRESSED, retry 60 s) | 3 |
| 2 | `probe-manifest.json` (SERVEROS-5B-PROBE) | MEDIUM | resource_admission | `resource_governor_hold` with full triggers (`mode SEVERE`, `allow_new_work false`, `disk 84.3%`, `reasons[3]`, `state_ts`) | 3 |

Registry unchanged: 67 tasks before/after, 0 SERVEROS rows.
Candidates preserved for when the hold clears. Full payloads:
`PHASE5B_WORKER_EVIDENCE.json`.

## P3 — Second worker

Not attempted: contingent on first admission; the hold makes an identical
refusal deterministic, so a second side-effecting attempt would add no
evidence. Decision: BLOCKED (recorded in the benchmark artifact).

## P4 — Hive teams/status (read-only)

- `teams`: realm roles assigned — Coding worker ×9, Debugger (R21-10),
  Design worker ×3, Integration engineer (single writer), Performance ×2.
- `status`: studio `r31.2-production-unblock-20261018`; realm 46 COMPLETE /
  14 QUEUED / 1 BLOCKED (R21-10 — *repairing it unblocks 14*, critical path
  length 9); friendsos 87 COMPLETE, healthy. 6 approvals pending (5 release
  reviews + native-Mac review gates). 3 route/model combos flagged `wasting`.
- Host line in status: load ~4.5, mem 9.91 GiB, disk 84.3% (same hold state).

## P5 — Agent capability inventory

Full artifact: `PHASE5B_AGENT_HEALTH.json`. Summary: deterministic executors
(shell/filesystem/echo/godot) usable without paid inference; production
routers opencode/claude/codex/cursor binaries present (portfolio);
opencode MISCONFIGURED under agentos user env; github/xai AUTH_REQUIRED;
hermes/langgraph/openclaw/grok/grokbot-office/openhands UNAVAILABLE;
langfuse ABSENT; chatbridge-client present; MCP bridge = module, not running.

## P6 — Failure recovery (tests added)

New `TestCrashRecoveryIntegration` in `tests/test_worker_admission.py` (3 tests):

1. ACTIVE lease orphaned → re-register same task replaces (exactly 1 record,
   newer `created_epoch`) → complete → RETIRED + reap requested (disposable).
2. Crashed lease never blocks an unrelated new task; independent completion.
3. `rollback_critical` completion stays `COMPLETED_PENDING_REAP`, never
   auto-reaped.

Studio-side recovery path read in P1 (`recover()` / `auto_retry()`).

## P7 — Control Tower

- Patched `collector.serveros_panel()` (backup `.bak-5b-20261021`): added
  `aos_mode`, `aos_allow_new_work`, `capacity_constraints`, `studio_active`,
  `active_leases`, `active_leases_by_class`, `provider_cooldown`,
  `disk_growth_bytes_per_day`. Panel live in `live.json` (age ≈1 s), current
  values: mode SEVERE, allow false, constraints
  `[band_pressed, autonomousos_allow_new_work_false]`, leases 0,
  growth −7.83 GB/day measured (churn), studio fields honestly `null` during a
  Studio DB lock (UNKNOWN, never fabricated).
- HTTP endpoint `:8787` → 302 (alive). `attention[]` carries disk-pressure +
  JanitorOS warn-floor warnings. `fleet_health` summary exposed
  (fully_healthy false).

## P8 — Release readiness (report only, nothing approved)

- 6 releases `REVIEW_REQUIRED`: 5 with `approvals=[]` and explicit
  rollback commands (publishing ×2, app-factory, demo-greeter ×2);
  REL-realm-e5b5dd7f has only the CLI REVIEW_REQUIRED transition record, no
  final approval, no rollback command.
- **Rollback targets verified read-only** (as portfolio owner; root reads
  trip git's dubious-ownership guard): all 5 rollback commits EXIST in the
  venture repos — publishing `664b20e`/`e4148ce`, app-factory `7a1a584`,
  demo-greeter `ec7177f`/`991cc24` — each is the parent baseline behind the
  reviewed tip (HEADs: `7db7310`, `73b75d0`, `5beb597`). Rollback commands
  are functional as recorded. REL-realm-e5b5dd7f has none.
- `releases/`: 28 archived snapshots; pointer `aos3-release` →
  `aos3-20261013T192232Z`.

## P9 — Verification

| Suite | Result |
|---|---|
| local `python3 -m unittest discover -s tests` | **958 OK** (14 server-only skips: 11 gate + 3 collector), 635 s under host load |
| server (as agentos user, AGENTOS_HOME) | **958 OK**, 123 s (0 skips) |
| autonomousos (server) | **86 OK**, 4.5 s |
| Growth/collector/crash-recovery tests | included on both sides after two-way sync |
| Bundle (refreshed post-commit) | verified by bare clone |

**Two-way test sync performed** (drift found during final audit): the 3
Phase-5A governor tests existed only server-side — synced into the repo;
the 9 growth tests existed only locally — synced to the server. Test-file
sets now identical (63 files, 958 tests each). `src/` drift: none (only
egg-info build metadata).

### Concurrency benchmark (`PHASE5B_CONCURRENCY_BENCHMARK.json`)

Deterministic 40-case matrix (pure `admit_worker` × `compose_snapshot`,
zero side effects): 2 admitted (HEALTHY+allow+storage only); **verified rules**:
AOS-allow-false admits 0, storage-denied admits 0, CRITICAL admits 0,
UNKNOWN (fail-closed) admits 0. Live host admission: all three profiles →
`autonomousos_hold`, retry 60 s. Host: cpu_heavy cap 0 / running 2 /
remaining 0; scheduler active 4; queued demand 5.

## Protections verified

hold not bypassed · registry not written by me · other ventures untouched ·
0 paid calls · no push · 0 unauthorized deletions · UNKNOWN never rendered 0
· no fabricated PASS.
