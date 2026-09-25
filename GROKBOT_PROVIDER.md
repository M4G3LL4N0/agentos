# GROKBOT_PROVIDER.md — grokbot-office integration

Status: **ADAPTER-ONLY, VERIFIED READ-ONLY**.

## What exists

`src/agentos/adapters/grokbot_office.py` is a read-only operating bridge to
the **grokbot-office** control plane CLI (`tsx src/cli.ts`), plus:
- adapter registration in `registry.py` under the capability
  `grokbot-office` (kind AGENT, 8 operations below);
- a `grokbot-office` executor cell (T5, external, CONFIDENTIAL, BATCH) with
  live posture probe (`mode` command → a measured `CONSERVE` or other posture).

### Adapter surface

- `READ_ONLY_COMMANDS` map — commands guarded as read-only:
  validate, route, should-create, allowed, mode, usage:report, roster,
  bridge:check.
- `FORBIDDEN_GROKBOT_COMMANDS` — state-changing commands that are never
  allowed through the bridge (usage:set/reset/ondemand/harvest/log,
  materialize:\*, bootstrap:set, webhook, handoff, create-card, bridge:bundle).
- `probe()` — honest three-step check: repo present → `tsx` present/`node_modules` →
  read-only `validate` exits 0; returns AVAILABLE (all), MISCONFIGURED
  (partial), or UNAVAILABLE (missing root/exe).
- `execute()` modes:
  - **SIMULATED** — returns the formatted command string (dry-run).
  - **INSPECT** — returns the argv that would be spawned.
  - **LIVE** — gated: requires authorization passing `gate_allows`
    (`live`/`approved` grant) else `AuthRequired`; any argv whose first
    token hits `FORBIDDEN_GROKBOT_COMMANDS` or carries an embedded secret
    (`contains_secret`) is refused.

## Measured behavior (reference host, 2026-09-21)

- `probe()` → **AVAILABLE** (repo + local `tsx` + `validate` exit 0).
- SIMULATED dry-run and INSPECT argv verified.
- LIVE without authorization → **blocked** (no auth key in the engine
  authorization store).
- Posture read: a local `mode` result was parsed into `cell.posture`; the
  public release omits account-specific usage values.

## Explicitly NOT done

- No `usage:set/reset/ondemand/harvest/log`, `materialize:*`,
  `bootstrap:set`, webhook/handoff, create-card, bridge:bundle —
  hard-refused by the adapter and never invoked.
- No account creation, no quota/trial manipulation, no paid actions,
  no deployment.
- No full CLI passthrough: only the 8 read-only commands are mapped.