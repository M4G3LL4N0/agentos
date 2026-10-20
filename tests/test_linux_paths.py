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

    def test_config_dir_env_override(self) -> None:
        old_home = os.environ.get("AGENTOS_HOME")
        old_cfg = os.environ.get("AGENTOS_CONFIG_DIR")
        try:
            with (
                tempfile.TemporaryDirectory() as home,
                tempfile.TemporaryDirectory() as cfg,
            ):
                os.environ["AGENTOS_HOME"] = home
                os.environ["AGENTOS_CONFIG_DIR"] = cfg
                Path(cfg, "config.json").write_text(
                    '{"max_attempts": 7}', encoding="utf-8"
                )
                from agentos.services import AgentOS

                app = AgentOS()
                self.assertEqual(app.config_path, Path(cfg) / "config.json")
                self.assertEqual(app.config["max_attempts"], 7)
                self.assertEqual(str(app.db_path), str(Path(home) / "agentos.db"))
        finally:
            for key, old in (("AGENTOS_HOME", old_home), ("AGENTOS_CONFIG_DIR", old_cfg)):
                if old is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = old

    def test_config_dir_defaults_to_home(self) -> None:
        old_cfg = os.environ.pop("AGENTOS_CONFIG_DIR", None)
        old_home = os.environ.get("AGENTOS_HOME")
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                os.environ["AGENTOS_HOME"] = tmpdir
                from agentos.services import AgentOS

                app = AgentOS()
                self.assertEqual(app.config_path, Path(tmpdir) / "config.json")
        finally:
            if old_cfg is None:
                os.environ.pop("AGENTOS_CONFIG_DIR", None)
            else:
                os.environ["AGENTOS_CONFIG_DIR"] = old_cfg
            if old_home is None:
                os.environ.pop("AGENTOS_HOME", None)
            else:
                os.environ["AGENTOS_HOME"] = old_home

    def test_save_config_creates_missing_config_dir(self) -> None:
        old_cfg = os.environ.get("AGENTOS_CONFIG_DIR")
        old_home = os.environ.get("AGENTOS_HOME")
        try:
            with (
                tempfile.TemporaryDirectory() as home,
                tempfile.TemporaryDirectory() as cfg,
            ):
                os.environ["AGENTOS_HOME"] = home
                os.environ["AGENTOS_CONFIG_DIR"] = str(Path(cfg) / "nested" / "etc")
                from agentos.services import AgentOS

                app = AgentOS()
                app._save_config()
                self.assertTrue(app.config_path.exists())
        finally:
            for key, old in (("AGENTOS_HOME", old_home), ("AGENTOS_CONFIG_DIR", old_cfg)):
                if old is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = old


if __name__ == "__main__":
    unittest.main()
