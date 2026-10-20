"""Phase 4B integration: ServerOS snapshot gate inside Hive submission.

Proves, against the real ``mesh.hive.execution`` module (server-only; skips
cleanly where the mesh tree is absent):

- request A (HIGH, fresh HEALTHY snapshot) -> accepted once, registry row +
  HIVE_TASK_SUBMITTED audit event
- request B (LOW, fresh PRESSED snapshot) -> deferred, registry untouched
- fresh CRITICAL snapshot -> refused (serveros_band_critical), registry
  digest unchanged
- duplicate ids -> refused by the pre-existing gate (unchanged behaviour)
- absent snapshot -> this gate skips; existing gates still decide
- no subprocess/worker side effect on any admission path (the module's
  subprocess handle is replaced with a tripwire; janitor stubbed like
  their own tests do)
"""

import hashlib
import json
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

_MESH_ROOT = "/srv/noaerth/autonomousos"
if Path(_MESH_ROOT).is_dir() and _MESH_ROOT not in sys.path:
    sys.path.insert(0, _MESH_ROOT)

try:
    from mesh.hive import execution

    HAVE_MESH = True
except ImportError:
    HAVE_MESH = False


def _studio_schema(db: str) -> None:
    connection = sqlite3.connect(db)
    connection.execute(
        "CREATE TABLE tasks(id TEXT PRIMARY KEY, title TEXT, prompt TEXT, "
        "level TEXT, agent TEXT, deps TEXT, state TEXT, attempts INTEGER, "
        "created INTEGER, venture TEXT)"
    )
    connection.execute("CREATE TABLE events(ts INTEGER, kind TEXT, detail TEXT)")
    connection.commit()
    connection.close()


def _write_manifest(path: str, tasks: list[dict]) -> None:
    Path(path).write_text(
        json.dumps({"schema": "noaerth.hive.submission/1", "tasks": tasks}),
        encoding="utf-8",
    )


def _digest(db: str) -> str:
    return hashlib.sha256(Path(db).read_bytes()).hexdigest()


def _task(task_id: str, level: str) -> dict:
    return {
        "id": task_id,
        "title": f"{task_id} title",
        "prompt": "implement the thing",
        "venture": "realm",
        "level": level,
    }


@unittest.skipUnless(HAVE_MESH, "mesh tree not present (server-only integration test)")
class TestServerosHiveGate(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.db = str(self.dir / "studio.sqlite3")
        _studio_schema(self.db)
        self.resource = self.dir / "resource.json"
        self._write_resource()
        self.snapshot = self.dir / "snapshot.json"
        self._write_snapshot("HEALTHY")
        # stub janitor like their own ExecutionAdapterTest; tripwire the
        # module subprocess handle so any spawn fails the test loudly
        self._original_janitor = execution.janitor_admission
        self._original_subprocess = execution.subprocess

        class _NoSpawn:
            def run(self, *args, **kwargs):
                raise AssertionError("admission path spawned a subprocess")

        execution.janitor_admission = lambda *_: {"accepted": True, "reason": "test"}
        execution.subprocess = _NoSpawn()  # type: ignore[assignment]
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        execution.janitor_admission = self._original_janitor
        execution.subprocess = self._original_subprocess

    def _write_resource(self, allow: bool = True, used_pct: float = 50.0) -> None:
        self.resource.write_text(
            json.dumps(
                {
                    "ts": int(time.time()),
                    "mode": "HEALTHY" if allow else "SEVERE",
                    "allow_new_work": allow,
                    "host": {"disk": {"used_pct": used_pct}},
                }
            ),
            encoding="utf-8",
        )

    def _write_snapshot(self, band: str, age_s: float = 1.0) -> None:
        self.snapshot.write_text(
            json.dumps(
                {
                    "resource_band": band,
                    "_meta": {"generated_epoch": time.time() - age_s},
                }
            ),
            encoding="utf-8",
        )

    def _submit(self, tasks: list[dict], *, snapshot: bool = True, name: str = "m"):
        manifest = self.dir / f"{name}.json"
        _write_manifest(str(manifest), tasks)
        kwargs = {}
        if snapshot:
            kwargs["snapshot_path"] = str(self.snapshot)
        else:
            kwargs["snapshot_path"] = str(self.dir / "absent.json")
        return execution.submit_manifest(
            str(manifest),
            studio_db=self.db,
            resource_state=str(self.resource),
            expected_bytes=1,
            **kwargs,
        )

    def _rows(self, query: str):
        connection = sqlite3.connect(self.db)
        try:
            return [r[0] for r in connection.execute(query)]
        finally:
            connection.close()

    def test_high_accepted_under_healthy_snapshot(self) -> None:
        result = self._submit([_task("GATE-A-01", "HIGH")])
        self.assertTrue(result["accepted"])
        self.assertEqual(
            result["reason"], "submitted_to_existing_studio_registry"
        )
        self.assertEqual(self._rows("SELECT id FROM tasks"), ["GATE-A-01"])
        self.assertEqual(
            self._rows("SELECT kind FROM events"), ["HIVE_TASK_SUBMITTED"]
        )

    def test_low_deferred_under_pressed_snapshot(self) -> None:
        self._write_snapshot("PRESSED")
        before = _digest(self.db)
        result = self._submit([_task("GATE-B-01", "LOW")])
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "serveros_low_priority_deferred")
        self.assertEqual(result["deferred_tasks"], ["GATE-B-01"])
        self.assertEqual(result["retry_after_s"], 60)
        self.assertEqual(_digest(self.db), before)
        self.assertEqual(self._rows("SELECT id FROM tasks"), [])

    def test_medium_unaffected_under_pressed_snapshot(self) -> None:
        self._write_snapshot("PRESSED")
        result = self._submit([_task("GATE-C-01", "MEDIUM")])
        self.assertTrue(result["accepted"])

    def test_critical_refused_registry_unchanged(self) -> None:
        self._write_snapshot("CRITICAL")
        before = _digest(self.db)
        result = self._submit([_task("GATE-D-01", "CRITICAL")])
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "serveros_band_critical")
        self.assertEqual(result["retry_after_s"], 300)
        self.assertEqual(_digest(self.db), before)
        self.assertEqual(self._rows("SELECT id FROM tasks"), [])

    def test_duplicate_refused_by_preexisting_gate(self) -> None:
        first = self._submit([_task("GATE-E-01", "HIGH")], name="e1")
        self.assertTrue(first["accepted"])
        with self.assertRaises(ValueError) as cm:
            self._submit([_task("GATE-E-01", "HIGH")], name="e2")
        self.assertIn("already exist", str(cm.exception))
        self.assertEqual(self._rows("SELECT id FROM tasks"), ["GATE-E-01"])

    def test_absent_snapshot_skips_gate_existing_gates_decide(self) -> None:
        result = self._submit([_task("GATE-F-01", "LOW")], snapshot=False)
        self.assertTrue(result["accepted"])  # gate skipped; healthy resource admits
        self.assertEqual(self._rows("SELECT id FROM tasks"), ["GATE-F-01"])

    def test_stale_snapshot_skips_gate(self) -> None:
        self._write_snapshot("CRITICAL", age_s=9999)
        result = self._submit([_task("GATE-G-01", "LOW")])
        self.assertTrue(result["accepted"])
        self.assertEqual(self._rows("SELECT id FROM tasks"), ["GATE-G-01"])

    def test_recovering_defers_low_like_pressed(self) -> None:
        self._write_snapshot("RECOVERING")
        result = self._submit([_task("GATE-H-01", "LOW")])
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "serveros_low_priority_deferred")


if __name__ == "__main__":
    unittest.main()
