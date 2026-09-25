"""Tests for adapters: real local execution behaviour."""

import tempfile
import unittest
from pathlib import Path

from agentos.adapters.base import ExecutionRequest
from agentos.adapters.echo import EchoAdapter
from agentos.adapters.filesystem import FilesystemAdapter
from agentos.adapters.shell import ShellAdapter


class TestShellAdapter(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = ShellAdapter()

    def test_success_captures_output_and_exit_code(self) -> None:
        result = self.adapter.execute(
            "shell.run", ExecutionRequest(operation="shell.run", params={"command": "echo hello"})
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("hello", result.output["stdout"])
        self.assertTrue(any(e.kind == "exit_code" for e in result.evidence))

    def test_nonzero_exit_is_failure_not_success(self) -> None:
        result = self.adapter.execute(
            "shell.run", ExecutionRequest(operation="shell.run", params={"command": "exit 3"})
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, 3)
        self.assertIsNotNone(result.error)

    def test_missing_command_is_failure(self) -> None:
        result = self.adapter.execute(
            "shell.run", ExecutionRequest(operation="shell.run", params={})
        )
        self.assertFalse(result.ok)
        self.assertIsNotNone(result.error)

    def test_timeout_is_failure(self) -> None:
        result = self.adapter.execute(
            "shell.run",
            ExecutionRequest(
                operation="shell.run",
                params={"command": "sleep 5", "timeout": 0.2},
            ),
        )
        self.assertFalse(result.ok)
        self.assertIn("timed out", result.error or "")

    def test_unsupported_operation(self) -> None:
        result = self.adapter.execute(
            "fs.read", ExecutionRequest(operation="fs.read", params={})
        )
        self.assertFalse(result.ok)

    def test_configured_operation_with_command_executes(self) -> None:
        result = self.adapter.execute(
            "greet.run",
            ExecutionRequest(
                operation="greet.run", params={"command": "echo configured-ok"}
            ),
        )
        self.assertTrue(result.ok)
        self.assertIn("configured-ok", result.output["stdout"])

    def test_probe(self) -> None:
        self.assertTrue(self.adapter.probe())


class TestFilesystemAdapter(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.adapter = FilesystemAdapter()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_write_then_read(self) -> None:
        target = self.root / "sub" / "hello.txt"
        written = self.adapter.execute(
            "fs.write",
            ExecutionRequest(
                operation="fs.write",
                params={"path": str(target), "content": "hello agentos"},
            ),
        )
        self.assertTrue(written.ok)
        read = self.adapter.execute(
            "fs.read",
            ExecutionRequest(operation="fs.read", params={"path": str(target)}),
        )
        self.assertTrue(read.ok)
        self.assertEqual(read.output["content"], "hello agentos")

    def test_read_missing_file_fails(self) -> None:
        result = self.adapter.execute(
            "fs.read",
            ExecutionRequest(
                operation="fs.read", params={"path": str(self.root / "nope.txt")}
            ),
        )
        self.assertFalse(result.ok)
        self.assertIn("no such file", result.error or "")

    def test_write_requires_content(self) -> None:
        result = self.adapter.execute(
            "fs.write",
            ExecutionRequest(
                operation="fs.write", params={"path": str(self.root / "x.txt")}
            ),
        )
        self.assertFalse(result.ok)

    def test_exists_and_list(self) -> None:
        (self.root / "a.txt").write_text("a", encoding="utf-8")
        exists = self.adapter.execute(
            "fs.exists",
            ExecutionRequest(
                operation="fs.exists", params={"path": str(self.root / "a.txt")}
            ),
        )
        self.assertTrue(exists.ok)
        self.assertTrue(exists.output["exists"])
        missing = self.adapter.execute(
            "fs.exists",
            ExecutionRequest(
                operation="fs.exists", params={"path": str(self.root / "zz.txt")}
            ),
        )
        self.assertTrue(missing.ok)
        self.assertFalse(missing.output["exists"])
        listed = self.adapter.execute(
            "fs.list",
            ExecutionRequest(operation="fs.list", params={"path": str(self.root)}),
        )
        self.assertTrue(listed.ok)
        self.assertIn("a.txt", listed.output["entries"])

    def test_probe(self) -> None:
        self.assertTrue(self.adapter.probe())


class TestEchoAdapter(unittest.TestCase):
    def test_echo_returns_params(self) -> None:
        adapter = EchoAdapter()
        result = adapter.execute(
            "echo", ExecutionRequest(operation="echo", params={"a": 1})
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.output["echo"], {"a": 1})
        self.assertTrue(adapter.probe())

    def test_echo_rejects_other_operations(self) -> None:
        adapter = EchoAdapter()
        result = adapter.execute(
            "shell.run", ExecutionRequest(operation="shell.run", params={})
        )
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()