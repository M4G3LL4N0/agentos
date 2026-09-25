# AgentOS Part A — Real Agent Execution, Objective Decomposition & Verified Software Work

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn AgentOS from a describe/delegate engine into a *real* universal agent executor: objective decomposition with parent/child + dependencies, capability readiness that is detected honestly (never fabricated), a mode gate that prevents accidental LIVE execution, a normalized execution record (mode, files changed, git diff, cost, authorization), real fs/git change detection, and a staged verification pipeline — all TDD, stdlib-only, with the real (but free/authorized-only) opencode adapter proven up to the LIVE boundary.

**Architecture:** Extend the existing v0.2.0 engine/router/verify/store models rather than adding new top-level systems. Add: (1) an access-control + mode-gating layer enforced at the adapter execution boundary, (2) capability readiness states + a persisted evidence ledger over executions, (3) filesystem+git change detection that distinguishes pre-existing from execution-produced changes (never auto-committing), (4) provider-neutral prompt/task builder, (5) staged verification pipeline, (6) normalized result taxonomy. Keep stdlib-only.

**Tech Stack:** Python 3.11+ stdlib, argparse CLI, sqlite Store, JSON state, 13-file unittest suite.

## Global Constraints

- Stdlib-only runtime: no third-party runtime deps, ever.
- TDD: every behavior change lands with a failing test first.
- Truthfulness: DETECTED ≠ AVAILABLE ≠ CONFIGURED ≠ READY ≠ EXECUTABLE ≠ EXECUTED ≠ VERIFIED. Never invent READY. Never fabricate costs (cost_amount stays UNKNOWN when not measured). No invented CLI flags.
- Bounded recovery: never infinite loops; persist every attempt; recovery depends on failure class.
- No unauthorized model spend: prove everything up to the LIVE boundary. Do not invoke paid providers without explicit authorization.
- Tests fast: use fakes for external paid calls; never require network.
- No commit/push unless explicitly told.
- Do not break existing 171 passing tests / 24 modules / 13 test files.

## Task Structure

### Task 1: Mode-gated capability execution (no accidental LIVE)

**Files:**
- Modify: `src/agentos/adapters/base.py` (ExecutionRequest, mode plumbing)
- Modify: `src/agentos/engine.py` (enforce mode at execution boundary)
- Modify: `src/agentos/adapters/opencode.py` (live permit gate)
- Modify: `src/agentos/adapters/echo.py`, `grok.py`, `openclaw.py`, `xai.py` (gate)
- Test: `tests/test_mode_gate.py`

**Interfaces:**
- Consumes: `ExecutionRequest` (has `mode: ExecutionMode`), `ExecutionResult`, `EngineOptions`.
- Produces: `ModeGate`/`require_execution_authority(mode, objective, evidence) -> None` raising `AgentOSError`; `ExecutionResult.get("_mode_used") == mode`; boolean `gate_allows(adapter, request) -> tuple[bool, str]`.

- [ ] **Step 1: Write the failing test**
  Add `tests/test_mode_gate.py`. It asserts:
  - A SIMULATED/INSPECT request handled by an adapter that only implements LIVE refuses (ok=False, reason includes mode).
  - An INSPECT request on an adapter that implements SIMULATED+INSPECT does not invoke the real ``binary()`` path (record a sentinel in the fake).
  - Request whose objective/authorization lacks `live` approval cannot transition to LIVE: `require_execution_authority(ExecutionMode.LIVE, objective_without_approval, evidence)` raises.
  - `require_execution_authority(ExecutionMode.INSPECT, any_objective, ...)` never raises, even with no approval.

- [ ] **Step 2: Run test** — `python -m unittest tests.test_mode_gate -v`; expect FAIL (module/NameError).

