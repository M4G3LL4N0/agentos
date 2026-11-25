from __future__ import annotations

import importlib.util
import os
import re
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = (ROOT / "deploy/README_CORE.md").read_text(encoding="utf-8")
CURRENT_STATE = (ROOT / "CURRENT_STATE.md").read_text(encoding="utf-8")
BUILD_MAP = (ROOT / "BUILD_MAP.md").read_text(encoding="utf-8")
EXECUTION_LOG = (ROOT / "EXECUTION_LOG.md").read_text(encoding="utf-8")
SECURITY = (ROOT / "SECURITY_BOUNDARIES.md").read_text(encoding="utf-8")
UNIT = (ROOT / "deploy/systemd/agentos.service").read_text(encoding="utf-8")


def load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SECRETS = load_script("create-agentos-secrets.py")
HEALTH = load_script("agentos-healthcheck.py")


class DeploymentRuntimeTests(unittest.TestCase):
    def test_unit_runs_unprivileged_with_persistent_state_and_local_bind(self):
        self.assertIn("User=agentos", UNIT)
        self.assertIn("Group=agentos", UNIT)
        self.assertIn("StateDirectory=agentos", UNIT)
        self.assertIn("Environment=AGENTOS_HOME=/var/lib/agentos", UNIT)
        self.assertIn("EnvironmentFile=/etc/agentos/agentos.env", UNIT)
        self.assertIn("--host 127.0.0.1 --port 8765", UNIT)
        self.assertNotIn("0.0.0.0", UNIT)
        self.assertIn("NoNewPrivileges=yes", UNIT)
        self.assertIn("ProtectSystem=strict", UNIT)
        self.assertIn("ProtectHome=yes", UNIT)
        self.assertIn("UMask=0077", UNIT)
        self.assertIn("StartLimitBurst=5", UNIT)

    def test_runbook_pins_release_and_keeps_remote_access_out_of_scope(self):
        self.assertIn("Status:** reviewable packaging only; not installed on Core 01", RUNBOOK)
        self.assertIn("bdd960cb17249c364225394ceff7d4ce7eed71f2", RUNBOOK)
        self.assertIn("Do not give it to Vercel, Telegram", RUNBOOK)
        self.assertIn("Do not open port 8765", RUNBOOK)
        self.assertIn("Core lease", RUNBOOK)
        self.assertIn("Telegram delivery receipt", RUNBOOK)

    def test_living_docs_distinguish_merged_code_from_undone_live_deployment(self):
        self.assertIn("Round 15/16", CURRENT_STATE)
        self.assertIn("has not been deployed to Core 01", CURRENT_STATE)
        self.assertIn("not installed or live-verified", BUILD_MAP)
        self.assertIn("APN operating score remains 1.3/5", EXECUTION_LOG)
        self.assertIn("implementation is merged to main", SECURITY)
        self.assertIn("has not been released or\n  deployed", SECURITY)
        self.assertIn("implementation is merged to main", SECURITY)
        self.assertNotIn("This proposal is not merged or deployed", SECURITY)

    def test_secret_file_is_random_private_and_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agentos.env"
            SECRETS.create_env_file(path)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            text = path.read_text(encoding="ascii")
            values = dict(line.split("=", 1) for line in text.splitlines())
            self.assertEqual(set(values), {"AGENTOS_API_TOKEN", "AGENTOS_API_READ_TOKEN"})
            self.assertTrue(all(re.fullmatch(r"[0-9a-f]{64}", value) for value in values.values()))
            self.assertNotEqual(values["AGENTOS_API_TOKEN"], values["AGENTOS_API_READ_TOKEN"])
            original = path.read_bytes()
            with self.assertRaises(FileExistsError):
                SECRETS.create_env_file(path)
            self.assertEqual(path.read_bytes(), original)

    def test_healthcheck_reads_private_file_and_calls_only_fixed_loopback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agentos.env"
            path.write_text("AGENTOS_API_TOKEN=" + "a" * 64 + "\n", encoding="ascii")
            path.chmod(0o600)
            self.assertEqual(HEALTH.load_api_token(path), "a" * 64)
            captured = {}

            class Response:
                status = 200
                def close(self): return None
                def read(self, limit):
                    captured["read_limit"] = limit
                    return b'{"ok":true,"healthy":true}'

            class Connection:
                def __init__(self, host, port, timeout):
                    captured["host"] = host
                    captured["port"] = port
                    captured["timeout"] = timeout
                def request(self, method, path, headers):
                    captured["method"] = method
                    captured["path"] = path
                    captured["authorization"] = headers["Authorization"]
                def getresponse(self): return Response()
                def close(self): return None

            with patch.object(HEALTH.http.client, "HTTPConnection", Connection):
                self.assertTrue(HEALTH.check_health("a" * 64))
            self.assertEqual(captured["host"], "127.0.0.1")
            self.assertEqual(captured["port"], 8765)
            self.assertEqual(captured["method"], "GET")
            self.assertEqual(captured["path"], "/health")
            self.assertEqual(captured["authorization"], "Bearer " + "a" * 64)
            self.assertEqual(captured["timeout"], 3.0)
            self.assertEqual(captured["read_limit"], 16_384)

    def test_healthcheck_rejects_broad_file_permissions_symlinks_and_invalid_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agentos.env"
            path.write_text("AGENTOS_API_TOKEN=" + "b" * 64 + "\n", encoding="ascii")
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                HEALTH.load_api_token(path)
            path.chmod(0o600)
            link = Path(tmp) / "link.env"
            link.symlink_to(path)
            with self.assertRaises((OSError, ValueError)):
                HEALTH.load_api_token(link)
            with self.assertRaises(ValueError):
                HEALTH.check_health("short")


if __name__ == "__main__":
    unittest.main()
