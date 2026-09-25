# GrokBot Workforce Canonical Architecture

## Overview

This document defines the canonical boundary between **PAIOS** (operating environment),
**AgentOS** (provider-neutral execution/control plane), and **GrokBot Workforce**
(organizational layer with Grok-specific materialization).

---

## Canonical Layers

### 1. PAIOS — Operating System / Application Environment

- Process isolation, filesystem, network, user session
- Hosts AgentOS and GrokBot Workforce
- Provides hardware/compute resources
- **No agent logic here**

### 2. AgentOS — Provider-Neutral Execution/Control Plane

**Core responsibilities:**
- Tasks, state, objectives, executions, verifications
- Providers, models, runtimes, computers, A2A peers, MCP servers
- Routing, cache, ResourceGovernor, verification, workflow, learning, metrics
- CLI, HTTP API, MCP stdio server — all over same shared core

**Key modules:**
- `store.py` — sqlite persistence (thread-safe, WAL)
- `registry.py` — capability catalog, health, readiness
- `engine.py` — execution control loop, recovery, verification pipeline
- `router.py` — capability-aware routing with rationale
- `services.py` — single facade (AgentOS class) for CLI/API/MCP
- `intelligence_cache.py` — L0–L6 shared cache over Store
- `efficiency.py` — triage, team, fingerprint, delta, grok_routing_policy
- `verification_engine.py` — verification state machine, cheapest sufficient
- `coach.py` — lesson generation from execution experience
- `workflow_compiler.py` — compile, match, version workflows
- `skill_model.py` — compact structured skills
- `role_classifier.py` — persistence decisions
- `autonomy_model.py` — progressive autonomy states
- `fleet_monitor.py` — event-driven detection, incident clustering
- `learned_routing.py` — executor profiles, historical signals
- `routines.py` — cheap detector + early exit
- `backup_reconstruction.py` — portable state export (no secrets)
- `persistent_bot_governor.py` — materialization decisions

**What AgentOS does NOT do:**
- No Grok-specific logic
- No persistent supervisor identities
- No browser session management
- No Grok cloud policy

### 3. GrokBot Workforce — Organizational Layer

**Durable supervisor identities (01/02/03 live):**
- 01 ChiefOfStaff — default persistent human-facing front door
- 02 IntelligenceChief — intelligence synthesis, cache, patterns
- 03 ProjectsChief — project execution, workflow, decomposition

**Virtual roles (131 non-live):**
- 133 LocationIntelligence — supervisor for intent/preference/tradeoffs
- 134 GrokBotEcosystemScout — delta pipeline for ecosystem

**Grok-specific materialization:**
- Grok cloud policy (browser session, persistent computer)
- Grok-specific routing bias (grok_bias FAVORED/DISFAVORED)
- GrokBot office bridge (read-only, 8 allowlisted commands)

**What Workforce does NOT do:**
- No task execution (delegates to AgentOS)
- No cache management (uses AgentOS IntelligenceCache)
- No verification logic (uses AgentOS VerificationEngine)
- No workflow compilation (uses AgentOS WorkflowCompiler)

---

## Boundaries

| Concern | PAIOS | AgentOS | GrokBot Workforce |
|---------|-------|---------|-------------------|
| Process/FS/Network | ✅ | ❌ | ❌ |
| Task/Objective lifecycle | ❌ | ✅ | ❌ |
| Provider/model/runtime | ❌ | ✅ | ❌ |
| A2A/MCP | ❌ | ✅ | ❌ |
| Routing/weights | ❌ | ✅ | ❌ |
| Cache (L0–L6) | ❌ | ✅ | ❌ |
| Verification | ❌ | ✅ | ❌ |
| Workflow/Skill | ❌ | ✅ | ❌ |
| Learning/Coach | ❌ | ✅ | ❌ |
| Fleet/Incidents | ❌ | ✅ | ❌ |
| Autonomy states | ❌ | ✅ | ❌ |
| Backup/Reconstruction | ❌ | ✅ | ❌ |
| Persistent supervisors | ❌ | ❌ | ✅ |
| Grok cloud policy | ❌ | ❌ | ✅ |
| Grok routing bias | ❌ | ❌ | ✅ |
| Browser session mgmt | ❌ | ❌ | ✅ |

---

## Shared Contracts

### IntelligenceCache (AgentOS)
- L0 exact, L1 artifact, L2 tool, L3 context, L4 semantic, L5 provider-doc, L6 fresh
- Honest hits (provenance + modelCalls=0), honest misses with reasons
- SecurityScope gating, secret rejection, narrow invalidation, mark_verified
- Durable counters + stats()

### Efficiency Kernel (AgentOS)
- `triage()` — cache > workflow > deterministic > cheap > premium > GROKBOT-late
- `plan_team()` — single worker default, verifier only on justification
- `TaskFingerprint` — dedupe across all roles
- `compute_delta()` — baseline ref + delta + task (never full history)
- `content_state()` — label → hash mapping

### VerificationEngine (AgentOS)
- State machine: PLANNED → RUNNING → BLOCKED → FAILED → REVIEW → VERIFICATION → VERIFIED_COMPLETE
- VerificationRecord: taskId, type, verifier, evidenceRefs[], result, confidence
- Cheapest sufficient: TEST > BUILD > DIFF > API > DATABASE > SOURCE > EXTERNAL_CONFIRMATION
- Outcomes: ACCEPT / RETRY_SAME_WORKER / RETRY_WITH_FEEDBACK / ESCALATE_* / REJECT