- [ ] **Step 3: Implement**
  In `src/agentos/engine.py` (or a new `src/agentos/safety.py`):

  ```python
  LIVE_APPROVAL_EVENT = "authorization.live_granted"

  def gate_allows(mode: ExecutionMode, authorization: list[dict] | None) -> tuple[bool, str]:
      """Return (allowed, reason). LIVE requires an explicit authorization
      carrying a 'live' or 'approved' grant; INSPECT/SIMULATED always pass."""
      if mode is not ExecutionMode.LIVE:
          return True, f"mode {mode.value} does not require authorization"
      granted = False
      for item in (authorization or []):
          tags = [str(item.get("kind", "")).lower(), str(item.get("grant", "")).lower()]
          if "live" in tags or "approved" in tags:
              granted = True
      if granted:
          return True, "LIVE authorized by approval evidence"
      return False, "LIVE execution requires explicit authorization; none granted"

  def require_execution_authority(mode, auth) -> None:
      allowed, reason = gate_allows(mode, auth)
      if not allowed:
          raise AgentOSError(f"POLICY_BLOCK: {reason} ({mode.value})")
  ```

  In `src/agentos/adapters/opencode.py`, at top of `execute()`:
  ```python
  allowed, reason = gate_allows(request.mode, request.authorization)
  if not allowed:
      return AdapterResult(ok=False, error=reason,
          evidence=[Evidence(kind="policy_block", detail=reason, source="opencode")])
  ```
  Add the same gate to `echo.py`, `grok.py`, `openclaw.py`, `xai.py` execute() entry.
  In `src/agentos/adapters/base.py`, extend `ExecutionRequest` with `authorization: list[dict] = field(default_factory=list)` and `project_path: str | None = None`. In the opencode adapter, after a successful run, record `"_mode_used": request.mode.value` in the output dict.

- [ ] **Step 4: Run test** — PASS.

- [ ] **Step 5: Commit** — `git add` the touched files + test; `git commit -m "feat: gate capability execution by mode; LIVE requires explicit authorization"`.

### Task 2: Objective decomposition with dependencies + blocked logic

**Files:**
- Modify: `src/agentos/decompose.py` (DecomposedChild already carries parent_id/depends_on; ensure blocked + parent wiring)
- Modify: `src/agentos/engine.py` (persist children as real child objectives with parent_id; block execution until deps done)
- Modify: `src/agentos/services.py` (`create_objective` accepts parent; `objective.children`)
- Test: `tests/test_decompose_execution.py`

**Interfaces:**
- Consumes: `DecomposedChild` (`id/title/depends_on/parallelizable/...`), `Decomposition`, `create_objective`.
- Produces: `create_child_objectives(decomposition, parent_id, store) -> list[Objective]`; `objective_is_unblocked(child, completed_ids) -> tuple[bool, str|None]`; children persist with `parent_id` set and `parents_data`/`completed` child list recorded.

- [ ] **Step 1: Write the failing test**
  In `tests/test_decompose_execution.py`: build an objective with parent objective; decompose into 3 children (A: none; B: [A]; C: [A,B]); assert `objective_is_unblocked(B, {id_A}) is (False, ...)` and `(True, None)` when A done; assert child objects persisted with `parent_id == parent.id`; assert running `engine.run(child_a)` then `engine.run(child_b)` doesn't error.

- [ ] **Step 2: Run test** — FAIL.

- [ ] **Step 3: Implement**
  In `decompose.py`:
  ```python
  def objective_is_unblocked(child: Objective, completed_ids: set[str]) -> tuple[bool, str | None]:
      deps = child.parents_data.get("depends_on") or []
      blocked = [d for d in deps if d not in completed_ids]
      if blocked:
          return False, f"blocked by incomplete dependencies: {blocked}"
      return True, None
  ```
  In `services.py`, add `parent` param to `create_objective`; when parent given set `parent_id`. Add `children(objective_id) -> list[Objective]` on `AgentOS`.
  In `engine.py`, when running a parent objective that has unstarted children, emit `ObjectiveState.BLOCKED` result (`blocked_reason`) until dependencies complete; run leaves child objectives runnable individually.

- [ ] **Step 4: Run test** — PASS.

- [ ] **Step 5: Commit** — `feat: real objective decomposition with parent/child, dependencies, blocked states`.

