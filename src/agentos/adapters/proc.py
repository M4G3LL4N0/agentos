"""Shared subprocess helper for CLI-backed adapters."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from typing import Any


def resolve_binary(configured: str | None, env_name: str, default: str) -> str | None:
    """Resolve a CLI binary: explicit config > env var > PATH lookup."""
    if configured:
        return configured
    env_value = os.environ.get(env_name)
    if env_value:
        return env_value
    return shutil.which(default)


def run_command(
    argv: list[str],
    timeout: float = 120.0,
    cwd: str | None = None,
    env_extra: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run argv (no shell) and return a structured result dict.

    Never raises on process failure; raises only if the binary itself
    cannot be started (FileNotFoundError/OSError surface as a dict too).
    """
    env = dict(os.environ)
    for key, value in (env_extra or {}).items():
        env[str(key)] = str(value)
    start = time.monotonic()
    try:
        proc = subprocess.run(
            argv,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd or None,
            env=env,
        )
    except subprocess.TimeoutExpired:
        duration_ms = int((time.monotonic() - start) * 1000)
        return {
            "stdout": "",
            "stderr": "",
            "exit_code": None,
            "duration_ms": duration_ms,
            "timed_out": True,
            "error": f"command timed out after {timeout:g}s (process killed)",
        }
    except FileNotFoundError:
        return {
            "stdout": "",
            "stderr": "",
            "exit_code": None,
            "duration_ms": int((time.monotonic() - start) * 1000),
            "error": f"executable not found: {argv[0] if argv else ''}",
        }
    except OSError as exc:
        return {
            "stdout": "",
            "stderr": "",
            "exit_code": None,
            "duration_ms": int((time.monotonic() - start) * 1000),
            "error": f"failed to start process: {exc}",
        }
    return {
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
        "exit_code": proc.returncode,
        "duration_ms": int((time.monotonic() - start) * 1000),
        "timed_out": False,
    }


def version_output(binary: str, args: list[str] | None = None, timeout: float = 15.0) -> dict[str, Any]:
    return run_command([binary, *(args or ["--version"])], timeout=timeout)


def binary_usable(binary: str | None) -> bool:
    """True when the binary resolves to something directly executable.

    ``shutil.which`` covers both PATH names and absolute paths (returned
    as-is only when executable), so a configured-but-missing binary is
    honestly reported as unusable.
    """
    if not binary:
        return False
    return shutil.which(binary) is not None