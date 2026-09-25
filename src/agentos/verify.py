"""Verification and failure classification.

Verification is first-class: an execution is not successful because an
adapter returned output, but because real checks passed against real
evidence (exit codes, file state, output content). Failures are classified,
preserved with evidence, and never promoted to success.
"""

from __future__ import annotations

import ast
import os
import subprocess
from enum import StrEnum
from pathlib import Path
from typing import Any

from agentos.adapters.base import text_of
from agentos.policies import redact_secrets
from agentos.models import (
    Evidence,
    Execution,
    Objective,
    QualityEvaluation,
    VerificationResult,
    new_id,
    utc_now_iso,
)

EXIT_CODE_METHOD = "exit_code"
CONTAINS_METHOD = "contains"
FILE_EXISTS_METHOD = "file_exists"
STRUCTURAL_METHOD = "structural"

# ----------------------------------------------------------------------
# Staged verification pipeline (Task 7).
#
# A verification pipeline is an ordered list of stages. Each stage runs
# real, bounded, non-mutating checks against a project directory and
# appends evidence to ONE VerificationResult. Overall VERIFIED requires
# every requested stage to have status "passed": stages that did not run
# are recorded as NOT_APPLICABLE (never passed), disallowed commands are
# recorded as policy_block and never executed.
# ----------------------------------------------------------------------


class VerifyStage(StrEnum):
    """Ordered verification stages. Values are lowercase stage names."""

    STATIC = "static"
    TEST = "test"
    TYPECHECK = "typecheck"
    LINT = "lint"
    BUILD = "build"
    RUNTIME = "runtime"
    DIFF_REVIEW = "diff_review"
    CUSTOM = "custom"


#: Ordered pipeline stages (default when no subset is requested).
VERIFY_STAGES: list[VerifyStage] = [
    VerifyStage.STATIC,
    VerifyStage.TEST,
    VerifyStage.TYPECHECK,
    VerifyStage.LINT,
    VerifyStage.BUILD,
    VerifyStage.RUNTIME,
    VerifyStage.DIFF_REVIEW,
    VerifyStage.CUSTOM,
]

#: Commands that are never executed by the pipeline (fail-closed substring
#: match). A rejected command yields policy_block evidence; the stage that
#: requested it cannot pass.
DISALLOWED_VERIFY_COMMANDS = (
    "git commit",
    "git push",
    "rm -rf",
    "git reset --hard",
)

#: Per-stage status recorded as the prefix of a stage summary's detail
#: (``"<status>: ..."``), and the exact NOT_APPLICABLE marker.
NOT_APPLICABLE = "NOT_APPLICABLE"
STAGE_PASSED = "passed"
STAGE_FAILED = "failed"
STAGE_POLICY_BLOCK = "policy_block"

#: Bound on any single verification subprocess (no unbounded verification).
VERIFY_COMMAND_TIMEOUT_SECONDS = 120.0

#: Bounds for the in-process STATIC scan (no unbounded walks/reads).
MAX_STATIC_FILES = 2000
MAX_STATIC_FILE_BYTES = 1_000_000

#: Directories never descended into during the STATIC scan.
STATIC_SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        "node_modules",
        "build",
        "dist",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
    }
)


