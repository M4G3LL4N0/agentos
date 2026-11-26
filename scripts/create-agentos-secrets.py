#!/usr/bin/env python3
"""Create a root-readable-only AgentOS EnvironmentFile without printing tokens."""
from __future__ import annotations

import argparse
import os
import secrets
import stat
from pathlib import Path


def create_env_file(path: str | Path) -> None:
    target = Path(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(target, flags, 0o600)
    try:
        payload = (
            f"AGENTOS_API_TOKEN={secrets.token_hex(32)}\n"
            f"AGENTOS_API_READ_TOKEN={secrets.token_hex(32)}\n"
            f"AGENTOS_API_HEALTH_TOKEN={secrets.token_hex(32)}\n"
        ).encode("ascii")
        with os.fdopen(fd, "wb", closefd=True) as stream:
            fd = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(target, 0o600)
    except Exception:
        if fd >= 0:
            os.close(fd)
        try:
            target.unlink()
        except OSError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", default="/etc/agentos/agentos.env")
    args = parser.parse_args()
    try:
        create_env_file(args.path)
    except FileExistsError:
        parser.error("refusing to overwrite an existing secret file")
    except OSError as exc:
        parser.error(f"could not create secret file: {exc}")
    print(f"Created protected AgentOS credential file: {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
