"""Tests for Routines - cheap detector + early exit, delta report."""

from __future__ import annotations

import os
import json
import tempfile
import unittest
from pathlib import Path

from agentos.store import Store
from agentos.routines import (
    Routine,
    DEFAULT_ROUTINES,
    BUILTIN_DETECTORS,
    detector_usage_snapshot,
    detector_ecosystem_delta,
    detector_repo_changes,
    detector_location_arrival,
    run_cheap_detector,
    generate_delta_report,
    RoutineEngine,
)


class TestRoutine(unittest.TestCase):
    def test_routine_creation(self):
        routine = Routine(
            id="routine_1",
            trigger="test",
            cheap_detector="usage_snapshot",
            change_condition="new files",
            workflow_id="wf1",
            early_exit="NO_ACTION",
            report_policy="delta",
            enabled=True,
        )
        self.assertEqual(routine.id, "routine_1")
        self.assertTrue(routine.enabled)


class TestBuiltinDetectors(unittest.TestCase):
    def test_detector_usage_snapshot_no_data_dir(self):
        store = Store(":memory:")
        result = detector_usage_snapshot(store, {})
        self.assertFalse(result["changed"])
        self.assertEqual(result["reason"], "no data_dir")

    def test_detector_usage_snapshot_no_path(self):
        store = Store(":memory:")
        result = detector_usage_snapshot(store, {"data_dir": "/nonexistent/path"})
        self.assertFalse(result["changed"])
        self.assertEqual(result["reason"], "data_dir not found")

    def test_detector_ecosystem_delta(self):
        store = Store(":memory:")
        result = detector_ecosystem_delta(store, {})
        self.assertIn("changed", result)

    def test_detector_repo_changes_no_path(self):
        store = Store(":memory:")
        result = detector_repo_changes(store, {})
        self.assertFalse(result["changed"])
        self.assertEqual(result["reason"], "no project_path")

    def test_detector_location_arrival(self):
        store = Store(":memory:")
        result = detector_location_arrival(store, {})
        self.assertFalse(result["changed"])

    def test_all_builtin_detectors_registered(self):
        self.assertIn("usage_snapshot", BUILTIN_DETECTORS)
        self.assertIn("ecosystem_delta", BUILTIN_DETECTORS)
        self.assertIn("repo_changes", BUILTIN_DETECTORS)
        self.assertIn("location_arrival", BUILTIN_DETECTORS)


class TestRunCheapDetector(unittest.TestCase):
    def test_run_unknown_detector(self):
        store = Store(":memory:")
        result = run_cheap_detector("unknown", store, {})
        self.assertFalse(result["changed"])
        self.assertIn("unknown detector", result["reason"])


class TestGenerateDeltaReport(unittest.TestCase):
    def test_generate_delta_report(self):
        report = generate_delta_report(
            baseline_ref="baseline@1",
            current_state={"file1": "hash1", "file2": "hash2"},
            task_intent="update files",
        )
        self.assertEqual(report["baseline_ref"], "baseline@1")
        self.assertEqual(report["task_intent"], "update files")
        self.assertEqual(report["report_policy"], "delta")
        self.assertIn("delta", report)


class TestDefaultRoutines(unittest.TestCase):
    def test_default_routines_defined(self):
        self.assertEqual(len(DEFAULT_ROUTINES), 4)
        routine_ids = {r["id"] for r in DEFAULT_ROUTINES}
        self.assertIn("routine_usage_refresh", routine_ids)
        self.assertIn("routine_ecosystem_scan", routine_ids)
        self.assertIn("routine_repo_audit", routine_ids)
        self.assertIn("routine_location_arrival", routine_ids)

    def test_routine_usage_refresh_structure(self):
        r = next(r for r in DEFAULT_ROUTINES if r["id"] == "routine_usage_refresh")
        self.assertEqual(r["trigger"], "usage_refresh")
        self.assertEqual(r["cheap_detector"], "usage_snapshot")
        self.assertEqual(r["workflow_id"], "skill_usage_refresh")
        self.assertEqual(r["early_exit"], "NO_ACTION")

    def test_routine_ecosystem_scan_structure(self):
        r = next(r for r in DEFAULT_ROUTINES if r["id"] == "routine_ecosystem_scan")
        self.assertEqual(r["cheap_detector"], "ecosystem_delta")
        self.assertEqual(r["workflow_id"], "skill_ecosystem_delta")

    def test_routine_repo_audit_structure(self):
        r = next(r for r in DEFAULT_ROUTINES if r["id"] == "routine_repo_audit")
        self.assertEqual(r["cheap_detector"], "repo_changes")
        self.assertEqual(r["workflow_id"], "skill_repo_audit")

    def test_routine_location_arrival_structure(self):
        r = next(r for r in DEFAULT_ROUTINES if r["id"] == "routine_location_arrival")
        self.assertEqual(r["cheap_detector"], "location_arrival")
        self.assertEqual(r["workflow_id"], "skill_location_arrival")
        self.assertEqual(r["report_policy"], "full")


class TestRoutineEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "test.db"))
        self.engine = RoutineEngine(self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_builtins(self):
        # Check routines were loaded
        routines = self.engine.list_routines()
        self.assertGreaterEqual(len(routines), 4)

    def test_run_routine_not_found(self):
        result = self.engine.run_routine("nonexistent", {})
        self.assertFalse(result["ok"])
        self.assertIn("not found or disabled", result["reason"])

    def test_run_routine_disabled(self):
        # Disable a routine
        self.store.conn.execute(
            "UPDATE routines SET enabled=0 WHERE id=?",
            ("routine_usage_refresh",)
        )
        self.store.conn.commit()

        result = self.engine.run_routine("routine_usage_refresh", {})
        self.assertFalse(result["ok"])

    def test_run_routine_usage_snapshot_no_change(self):
        # No data_dir -> no change
        result = self.engine.run_routine("routine_usage_refresh", {})
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "NO_ACTION")
        self.assertFalse(result["detector_result"]["changed"])

    def test_enable_routine(self):
        # Disable first
        self.store.conn.execute(
            "UPDATE routines SET enabled=0 WHERE id=?",
            ("routine_usage_refresh",)
        )
        self.store.conn.commit()

        # Enable it
        ok = self.engine.enable_routine("routine_usage_refresh", True)
        self.assertTrue(ok)

        # Check it's enabled
        row = self.store.conn.execute(
            "SELECT enabled FROM routines WHERE id=?", ("routine_usage_refresh",)
        ).fetchone()
        self.assertEqual(row["enabled"], 1)

    def test_disable_routine(self):
        ok = self.engine.enable_routine("routine_usage_refresh", False)
        self.assertTrue(ok)

        row = self.store.conn.execute(
            "SELECT enabled FROM routines WHERE id=?", ("routine_usage_refresh",)
        ).fetchone()
        self.assertEqual(row["enabled"], 0)


class TestRoutineModel(unittest.TestCase):
    def test_routine_to_dict(self):
        routine = Routine(
            id="routine_test",
            trigger="test",
            cheap_detector="usage_snapshot",
            change_condition="new files",
            workflow_id="wf1",
            early_exit="NO_ACTION",
            report_policy="delta",
        )
        d = routine.to_dict()
        self.assertEqual(d["id"], "routine_test")
        self.assertEqual(d["trigger"], "test")
        self.assertEqual(d["cheap_detector"], "usage_snapshot")


if __name__ == "__main__":
    unittest.main()