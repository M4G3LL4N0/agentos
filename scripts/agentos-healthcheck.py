#!/usr/bin/env python3
"""Check authenticated AgentOS health through the fixed loopback endpoint."""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import stat
import sys
from pathlib import Path

HEALTH_HOST = "127.0.0.1"
HEALTH_PORT = 8765
HEALTH_PATH = "/health"
_TOKEN = re.compile(r"^[0-9a-f]{64}$")


def load_api_token(path: str | Path) -> str:
    target = Path(path)
    info = target.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError("credential file must be a regular file with mode 0600 or stricter")
    fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    with os.fdopen(fd, "r", encoding="ascii") as stream:
        values: dict[str, str] = {}
        for raw in stream:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            if not separator or key not in {"AGENTOS_API_TOKEN", "AGENTOS_API_READ_TOKEN"}:
                raise ValueError("credential file contains an unsupported entry")
            if key in values:
                raise ValueError("credential file contains a duplicate key")
            values[key] = value
    token = values.get("AGENTOS_API_TOKEN", "")
    if not _TOKEN.fullmatch(token):
        raise ValueError("credential file has no valid main API token")
    for key in ("AGENTOS_API_TOKEN", "AGENTOS_API_READ_TOKEN"):
        if key in values and not _TOKEN.fullmatch(values[key]):
            raise ValueError("credential file contains an invalid API token")
    return token


def check_health(token: str, timeout: float = 3.0) -> bool:
    if not _TOKEN.fullmatch(token):
        raise ValueError("invalid API token")
    connection = http.client.HTTPConnection(HEALTH_HOST, HEALTH_PORT, timeout=timeout)
    try:
        connection.request(
            "GET",
            HEALTH_PATH,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        response = connection.getresponse()
        try:
            if response.status != 200:
                return False
            payload = json.loads(response.read(16_384).decode("utf-8"))
            return isinstance(payload, dict) and payload.get("healthy") is True
        finally:
            response.close()
    except (OSError, http.client.HTTPException, ValueError, UnicodeDecodeError):
        return False
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default="/etc/agentos/agentos.env")
    args = parser.parse_args()
    try:
        token = load_api_token(args.env_file)
    except (OSError, ValueError):
        print("AgentOS loopback health: FAIL (credential file unavailable or invalid)", file=sys.stderr)
        return 1
    if not check_health(token):
        print("AgentOS loopback health: FAIL (authenticated health check failed)", file=sys.stderr)
        return 1
    print("AgentOS loopback health: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