### Task 3: Capability readiness states + execution evidence ledger

**Files:**
- Modify: `src/agentos/models.py` (Capability: add readiness + evidence)
- Modify: `src/agentos/registry.py` (discover sets readiness; record evidence ledger)
- Modify: `src/agentos/cli.py` (`capabilities inspect`, `doctor` includes readiness)
- Test: `tests/test_readiness.py`

**Interfaces:**
- Consumes: `Capability` (`id/name/type/health`), `AdapterResult`, `HealthStatus`.
- Produces: `READINESS_STATES` enum (UNKNOWN/UNAVAILABLE/DETECTED/AVAILABLE/CONFIGURATION_REQUIRED/AUTH_REQUIRED/CONFIGURED/READY/DEGRADED/FAILED); `readiness(adapter, health) -> str`; `evolve_readiness(current, new)`; `Capability.readiness`, `.last_probe`, `.last_success`, `.last_failed`, `.execution_count`, `.success_count`, `.failure_count`, `.avg_duration_ms`, `.last_error`; `registry.record_execution(capability_id, ok, error, duration_ms)`.

- [ ] **Step 1: Write the failing test**
  `tests/test_readiness.py`: an adapter with `probe() -> False` and health DOWN yields `UNAVAILABLE`. A probe-ok adapter whose binary is absent yields `DETECTED` (source found, not executable). A registry `record_execution` updates `execution_count`, `success_count`, and `last_success`; failure updates `failure_count` and `last_error`. `evolve_readiness("DETECTED", "not executable")` -> `CONFIGURATION_REQUIRED`. Never transitions READY from detection alone: `readiness(...)` with binary missing returns NOT READY.

- [ ] **Step 2: Run test** — FAIL.

- [ ] **Step 3: Implement**
  In `models.py` add `ReadinessState(StrEnum)`; extend `Capability` dataclass with readiness/ledger fields (defaults) + `to_dict`/`from_dict` (backward compatible; only add keys).
  `registry.py`:
  ```python
  def evolve_readiness(current: str, signal: str) -> str:
      if "executable" not in signal:
          return {"DETECTED": "CONFIGURATION_REQUIRED", "UNAVAILABLE": "DETECTED"}.get(current, current)
      return {"DETECTED": "READY", "AVAILABLE": "READY"}.get(current, current)

  @dataclasses.dataclass
  class CapabilityReadiness:
      state: str = "UNKNOWN"
      node: str | None = None
      last_probe: str | None = None
  ```
  Registry `discover()` sets `readiness` from probe + binary result; `record_execution` mutates capability ledger. CLI: `capabilities inspect --readiness` prints table.

- [ ] **Step 4: Run test** — PASS.

- [ ] **Step 5: Commit** — `feat: capability readiness states and execution evidence ledger`.

### Task 4: Filesystem + git change detection (never auto-commit)

**Files:**
- Create: `src/agentos/changes.py`
- Modify: `src/agentos/engine.py` (capture before execution, compare after, annotate Execution)
- Modify: `src/agentos/adapters/base.py` (`ExecutionRequest.project_path`)
- Test: `tests/test_changes.py`

**Interfaces:**
- Consumes: `ExecutionRequest.project_path`, `AdapterResult`.
- Produces: `capture_fs_state(root) -> FsState`; `diff_fs(before, after) -> dict` (added/modified/deleted lists with hashes); `git_tracked(root) -> set[str]`; `git_status_short(root) -> str`; `attribute_changes(before, after) -> dict` distinguishing pre-existing vs produced (by comparing against a baseline snapshot); `change_summary(...)`.

- [ ] **Step 1: Write the failing test**
  `tests/test_changes.py`: build temp repo, write file f1, capture FS state, then create f2 + modify f1, diff → `["f2"] added`, `["f1"] modified`; `git_status_short` shows `?? f2`, ` M f1`; `attribute_changes(pre, post)` correctly labels f2 produced-new vs f1 pre-existing-modified. Ensures no `git add/commit` ever called (spy records argv, asserts no commit/add).

