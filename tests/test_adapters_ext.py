"""INTEGRATION tests: external adapters with PATH shims (no model calls)."""

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from agentos.adapters.base import ExecutionRequest
from agentos.adapters.github import GitHubAdapter
from agentos.adapters.grok import GrokAdapter
from agentos.adapters.langgraph import LangGraphAdapter
from agentos.adapters.opencode import OpenCodeAdapter
from agentos.adapters.openclaw import OpenClawAdapter
from agentos.adapters.xai import XAIAdapter
from agentos.models import HealthStatus


def write_shim(directory: Path, name: str, script: str) -> str:
    path = directory / name
    path.write_text(script, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


class ShimTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.bin = Path(self.tmp.name) / "bin"
        self.bin.mkdir()
        self.old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(self.bin) + os.pathsep + self.old_path

    def tearDown(self) -> None:
        os.environ["PATH"] = self.old_path
        self.tmp.cleanup()


class TestOpenCodeAdapter(ShimTestCase):
    def test_missing_binary_reports_unavailable(self) -> None:
        adapter = OpenCodeAdapter(config={"binary": "/nonexistent/opencode"})
        self.assertEqual(adapter.probe(), HealthStatus.UNAVAILABLE)
        result = adapter.execute(
            "opencode.run",
            ExecutionRequest(operation="opencode.run", params={"prompt": "hi"}),
        )
        self.assertFalse(result.ok)
        self.assertIn("not found", result.error or "")

    def test_successful_run_parses_output(self) -> None:
        write_shim(
            self.bin,
            "opencode",
            '#!/bin/sh\necho \'{"type":"message.part.updated","part":{"type":"text","text":"did the thing"}}\'\nexit 0\n',
        )
        adapter = OpenCodeAdapter()
        self.assertEqual(adapter.probe(), HealthStatus.AVAILABLE)
        result = adapter.execute(
            "opencode.run",
            ExecutionRequest(
                operation="opencode.run",
                params={"prompt": "implement x", "project": "/tmp", "model": "m", "title": "t"},
            ),
        )
        self.assertTrue(result.ok)
        self.assertIn("did the thing", result.output["text"])
        self.assertEqual(result.exit_code, 0)

    def test_missing_prompt_is_invalid_request(self) -> None:
        write_shim(self.bin, "opencode", '#!/bin/sh\nexit 0\n')
        adapter = OpenCodeAdapter()
        result = adapter.execute(
            "opencode.run", ExecutionRequest(operation="opencode.run", params={})
        )
        self.assertFalse(result.ok)
        self.assertIn("prompt", result.error or "")

    def test_nonzero_exit_is_failure(self) -> None:
        write_shim(
            self.bin, "opencode", '#!/bin/sh\necho "auth failed" >&2\nexit 1\n'
        )
        adapter = OpenCodeAdapter()
        result = adapter.execute(
            "opencode.run",
            ExecutionRequest(operation="opencode.run", params={"prompt": "hi"}),
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, 1)


class TestGitHubAdapter(ShimTestCase):
    def setUp(self) -> None:
        super().setUp()
        write_shim(
            self.bin,
            "gh",
            '#!/bin/sh\n'
            'if [ "$1" = "auth" ]; then echo "Logged in"; exit 0; fi\n'
            'if [ "$1" = "--version" ]; then echo "gh test"; exit 0; fi\n'
            'echo \'{"nameWithOwner":"o/r"}\'; exit 0\n',
        )

    def test_probe_authenticated(self) -> None:
        self.assertEqual(GitHubAdapter().probe(), HealthStatus.AVAILABLE)

    def test_missing_binary_unavailable(self) -> None:
        adapter = GitHubAdapter(config={"binary": "/nonexistent/gh"})
        self.assertEqual(adapter.probe(), HealthStatus.UNAVAILABLE)

    def test_repo_read(self) -> None:
        result = GitHubAdapter().execute(
            "github.repo",
            ExecutionRequest(
                operation="github.repo", params={"repo": "o/r"}
            ),
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.output["data"]["nameWithOwner"], "o/r")

    def test_requires_repo_param(self) -> None:
        result = GitHubAdapter().execute(
            "github.repo", ExecutionRequest(operation="github.repo", params={})
        )
        self.assertFalse(result.ok)

    def test_unauthenticated_probe(self) -> None:
        write_shim(
            self.bin, "gh", '#!/bin/sh\nif [ "$1" = "auth" ]; then exit 1; fi\necho "gh"; exit 0\n'
        )
        self.assertEqual(GitHubAdapter().probe(), HealthStatus.AUTH_REQUIRED)


class TestXAIAdapter(unittest.TestCase):
    def test_no_key_is_auth_required(self) -> None:
        old = os.environ.pop("XAI_API_KEY", None)
        old2 = os.environ.pop("XAI_KEY", None)
        try:
            adapter = XAIAdapter()
            self.assertEqual(adapter.probe(), HealthStatus.AUTH_REQUIRED)
            result = adapter.execute(
                "xai.chat",
                ExecutionRequest(
                    operation="xai.chat",
                    params={"model": "some-model", "prompt": "hi"},
                ),
            )
            self.assertFalse(result.ok)
            self.assertIn("API key", result.error or "")
        finally:
            if old is not None:
                os.environ["XAI_API_KEY"] = old
            if old2 is not None:
                os.environ["XAI_KEY"] = old2

    def test_model_is_required_never_assumed(self) -> None:
        adapter = XAIAdapter(config={"api_key": "test-key"})
        result = adapter.execute(
            "xai.chat",
            ExecutionRequest(operation="xai.chat", params={"prompt": "hi"}),
        )
        self.assertFalse(result.ok)
        self.assertIn("model", result.error or "")

    def test_real_http_round_trip_against_local_server(self) -> None:
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # noqa: ANN002, ANN202
                pass

            def do_POST(self):  # noqa: ANN202
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                assert body["model"] == "test-model"
                assert self.headers.get("Authorization") == "Bearer test-key"
                payload = {
                    "model": "test-model",
                    "choices": [
                        {
                            "message": {"content": "hello back", "tool_calls": None},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"total_tokens": 7},
                }
                raw = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            adapter = XAIAdapter(
                config={
                    "api_key": "test-key",
                    "base_url": f"http://127.0.0.1:{port}",
                }
            )
            result = adapter.execute(
                "xai.chat",
                ExecutionRequest(
                    operation="xai.chat",
                    params={"model": "test-model", "prompt": "hi"},
                ),
            )
            self.assertTrue(result.ok)
            self.assertEqual(result.output["text"], "hello back")
            self.assertEqual(result.output["usage"]["total_tokens"], 7)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_http_401_is_auth_failure(self) -> None:
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # noqa: ANN002, ANN202
                pass

            def do_POST(self):  # noqa: ANN202
                self.send_response(401)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            adapter = XAIAdapter(
                config={"api_key": "bad", "base_url": f"http://127.0.0.1:{port}"}
            )
            result = adapter.execute(
                "xai.chat",
                ExecutionRequest(
                    operation="xai.chat",
                    params={"model": "m", "prompt": "hi"},
                ),
            )
            self.assertFalse(result.ok)
            self.assertIn("auth", (result.error or "").lower())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


class TestGrokAdapter(ShimTestCase):
    def test_missing_binary_unavailable(self) -> None:
        adapter = GrokAdapter(config={"binary": "/nonexistent/grok"})
        self.assertEqual(adapter.probe(), HealthStatus.UNAVAILABLE)

    def test_single_turn_run(self) -> None:
        write_shim(
            self.bin,
            "grok",
            '#!/bin/sh\nif [ "$1" = "--version" ]; then echo "grok test"; exit 0; fi\necho "grok says ok"; exit 0\n',
        )
        adapter = GrokAdapter()
        self.assertEqual(adapter.probe(), HealthStatus.AVAILABLE)
        result = adapter.execute(
            "grok.run",
            ExecutionRequest(operation="grok.run", params={"prompt": "hi"}),
        )
        self.assertTrue(result.ok)
        self.assertIn("grok says ok", result.output["text"])

    def test_missing_prompt_rejected(self) -> None:
        write_shim(self.bin, "grok", '#!/bin/sh\necho "grok"; exit 0\n')
        adapter = GrokAdapter()
        result = adapter.execute(
            "grok.run", ExecutionRequest(operation="grok.run", params={})
        )
        self.assertFalse(result.ok)


class TestOpenClawAdapter(ShimTestCase):
    def test_missing_binary_unavailable(self) -> None:
        adapter = OpenClawAdapter(config={"binary": "/nonexistent/openclaw"})
        self.assertEqual(adapter.probe(), HealthStatus.UNAVAILABLE)

    def test_agent_turn_json(self) -> None:
        write_shim(
            self.bin,
            "openclaw",
            '#!/bin/sh\nif [ "$1" = "--version" ]; then echo "openclaw test"; exit 0; fi\necho \'{"reply":"turn complete"}\'; exit 0\n',
        )
        adapter = OpenClawAdapter()
        self.assertEqual(adapter.probe(), HealthStatus.AVAILABLE)
        result = adapter.execute(
            "openclaw.run",
            ExecutionRequest(operation="openclaw.run", params={"message": "do it"}),
        )
        self.assertTrue(result.ok)
        self.assertIn("turn complete", result.output["text"])

    def test_deliver_flag_never_set_by_adapter(self) -> None:
        shim = self.bin / "openclaw"
        shim.write_text(
            '#!/bin/sh\necho "$@" >> "%s"\necho \'{"reply":"ok"}\'\nexit 0\n'
            % (self.bin / "args.log"),
            encoding="utf-8",
        )
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
        adapter = OpenClawAdapter()
        adapter.execute(
            "openclaw.run",
            ExecutionRequest(operation="openclaw.run", params={"message": "hi"}),
        )
        logged = (self.bin / "args.log").read_text(encoding="utf-8")
        self.assertNotIn("--deliver", logged)


class TestLangGraphAdapter(unittest.TestCase):
    def test_reports_unavailable_without_package(self) -> None:
        adapter = LangGraphAdapter()
        health = adapter.probe()
        result = adapter.execute(
            "graph.run", ExecutionRequest(operation="graph.run", params={})
        )
        self.assertFalse(result.ok)
        if health == HealthStatus.UNAVAILABLE:
            self.assertIn("not installed", result.error or "")
        else:
            self.assertIn("not wired", result.error or "")


if __name__ == "__main__":
    unittest.main()