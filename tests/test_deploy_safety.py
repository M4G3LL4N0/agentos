"""Regression tests: deployment must never delete state (Phase 3 §8).

These are content-contract tests over the deploy scripts. They encode the
Phase 2 incident (rsync --delete destroyed live state) as permanent guards:
any future edit that reintroduces state deletion, syncs before snapshotting,
or drops the health/rollback gates fails here.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"


class TestSafeDeployScript(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = (DEPLOY / "safe-deploy.sh").read_text(encoding="utf-8")

    def test_delete_flag_only_in_pure_code_subtrees(self) -> None:
        code = "\n".join(
            line
            for line in self.script.splitlines()
            if not line.lstrip().startswith("#")
        )
        joined = code.replace("\\\n", " ")
        invocations = re.findall(r"rsync\b[^&|;\n]+", joined)
        delete_invocations = [i for i in invocations if "--delete" in i]
        self.assertTrue(delete_invocations, "expected restricted --delete hygiene syncs")
        for inv in delete_invocations:
            self.assertRegex(
                inv,
                r"\$HOST:\$REMOTE_CODE/(src|tests)/",
                f"--delete outside pure-code subtrees: {inv!r}",
            )

    def test_state_paths_excluded_from_sync(self) -> None:
        for pattern in (
            "agentos.db*",
            "loop_state.json",
            "loop_limits.json",
            "state/",
            "logs/",
            "contracts/",
            "config.json",
        ):
            self.assertRegex(self.script, rf"--exclude '?{re.escape(pattern)}")

    def test_release_snapshot_precedes_sync(self) -> None:
        snapshot_at = self.script.index("== release snapshot (rollback artifact) ==")
        sync_at = self.script.index("== sync code (state excluded")
        self.assertLess(snapshot_at, sync_at)

    def test_db_snapshot_uses_sqlite_backup_api(self) -> None:
        self.assertIn("src.backup(dst)", self.script)

    def test_rollback_trapped_on_failure(self) -> None:
        self.assertIn("rollback()", self.script)
        self.assertIn("trap 'rollback' ERR", self.script)
        self.assertIn("ROLLING BACK", self.script)
        self.assertIn("tar -xzf $RELEASE_TAR", self.script)

    def test_health_gate_after_restart(self) -> None:
        self.assertIn("restart + health gate", self.script)
        self.assertIn('"healthy":true', self.script)

    def test_storage_admission_gate(self) -> None:
        self.assertIn("janitor space admit", self.script)

    def test_preflight_tests_run_by_default(self) -> None:
        self.assertIn("--skip-tests", self.script)
        self.assertIn("unittest discover", self.script)
        skip_index = self.script.index("SKIP_TESTS")
        tests_index = self.script.index("unittest discover")
        self.assertLess(skip_index, tests_index)

    def test_dry_run_exits_before_mutation(self) -> None:
        dry_at = self.script.index("DRY_RUN_OK")
        snapshot_at = self.script.index("== release snapshot (rollback artifact) ==")
        self.assertLess(dry_at, snapshot_at)


class TestLegacySyncScript(unittest.TestCase):
    def test_legacy_sync_has_no_delete(self) -> None:
        legacy = (DEPLOY / "sync-phase2.sh").read_text(encoding="utf-8")
        rsync_lines = [
            line
            for line in legacy.splitlines()
            if "rsync" in line or line.lstrip().startswith("--")
        ]
        for line in rsync_lines:
            self.assertNotIn("--delete", line, f"destructive rsync arg: {line!r}")
        for pattern in ("agentos.db", "loop_state", "state/", "logs/", "contracts"):
            self.assertRegex(legacy, re.escape(pattern))

    def test_state_deletion_regression_sentinel(self) -> None:
        """The historical failure shape must never reappear in any deploy file."""
        for path in DEPLOY.glob("*.sh"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(
                "rsync --delete /", text, f"raw destructive rsync in {path.name}"
            )


if __name__ == "__main__":
    unittest.main()