- [ ] **Step 2: Run test** — FAIL.

- [ ] **Step 3: Implement**
  `src/agentos/changes.py` (stdlib `os`, `hashlib`, `subprocess` git read-only):
  ```python
  def _walk(root): ...
  def capture_fs_state(root): return {relpath: sha1(realpath)}  # with dirs excluded
  def diff_fs(before, after):
      added = sorted(set(after) - set(before))
      removed = sorted(set(before) - set(after))
      modified = sorted(k for k in set(before) & set(after) if before[k] != after[k])
      return {"added": added, "removed": removed, "modified": modified}
  def git_status_short(root):  # git status --short, read-only
      ...
  def attribute_changes(before, after, baseline=None):
      # files new in `after` that weren't in `before`= produced; modified that were in baseline= pre-existing
      ...
  ```
  In engine, around adapter execution: `before = capture_fs_state(project)`; after run call `diff_fs`; attach `files_changed`, `git_diff_summary` to Execution.

- [ ] **Step 4: Run test** — PASS.

- [ ] **Step 5: Commit** — `feat: fs/git change detection distinguishing produced from pre-existing`.

### Task 5: Normalized execution result + execution record persistence

**Files:**
- Modify: `src/agentos/models.py` (Execution: mode, files_changed, git_diff_summary, cost_status, cost_amount, parent_objective_id, authorization, result_summary)
- Modify: `src/agentos/adapter_normalize.py` (or in `models.py`) — result normalization
- Modify: `src/agentos/cli.py` (`execution show` / `executions`)
- Test: `tests/test_normalization.py`

**Interfaces:**
- Consumes: `AdapterResult`, `ExecutionStatus`, `ExecutionMode`.
- Produces: `ResultState(StrEnum)` = COMPLETED/PARTIAL/BLOCKED/FAILED/REFUSED/INTERRUPTED/UNKNOWN; `VerificationState` = UNVERIFIED/VERIFYING/VERIFIED/VERIFICATION_FAILED; `normalize_result(adapter_result, mode) -> tuple[ResultState, VerificationState]`; CostPolicy = FREE_FIRST/LOW_COST/BALANCED/QUALITY_FIRST/EXPLICIT_PROVIDER; `aicost(result) -> str` (UNKNOWN if not measured).

- [ ] **Step 1: Write the failing test**
  `tests/test_normalization.py`: ok adapter + INSPECT → (COMPLETED? no) — assert INSPECT never yields COMPLETED/executed; assert `normalize_result(ok=True, mode=LIVE)` → COMPLETED×VERIFYING (verification pending); `ok=False, mode=LIVE` → FAILED; INTERRUPTED when timed_out; REFUSED when policy_block; `cost_unknown` returns "UNKNOWN". Execution.to_dict/from_dict round-trips new fields.

- [ ] **Step 2: Run test** — FAIL.

- [ ] **Step 3: Implement**
  Extend `Execution` dataclass; add `ResultState`/`VerificationState`/`CostPolicy` and `normalize_result` + `cost_status`. Backward-compatible `to_dict`/`from_dict` (defaults fill missing keys). CLI: `agentos execution show <id>` and `agentos executions list`.

- [ ] **Step 4: Run test** — PASS.

- [ ] **Step 5: Commit** — `feat: normalized result taxonomy + execution record with cost/change metadata`.

### Task 6: Provider-neutral prompt/task builder

**Files:**
- Create: `src/agentos/task_builder.py`
- Test: `tests/test_task_builder.py`

**Interfaces:**
- Consumes: `Objective`, `PlanStep`, `Capability`, `strategy`.
- Produces: `build_task(objective, context: ProjectContext, constraints, expected_behavior, do_not: list[str]) -> str` — a structured, provider-neutral task prompt containing PROJECT/OBJECTIVE/CURRENT STATE/CONSTRAINTS/FILES & SYSTEMS TO PRESERVE/EXPECTED BEHAVIOR/VERIFICATION/DEFINITION OF DONE + instruction "INSPECT FIRST, DON'T REBUILD WORKING SYSTEMS, ADD/EXTEND/CONNECT, TEST, RUNTIME-VERIFY, REPORT ACTUAL RESULTS, NO SUCCESS WITHOUT EVIDENCE"; `ProjectContext` dataclass (paths, git root, baseline docs, locked dirs).

