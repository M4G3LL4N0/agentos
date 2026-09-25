# Smallest Capable Team

`plan_team()` in `src/agentos/efficiency.py`. Builds the smallest team that
satisfies the request — no fan-out without justification.

## Policy

- **1 worker is the overwhelming default.** `plan_team()` with no arguments
  returns size 1, roles `["worker"]`, parallel 1.
- **0 workers** only when the request is fully self-contained (no team at all).
- **supervisor + worker(s)** only on meaningful decomposition or ≥2
  independent subtasks.
- **verifier joins only on justification**: `quality_floor="VERIFIED"` or an
  explicit `verification_burden` — deterministic evidence never triggers a
  second model call.
- **fan-out** only with independent subtasks *plus* the verified quality
  floor; large fan-out requires explicit economic justification.

## Output

`TeamPlan` → `to_dict()`: `size`, `roles`, `rationale`, `parallel`. Exposed
via `services.efficiency_team()` and `agentos efficiency team [--decomposable
--subtasks N --quality-floor VERIFIED ...]`.

## Facts

- Default `agentos efficiency team` → `size=1 roles=worker rationale=single
  worker; no fan-out without justification`.
- `--decomposable --subtasks 3 --quality-floor VERIFIED` →
  `size=3 roles=supervisor,worker,verifier`.