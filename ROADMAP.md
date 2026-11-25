# Roadmap

1. Keep the v0.5.0 release reproducible on the supported Python matrix.
2. Add a production authentication and authorization boundary for the HTTP API.
3. Add execution sandboxing and stronger approval UX.
4. Build a first-class MCP client behind explicit review and credential policy.
5. Add portable schema migrations and reviewed external-worker adapters.
6. Expand measured federation, resource-governor, and learned-routing evidence.
7. Improve portability and deterministic backup manifests without publishing local state.

## NOAERTH APN fleet integration — planned

The AgentOS north star remains the provider-neutral engine mission. The APN contract describes how AgentOS should serve as the sole Core scheduler across NOAERTH Core, Forge, Storage, Telegram, and Tower. It does not claim that those systems are connected.

Follow the staged gates in [APN_FLEET_INTEGRATION.md](APN_FLEET_INTEGRATION.md):

1. Establish authenticated read-only baselines and confirm the deployed Core revision.
2. Inspect and harden the Core API boundary; implement idempotent task admission, expiring leases/fencing, bounded retry, transactional reporting and reconciliation only where source inspection proves the gap.
3. Run one isolated Forge task with an independent verifier and authenticated Storage receipt.
4. Connect Telegram and Tower as policy-bound interfaces after Core authority is proven.
5. Test outages, split-brain, replay, kill switch and off-host restore.
6. Measure sustained outcomes under predeclared cost, safety, quality and reliability limits.

These are **PLANNED** integration gates. They do not change AgentOS's local release status or the NOAERTH APN operational score. A runtime change must update `CURRENT_STATE.md`, `BUILD_MAP.md`, `EXECUTION_LOG.md` and relevant tests as required by `AGENTS.md`.

`VERIFIED` means covered by automated tests or a recorded local run. `OPTIONAL`, `UNCONFIGURED`, `EXPERIMENTAL`, and `PLANNED` items are not live claims.
