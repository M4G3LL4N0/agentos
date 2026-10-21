"""Regression: control/collector.py must be safely importable (Phase 5A/P4).

The Phase-4B incident: a plain import of collector.py entered its module-level
`while True` loop, spawning a duplicate live.json writer. The loop now lives
behind main() + a __main__ guard. This test proves, on the server (skips
locally where the file is absent):

1. importing collector.py RETURNS promptly (a module-level loop hangs the
   import forever — detected as subprocess timeout, so a regression fails
   the suite instead of hanging it);
2. the import spawns no collector process of its own;
3. the source still carries the entry-point guard.

Filesystem mtime is deliberately NOT asserted: the systemd-managed collector
writes live.json every 2s, so any mtime comparison races the live service.
Prompt return is the authoritative signal — the loop body is the only writer.
"""

import importlib.util
import subprocess
import sys
import time
import unittest
from pathlib import Path

COLLECTOR = Path("/srv/noaerth/control/collector.py")


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
    def test_import_returns_promptly_in_subprocess(self) -> None:
        """A module-level loop would hang this child until the timeout fires."""
        child = (
            "import importlib.util, time\n"
            f"spec = importlib.util.spec_from_file_location('collector_check', {str(COLLECTOR)!r})\n"
            "mod = importlib.util.module_from_spec(spec)\n"
            "t = time.time()\n"
            "spec.loader.exec_module(mod)\n"
            "print('IMPORT_DONE %.3f has_main=%s' % (time.time() - t, hasattr(mod, 'main')))\n"
        )
        try:
            proc = subprocess.run(
                [sys.executable, "-c", child],
                capture_output=True, text=True, timeout=15, check=False,
            )
        except subprocess.TimeoutExpired:
            self.fail("import hung >=15s: module-level loop is back")
        self.assertEqual(proc.returncode, 0, proc.stderr[-400:])
        self.assertIn("IMPORT_DONE", proc.stdout)
        self.assertIn("has_main=True", proc.stdout)

    def test_plain_import_spawns_no_collector_process(self) -> None:
        before = _procs_matching("collector.py")
        spec = importlib.util.spec_from_file_location(
            "collector_under_test", COLLECTOR
        )
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        started = time.time()
        spec.loader.exec_module(module)
        self.assertLess(time.time() - started, 10.0, "import blocked")
        time.sleep(0.5)
        after = _procs_matching("collector.py")
        # +1 tolerates the single systemd-managed instance already running.
        self.assertLessEqual(
            after, before + 1,
            f"import spawned collector processes: before={before} after={after}",
        )
        self.assertTrue(hasattr(module, "main"), "collector.py lacks main() entry point")

    def test_main_guard_present_in_source(self) -> None:
        source = COLLECTOR.read_text(encoding="utf-8")
        self.assertIn('if __name__ == "__main__":', source)
        self.assertIn("def main():", source)


if __name__ == "__main__":
    unittest.main()
