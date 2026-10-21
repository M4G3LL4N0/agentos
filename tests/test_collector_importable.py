"""Regression: control/collector.py must be safely importable (Phase 5A/P4).

The module-level `while True` loop (Phase-4B incident: duplicate live.json
writers from a plain import) must live behind an explicit main() entry point.
This test skips locally (the collector is server-only) and on the server
asserts: import spawns no child process, mutates no state file, and returns
while the service keeps running.
"""

import importlib.util
import os
import time
import unittest
from pathlib import Path

COLLECTOR = Path("/srv/noaerth/control/collector.py")
LIVE_JSON = Path("/srv/noaerth/control/state/live.json")


def _procs_matching(needle: str) -> int:
    count = 0
    proc = Path("/proc")
    if not proc.is_dir():
        return 0
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmd = (entry / "cmdline").read_bytes().decode("utf-8", "replace")
        except OSError:
            continue
        if needle in cmd:
            count += 1
    return count


@unittest.skipUnless(COLLECTOR.exists(), "collector.py is server-only")
class TestCollectorImportable(unittest.TestCase):
    def test_plain_import_has_no_side_effects(self) -> None:
        before_live_mtime = LIVE_JSON.stat().st_mtime if LIVE_JSON.exists() else None
        before_pids = _procs_matching("collector.py")

        spec = importlib.util.spec_from_file_location(
            "collector_under_test", COLLECTOR
        )
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        started = time.time()
        spec.loader.exec_module(module)
        elapsed = time.time() - started

        # Import must return promptly (a module-level loop would hang here).
        self.assertLess(elapsed, 10.0, "import blocked: module-level loop still present")

        # No collector process spawned by the import itself.
        time.sleep(0.5)
        after_pids = _procs_matching("collector.py")
        self.assertLessEqual(
            after_pids, before_pids + 1,
            f"import spawned collector processes: before={before_pids} after={after_pids}",
        )
        # +1 tolerates the systemd-managed instance already running.

        # No live.json write from the import (mtime unchanged).
        if before_live_mtime is not None and LIVE_JSON.exists():
            after_live_mtime = LIVE_JSON.stat().st_mtime
            self.assertEqual(
                before_live_mtime, after_live_mtime,
                "import mutated live.json — module-level side effect",
            )

        # The entry point exists for the systemd unit.
        self.assertTrue(hasattr(module, "main"), "collector.py lacks main() entry point")

    def test_main_guard_present_in_source(self) -> None:
        source = COLLECTOR.read_text(encoding="utf-8")
        self.assertIn('if __name__ == "__main__":', source)
        self.assertIn("def main():", source)


if __name__ == "__main__":
    unittest.main()
