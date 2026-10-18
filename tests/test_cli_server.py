"""Tests for server CLI commands: workers, providers, logs."""

from __future__ import annotations

import unittest


class TestCLIServer(unittest.TestCase):
    def test_workers_command(self) -> None:
        from agentos.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["workers"])
        self.assertEqual(args.command, "workers")

    def test_providers_command(self) -> None:
        from agentos.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["providers"])
        self.assertEqual(args.command, "providers")

    def test_logs_command(self) -> None:
        from agentos.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["logs"])
        self.assertEqual(args.command, "logs")

    def test_logs_with_limit(self) -> None:
        from agentos.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["logs", "--limit", "100"])
        self.assertEqual(args.command, "logs")
        self.assertEqual(args.limit, 100)

    def test_recover_command(self) -> None:
        from agentos.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["recover", "obj_123"])
        self.assertEqual(args.command, "recover")
        self.assertEqual(args.objective_id, "obj_123")

    def test_status_command(self) -> None:
        from agentos.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["status"])
        self.assertEqual(args.command, "status")

    def test_doctor_command(self) -> None:
        from agentos.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["doctor"])
        self.assertEqual(args.command, "doctor")

    def test_objectives_command(self) -> None:
        from agentos.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["objectives"])
        self.assertEqual(args.command, "objectives")


if __name__ == "__main__":
    unittest.main()
