# ROUTING_ECONOMICS.md — Routing, tiers, premium gating

Status: **IMPLEMENTED + VERIFIED** (pure + tests; measured truthful).

## Tier map (authoritative, `federation.tier_map`)

- LOCAL: T0 platform · T1 engine-owned · T2 credential-owned · T3 delegated ·
  T4 deployment boundary.
- EXTERNAL: T5 premium / tier-gated · T6 fully remote / third-party.

## Routing

`resolve_route(job, cells, approval_evidence=None)` is **pure** (no
persistence, no execution). For each cell it computes a score from:

1. **Capability match** — does the cell support `job.targetCapability`
   (exact id or operation member)?
2. **Data-class gate** — cell's `data_class` must admit the job's data
   class (PUBLIC < CONFIDENTIAL < HIGHLY_SENSITIVE).
3. **Latency gate** — cell's `latency_class` must satisfy job latency
   (LOW < MEDIUM < BATCH).
4. **HS-external guard** — HIGHLY_SENSITIVE jobs never route to
   `external` cells.
5. **Health gate** — MISCONFIGURED/UNAVAILABLE/DOWN cells score zero.
6. **Policy level + tier preference** — lower tiers win ties; INVITED
   beats OBSERVABLE beats ISOLATED.
7. **Posture** — a deputized cell's usage posture qualifies its score
   (e.g. grokbot-office only when posture != EXHAUSTED).

The best cell becomes `decision.chosen` with a `reason` string. No chosen
cell ⇒ decision with `chosen=None` and a clear reason.

## Premium gating

- A chosen EXTERNAL (T5+) cell ⇒ `decision.premiumGated=True` and, when no
  approval evidence is present, a `plan_before_premium` object is
  produced: `plan` (steps), `cost_model` (usage delta POST vs PRE),
  `telemetry`, and required `approvals`.
- With approval evidence (`live`/`approved` grant passing `gate_allows`),
  the premium path may execute via the read-only bridge only.
- `federate_submit` never auto-executes a premium-gated job — it persists
  and returns the plan with `executed=False`.

## Purpose-built job pre-submit hooks

`hookup_job_request`, `guard_job_request`, `approve_job_request` (purpose-built
for this mission) classify params, gate lat/data, and attach approval
evidence to single-job requests before submission.

## Verification

- Tests: tier map (LOCAL/EXTERNAL split, T5/T6 EXTERNAL), scoring, gates,
  premium gating, echo→local-tools routing, grokbot-office routing at
  exactly BATCH+CONFIDENTIAL, HS-external exclusion, posture-qualified
  scoring (EXHAUSTED/SATURATED tests).
- Live: `federate status --refresh` shows both cells; route of an
  echo job lands on `local-tools`; premium route produced a plan and did
  not execute without approval.
## Fabric economics extension (IMPLEMENTED + VERIFIED, 2026-09-22)

- Generic scarcity profiles (`scarcity.py`, qualitative only, no money
  invented); reset modes PRESERVE/NORMAL/HARVEST with all-guards harvest;
  harvest backlog classes exist but stay disabled.
- Explainable routing (`federate route --explain`, `route --job`):
  task class, required capabilities, quality floor, data class, node
  state, candidates, chosen, why, fallback, premium/scarce + approval flags.
- Calibration is explicit weights only (`routing_calibration`); scoring
  untouched until an operator enables it; every route keeps explanation.
- Bounded failover + 5-stage pipelines with recorded reasons.
