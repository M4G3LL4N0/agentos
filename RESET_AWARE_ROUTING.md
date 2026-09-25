# Reset-Aware Routing

Status: **IMPLEMENTED** (modes + harvest guards + scarcity profiles).
Live quota feeds **UNAVAILABLE** — all figures are operator-configured
or measured posture, never fabricated.

Modes (`scarcity.normalize_reset_mode`, default `PRESERVE`):

- `PRESERVE` — scarce quota heavily penalized
  (`FREE 0 / LOW 2 / NORMAL 5 / HIGH 20 / SCARCE 60 / CRITICAL 200`).
- `NORMAL` — ordinary routing, no scarcity adjustment.
- `HARVEST` — scarcity relief (max −15) only when **all** hold:
  human explicitly enabled it (`routing.harvest.enabled`),
  reset time is known/reliable (`reset_known`),
  meaningful allowance would otherwise expire (`allowance_expiring`),
  useful queued work exists (`queued_work` non-empty).
  Anything less falls back to `PRESERVE` with a recorded refusal reason.
  Quota is never consumed merely because it resets.

Scarcity profile per executor (`scarcity.scarcity_profile`): monetary
cost class, quota scarcity, reset window, used/remaining percent **only
if actually known** (`None` otherwise), compute scarcity, latency
class, human-attention cost, external-dependency risk. Qualitative
classes only: `FREE/LOW/NORMAL/HIGH/SCARCE/CRITICAL`.

Current Grok primary: scarcity `HIGH`, mode `CONSERVE` (measured
`usedPct≈79` when the posture read succeeds). Scarcity penalizes; it
never prohibits — a genuine persistent-computer/cloud-browser/offline
requirement can still select Grok, with approval.

Harvest backlog classes (`CapabilityScout`, `GitHubArchaeologist`,
`InternetChangeDetector`, `UnknownUnknownScout`, `SerendipityScout`)
exist as data and stay disabled unless explicitly authorized.

Tests: `tests/test_fabric.py::ScarcityTests` (4) + `usage status` CLI.
**VERIFIED**.