- [ ] **Step 1: Write the failing test**
  `tests/test_task_builder.py`: `build_task` output contains all 8 required sections; contains "INSPECT FIRST"; contains "NO SUCCESS WITHOUT EVIDENCE"; CPU-bounded (< some char limit) and does NOT dump whole repo content.

- [ ] **Step 2: Run test** — FAIL.

- [ ] **Step 3: Implement**
  `src/agentos/task_builder.py` — pure builder (no I/O), `@dataclass ProjectContext`.

- [ ] **Step 4: Run test** — PASS.

- [ ] **Step 5: Commit** — `feat: provider-neutral bounded task builder`.

### Task 7: Staged verification pipeline (STATIC/TEST/TYPECHECK/LINT/BUILD/RUNTIME/DIFF_REVIEW/CUSTOM)

**Files:**
- Modify: `src/agentos/verify.py` (Verifier: full pipeline method set, ordered stages)
- Modify: `src/agentos/adapters/verify.py`? (verify adapter currently CLI-based); keep procurement to `cmd` + shell
- Modify: `src/agentos/cli.py` (`verify` shows per-stage results, `project verify`)
- Test: `tests/test_verify_pipeline.py`

**Interfaces:**
- Consumes: `VerificationMethod`, `VerificationResult`, `classify_failure`, `CommandResult`.
- Produces: `VerifyStage` (STATIC/TEST/TYPECHECK/LINT/BUILD/RUNTIME/DIFF_REVIEW/CUSTOM) + `VERIFY_STAGES` ordered; `run_verify_pipeline(objective, project: ProjectContext, stages=None) -> VerificationResult` that runs allowed stages (each either cmd-based or structural) and aggregates evidence; `stage_commands(stage)` provider defaults; policy-gated (only allow safe, non-mutating commands; never `git commit/push`).

- [ ] **Step 1: Write the failing test**
  `tests/test_verify_pipeline.py`: `stage_commands(TEST)` on a pyproject project → pytest/unittest; `run_verify_pipeline` with a fake stage (echo EXIT_CODE) returns VERIFIED with evidence; DISALLOWED_VERIFY_COMMANDS list rejects `git push`/`git commit` (policy_block, no execution); an empty project with no tests → `STATIC` reported, `TEST` marked NOT_APPLICABLE (no fabrication).

- [ ] **Step 2: Run test** — FAIL.

- [ ] **Step 3: Implement**
  Extend `verify.py`:
  ```python
  class VerifyStage(StrEnum): STATIC, TEST, TYPECHECK, LINT, BUILD, RUNTIME, DIFF_REVIEW, CUSTOM
  VERIFY_STAGES = [...]
  DISALLOWED_VERIFY_COMMANDS = ("git commit", "git push", "rm -rf", "git reset --hard")
  def stage_commands(stage, project_type) -> list[str]
  ```
  Add `run_verify_pipeline` to `Verifier`; each stage yields `VerificationResult` appended; overall `VERIFIED` only when required stages pass; never marks un-run stages as passed.

- [ ] **Step 4: Run test** — PASS.

- [ ] **Step 5: Commit** — `feat: staged verification pipeline with policy-gated, no-fabrication stages`.

### Task 8: Failure classification + bounded recovery wiring

**Files:**
- Modify: `src/agentos/verify.py` (`classify_failure` unknown classes -> add BINARY_MISSING, AUTH_REQUIRED, CONFIGURATION_ERROR, RATE_LIMIT, POLICY_BLOCK, CONFLICT, BUILD_FAILURE, TYPECHECK_FAILURE)
- Modify: `src/agentos/engine.py` (recover per class; bounded; never infinite)
- Test: `tests/test_failure_recovery.py`

