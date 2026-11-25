# NOAERTH APN Fleet Integration Contract

**Status:** Implementation contract; not evidence that the fleet is connected  
**Reviewed:** 2026-11-25  
**Owner:** NOAERTH operator; AgentOS maintainer owns Core implementation  
**Core implementation repo:** `M4G3LL4N0/agentos` (AgentOS has its own `NORTHSTAR.md`; this document connects that engine to the separate APN fleet objective)  
**Tower source:** Unresolved. The Forge Control Vercel project is manually deployed, has no verified Git source in its project metadata, and is not a scheduler.  
**Operational score:** 1.3/5 per Round 11 local scorecard; do not raise from this document or its PR.

## Outcome

Deliver one system that accepts a human outcome, creates a bounded and authorized Core task, executes it on an isolated Forge worker, independently verifies the exact result, preserves a retrievable and authenticated artifact in Storage, reconciles the task in Core, and reports the correlated outcome through the authorized Telegram channel and Tower. It must keep working when the Mac or ChatGPT session is unavailable.

The existing AgentOS north star governs the provider-neutral engine. This contract governs its intended integration into the three-server NOAERTH production network. AgentOS remains the only scheduler and task authority; this plan does not create a competing OrgOS/Paperclip/LangGraph scheduler.

## Evidence vocabulary

- **Repo verified:** visible in the AgentOS source or its tracked tests/docs. This does not mean deployed.
- **Observed live:** collected from the named live host/API at a timestamp with source identity.
- **Claimed/imported:** provided by an operator or imported file, not independently authenticated.
- **Target:** required but not yet demonstrated.
- **Blocked/unknown:** required source or evidence unavailable. Never display as green.

AgentOS repository documentation reports v0.5.0, a local release gate with 710 tests, CLI/API/MCP interfaces and local runtime proofs. Those are repository claims as recorded in `CURRENT_STATE.md`; they do not prove Core 01 is running this revision, that the API is authenticated, or that Forge/Storage/Telegram are connected. Recheck the repo and deployed hosts before each operational score change.

## Authority and deployment map

| Component | Intended responsibility | Boundary and required proof |
|---|---|---|
| Human operator | Outcomes, policies, approvals for consequential actions, spending, identity changes and production release | Approval binds actor, task, exact action, scope, expiry and idempotency key |
| Core 01 / AgentOS | Sole global scheduler; task ledger, policy, approval, lease/fencing, retry, outbox and final reconciliation | One leader epoch; authenticated API; durable restart; duplicate and stale-worker tests |
| Forge 01 | Isolated execution factory; OpenCode default, optional OpenClaw ACP and scoped specialist adapters | A task-specific lease and worktree; no control-plane secrets; no merge/deploy authority by default |
| Storage 01 | Artifact/evidence archive, backups and restore | Authenticated receipt with object ID, digest, byte size, retention, retrieval and restore test |
| OpenClaw | Telegram intake/reporting and optional ACP session control | Allowlisted request surface; it proposes Core work and cannot bypass policy or leases |
| Hermes | Narrow research/MCP specialist | Per-task tool allowlist and resource cap; no global scheduling |
| Paperclip | Agent/run/budget visibility and approval UI | Read-only mirror until it reconciles against Core; never a second task ledger |
| LangGraph | Optional durable sub-workflow for a demonstrated branching/checkpoint gap | Core owns task identity, approval, lease and terminal state |
| Telegram | Mobile command and status surface | Authenticated allowlist, replay/dedupe/expiry controls; a delivery receipt proves delivery only |
| Tower / Vercel | Human-visible operations view and evidence import | Read-only; report source/freshness and unknown state; never infer live status from local data |
| Mac / local CLI | Operator development, export and fallback access | Convenience only; fleet operation cannot depend on it |

Protocols solve different problems: ACP manages an agent session; MCP grants scoped tool access; authenticated HTTP/webhooks carry control events; Git branches/PRs isolate code; Storage preserves artifacts. They are complementary.