### Coach (AgentOS)
- Triggers: retries, worker_failed, routing_poor, context_excessive, cache_miss, human_correction, recurring, verification_expensive, workflow_repeated, provider_under/over
- LessonCandidate: id, taskClass, scope, symptom, rootCause, lesson, evidenceRefs, confidence, recommendedChange, status (CANDIDATE→VALIDATED→ADOPTED→REJECTED/SUPERSEDED/REVOKED)
- Promotion: 1 obs = CANDIDATE, repeat = VALIDATED, human/test = ADOPTED, conflict = REVOKED

### WorkflowCompiler (AgentOS)
- WorkflowDefinition: id, name, taskClass, version, inputs, preconditions, steps, capabilityRequirements, preferredExecutors, fallbackExecutors, cachePolicy, approvalPolicy, verificationPolicy, outputs, successEvidence, failureHandling, metrics, status, maturity
- Maturity: FIRST_RUN → REPEAT → MATURE → EXCEPTION
- Matching: preconditions + input schema compatibility
- Compilation: from 3+ successful executions with same strategy

### SkillModel (AgentOS)
- SkillDefinition: id, objective, inputs, constraints, decisionRules, tools, outputs, verification, approvalBoundary, failureHandling, contextRefs, version
- Compact structured procedures (not giant prompts)
- 12 built-in skills: repo-audit, usage-refresh, ecosystem-delta, location-arrival, tonight, roadtrip, fuel-stop-stack, weather-departure, sunset, weekly-city-plan, workforce-health, project-reality

### RoleClassifier (AgentOS)
- Factors: taskFrequency, persistentContextValue, computerAffinity, browserSessionAffinity, uniquePermissions, supervisoryValue, workflowRepeatability, canSkillReplace, canWorkflowReplace, canEphemeralWorkerReplace
- Decisions: CREATE_PERSISTENT / KEEP_VIRTUAL / CONVERT_SKILL / CONVERT_WORKFLOW / EPHEMERAL / TOOL / RETIRE_IF_UNUSED

### AutonomyModel (AgentOS)
- States: DISCOVERED → OBSERVE → PROPOSE → APPROVAL_REQUIRED → LIMITED_AUTONOMY → CERTIFIED_AUTONOMY
- Revocation: SUSPENDED / REVOKED (security, scope, verification_fail_rate, human_correction_spike)
- Promotion requires evidence thresholds per state

### FleetMonitor (AgentOS)
- Signals: stalled/duplicate/orphaned jobs, retry storms, queue congestion, deadline risk, quota exhaustion, unexpected spend, provider degradation, unverified completion, common blockers, inefficient fan-out, unused persistent agents, repeat human correction
- IncidentRecord: signature, affectedJobs[], affectedExecutors[], probableCause, confidence, status, mitigation, resolution, lessonRef
- Response: detect → suppress redundant retry → route around → escalate once → resolve → lesson

### LearnedRouting (AgentOS)
- ExecutorProfile: pass_rate, retry_rate, verification_pass_rate, human_correction_rate, avg_context_size, avg_latency
- Weights: capability_fit, quality, health, scarcity, cost, latency, historical_pass_rate, retry_rate (penalty), verification_success, human_correction (penalty), context_cost (penalty)
- Explainable routing preserved

### Routines (AgentOS)
- Cheap detector → early exit (NO_ACTION) or workflow
- Detectors: usage_snapshot, ecosystem_delta, repo_changes, location_arrival
- Delta report: baseline_ref + changedInputs + invalidatedCacheRefs + changedFacts

### Backup & Reconstruction (AgentOS)
- Portable export (tables only, no secrets): objectives, capabilities, executions, events, verification, failures, plans, patterns, gaps, quality, federation, mcp, ecosystem, workflows, cache, fingerprints, lessons, workflows, skills, incidents, metrics, autonomy, routines, persistence_decisions
- Excludes: credentials, tokens, cookies, private keys, recovery codes, browser sessions
- Reconstruction: roles, skills, workflows, autonomy, lessons, incidents

### PersistentBotGovernor (AgentOS)
- Evaluates virtual roles → MaterializationPlan
- Decisions: CREATE_PERSISTENT / KEEP_VIRTUAL / CONVERT_SKILL / CONVERT_WORKFLOW / EPHEMERAL / TOOL / RETIRE_IF_UNUSED
- Approval required for CREATE_PERSISTENT

---

## Current Status (2026-09-24)

### Live (01/02/03)
- ChiefOfStaff, IntelligenceChief, ProjectsChief — verified_live = True

### Virtual (133/134)
- LocationIntelligence (133) — virtual supervisor with 7 compiled skills
- GrokBotEcosystemScout (134) — virtual delta pipeline

### Not Materialized
- No new bots created during this mission
- No Grok calls, no paid model calls

---

## Verification Status

- **456 tests OK** (433 baseline + 23 new kernel tests)
- All historical tests pass
- No paid usage, no Grok task execution, no network side effects
- PYC authority eliminated — all source reconstructed from git history, tests, runtime behavior, bytecode evidence where necessary

---

## Next Highest-Value Step

1. **Wire verification engine into engine.py control loop** — automatic verification after execution
2. **Wire coach into engine.py** — post-execution lesson generation
3. **Wire workflow compiler into engine.py** — auto-compile on recurring task classes
4. **Wire fleet monitor into event bus** — real-time incident detection
5. **Wire learned routing into router.py** — use executor profiles for routing
6. **Wire routines into scheduler** — periodic cheap detector runs
7. **Integrate backup into CLI** — `agentos backup create/restore/reconstruct`

All components implemented, tested, and documented. Ready for integration.