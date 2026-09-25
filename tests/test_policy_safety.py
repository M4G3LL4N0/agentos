"""SAFETY tests: policy enforcement, restricted operations, approvals, secrets."""

import tempfile
import unittest
from pathlib import Path

from agentos.adapters.base import ExecutionRequest
from agentos.adapters.filesystem import FilesystemAdapter
from agentos.adapters.shell import ShellAdapter
from agentos.policies import ExecutionPolicy, redact_secrets, redact_value, scrub_env


class TestPolicyDecisions(unittest.TestCase):
    def test_blocked_destructive_patterns(self) -> None:
        policy = ExecutionPolicy()
        for command in ("rm -rf /", "rm -rf ~", "mkfs -t ext4 /dev/sda1"):
            decision = policy.check_shell(command)
            self.assertFalse(decision.allowed, command)
            self.assertTrue(decision.reasons)

    def test_benign_commands_allowed(self) -> None:
        policy = ExecutionPolicy()
        decision = policy.check_shell("echo hello && ls /tmp")
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.requires_approval)

    def test_destructive_needs_approval_and_is_denied_by_default(self) -> None:
        policy = ExecutionPolicy()
        decision = policy.check_shell("rm -rf ./build", operation_class="standard")
        self.assertFalse(decision.allowed)

    def test_approval_required_class(self) -> None:
        policy = ExecutionPolicy()
        decision = policy.check_shell("kubectl apply -f prod.yaml", operation_class="production")
        self.assertFalse(decision.allowed)
        self.assertIn("requires approval", "; ".join(decision.reasons))
        policy.approvals_granted.append("production")
        decision2 = policy.check_shell("kubectl apply -f prod.yaml", operation_class="production")
        self.assertTrue(decision2.allowed)

    def test_write_roots(self) -> None:
        policy = ExecutionPolicy(allowed_write_roots=["/tmp/agentos-safe"])
        self.assertTrue(policy.check_write_path("/tmp/agentos-safe/a.txt").allowed)
        self.assertFalse(policy.check_write_path("/etc/passwd").allowed)
        self.assertTrue(ExecutionPolicy().check_write_path("/anywhere/x").allowed)


class TestSecretHandling(unittest.TestCase):
    def test_redact_key_value_secrets(self) -> None:
        text = redact_secrets("call with api_key=supersecret123 and token: abc")
        self.assertNotIn("supersecret123", text)
        self.assertNotIn("abc", text)
        self.assertIn("***redacted***", text)

    def test_redact_extended_secret_forms(self) -> None:
        cases = (
            "access_token=DO_NOT_TRACE",
            "refresh_token: REFRESH_ME",
            "client_secret=CLIENT_ME",
            "Bearer BEARER_ME",
            "-----BEGIN PRIVATE KEY-----\nPRIVATE_ME\n-----END PRIVATE KEY-----",
        )
        for value in cases:
            redacted = redact_secrets(value)
            for marker in (
                "DO_NOT_TRACE", "REFRESH_ME", "CLIENT_ME",
                "BEARER_ME", "PRIVATE_ME",
            ):
                self.assertNotIn(marker, redacted)

    def test_redact_value_recurses_without_hiding_cache_key(self) -> None:
        value = {
            "cache_key": "keep-me",
            "nested": [{"access_token": "hide-me"}],
            "message": "password=hide-too",
        }
        redacted = redact_value(value)
        self.assertEqual(redacted["cache_key"], "keep-me")
        self.assertEqual(redacted["nested"][0]["access_token"], "[REDACTED]")
        self.assertNotIn("hide-me", str(redacted))
        self.assertNotIn("hide-too", str(redacted))

    def test_redact_value_redacts_nested_secret_key_hints(self) -> None:
        redacted = redact_value({
            "db_password_hint": "LEAK",
            "nested": {"api_key_material": "LEAK_TOO"},
            "monkey": "keep",
        })
        self.assertEqual(redacted["db_password_hint"], "[REDACTED]")
        self.assertEqual(redacted["nested"]["api_key_material"], "[REDACTED]")
        self.assertEqual(redacted["monkey"], "keep")

    def test_redact_secrets_redacts_json_secret_fields(self) -> None:
        redacted = redact_secrets('{"db_password_hint":"LEAK","message":"safe"}')
        self.assertNotIn("LEAK", redacted)
        self.assertIn("safe", redacted)

    def test_scrub_env(self) -> None:
        scrubbed = scrub_env({"MY_API_KEY": "shh", "PATH": "/usr/bin"})
        self.assertEqual(scrubbed["MY_API_KEY"], "***redacted***")
        self.assertEqual(scrubbed["PATH"], "/usr/bin")

    def test_shell_output_redacts_stderr(self) -> None:
        adapter = ShellAdapter()
        result = adapter.execute(
            "shell.run",
            ExecutionRequest(
                operation="shell.run",
                params={"command": "echo oops >&2; echo token=leaked >&2; exit 0"},
            ),
        )
        self.assertTrue(result.ok)
        self.assertNotIn("leaked", result.output["stderr"])


class TestShellEnforcement(unittest.TestCase):
    def test_policy_denied_command_never_executes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "pwned"
            adapter = ShellAdapter()
            result = adapter.execute(
                "shell.run",
                ExecutionRequest(
                    operation="shell.run",
                    params={"command": f"rm -rf /tmp && touch {marker}"},
                ),
            )
            self.assertFalse(result.ok)
            self.assertIn("policy", (result.error or "").lower())
            self.assertFalse(marker.exists())

    def test_argv_form_executes_without_shell(self) -> None:
        adapter = ShellAdapter()
        result = adapter.execute(
            "shell.run",
            ExecutionRequest(
                operation="shell.run", params={"argv": ["echo", "argv-ok"]}
            ),
        )
        self.assertTrue(result.ok)
        self.assertIn("argv-ok", result.output["stdout"])

    def test_duration_recorded(self) -> None:
        adapter = ShellAdapter()
        result = adapter.execute(
            "shell.run",
            ExecutionRequest(operation="shell.run", params={"command": "true"}),
        )
        self.assertTrue(result.ok)
        self.assertIn("duration_ms", result.output)
        self.assertGreaterEqual(result.output["duration_ms"], 0)

    def test_approval_gated_operation_class(self) -> None:
        adapter = ShellAdapter()
        result = adapter.execute(
            "shell.run",
            ExecutionRequest(
                operation="shell.run",
                params={"command": "echo hi", "operation_class": "production"},
            ),
        )
        self.assertFalse(result.ok)
        self.assertIn("approval", (result.error or "").lower())


class TestFilesystemPolicy(unittest.TestCase):
    def test_write_outside_roots_refused(self) -> None:
        policy = ExecutionPolicy(allowed_write_roots=["/tmp/agentos-safe"])
        adapter = FilesystemAdapter(policy=policy)
        result = adapter.execute(
            "fs.write",
            ExecutionRequest(
                operation="fs.write",
                params={"path": "/tmp/elsewhere/x.txt", "content": "no"},
            ),
        )
        self.assertFalse(result.ok)
        self.assertIn("policy", (result.error or "").lower())

    def test_read_is_not_path_gated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "r.txt"
            target.write_text("hi", encoding="utf-8")
            policy = ExecutionPolicy(allowed_write_roots=["/tmp/agentos-safe"])
            adapter = FilesystemAdapter(policy=policy)
            result = adapter.execute(
                "fs.read",
                ExecutionRequest(operation="fs.read", params={"path": str(target)}),
            )
            self.assertTrue(result.ok)


if __name__ == "__main__":
    unittest.main()