## Task contract

Every globally scheduled task must carry:

- stable task, parent-goal, deduplication and idempotency IDs;
- project/data class, objective, dependencies, acceptance criteria and risk tier;
- authorized actor and approval receipt where required;
- allowlisted tools/actions, deadline, retry cap/cooldown, budget and resource ceiling;
- worker identity, expiring lease, monotonically increasing fencing epoch and cancellation state;
- exact branch/revision and harness/model versions;
- independent verifier identity, reproducible check output and exact revision binding;
- artifact object IDs, byte sizes, digests, trusted issuer/signature or equivalent authenticated transport, collection time and replay-resistant sequence;
- Storage retention/retrieval receipt, Core reconciliation event and Telegram delivery receipt if sent.

A digest detects byte changes but does not authenticate who produced the bytes. The receiver must authenticate the issuer, bind claims to task/event/revision/time, reject replay, fetch the object and recompute its size and hash.

Terminal success means: verified on the exact worker revision, required artifacts retrievable, Storage receipt accepted, Core terminal state reconciled, and policy-required report delivered. A completed adapter call or worker claim is not completion.

## Implementation waves and stop gates

### Wave 0 — Establish authority and baseline

**Build:** map the exact AgentOS revision and deployment to Core 01; identify Core/Forge/Storage host identities; inspect Vercel source linkage; record Telegram bot/channel identity without secrets; find existing databases, backups, timers, OpenClaw/OpenCode/Hermes/Paperclip/LangGraph installs and policy configuration. Prefer the existing Core and task store.  
**Gate:** signed/otherwise authenticated, timestamped read-only snapshot from all three hosts; source digests and blocked paths recorded; stale and unavailable states visible; canonical Git source and ownership recorded.  
**Stop if:** host identity cannot be verified, secrets would be exposed, or two schedulers claim authority.

### Wave 1 — Harden the Core authority boundary

**Build:** put the API behind private networking and strong authentication/authorization before remote access; keep loopback-only use where auth is absent. Add project/data-class scopes, policy decisions, append-only approval records, request dedupe, expiring leases, fencing epochs, cancellation, bounded retries, transactional outbox and reconciliation using the existing service/store architecture. Version schema changes and back up before migration.  
**Gate:** concurrent duplicate requests return one stable task; expired/stale workers cannot commit; restart preserves approvals/leases/outbox consistently; unauthorized and cross-project requests fail closed; every transition is auditable.  
**Do not:** expose the unauthenticated API publicly, add another scheduler, or allow adapter code to read server credentials.

### Wave 2 — Connect one isolated Forge worker

**Build:** a Core-issued lease adapter creates a task-scoped worktree; begin with one harmless deterministic task; capture exact commit and resource/time use. OpenCode is the default harness. Add OpenClaw ACP or Hermes only as separate adapters when host install, credential, scope and value are verified.  
**Gate:** one worker executes only its lease; cancellation and expiry fence late writes; worker cannot merge or deploy; independent verifier runs in a separate identity/workspace against the same immutable revision.  
**Stop if:** same writable checkout is shared, lease cannot be enforced, or task secrets cross project boundaries.

### Wave 3 — Verify, archive, reconcile

**Build:** an independent verifier re-runs declared acceptance checks; Storage receives artifacts and a signed/authenticated manifest; Core checks object retrieval/hash/size/retention before accepting completion. Make archive and Core updates idempotent and recoverable after interruption.  
**Gate:** task success cannot be created from a worker claim; tampered, missing, expired, replayed or wrong-revision evidence is rejected; partial failure recovers without duplicate side effects.

### Wave 4 — Add Telegram and Tower as interfaces

