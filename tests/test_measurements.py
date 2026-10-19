"""Tests for efficiency measurement — real measured numbers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentos.measurements import (
    EfficiencyReport,
    full_report,
    measure_import_seconds,
    measure_run_seconds,
    measure_startup_seconds,
)


class TestEfficiency(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())

    def test_import_seconds_measured(self) -> None:
        s = measure_import_seconds()
        self.assertIsInstance(s, float)
        self.assertGreaterEqual(s, 0.0)

    def test_startup_seconds_measured(self) -> None:
        s = measure_startup_seconds(self.home)
        self.assertIsInstance(s, float)
        self.assertGreater(s, 0.0)
        self.assertLess(s, 5.0)  # must stay snappy

    def test_run_seconds_measured(self) -> None:
        s = measure_run_seconds(self.home)
        self.assertIsInstance(s, float)
        self.assertGreaterEqual(s, 0.0)
        self.assertLess(s, 10.0)

    def test_full_report(self) -> None:
        report = full_report(self.home)
        self.assertIsInstance(report, EfficiencyReport)
        self.assertGreater(report.startup_seconds, 0.0)
        self.assertGreater(report.db_bytes, 0)
        self.assertGreater(report.home_bytes, 0)
        self.assertGreaterEqual(report.objective_count, 1)
        self.assertGreaterEqual(report.event_count, 0)
        flat = report.to_flat()
        self.assertIn("startup_seconds", flat)
        self.assertNotIn("to_dict", flat)

    def test_report_bounds(self) -> None:
        report = full_report(self.home)
        # fresh DB with one objective must stay tiny
        self.assertLess(report.db_bytes, 10 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
