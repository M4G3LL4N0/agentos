# MCP Trust

Status: **IMPLEMENTED** as policy (lifecycle gates + trust levels);
live sandboxing **UNAVAILABLE** (stated, not silent); MCP client
transport **DEFERRED**.

Trust model (`policies.TrustLevel`):

- `LOCAL_TRUSTED` — loopback-native cells.
- `INTERNAL_TRUSTED` — operator-configured internal cells.
- `APPROVED_EXTERNAL` — explicitly approved MCP candidates / A2A peers
  with evidence. Capped at `INTERNAL` data unless re-approved.
- `UNTRUSTED` — default for all public A2A/MCP discovery.

Enforcement (`federation.trust_gate`, `policies.trust_allows`):

- Highly-sensitive work never routes to public/untrusted external
  executors — refused with a recorded reason.
- `CONFIDENTIAL`+ work never routes to `UNTRUSTED` external cells.
- Approval class is assigned by task/policy
  (`policies.approval_class_for`); a cell declares `maxApprovalClass`
  (default `READ_ONLY`) and **executors can never reduce the class**.
- The gate is applied to every *new* remote/external family
  (`a2a-remote`, `mcp:*`) before execution. Legacy local/bridge cells
  keep their historical scorer semantics (unchanged, tested).

Discovery ≠ trust ≠ install, at every state. No credential ever crosses
a trust boundary: cell configs carrying secret-looking values are
rejected at construction (`ExecutorCell.__post_init__`).

Tests: `tests/test_fabric.py::ApprovalTrustTests` (4). **VERIFIED**.