def classify_failure(error: str | None, exit_code: int | None) -> str:
    """Classify a failure into a typed category from real signals.

    Order matters: POLICY_BLOCK is fail-closed and comes before the
    generic execution fallback; BINARY_MISSING comes before not_found
    ("binary not found" would otherwise match the generic file case).
    Every legacy return value is preserved verbatim ("timeout",
    "not_found", "permission", "auth", "connectivity",
    "invalid_request", "exit_code", "execution", "unknown"): "auth" is
    still returned for bare 401/403/unauthorized signals while
    AUTH_REQUIRED is the canonical name for credential-shaped failures.
    """
    text = (error or "").lower()
    if any(
        phrase in text
        for phrase in (
            "policy_block",
            "blocked by policy",
            "policy denied",
            "policy violation",
            "refused by policy",
            "disallowed",
            "content policy",
            "safety policy",
        )
    ):
        return "POLICY_BLOCK"
    if any(
        phrase in text
        for phrase in (
            "binary not found",
            "binary missing",
            "command not found",
            "no such command",
            "executable not found",
        )
    ):
        return "BINARY_MISSING"
    if "timed out" in text or "timeout" in text:
        return "timeout"
    if any(
        phrase in text
        for phrase in (
            "rate limit",
            "rate_limit",
            "too many requests",
            "quota exceeded",
            "throttl",
            "429",
        )
    ):
        return "RATE_LIMIT"
    if any(
        phrase in text
        for phrase in (
            "auth required",
            "auth_required",
            "authentication required",
            "missing api key",
            "no api key",
            "api key",
            "api_key",
            "apikey",
            "credential",
            "missing token",
            "login required",
            "sign-in required",
        )
    ):
        return "AUTH_REQUIRED"
    if "test failed" in text or "tests failed" in text or "failing test" in text:
        return "TEST_FAILURE"
    if "no such file" in text or "not found" in text or "no such directory" in text:
        return "not_found"
    if "permission denied" in text or "forbidden" in text:
        return "permission"
    if "unauthorized" in text or "401" in text or "403" in text:
        return "auth"
    if "connection" in text or "refused" in text or "unreachable" in text:
        return "connectivity"
    if "merge conflict" in text or "version conflict" in text or "conflict" in text:
        return "CONFLICT"
    if any(
        phrase in text
        for phrase in (
            "build failed",
            "build failure",
            "compilation failed",
            "compilation error",
            "compile error",
        )
    ):
        return "BUILD_FAILURE"
    if any(
        phrase in text
        for phrase in (
            "typecheck",
            "type check",
            "mypy",
            "tsc ",
            "type error",
        )
    ):
        return "TYPECHECK_FAILURE"
    if any(
        phrase in text
        for phrase in (
            "configuration",
            "misconfigured",
            "config error",
            "invalid config",
            "bad config",
            "missing configuration",
        )
    ):
        return "CONFIGURATION_ERROR"
    if "requires a" in text or "missing" in text or "unsupported" in text:
        return "invalid_request"
    if exit_code is not None and exit_code != 0:
        return "exit_code"
    if error:
        return "execution"
    return "unknown"


#: Failure classes where a bounded retry could plausibly help. Legacy
#: lowercase names keep working; matching is case-insensitive so the new
#: UPPER_CASE canonical names ("TIMEOUT", "RATE_LIMIT", ...) map too.
#: "auth" stays mapped (non-retryable) alongside canonical AUTH_REQUIRED.
_RETRYABLE_FAILURES = frozenset(
    {
        "timeout",
        "connectivity",
        "execution",
        "exit_code",
        "unknown",
        "rate_limit",
        "test_failure",
    }
)


def retryable(failure_type: str) -> bool:
    """Whether a bounded retry could plausibly help."""
    return str(failure_type or "").strip().lower() in _RETRYABLE_FAILURES


def backoff_delay_seconds(attempt: int, base: float = 1.0, cap: float = 30.0) -> float:
    """Exponential backoff (computed and logged; local retries are instant)."""
    delay = base * (2 ** max(0, attempt - 1))
    return min(delay, cap)


def suggest_next_action(
    failure_type: str, attempts_used: int, max_attempts: int, operation: str
) -> str:
    if attempts_used >= max_attempts:
        return (
            f"retries exhausted ({attempts_used}/{max_attempts}) for {operation!r} "
            f"with {failure_type}; inspect failure evidence, fix inputs/constraints, "
            "then run recover with a corrected strategy"
        )
    return (
        f"retry {operation!r} (failure: {failure_type}); "
        "check evidence for the concrete error first"
    )


