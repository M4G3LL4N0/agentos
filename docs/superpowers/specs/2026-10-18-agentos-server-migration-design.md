# AgentOS Server Migration + Hive Integration Design

**Date:** 2026-10-18
**Status:** Approved
**Author:** AgentOS Architect

## Goal

Turn AgentOS 0.5.0 into a reliable, Linux-compatible execution service integrated with NOAERTH's existing autonomous infrastructure on Contabo (100.127.182.126).

## Architecture

### Integration Boundaries

| Layer | AgentOS Responsibility | Reuses Existing |
|---|---|---|
| Objective Management | Create, track, verify, recover objectives | — |
| Planning | Strategy selection, step decomposition | — |
| Delegation | Route tasks to capabilities | AI Mesh provider routing |
| Verification | Evidence collection, quality evaluation | — |
| Recovery | Bounded retries, crash recovery | — |
| Resource Governance | — | AutonomousOS demand/allocation |
| Task Scheduling | — | Hive manifest → REALM Studio tasks |
| Provider Intelligence | — | AI Mesh providers.py + routing.py |
| Storage Admission | — | JanitorOS `space admit` |
| Worker Execution | — | REALM Studio task registry |
| Process Management | — | systemd |

### System Registration

AgentOS registers as a new AutonomousOS system:

```
autonomousos register agentos "AgentOS" 60 /srv/noaerth/agentos/state --minimum 1 --maximum 4
```

Priority 60 places it between PortfolioOS (70) and FriendsOS (50). AgentOS publishes `demand.json` and reads `autonomousos-allocation.json` for its execution budget.

### Hive Adapter

AgentOS connects to the existing Hive at `/srv/noaerth/autonomousos/mesh/hive/`:

- **Submission:** Uses `noaerth.hive.submission/1` manifest schema (1-8 tasks, each with id/title/prompt/level/agent/deps/venture)
- **Storage Admission:** Calls `janitor space admit --bytes <N> --json` before submission
- **Resource Admission:** Reads `resource.json`, checks `allow_new_work` and mode (blocks on SEVERE/CRITICAL/ERROR)
- **Task Registry:** Submits to REALM Studio's `tasks` table at `/srv/noaerth/realm-studio/state/studio.sqlite3`
- **Event Normalization:** Uses `hive/contract.py` for exit code classification and provenance levels

### Provider Integration

AgentOS delegates provider discovery and routing to AI Mesh:

- **Discovery:** `mesh/providers.py` — finds opencode, cursor-agent, codex, claude binaries
- **Routing:** `mesh/routing.py` — cost-aware selection with reliability scoring
- **Admission:** `mesh/admission.py` — lease-based resource caps
- **States:** DETECTED, CONFIGURED, READY, AUTH_REQUIRED, RATE_LIMITED, UNAVAILABLE, UNKNOWN
- **Free-first:** Prefers cost_rank <= 2 providers; blocks unapproved API charges

### JanitorOS Integration

AgentOS requests storage admission before creating substantial artifacts:

- **Before worktree creation:** `janitor space admit --bytes <estimated> --json`
- **Before build artifacts:** Same admission check
- **Protected paths:** Never writes to JanitorOS protected paths
- **Cleanup policy:** Respects JanitorOS cleanup decisions; never inventes a replacement

### Server Edition

- **Python:** 3.12+ (already compatible, stdlib only)
- **Persistence:** SQLite WAL mode (already implemented)
- **Service:** systemd unit with dedicated `agentos` user
- **Paths:** Configurable via `AGENTOS_HOME` environment variable
- **Logging:** Structured event logs (already in EventBus)
- **Resource limits:** Via AutonomousOS allocation + systemd limits
- **Graceful shutdown:** SIGTERM handler with in-flight task completion
- **Crash recovery:** SQLite WAL + objective state persistence

### CLI Integration

- **Standalone:** `agentos <cmd>` — preserved as-is
- **NOAERTH:** `noaerth agentos <cmd>` — delegates to `agentos` CLI
- **Existing:** `autonomousos` CLI — unchanged

## Data Flow

```
User → noaerth agentos "fix the bug"
  → agentos CLI
    → AgentOS service layer
      → Engine: create objective, plan, decompose
        → Hive adapter: submit task manifest
          → JanitorOS: storage admission
          → AutonomousOS: resource admission
          → REALM Studio: task registry
        → AI Mesh: provider routing
        → Worker: execute via selected provider
      → Engine: collect evidence, verify
      → Engine: update objective state
    → CLI: report result
```

## Non-Goals

- No Redis/PostgreSQL/Kubernetes
- No competing hive scheduler
- No replacement for provider routing
- No duplication of JanitorOS
- No automatic GitHub pushes
- No paid model calls without authorization
- No public network exposure

## Risks

| Risk | Mitigation |
|---|---|
| Disk pressure (79% full) | JanitorOS admission control; AgentOS respects disk limits |
| AutonomousOS not running | AgentOS operates in standalone mode; registers when available |
| Hive submission fails | Bounded retries with backoff; objective marked BLOCKED |
| Provider unavailable | AI Mesh routing falls back to next provider; honest health reporting |
| systemd resource limits | Configurable via unit file; respects AutonomousOS allocation |

## Acceptance Criteria

1. AgentOS receives an objective via `noaerth agentos` or `agentos` CLI
2. AgentOS selects existing hive infrastructure for task submission
3. Hive accepts the task (storage + resource admission pass)
4. A worker executes a safe task
5. AgentOS receives actual execution evidence
6. Verification runs and produces a verdict
7. Objective state updates (COMPLETED/FAILED/BLOCKED)
8. Restart preserves objective state (SQLite WAL)
9. systemd service starts, stops, and restarts cleanly
10. `noaerth agentos status/doctor/objectives/workers/providers/logs/recover` all work
