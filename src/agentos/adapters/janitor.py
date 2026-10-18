"""JanitorOS adapter: storage admission and protected path enforcement.

Connects AgentOS to the existing JanitorOS storage steward. AgentOS must
request storage admission before creating substantial worktrees, build
artifacts, or temporary files. Never writes to protected paths.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

PROTECTED_PATTERNS = [
    ".git",
    ".env",
    ".env.local",
    ".env.production",
    "id_rsa",
    "id_ed25519",
    ".ssh",
    ".gnupg",
    ".aws",
    ".config/gh",
    ".key",
    ".pem",
    ".p12",
    ".netrc",
    ".npmrc",
]


class JanitorOSAdapter:
    """Adapter to JanitorOS storage admission control."""

    def __init__(self, janitor_binary: str | None = None) -> None:
        self.janitor_binary = janitor_binary or self._find_janitor()

    def _find_janitor(self) -> str | None:
        for path in [
            "/home/portfolio/.local/bin/janitor",
            "/usr/local/bin/janitor",
        ]:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
        return shutil.which("janitor")

    def _run_janitor(self, args: list[str]) -> dict[str, Any]:
        if not self.janitor_binary or not os.path.isfile(self.janitor_binary):
            return {"ok": False, "error": "janitor binary not found"}
        try:
            result = subprocess.run(
                [self.janitor_binary] + args,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                try:
                    return json.loads(result.stdout)
                except json.JSONDecodeError:
                    return {"ok": True, "raw": result.stdout}
            return {
                "ok": False,
                "error": result.stderr or f"exit {result.returncode}",
            }
        except (subprocess.TimeoutExpired, OSError) as exc:
            return {"ok": False, "error": str(exc)}

    def request_admission(self, bytes_needed: int) -> dict[str, Any]:
        result = self._run_janitor(
            ["space", "admit", "--bytes", str(bytes_needed), "--json"]
        )
        if not result.get("ok"):
            return {
                "ok": False,
                "error": result.get("error", "janitor call failed"),
            }
        if result.get("admitted", False):
            return {"ok": True, "admitted": True}
        return {
            "ok": False,
            "error": result.get("reason", "admission denied"),
        }

    def check_protected_path(self, path: str) -> bool:
        path_obj = Path(path)
        for part in path_obj.parts:
            for pattern in PROTECTED_PATTERNS:
                if pattern in part:
                    return True
        return False

    def get_storage_forecast(self) -> dict[str, Any]:
        usage = shutil.disk_usage("/")
        return {
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "available_bytes": usage.free,
            "used_pct": round(usage.used / usage.total * 100, 1),
        }