class Verifier:
    """Runs real verification checks against execution evidence."""

    def verify(self, objective: Objective, execution: Execution) -> VerificationResult:
        method, expected = self._resolve_method(objective, execution)
        evidence: list[Evidence] = []
        ok = True

        if execution.error is not None:
            ok = False
            evidence.append(
                Evidence(
                    kind="adapter_error",
                    detail=f"adapter reported error: {redact_secrets(str(execution.error))}",
                    source="verifier",
                )
            )

        check_ok, check_evidence = self._run_method(
            method, expected, objective, execution
        )
        evidence.extend(check_evidence)
        if not check_ok:
            ok = False

        return VerificationResult(
            id=new_id("vrf"),
            objective_id=objective.id,
            execution_id=execution.id,
            verified=ok,
            method=method,
            evidence=evidence,
            created_at=utc_now_iso(),
        )

    # ------------------------------------------------------------------
    def _resolve_method(
        self, objective: Objective, execution: Execution
    ) -> tuple[str, str | None]:
        # 1. Explicit per-objective override wins.
        context_verify = objective.context.get("verify")
        if isinstance(context_verify, dict) and context_verify.get("method"):
            return str(context_verify["method"]), self._opt_str(
                context_verify.get("expected")
            )
        if isinstance(context_verify, str) and context_verify:
            return context_verify, None
        for constraint in objective.constraints:
            if isinstance(constraint, str) and constraint.startswith("verify:"):
                return constraint.split(":", 1)[1].strip() or STRUCTURAL_METHOD, None
            if isinstance(constraint, str) and constraint.startswith("expected:"):
                return CONTAINS_METHOD, constraint.split(":", 1)[1]
        # 2. Capability-declared verification comes next.
        capability_method = (execution.request.get("verify_method") or "").strip()
        if capability_method:
            return capability_method, self._opt_str(
                execution.request.get("verify_expected")
            )
        # 3. Adapter default.
        return STRUCTURAL_METHOD, None

    @staticmethod
    def _opt_str(value: Any) -> str | None:
        return str(value) if value is not None else None

    def _run_method(
        self,
        method: str,
        expected: str | None,
        objective: Objective,
        execution: Execution,
    ) -> tuple[bool, list[Evidence]]:
        if method == EXIT_CODE_METHOD:
            return self._check_exit_code(execution)
        if method == CONTAINS_METHOD:
            return self._check_contains(execution, expected)
        if method == FILE_EXISTS_METHOD:
            return self._check_file_exists(objective, execution, expected)
        return self._check_structural(execution)

    def _check_exit_code(self, execution: Execution) -> tuple[bool, list[Evidence]]:
        code = execution.output.get("exit_code")
        if code is None:
            return False, [
                Evidence(
                    kind="exit_code",
                    detail="no exit code reported by adapter",
                    source="verifier",
                )
            ]
        if int(code) == 0:
            return True, [
                Evidence(
                    kind="exit_code",
                    detail="exit code 0",
                    source="verifier",
                )
            ]
        return False, [
            Evidence(
                kind="exit_code",
                detail=f"exit code {code} != 0",
                source="verifier",
            )
        ]

    def _check_contains(
        self, execution: Execution, expected: str | None
    ) -> tuple[bool, list[Evidence]]:
        structural_ok, structural_evidence = self._check_structural(execution)
        evidence = list(structural_evidence)
        if not structural_ok:
            return False, evidence
        if not expected:
            evidence.append(
                Evidence(
                    kind="contains",
                    detail="no expected substring configured; structural check only",
                    source="verifier",
                )
            )
            return True, evidence
        text = text_of(execution.output)
        if expected in text:
            evidence.append(
                Evidence(
                    kind="contains",
                    detail=f"expected substring {expected!r} present in output",
                    source="verifier",
                )
            )
            return True, evidence
        evidence.append(
            Evidence(
                kind="contains",
                detail=f"expected substring {expected!r} not found in output",
                source="verifier",
            )
        )
        return False, evidence

    def _check_file_exists(
        self,
        objective: Objective,
        execution: Execution,
        expected: str | None,
    ) -> tuple[bool, list[Evidence]]:
        structural_ok, structural_evidence = self._check_structural(execution)
        evidence = list(structural_evidence)
        if not structural_ok:
            return False, evidence
        path_text = (
            expected
            or execution.request.get("params", {}).get("path")
            or (execution.output.get("path") if isinstance(execution.output, dict) else None)
            or objective.context.get("path")
        )
        if not path_text:
            evidence.append(
                Evidence(
                    kind="file_exists",
                    detail="no path available to check",
                    source="verifier",
                )
            )
            return False, evidence
        exists = Path(str(path_text)).expanduser().exists()
        evidence.append(
            Evidence(
                kind="file_exists",
                detail=f"path {path_text} exists={exists}",
                source="verifier",
            )
        )
        return exists, evidence

    def _check_structural(self, execution: Execution) -> tuple[bool, list[Evidence]]:
        evidence: list[Evidence] = []
        if execution.error is not None:
            evidence.append(
                Evidence(
                    kind="structural",
                    detail=f"adapter error present: {execution.error}",
                    source="verifier",
                )
            )
            return False, evidence
        if not isinstance(execution.output, dict) or not execution.output:
            evidence.append(
                Evidence(
                    kind="structural",
                    detail="adapter returned empty output",
                    source="verifier",
                )
            )
            return False, evidence
        evidence.append(
            Evidence(
                kind="structural",
                detail="adapter succeeded with non-empty output and no error",
                source="verifier",
            )
        )
        return True, evidence

    # ------------------------------------------------------------------
    # staged pipeline
    # ------------------------------------------------------------------
    def run_verify_pipeline(
        self,
        objective: Objective,
        project: Any,
        stages: list[VerifyStage | str] | None = None,
        timeout_seconds: float = VERIFY_COMMAND_TIMEOUT_SECONDS,
    ) -> VerificationResult:
        """Run staged verification against a project, aggregating evidence.

        ``project`` is a ``ProjectContext`` (uses its ``git_root``) or a
        plain path (``str`` / ``Path``). ``stages`` defaults to the full
        ``VERIFY_STAGES`` order. Overall ``verified`` is True only when
        every requested stage ends ``passed`` — ``NOT_APPLICABLE``,
        ``failed`` and ``policy_block`` stages never count as passed.

        The result attaches to the objective (``execution_id`` mirrors
        ``objective_id``) because no single execution is under test.
        """
        root = _project_root(project)
        wanted = [_coerce_stage(s) for s in (VERIFY_STAGES if stages is None else stages)]
        project_type = _detect_project_type(root, objective)
        evidence: list[Evidence] = []
        statuses: list[str] = []
        for stage in wanted:
            status, stage_evidence = self._run_stage(
                stage, objective, root, project_type, timeout_seconds
            )
            statuses.append(status)
            evidence.extend(stage_evidence)
        passed = sum(1 for s in statuses if s == STAGE_PASSED)
        verified = bool(wanted) and passed == len(wanted)
        evidence.append(
            Evidence(
                kind="pipeline",
                detail=(
                    f"{'passed' if verified else 'failed'}: "
                    f"pipeline {passed}/{len(wanted)} stages passed "
                    f"(project_type={project_type}, root={root})"
                ),
                source="verifier",
            )
        )
        return VerificationResult(
            id=new_id("vrf"),
            objective_id=objective.id,
            execution_id=objective.id,
            verified=verified,
            method="pipeline",
            evidence=evidence,
            created_at=utc_now_iso(),
        )

    def _run_stage(
        self,
        stage: VerifyStage,
        objective: Objective,
        root: Path,
        project_type: str,
        timeout_seconds: float,
    ) -> tuple[str, list[Evidence]]:
        if stage == VerifyStage.STATIC:
            return _run_static_stage(root)
        if stage == VerifyStage.TEST:
            if not _has_tests(root):
                return _not_applicable(stage, "no test files found")
            return self._run_command_stage(
                stage, stage_commands(stage, project_type), root, timeout_seconds
            )
        if stage in (VerifyStage.TYPECHECK, VerifyStage.LINT, VerifyStage.BUILD):
            commands = stage_commands(stage, project_type)
            if not commands:
                return _not_applicable(
                    stage, f"no {stage.value} commands for project_type={project_type}"
                )
            return self._run_command_stage(stage, commands, root, timeout_seconds)
        if stage == VerifyStage.RUNTIME:
            commands = _string_list(
                (objective.context or {}).get("runtime_commands")
            )
            if not commands:
                return _not_applicable(stage, "no runtime_commands declared")
            return self._run_command_stage(stage, commands, root, timeout_seconds)
        if stage == VerifyStage.DIFF_REVIEW:
            if not _is_git_repo(root):
                return _not_applicable(stage, "not a git repository")
            return self._run_command_stage(
                stage, ["git status --short", "git diff --stat"], root, timeout_seconds
            )
        # CUSTOM: caller-supplied non-mutating commands, still policy-gated.
        commands = _string_list((objective.context or {}).get("verify_commands"))
        if not commands:
            return _not_applicable(stage, "no verify_commands declared")
        return self._run_command_stage(stage, commands, root, timeout_seconds)

    def _run_command_stage(
        self,
        stage: VerifyStage,
        commands: list[str],
        root: Path,
        timeout_seconds: float,
    ) -> tuple[str, list[Evidence]]:
        per_command: list[Evidence] = []
        blocked = 0
        failures = 0
        ran = 0
        for command in commands:
            if _is_disallowed(command):
                blocked += 1
                per_command.append(
                    Evidence(
                        kind="policy_block",
                        detail=(
                            f"policy_block: command {command!r} matches "
                            "DISALLOWED_VERIFY_COMMANDS; never executed"
                        ),
                        source="verifier",
                    )
                )
                continue
            ran += 1
            try:
                completed = _run_command(command, root, timeout_seconds)
            except Exception as exc:  # verification must never crash the loop
                failures += 1
                per_command.append(
                    Evidence(
                        kind=f"stage:{stage.value}:cmd",
                        detail=f"failed: {command!r} raised {type(exc).__name__}: {exc}",
                        source="verifier",
                    )
                )
                continue
            outcome = str(completed.get("error") or "") or f"exit {completed.get('exit_code')}"
            snippet = _truncate_text(
                str(completed.get("stdout") or completed.get("stderr") or ""),
                300,
            )
            if completed.get("exit_code") == 0 and not completed.get("error"):
                per_command.append(
                    Evidence(
                        kind=f"stage:{stage.value}:cmd",
                        detail=f"exit 0: {command!r}"
                        + (f" output: {snippet}" if snippet else ""),
                        source="verifier",
                    )
                )
            else:
                failures += 1
                per_command.append(
                    Evidence(
                        kind=f"stage:{stage.value}:cmd",
                        detail=f"failed: {command!r} -> {outcome}"
                        + (f" output: {snippet}" if snippet else ""),
                        source="verifier",
                    )
                )
        if blocked:
            status = STAGE_POLICY_BLOCK
            summary = (
                f"{status}: {blocked} command(s) rejected by policy "
                f"({ran} ran, {failures} failed); disallowed commands never execute"
            )
        elif ran == 0 or failures:
            status = STAGE_FAILED
            summary = f"{status}: {failures} of {ran} command(s) failed"
        else:
            status = STAGE_PASSED
            summary = f"{status}: all {ran} command(s) exited 0"
        return status, [
            Evidence(kind=f"stage:{stage.value}", detail=summary, source="verifier"),
            *per_command,
        ]


