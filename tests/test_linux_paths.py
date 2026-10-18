"""Tests for Linux path configuration and AGENTOS_HOME support."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path


class TestLinuxPaths(unittest.TestCase):
    def test_agentos_home_env_var(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["AGENTOS_HOME"] = tmpdir
            from agentos.services import default_home

            result = default_home()
            self.assertEqual(str(result), tmpdir)

    def test_default_home_is_dot_agentos(self) -> None:
        old = os.environ.pop("AGENTOS_HOME", None)
        try:
            from agentos.services import default_home

            result = default_home()
            self.assertEqual(str(result), str(Path.home() / ".agentos"))
        finally:
            if old:
                os.environ["AGENTOS_HOME"] = old

    def test_custom_home_for_server(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["AGENTOS_HOME"] = tmpdir
            from agentos.services import AgentOS

            app = AgentOS()
            self.assertEqual(str(app.home), tmpdir)
            self.assertEqual(app.db_path.name, "agentos.db")

    def test_home_expands_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["AGENTOS_HOME"] = tmpdir
            from agentos.services import AgentOS

            app = AgentOS()
            self.assertTrue(app.home.is_absolute())


if __name__ == "__main__":
    unittest.main()