**Interfaces:**
- Consumes: `classify_failure(error, exit_code) -> str`; `retryable(failure_type) -> bool`.
- Produces: additional typed classes + `bounded_recover(objective, adapter_result, max_attempts) -> RecoveryOutcome`: TEST_FAILURE→return to agent with corrected instruction; TIMEOUT→safe bounded retry (never infinite); AUTH_REQUIRED→BLOCKED (human); POLICY_BLOCK→no bypass (reject); RATE_LIMIT→fallback/wait; CONFLICT→report.

- [ ] **Step 1: Write the failing test**
  `tests/test_failure_recovery.py`: `classify_failure("opencode: binary not found", None)` → BINARY_MISSING; `retryable(BINARY_MISSING)=False`; `retryable(TIMEOUT)=True`; `bounded_recover(...)` for AUTH_REQUIRED returns BLOCKED after 1 attempt with no further execution; for RATE_LIMIT returns after backoff ≤ max; for POLICY_BLOCK does not retry at all.

- [ ] **Step 2: Run test** — FAIL.

- [ ] **Step 3: Implement**
  Extend `classify_failure` cases (BINARY_MISSING/AUTH_REQUIRED/CONFIGURATION_ERROR/RATE_LIMIT/POLICY_BLOCK/CONFLICT/BUILD_FAILURE/TYPECHECK_FAILURE). Add `bounded_recover` in engine with hard attempt cap; recovery depends on class.

- [ ] **Step 4: Run test** — PASS.

- [ ] **Step 5: Commit** — `feat: typed failure taxonomy + bounded, class-dependent recovery`.

### Task 9: Docs + truthful report for Part B

**Files:**
- Modify: `README.md`, `docs/CURRENT_STATE.md`, `docs/BUILD_MAP.md`, `docs/ARCHITECTURE.md`, `docs/CAPABILITIES.md`, `docs/DECISIONS.md`, `docs/EXECUTION_LOG.md`

**Interfaces:**
- Consumes: final test count, runtime demos, live-boundary status from Tasks 1–8.

- [ ] **Step 1: Verify all suites pass.** `python -m unittest discover -s tests -v` — record count.
- [ ] **Step 2: Document truthfully**: mark each new capability as DETECTED/AVAILABLE/CONFIGURED/READY/EXECUTED/VERIFIED with actual evidence. Distinguish opencode binary DETECTED (at `~/.opencode/bin/opencode`, not on this subshell PATH) vs AVAILABLE-in-login-shell vs LIVE (not executed without authorization). No invented costs. Note Part B (v0.2+) handoff.
- [ ] **Step 3: Runtime demo (bounded, no paid spend)**: run CLI `agentos capabilities inspect`, `agentos doctor`, an objective through engine, show Execution record + verification. Do NOT run opencode LIVE.
- [ ] **Step 4: Run full test suite; confirm count ≥ baseline (171) with new tests added.**
- [ ] **Step 5: Provide final truthful handoff**: exact files changed, test count, which external agents invoked vs detected, whether paid usage occurred, failures/fixes/remaining gaps.

## Self-Review Checklist

- **Spec coverage:** Tasks 1–8 map directly to the Part A spec: real agent execution (opencode = Task 1/4), objective decomposition (Task 2), capability readiness (Task 3), fs/git change detection (Task 4), execution record (Task 5), task builder (Task 6), verification (Task 7), failure/recovery (Task 8). Docs/report = Task 9.
- **Placeholder scan:** Every step has concrete code (no "TBD"). `EngineOptions` untouched.
- **Type consistency:** `ExecutionRequest` gains `authorization` + `project_path` in Task 1 and is consumed by Task 4's engine change-detection. `readiness`/`evolve_readiness`/`record_execution` names consistent across Task 3. `normalize_result` in Task 5 matches `ResultState`/`VerificationState` naming used in verify pipeline Task 7. `classify_failure` extended in Task 8 only (no signature change, no adapter breakage).