# ----------------------------------------------------------------------
# pipeline module helpers (pure policy/resolution + one bounded runner)
# ----------------------------------------------------------------------

#: Canonical default commands per (stage, project_type). STATIC, RUNTIME,
#: DIFF_REVIEW and CUSTOM are resolved dynamically (scan / context / git).
_STAGE_DEFAULT_COMMANDS: dict[tuple[str, str], list[str]] = {
    ("test", "pyproject"): ["python -m pytest -q"],
    ("test", "node"): ["npm test -- --silent"],
    ("test", "generic"): ["python -m unittest discover"],
    ("typecheck", "pyproject"): ["python -m mypy ."],
    ("typecheck", "node"): ["./node_modules/.bin/tsc --noEmit"],
    ("lint", "pyproject"): ["python -m ruff check ."],
    ("lint", "node"): ["npm run lint"],
    ("build", "pyproject"): ["python -m build"],
    ("build", "node"): ["npm run build"],
}


def _coerce_stage(stage: VerifyStage | str) -> VerifyStage:
    if isinstance(stage, VerifyStage):
        return stage
    text = str(stage).strip()
    try:
        return VerifyStage(text.lower())
    except ValueError:
        return VerifyStage[text.upper()]


def stage_commands(stage: VerifyStage | str, project_type: str) -> list[str]:
    """Default verification commands for a stage and project type.

    ``project_type`` is one of ``pyproject`` / ``node`` / ``generic``
    (``python`` is accepted as an alias for ``pyproject``); anything else
    falls back to ``generic``. Stages resolved dynamically (STATIC,
    RUNTIME, DIFF_REVIEW, CUSTOM) return [] here — the pipeline resolves
    them from the project tree or the objective context instead.
    """
    key = _coerce_stage(stage).value
    ptype = str(project_type or "").strip().lower()
    if ptype in ("python", "pyproject"):
        ptype = "pyproject"
    elif ptype != "node":
        ptype = "generic"
    return list(_STAGE_DEFAULT_COMMANDS.get((key, ptype), []))


