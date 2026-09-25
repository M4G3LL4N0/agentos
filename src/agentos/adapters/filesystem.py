"""Filesystem adapter: real local file operations."""

from __future__ import annotations

from pathlib import Path

from agentos.adapters.base import AdapterResult, ExecutionRequest
from agentos.models import Evidence

READ_OPERATION = "fs.read"
WRITE_OPERATION = "fs.write"
EXISTS_OPERATION = "fs.exists"
LIST_OPERATION = "fs.list"

SUPPORTED = {READ_OPERATION, WRITE_OPERATION, EXISTS_OPERATION, LIST_OPERATION}


class FilesystemAdapter:
    """Reads, writes, stats and lists files on the local filesystem."""

    name = "filesystem"

    def __init__(self, policy=None) -> None:
        from agentos.policies import ExecutionPolicy

        self.policy = policy or ExecutionPolicy()

    def execute(self, operation: str, request: ExecutionRequest) -> AdapterResult:
        if operation not in SUPPORTED:
            return AdapterResult(
                ok=False,
                error=f"filesystem adapter does not support operation {operation!r}",
                evidence=[
                    Evidence(
                        kind="unsupported_operation",
                        detail=f"filesystem adapter received {operation!r}",
                        source="filesystem",
                    )
                ],
            )
        if operation == READ_OPERATION:
            return self._read(request)
        if operation == WRITE_OPERATION:
            return self._write(request)
        if operation == EXISTS_OPERATION:
            return self._exists(request)
        return self._list(request)

    def _path_param(self, request: ExecutionRequest) -> str | None:
        path = request.params.get("path")
        if path is None:
            return None
        text = str(path).strip()
        return text or None

    def _read(self, request: ExecutionRequest) -> AdapterResult:
        raw = self._path_param(request)
        if raw is None:
            return AdapterResult(
                ok=False,
                error="fs.read requires a 'path' param",
                evidence=[
                    Evidence(
                        kind="missing_input", detail="no path provided", source="filesystem"
                    )
                ],
            )
        path = Path(raw).expanduser()
        try:
            content = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return AdapterResult(
                ok=False,
                error=f"no such file: {path}",
                evidence=[
                    Evidence(
                        kind="not_found", detail=f"missing {path}", source="filesystem"
                    )
                ],
            )
        except OSError as exc:
            return AdapterResult(
                ok=False,
                error=f"cannot read {path}: {exc}",
                evidence=[
                    Evidence(kind="os_error", detail=str(exc), source="filesystem")
                ],
            )
        return AdapterResult(
            ok=True,
            output={"path": str(path), "content": content, "length": len(content)},
            exit_code=0,
            evidence=[
                Evidence(
                    kind="file_read",
                    detail=f"read {path} ({len(content)} chars)",
                    source="filesystem",
                )
            ],
        )

    def _write(self, request: ExecutionRequest) -> AdapterResult:
        raw = self._path_param(request)
        if raw is None:
            return AdapterResult(
                ok=False,
                error="fs.write requires a 'path' param",
                evidence=[
                    Evidence(
                        kind="missing_input", detail="no path provided", source="filesystem"
                    )
                ],
            )
        if "content" not in request.params or request.params["content"] is None:
            return AdapterResult(
                ok=False,
                error="fs.write requires a 'content' param",
                evidence=[
                    Evidence(
                        kind="missing_input",
                        detail="no content provided",
                        source="filesystem",
                    )
                ],
            )
        content = str(request.params["content"])
        decision = self.policy.check_write_path(raw)
        if not decision.allowed:
            return AdapterResult(
                ok=False,
                error="refused by execution policy: " + "; ".join(decision.reasons),
                evidence=[
                    Evidence(
                        kind="policy_denied",
                        detail="; ".join(decision.reasons),
                        source="filesystem",
                    )
                ],
            )
        path = Path(raw).expanduser()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except OSError as exc:
            return AdapterResult(
                ok=False,
                error=f"cannot write {path}: {exc}",
                evidence=[
                    Evidence(kind="os_error", detail=str(exc), source="filesystem")
                ],
            )
        return AdapterResult(
            ok=True,
            output={"path": str(path), "length": len(content)},
            exit_code=0,
            evidence=[
                Evidence(
                    kind="file_written",
                    detail=f"wrote {path} ({len(content)} chars)",
                    source="filesystem",
                )
            ],
        )

    def _exists(self, request: ExecutionRequest) -> AdapterResult:
        raw = self._path_param(request)
        if raw is None:
            return AdapterResult(
                ok=False,
                error="fs.exists requires a 'path' param",
                evidence=[
                    Evidence(
                        kind="missing_input", detail="no path provided", source="filesystem"
                    )
                ],
            )
        path = Path(raw).expanduser()
        exists = path.exists()
        return AdapterResult(
            ok=True,
            output={
                "path": str(path),
                "exists": exists,
                "is_file": path.is_file() if exists else False,
                "is_dir": path.is_dir() if exists else False,
            },
            exit_code=0,
            evidence=[
                Evidence(
                    kind="file_stat",
                    detail=f"exists={exists} {path}",
                    source="filesystem",
                )
            ],
        )

    def _list(self, request: ExecutionRequest) -> AdapterResult:
        raw = self._path_param(request)
        if raw is None:
            return AdapterResult(
                ok=False,
                error="fs.list requires a 'path' param",
                evidence=[
                    Evidence(
                        kind="missing_input", detail="no path provided", source="filesystem"
                    )
                ],
            )
        path = Path(raw).expanduser()
        try:
            entries = sorted(p.name for p in path.iterdir())
        except FileNotFoundError:
            return AdapterResult(
                ok=False,
                error=f"no such directory: {path}",
                evidence=[
                    Evidence(
                        kind="not_found", detail=f"missing {path}", source="filesystem"
                    )
                ],
            )
        except OSError as exc:
            return AdapterResult(
                ok=False,
                error=f"cannot list {path}: {exc}",
                evidence=[
                    Evidence(kind="os_error", detail=str(exc), source="filesystem")
                ],
            )
        return AdapterResult(
            ok=True,
            output={"path": str(path), "entries": entries, "count": len(entries)},
            exit_code=0,
            evidence=[
                Evidence(
                    kind="dir_listed",
                    detail=f"listed {path} ({len(entries)} entries)",
                    source="filesystem",
                )
            ],
        )

    def probe(self) -> bool:
        import tempfile

        try:
            with tempfile.TemporaryDirectory() as tmp:
                probe = Path(tmp) / "agentos_probe.txt"
                probe.write_text("ok", encoding="utf-8")
                return probe.read_text(encoding="utf-8") == "ok"
        except Exception:
            return False