"""GitHub capability boundary: read-oriented operations via the ``gh`` CLI.

Read first: repository, branch, file, commit, issue and pull-request
inspection. No write operations exist in this adapter — no push, merge or
remote mutation is possible through it, by construction. GitHub becomes
persistent project context (e.g. RESEARCH_THEN_EXECUTE research steps),
not an uncontrolled execution target.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from agentos.adapters.base import AdapterResult, ExecutionRequest
from agentos.adapters.proc import resolve_binary, run_command, version_output
from agentos.models import Evidence, HealthStatus

REPO_OPERATION = "github.repo"
BRANCH_OPERATION = "github.branch"
FILE_OPERATION = "github.file"
COMMIT_OPERATION = "github.commit"
ISSUE_OPERATION = "github.issue"
PR_OPERATION = "github.pr"

SUPPORTED = {
    REPO_OPERATION,
    BRANCH_OPERATION,
    FILE_OPERATION,
    COMMIT_OPERATION,
    ISSUE_OPERATION,
    PR_OPERATION,
}


class GitHubAdapter:
    """Inspects GitHub repositories through the authenticated ``gh`` CLI."""

    name = "github"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})

    def binary(self) -> str | None:
        binary = self.config.get("binary")
        return resolve_binary(str(binary) if binary else None, "GH_BIN", "gh")

    def execute(self, operation: str, request: ExecutionRequest) -> AdapterResult:
        if operation not in SUPPORTED:
            return AdapterResult(
                ok=False,
                error=f"github adapter does not support operation {operation!r}",
                evidence=[
                    Evidence(
                        kind="unsupported_operation",
                        detail=f"github adapter received {operation!r}",
                        source="github",
                    )
                ],
            )
        binary = self.binary()
        if binary is None:
            return AdapterResult(
                ok=False,
                error="gh binary not found (set github.binary or install gh)",
                evidence=[
                    Evidence(
                        kind="not_found", detail="gh executable missing", source="github"
                    )
                ],
            )
        try:
            argv = self._argv(binary, operation, request.params)
        except ValueError as exc:
            return AdapterResult(
                ok=False,
                error=str(exc),
                evidence=[
                    Evidence(kind="missing_input", detail=str(exc), source="github")
                ],
            )
        timeout = float(
            request.params.get("timeout_seconds")
            or request.params.get("timeout")
            or request.timeout_seconds
        )
        result = run_command(argv, timeout=timeout)
        return self._result(operation, result)

    def _argv(
        self, binary: str, operation: str, params: dict[str, Any]
    ) -> list[str]:
        repo = params.get("repo")
        if not repo:
            raise ValueError(f"{operation} requires a 'repo' param (owner/name)")
        if operation == REPO_OPERATION:
            return [
                binary, "repo", "view", str(repo), "--json",
                "nameWithOwner,description,defaultBranchRef,stargazerCount,forkCount,updatedAt",
            ]
        if operation == BRANCH_OPERATION:
            branch = params.get("branch")
            if branch:
                return [
                    binary, "api", f"repos/{repo}/branches/{branch}",
                    "--jq", "{name: .name, sha: .commit.sha}",
                ]
            return [binary, "api", f"repos/{repo}/branches", "--jq", ".[].name"]
        if operation == FILE_OPERATION:
            path = params.get("path")
            if not path:
                raise ValueError("github.file requires a 'path' param")
            url = f"repos/{repo}/contents/{path}"
            ref = params.get("ref") or params.get("branch")
            if ref:
                url += f"?ref={ref}"
            return [binary, "api", url]
        if operation == COMMIT_OPERATION:
            sha = params.get("sha") or params.get("ref") or "HEAD"
            return [
                binary, "api", f"repos/{repo}/commits/{sha}", "--jq",
                "{sha: .sha, message: .commit.message, author: .commit.author.name, date: .commit.author.date}",
            ]
        if operation == ISSUE_OPERATION:
            number = params.get("number")
            if number is None:
                raise ValueError("github.issue requires a 'number' param")
            return [
                binary, "issue", "view", str(number), "--repo", str(repo), "--json",
                "number,title,state,author,createdAt,body",
            ]
        number = params.get("number")
        if number is None:
            raise ValueError("github.pr requires a 'number' param")
        return [
            binary, "pr", "view", str(number), "--repo", str(repo), "--json",
            "number,title,state,mergeable,additions,deletions,changedFiles,headRefName,baseRefName",
        ]

    def _result(self, operation: str, result: dict[str, Any]) -> AdapterResult:
        if result.get("timed_out"):
            return AdapterResult(
                ok=False,
                output={"exit_code": None, "duration_ms": result["duration_ms"]},
                error=str(result.get("error")),
                evidence=[
                    Evidence(kind="timeout", detail=str(result.get("error")), source="github")
                ],
            )
        if result.get("error"):
            return AdapterResult(
                ok=False,
                error=str(result.get("error")),
                evidence=[
                    Evidence(kind="not_found", detail=str(result.get("error")), source="github")
                ],
            )
        if result["exit_code"] != 0:
            return AdapterResult(
                ok=False,
                output={
                    "stdout": result["stdout"],
                    "stderr": result["stderr"],
                    "exit_code": result["exit_code"],
                    "duration_ms": result["duration_ms"],
                },
                error=(
                    f"gh exit_code={result['exit_code']}: "
                    f"{(result['stderr'] or '').strip()[:500]}"
                ),
                evidence=[
                    Evidence(
                        kind="exit_code",
                        detail=f"gh exit code {result['exit_code']}",
                        source="github",
                    )
                ],
            )
        output: dict[str, Any] = {
            "exit_code": 0,
            "duration_ms": result["duration_ms"],
        }
        if operation == FILE_OPERATION:
            content, decoded = self._decode_contents(result["stdout"])
            output["content"] = content
            output["decoded"] = decoded
        else:
            try:
                output["data"] = json.loads(result["stdout"]) if result["stdout"].strip() else None
            except json.JSONDecodeError:
                output["text"] = result["stdout"]
        return AdapterResult(
            ok=True,
            output=output,
            exit_code=0,
            evidence=[
                Evidence(
                    kind="github_read",
                    detail=f"{operation} ok in {result['duration_ms']}ms",
                    source="github",
                )
            ],
        )

    @staticmethod
    def _decode_contents(stdout: str) -> tuple[str, bool]:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            return stdout, False
        if isinstance(payload, dict) and payload.get("encoding") == "base64":
            try:
                text = base64.b64decode(payload.get("content", ""), validate=False).decode(
                    "utf-8", errors="replace"
                )
                return text, True
            except (ValueError, TypeError):
                return stdout, False
        if isinstance(payload, list):
            names = [item.get("name") for item in payload if isinstance(item, dict)]
            return "\n".join(n for n in names if n), True
        return stdout, False

    def probe(self) -> HealthStatus:
        from agentos.adapters.proc import binary_usable

        binary = self.binary()
        if not binary_usable(binary):
            return HealthStatus.UNAVAILABLE
        version = version_output(binary)
        if version.get("exit_code") != 0:
            return HealthStatus.MISCONFIGURED
        auth = run_command([binary, "auth", "status"], timeout=15.0)
        if auth.get("exit_code") == 0:
            return HealthStatus.AVAILABLE
        return HealthStatus.AUTH_REQUIRED