def _is_disallowed(command: str) -> bool:
    """Fail-closed substring match against DISALLOWED_VERIFY_COMMANDS."""
    text = str(command or "").strip()
    return any(phrase in text for phrase in DISALLOWED_VERIFY_COMMANDS)


def _run_command(command: str, cwd: Path, timeout: float) -> dict[str, Any]:
    """Run one non-mutating verification command (bounded). Never raises."""
    try:
        proc = subprocess.run(
            str(command),
            shell=True,
            capture_output=True,
            text=True,
            timeout=max(1.0, float(timeout)),
            cwd=str(cwd),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "cmd": command,
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "error": f"timed out after {timeout:g}s (process killed): {exc}",
        }
    except OSError as exc:
        return {
            "cmd": command,
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "cmd": command,
        "exit_code": proc.returncode,
        "stdout": redact_secrets(proc.stdout or ""),
        "stderr": redact_secrets(proc.stderr or ""),
        "error": None,
    }


def _project_root(project: Any) -> Path:
    """Resolve the pipeline root: ProjectContext (git_root) or plain path."""
    git_root = getattr(project, "git_root", None)
    if isinstance(git_root, str) and git_root.strip():
        return Path(git_root).expanduser()
    if isinstance(project, (str, Path)):
        return Path(project).expanduser()
    raise ValueError(
        "project must be a ProjectContext (with git_root) or a plain path, "
        f"got {type(project).__name__}"
    )


