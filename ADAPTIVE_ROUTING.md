# Adaptive Routing

Status: **IMPLEMENTED** as explicit, inspectable calibration
(`services.routing_calibration`); self-modification **UNAVAILABLE**
by design (refused, not missing).

Signals available to routing today: capability-specific history
(`capability_stats`), recent failures, measured latency (benchmarks),
known scarcity (`scarcity_profile`), known cost class, tool
reliability (health/readiness), benchmark quality signals. Human
usefulness ratings: accepted as explicit operator weight overrides
only.

Rules enforced:

- Weights are explicit and configurable (`routing.weights`; built-in
  defaults otherwise). `routing_calibration()` reports both the merged
  weights and their source.
- No opaque self-modification: weights change only via operator
  config; the scorer is untouched until calibration is enabled.
- Every route retains its explanation (`federate route --explain` /
  `route --job …`, rationale + candidates + fallback recorded on the
  decision and persisted with the job).

Tests: `tests/test_fabric.py` (calibration determinism via repeated
`routing_calibration()` equality is covered in smokes; scoring
tiebreak determinism in `test_federation.py`). **VERIFIED**.
