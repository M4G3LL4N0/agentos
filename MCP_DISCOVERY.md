# MCP Discovery

Status: **IMPLEMENTED** (lifecycle + CLI + storage), registry reads
**UNCONFIGURED** by default, live registry fetch **UNAVAILABLE** unless an
operator configures `config.mcp.registry_base_url`. No auto-install exists
at any state.

Architecture: `DISCOVER → INSPECT → POLICY → TEST → CERTIFY → APPROVE →
REGISTER`, implemented in `src/agentos/mcp_discovery.py` (pure) +
`Store.mcp_candidates` (`mcp_candidates` table) + `services.mcp_*` +
`agentos mcp …` CLI.

- `mcp discover "<query>"` — read-only `GET /v0.1/servers?search=&limit=`
  against the configured registry. Returns `UNCONFIGURED` with no base
  URL, `UNAVAILABLE` on network failure. Candidates land as `DISCOVERED`
  inert metadata.
- `mcp candidates` — list every candidate with lifecycle state.
- `mcp inspect <id>` — `DISCOVERED → INSPECTED` (metadata review only).
- `mcp test <id>` — `INSPECTED → TESTING → TESTED` via bounded *static*
  certification only: schema validity, declared permissions, network
  expectations, excessive surface, secret scan. Startup, enumeration,
  timeout behavior and side effects are reported **unverified** — no live
  sandbox exists in this build, stated honestly in every test result.
- `mcp approve <id>` — `TESTED → APPROVED`, explicit evidence required;
  registers an **unavailable** `mcp:<id>` cell (`availability=False`,
  `health=UNCONFIGURED`, trust `APPROVED_EXTERNAL`) so approval is
  metadata, never a live executor (MCP client transport is **DEFERRED**).
- `mcp reject <id>` / `mcp revoke <id>` — terminal refusals; revoke also
  disables the registered cell.

Stored per candidate: id, name, publisher, source, repository, version,
capabilities, requested permissions, runtime requirements, risk class,
signature/provenance, test result, approval state, timestamps.

Tests: `tests/test_mcp_lifecycle.py` (7, incl. lifecycle, evidence gate,
revocation, restart persistence). **VERIFIED** via green suite.