def _detect_project_type(root: Path, objective: Objective) -> str:
    override = str((objective.context or {}).get("project_type") or "").lower()
    if override in ("pyproject", "python"):
        return "pyproject"
    if override in ("node", "generic"):
        return override
    if (root / "pyproject.toml").is_file():
        return "pyproject"
    if (root / "package.json").is_file():
        return "node"
    return "generic"


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if str(v).strip()]
    return []


def _truncate_text(text: str, cap: int) -> str:
    text = str(text or "").strip()
    if len(text) <= cap:
        return text
    return text[:cap] + "…"


def _not_applicable(stage: VerifyStage, reason: str) -> tuple[str, list[Evidence]]:
    return NOT_APPLICABLE, [
        Evidence(
            kind=f"stage:{stage.value}",
            detail=f"{NOT_APPLICABLE}: {reason}",
            source="verifier",
        )
    ]


def _has_tests(root: Path) -> bool:
    if (root / "tests").is_dir():
        return True
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                if name.startswith("test_") and name.endswith(".py"):
                    return True
                if name.endswith("_test.py") or name.endswith("_test.js"):
                    return True
            if len(dirpath.split(os.sep)) > 12:
                dirnames[:] = []
    except OSError:
        return False
    return False


def _is_git_repo(root: Path) -> bool:
    current = root.resolve()
    for _ in range(6):
        if (current / ".git").exists():
            return True
        parent = current.parent
        if parent == current:
            return False
        current = parent
    return False


