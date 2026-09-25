# Resource Governor

Provider-neutral usage governor + supervisor operating model, additive to the
federated fabric (v0.4.0 → v0.4.1). Genuinely useful: usage is *data you
import*, never optimistic fabrication.

## Status

- **IMPLEMENTED + VERIFIED** (tests + live CLI, isolated homes, zero cost):
  usage snapshot model (`resource_governor.py`), usage ledger
  (`usage_ledger.py`), governor routing (`governor_route`), four-way
  separation (`separation_profile`), verification-first task model
  (`task_verification.py`), supervisor operating model (`supervisor.py`),
  store tables + services + CLI.
- **NOT IMPLEMENTED / DEFERRED**, stated honestly: no live supervisor loop,
  no ChiefOfStaff role, no model-based judge; SNIFFER/OBSERVE phases remain
  UNCONFIGURED.

## Honesty model for usage

No usage value is ever invented.

- `parse_snapshot` validates percentages strictly (0–100, NaN/booleans
  rejected) and parses `resetsAt` only when present — a reset date is never
  synthesized.
- `freshness(asOf)` classifies LIVE (≤ 12 h default) / STALE / UNKNOWN. A
  missing or future `asOf` is UNKNOWN, never assumed fresh; a stale snapshot
  is relabeled `STALE_CACHE`, never presented as live.
- `resolve_usage` resolution precedence (freshness dominates source):
  fresh official → fresh audited-unofficial → fresh manual → stale-known
  (with an explicit STALE warning) → UNKNOWN. `UNKNOWN` is never treated as
  0% or unlimited.
- The AgentOS data layer accepts **no externally-supplied offset sources**;
  `usage import` refuses `allowed_external_offset_sources`.

## Usage sources

`UsageSource`: `OFFICIAL_UI`, `OFFICIAL_API`, `AUDITED_UNOFFICIAL`,
`MANUAL`, `STALE_CACHE`, `UNKNOWN`.

## Storage (additive, sqlite)

Four new tables (+ counts/integrity updates):

- `usage_snapshots` — validated snapshot data as imported (upsert per
  `provider|account`), not fabricated.
- `usage_ledger` — durable usage accounting records du jour (data in, data
  out; summaries sum only what was recorded).
- `routing_outcomes` — every governor routing decision + its result.
- `job_verifications` — verification-first records per job.

## Governor routing

`governor_route` keeps **all federation safety gates** — capability, data
class, quality floor, latency, HS-external isolation, health — via the
existing `score_executor_cell`, then cost-ranks the *eligible* cells by total
resource cost:

```
quotaScarcity · money · latency · humanAttention · failureProbability ·
verificationCost · contextTransfer · computerAvailability · sessionAffinity
```

- Weights come from explicit `governor.weights` config; otherwise built-in
  defaults. Negative weights are rejected loudly. Weights only steer cost
  ranking; they never bypass safety gates.
- `failureProbability` is learned **only** from recorded `routing_outcomes`
  (verified/failed per chosen cell). Unknown history adds no cost; a
  cheap-but-failing worker loses to a stronger one via expected failure cost.
- Every call records a `routing_outcome` (PENDING). `usage/… governor verify
  --job <id> --status VERIFIED|FAILED` records the real result, feeding the
  learning loop.

## Four-way separation

`separation_profile(cell)` — a role/identity **never** implies a model or a
computer. `identityPersistent`, `preferredRuntimes`, `preferredModels`,
`computerAffinity`, `requiredCapabilities`, `supervisor`, `workerPolicy`,
`verificationPolicy` are explicit, separately configured preferences.
Always `separation: MAINTAINED`.

## Verification-first task model

`task_verification.py`:

- `TaskVerificationStatus`: UNVERIFIED → REQUIRED → PENDING → VERIFIED/FAILED
  (NOT_REQUIRED for skip paths). Illegal transitions are refused.
- Verification types include tests/build/runtime/screenshots/api_response/
  source_verification/independent_model_review/file_diff/deterministic.
- **Verifier/worker separation without waste**: deterministic evidence types
  (`deterministic`, `file_diff`, `source_verification`) never trigger a
  second model/verifier call; meaningful types prefer a different executor
  that declares a verification policy or a VERIFIED+ quality ladder.

## CLI

- `agentos usage status|snapshots|resolve|import|refresh|add|ledger`
- `agentos governor weights|route|outcomes|verify`
- `agentos supervisor status`

All data-only; `usage refresh` reads only an operator-configured local
export file (`usage.data_dir`), never the network.

## Tests

`tests/test_resource_governor.py` (26): validation/percents, reset honesty,
source precedence, freshness dominance, weights defaults/overrides/rejects,
routing consumes scarcity state, backup on no-eligible cells, ledger
persistence + sums, verification transitions + separation, supervisor
honesty, and store/service integration (import→resolve, ledger add,
outcome lifecycle, bad-verify refusal).