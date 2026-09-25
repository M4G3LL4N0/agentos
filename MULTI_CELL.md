# Multi-Cell Federation

Status: **IMPLEMENTED** (config-driven cells, specialization routing,
isolation enforcement). Additional Grok cells **UNCONFIGURED** by
default — nothing is assumed to exist.

`ExecutorCell` carries the full CellDefinition extension
(`federation.py`): id, provider, owner_label, authorized, health,
usage band, used_pct, reset_at, live_roles, virtual_role_count,
credential_boundary, data_classes_allowed, persistent_computer,
persistent_browser, transport, priority, last_verified_at, trust
(default `UNTRUSTED`). All fields optional with safe defaults; old
stored cells load unchanged.

Loading (`services._federation_cells`): `federation.cells` config
entries are materialized as **metadata only — never probed, never
contacted**. Health stays as configured (`UNKNOWN` unless the operator
recorded a verification with `lastVerifiedAt`).

Current real cell: `grokbot-office` (the `grok-primary` environment) is
probed read-only as before; `authorized=true`, `CONSERVE`, `usedPct≈79`
is measured posture when readable, never assumed. Sibling cells
(`grok-research`, `grok-private`, `grok-family`) are examples only.

Within one Grok cell, native GrokBot A2A is preferred; across cells,
`JobEnvelope`/A2A federation applies. Specialization: an authorized
cell declaring `config.sessions` earns a recorded `session-match`
bonus for jobs requiring that session — credentials are never
duplicated to obtain it.

Isolation (tested): per-cell config objects are never shared; secret
values are rejected at cell construction; no cross-cell credential
assumptions exist in routing, storage, or envelopes.

Tests: `tests/test_fabric.py::CellIsolationTests` (4) + live smoke of a
simulated `grok-research` cell (loads `UNKNOWN`, uncontacted).
**VERIFIED**.