def _run_static_stage(root: Path) -> tuple[str, list[Evidence]]:
    """In-process static scan: parse every Python file with ast (no writes)."""
    walked = 0
    sources: list[Path] = []
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d
                for d in dirnames
                if d not in STATIC_SKIP_DIRS and not d.startswith(".")
            ]
            for name in filenames:
                walked += 1
                if walked > MAX_STATIC_FILES:
                    break
                if name.endswith(".py"):
                    sources.append(Path(dirpath) / name)
            if walked > MAX_STATIC_FILES:
                break
    except OSError as exc:
        return STAGE_FAILED, [
            Evidence(
                kind="stage:static",
                detail=f"failed: cannot walk project root {root}: {exc}",
                source="verifier",
            )
        ]
    errors: list[Evidence] = []
    for path in sources:
        try:
            if path.stat().st_size > MAX_STATIC_FILE_BYTES:
                continue
            ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
        except (SyntaxError, ValueError, OSError) as exc:
            errors.append(
                Evidence(
                    kind="stage:static:detail",
                    detail=f"failed: {path}: {exc}",
                    source="verifier",
                )
            )
    if errors:
        return STAGE_FAILED, [
            Evidence(
                kind="stage:static",
                detail=f"failed: {len(errors)} of {len(sources)} python files "
                "have syntax errors",
                source="verifier",
            ),
            *errors,
        ]
    return STAGE_PASSED, [
        Evidence(
            kind="stage:static",
            detail=f"passed: static scan clean ({len(sources)} python files "
            f"parsed, {walked} files walked)",
            source="verifier",
        )
    ]


def evaluate_quality(
    objective: Objective,
    execution: Execution,
    verification: VerificationResult,
) -> QualityEvaluation:
    """Grade a verified execution: executed + verified, but is it good?

    Heuristic rubric over structured evidence (no model required):
    verified result, evidence depth, output substance, clean stderr,
    sane duration. Grades: pass (>=80), needs_review (50-79), fail (<50
    or unverified). Findings explain every deduction.
    """
    score = 0
    findings: list[str] = []
    if verification.verified:
        score += 50
    else:
        findings.append("execution did not verify; quality fails by definition")
        return QualityEvaluation(
            id=new_id("qual"),
            objective_id=objective.id,
            execution_id=execution.id,
            score=0,
            grade="fail",
            findings=findings,
            evaluator="heuristic",
            created_at=utc_now_iso(),
        )
    evidence_count = len(verification.evidence) + len(execution.evidence)
    if evidence_count >= 3:
        score += 15
    elif evidence_count >= 1:
        score += 8
        findings.append("thin evidence trail (fewer than 3 items)")
    else:
        findings.append("no evidence recorded")
    text = text_of(execution.output) if isinstance(execution.output, dict) else ""
    if len(text.strip()) >= 20:
        score += 15
    elif text.strip():
        score += 7
        findings.append("output substance is minimal")
    else:
        findings.append("no substantive output captured")
    stderr = ""
    if isinstance(execution.output, dict):
        stderr = str(execution.output.get("stderr") or "").strip()
    if not stderr:
        score += 10
    else:
        score += 4
        findings.append("stderr is non-empty; review warnings")
    duration = None
    if isinstance(execution.output, dict):
        duration = execution.output.get("duration_ms")
    if isinstance(duration, (int, float)) and duration >= 0:
        score += 10
    else:
        score += 5
        findings.append("no duration telemetry recorded")
    grade = "pass" if score >= 80 else ("needs_review" if score >= 50 else "fail")
    if grade == "pass" and not findings:
        findings.append("all quality checks passed")
    return QualityEvaluation(
        id=new_id("qual"),
        objective_id=objective.id,
        execution_id=execution.id,
        score=min(100, score),
        grade=grade,
        findings=findings,
        evaluator="heuristic",
        created_at=utc_now_iso(),
    )
