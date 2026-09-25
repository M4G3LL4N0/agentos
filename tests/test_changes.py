"""Task 4: filesystem + git change detection (git strictly read-only).

TDD failing test first: exercises capture_fs_state, diff_fs,
git_tracked, git_status_short, attribute_changes, change_summary, and
asserts the module never invokes git add/commit (read-only).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest


def _git_available() -> bool:
    return shutil.which("git") is not None


class TestChanges(unittest.TestCase):
    def test_diff_added_and_modified(self) -> None:
        from agentos.changes import capture_fs_state, diff_fs

        root = tempfile.mkdtemp(prefix="agentos-changes-")
        self.addCleanup(shutil.rmtree, root, True)
        with open(os.path.join(root, "f1"), "w") as fh:
            fh.write("one")
        pre = capture_fs_state(root)
        self.assertIn("f1", pre)
        # create f2 + modify f1
        with open(os.path.join(root, "f2"), "w") as fh:
            fh.write("two")
        with open(os.path.join(root, "f1"), "w") as fh:
            fh.write("one-mutated")
        post = capture_fs_state(root)
        d = diff_fs(pre, post)
        self.assertEqual(d["added"], ["f2"])
        self.assertEqual(d["modified"], ["f1"])
        self.assertEqual(d["deleted"], [])

    def test_git_status_short(self) -> None:
        if not _git_available():
            self.skipTest("git binary not present")
        from agentos.changes import capture_fs_state, git_status_short, git_tracked

        root = tempfile.mkdtemp(prefix="agentos-changes-git-")
        self.addCleanup(shutil.rmtree, root, True)
        subprocess.run(["git", "init"], cwd=root, check=True,
                       capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=root,
                       check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=root,
                       check=True, capture_output=True)
        with open(os.path.join(root, "f1"), "w") as fh:
            fh.write("one")
        subprocess.run(["git", "add", "f1"], cwd=root, check=True,
                       capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True,
                       capture_output=True)
        pre = capture_fs_state(root)
        self.assertIn("f1", git_tracked(root))
        with open(os.path.join(root, "f2"), "w") as fh:
            fh.write("two")
        with open(os.path.join(root, "f1"), "w") as fh:
            fh.write("one-mutated")
        _post = capture_fs_state(root)
        status = git_status_short(root)
        self.assertIn("?? f2", status)
        self.assertIn("M f1", status)
        self.assertIsNotNone(pre)
        self.assertIsNotNone(_post)

    def test_attribute_changes_produced_vs_preexisting(self) -> None:
        from agentos.changes import (
            attribute_changes,
            capture_fs_state,
            change_summary,
        )

        root = tempfile.mkdtemp(prefix="agentos-changes-attr-")
        self.addCleanup(shutil.rmtree, root, True)
        with open(os.path.join(root, "f1"), "w") as fh:
            fh.write("one")
        pre = capture_fs_state(root)
        with open(os.path.join(root, "f2"), "w") as fh:
            fh.write("two")
        with open(os.path.join(root, "f1"), "w") as fh:
            fh.write("one-mutated")
        post = capture_fs_state(root)
        attr = attribute_changes(pre, post, baseline=pre)
        self.assertIn("f2", attr["produced_new"])
        self.assertIn("f2", attr["produced"])
        self.assertIn("f1", attr["pre_existing_modified"])
        self.assertIn("f1", attr["pre_existing"])
        summary = change_summary(pre, post, baseline=pre)
        self.assertIsInstance(summary, str)
        self.assertIn("f2", summary)
        self.assertIn("f1", summary)

    def test_git_read_only_spy(self) -> None:
        """No git add/commit may ever be invoked by agentos.changes."""
        import agentos.changes as changes

        calls: list[list[str]] = []
        real_run = subprocess.run

        def spy(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
            argv = list(args[0]) if args else list(kwargs.get("args", []))
            calls.append([str(a) for a in argv])
            return real_run(*args, **kwargs)

        root = tempfile.mkdtemp(prefix="agentos-changes-spy-")
        self.addCleanup(shutil.rmtree, root, True)
        orig_run = subprocess.run
        subprocess.run = spy  # type: ignore[assignment]
        try:
            changes.capture_fs_state(root)
            changes.diff_fs({}, {})
            changes.git_tracked(root)
            changes.git_status_short(root)
            changes.attribute_changes({}, {})
            changes.change_summary({}, {})
        finally:
            subprocess.run = orig_run
        forbidden = {"add", "commit", "push", "reset", "clean", "checkout"}
        for argv in calls:
            if argv and os.path.basename(argv[0]) == "git":
                sub = argv[1] if len(argv) > 1 else ""
                self.assertNotIn(
                    sub, forbidden,
                    f"read-only violation: git {sub} invoked by changes module",
                )


if __name__ == "__main__":
    unittest.main()


class TestEngineChangeFootprint(unittest.TestCase):
    """Engine annotates executions with files_changed + git_diff_summary."""

    def test_execution_carries_change_footprint(self) -> None:
        from pathlib import Path

        from agentos.engine import Engine
        from agentos.events import EventBus
        from agentos.models import new_id, Objective
        from agentos.registry import CapabilityRegistry
        from agentos.store import Store

        tmp = tempfile.mkdtemp(prefix="agentos-changes-eng-")
        self.addCleanup(shutil.rmtree, tmp, True)
        project = os.path.join(tmp, "proj")
        os.makedirs(project)
        target = os.path.join(project, "note.txt")
        dbdir = tempfile.mkdtemp(prefix="agentos-changes-db-")
        self.addCleanup(shutil.rmtree, dbdir, True)
        store = Store(Path(dbdir) / "agentos.db")
        self.addCleanup(store.close)
        bus = EventBus(store)
        registry = CapabilityRegistry(store, bus)
        engine = Engine(store, bus, registry)
        objective = Objective(
            id=new_id("obj"),
            title="write a file",
            description="d",
            context={
                "operation": "fs.write",
                "params": {
                    "path": target,
                    "content": "proof",
                    "project_path": project,
                },
            },
        )
        store.save_objective(objective)
        report = engine.run(objective.id)
        self.assertEqual(report.state.value, "COMPLETED")
        executions = store.list_executions(objective_id=objective.id)
        self.assertTrue(executions)
        output = executions[-1].output
        self.assertIn("files_changed", output)
        self.assertIn("git_diff_summary", output)
        self.assertIn("note.txt", output["files_changed"]["added"])
        self.assertIn("note.txt", output["files_changed"]["produced"])
        kinds = {e.kind for e in executions[-1].evidence}
        self.assertIn("change_footprint", kinds)