**Build:** Core outbox emits correlated events to one allowlisted Telegram bot/channel; inbound requests are proposals to Core. Add actor/chat allowlists, message/update dedupe, expiry, approval binding, replay protection, prompt-injection handling and bounded retries. Tower reads authenticated summaries and shows what is running, what shipped with revision/evidence, blockers, resource/spend risk and next approved action.  
**Gate:** real Telegram delivery ID is correlated to a Core event; duplicates/replays/unauthorized chat and expired approvals are rejected; Tower shows source, freshness and stale/unknown correctly; delivery failure never reports success.

### Wave 5 — Fault, recovery and restore

**Build:** bounded service restart and self-repair with circuit breakers, kill switch, independent post-action recheck and event log. Keep independent dead-man monitoring outside Core. Exercise worker termination, provider limits, Telegram outage, Storage outage, Core outage/restart, stale leader, network partition and off-host restore.  
**Gate:** zero split brain, duplicate irreversible side effects, secret leaks, silent task loss or false-green reporting in the test set; restore meets predeclared per-data-class RPO/RTO; Core/Telegram simultaneous outage is detected externally.

### Wave 6 — Sustained useful autonomy

**Build:** run under frozen SLO/resource/cost budgets; track project-specific realized value from its authoritative source; compare free-first routing on held-out tasks; enforce project WIP/budgets and starvation aging. Use Paperclip as a mirror. Add LangGraph only for proven workflow needs.  
**Gate:** initial council proposal: rolling 30 days and at least 20 representative completed tasks; extend if diversity is insufficient. SLOs must be calibrated from baseline and frozen before the sample starts. Maintain hard safety invariants and show measured value, quality, cost and recovery improvement.

## Required operational score evidence

Keep these five equally weighted categories separate: truthful visibility; Core authority; verified production; durable recovery; sustained useful autonomy. Current Round 11 baseline is 1.3/5 and remains unchanged until fresh receipts pass gates. A PR, test fixture, documentation page, preview deploy or model simulation earns no operational points.

For any score increase, attach source identity, collection time/freshness, task/event IDs, exact revision, approval and lease evidence, verifier result, artifact digest/size and retrieval receipt, Core reconciliation, delivery ID if applicable, failure/recovery records, and an independent reviewer. Score only the gate whose evidence passed.

## First executable job

After Wave 0 and the live Core lease/API have been inspected, run one reversible task: create a new file containing a fixed string inside a disposable task worktree. It must not touch production, credentials, external accounts or user data. Require a Core lease and idempotency key, distinct verifier identity, exact-revision content/hash check, Storage archive/retrieval/size/hash receipt, Core reconciliation, and one correlated Telegram report. Then safely replay the request and expire the worker lease; both must be rejected or deduplicated without duplicate side effects.

## Decision rules

1. Do not replace AgentOS, rewrite the Tower or introduce Temporal until a measured gap requires it.
2. Do not connect this APN contract to the similarly named Silicon Control Tower repository; that repository is a separate prototype.
3. Do not treat a manually uploaded Tower bundle or Vercel preview as canonical source control.
4. Do not sync docs to Core/Forge/Storage until the canonical Git source and each host's sync receipt are established.
5. Do not enable paid model fallbacks or raise concurrency without explicit policy and measured budget headroom.
6. Keep PR creation, merge and production deployment as distinct approval gates.
7. Every round chooses one blocking gate, implements one measurable jump, runs targeted tests and an independent review, publishes evidence, updates the scorecard, and selects the next gate.

## Round 12 starting point

The AgentOS repository is a likely Core implementation source, but it has not been inspected against a live Core host in this task. The Forge Control bundle is currently available only as a local working copy and manual Vercel deployment; the Vercel project reports SSO enabled for all deployments and production unchanged. No Core/Forge/Storage SSH session, Telegram delivery, or live agent session was available in this execution environment. Therefore this PR is the first codebase-alignment step, not the fleet integration itself.

Immediate next step: create a reproducible, authorized read-only baseline from Core/Forge/Storage, then map the actual AgentOS service/store/API contracts to Wave 1 before changing runtime behavior.
