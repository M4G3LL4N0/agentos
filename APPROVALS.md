# Approvals

Status: **IMPLEMENTED** (canonical classes + enforcement points).
Human UX beyond CLI grant/revoke **DEFERRED** (no new surface).

Canonical classes (`policies.ApprovalClass`, least → most
consequential): `READ_ONLY`, `LOCAL_REVERSIBLE`,
`EXTERNAL_REVERSIBLE`, `EXTERNAL_CONSEQUENTIAL`, `FINANCIAL`,
`LEGAL`, `SECURITY`, `IRREVERSIBLE`.

Assignment (`policies.approval_class_for`): derived from the task's
operation text, data class and externality — by task/policy, never by
the executor. An executor declares `maxApprovalClass`
(default `READ_ONLY`) and **cannot reduce** the assigned class; an
over-class route is refused with a recorded reason (`trust_gate`).

Enforced across: local executors (existing policy gates), A2A remote
cells (`_execute_a2a_cell`: trust gate + LIVE-needs-evidence),
MCP candidates (evidence-gated approve; approved cells unavailable
until a transport exists), Grok cells (plan-before-premium +
approval evidence, unchanged), browser workers (BROWSER-typed,
unavailable until Chrome is measured).

Failover (`failover.run_with_failover`) and pipelines
(`failover.run_pipeline`, max 5 stages) are bounded
(`max_attempts`/`max_executor_switches`/`max_tier_escalations`/
`max_scarcity_escalation`), record every fallback reason, and always
terminate.

Tests: `ApprovalTrustTests` + `FailoverTests` in `test_fabric.py` (8).
**VERIFIED**.
