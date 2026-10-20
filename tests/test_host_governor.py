"""Tests for the host resource governor (Phase 3 §9): bands, hysteresis,
persistence, honest handling of unmeasured signals, and loop integration."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agentos.host_governor import (
    BAND_CRITICAL,
    BAND_HEALTHY,
    BAND_PRESSED,
    BAND_RECOVERING,
    HISTORY_CAP,
    GovernorState,
    HostGovernor,
    HostMetrics,
)


def healthy_metrics() -> HostMetrics:
    return HostMetrics(
        disk_free_pct=60.0, load1_per_cpu=0.2, mem_available_pct=70.0, swap_used_pct=0.0
    )


class FakeClock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.value = now

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class TestBandComputation(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = FakeClock()
        self.gov = HostGovernor(self.tmp.name, now=self.clock)

    def test_healthy_metrics_stay_healthy_and_admit_all(self) -> None:
        result = self.gov.admission(metrics=healthy_metrics(), bytes_needed=10**9, heavy=True)
        self.assertEqual(result["band"], BAND_HEALTHY)
        self.assertTrue(result["admit"])

    def test_disk_pressed_admits_small_refuses_heavy_and_big(self) -> None:
        m = healthy_metrics()
        m.disk_free_pct = 15.0
        small = self.gov.admission(metrics=m, bytes_needed=1024)
        self.assertEqual(small["band"], BAND_PRESSED)
        self.assertTrue(small["admit"])
        heavy = self.gov.admission(metrics=m, heavy=True)
        self.assertFalse(heavy["admit"])
        big = self.gov.admission(metrics=m, bytes_needed=10**9)
        self.assertFalse(big["admit"])
        self.assertIn("disk_free_pct", big["reason"])

    def test_disk_critical_denies_everything(self) -> None:
        m = healthy_metrics()
        m.disk_free_pct = 5.0
        result = self.gov.admission(metrics=m, bytes_needed=1)
        self.assertEqual(result["band"], BAND_CRITICAL)
        self.assertFalse(result["admit"])

    def test_load_critical_is_higher_is_worse(self) -> None:
        m = healthy_metrics()
        m.load1_per_cpu = 5.0
        result = self.gov.admission(metrics=m)
        self.assertEqual(result["band"], BAND_CRITICAL)
        self.assertFalse(result["admit"])

    def test_worst_signal_wins(self) -> None:
        m = healthy_metrics()
        m.mem_available_pct = 10.0
        m.disk_free_pct = 55.0
        result = self.gov.admission(metrics=m)
        self.assertEqual(result["band"], BAND_PRESSED)
        self.assertIn("mem_available_pct", result["driving_metrics"][0])

    def test_none_signals_are_excluded_not_invented(self) -> None:
        m = HostMetrics(disk_free_pct=60.0)
        result = self.gov.admission(metrics=m)
        self.assertEqual(result["band"], BAND_HEALTHY)
        self.assertIsNone(result["metrics"]["load1_per_cpu"])

    def test_no_signals_keeps_current_band(self) -> None:
        state = GovernorState(band=BAND_PRESSED, since="x", last_change_at=self.clock())
        self.gov.save_state(state)
        result = self.gov.admission(metrics=HostMetrics(), state=self.gov.load_state())
        self.assertEqual(result["band"], BAND_PRESSED)


class TestHysteresis(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = FakeClock()
        self.gov = HostGovernor(
            self.tmp.name, config={"hostGovernor": {"min_dwell_seconds": 300}}, now=self.clock
        )

    def test_pressed_does_not_exit_before_dwell(self) -> None:
        m = healthy_metrics()
        m.disk_free_pct = 15.0
        self.gov.admission(metrics=m)
        self.clock.advance(100)
        clear = healthy_metrics()
        result = self.gov.admission(metrics=clear)
        self.assertEqual(result["band"], BAND_PRESSED)

    def test_pressed_exits_after_dwell_when_clear(self) -> None:
        m = healthy_metrics()
        m.disk_free_pct = 15.0
        self.gov.admission(metrics=m)
        self.clock.advance(301)
        clear = healthy_metrics()
        result = self.gov.admission(metrics=clear)
        self.assertEqual(result["band"], BAND_HEALTHY)

    def test_exit_threshold_stricter_than_entry(self) -> None:
        m = healthy_metrics()
        m.disk_free_pct = 15.0
        self.gov.admission(metrics=m)
        self.clock.advance(1000)
        mostly = healthy_metrics()
        mostly.disk_free_pct = 24.0  # better than entry (20) but worse than exit (25)
        result = self.gov.admission(metrics=mostly)
        self.assertEqual(result["band"], BAND_PRESSED)
        mostly.disk_free_pct = 25.0
        result = self.gov.admission(metrics=mostly)
        self.assertEqual(result["band"], BAND_HEALTHY)

    def test_critical_exits_only_into_recovering(self) -> None:
        m = healthy_metrics()
        m.disk_free_pct = 5.0
        self.gov.admission(metrics=m)
        self.clock.advance(1000)
        clear = healthy_metrics()
        result = self.gov.admission(metrics=clear)
        self.assertEqual(result["band"], BAND_RECOVERING)
        heavy = self.gov.admission(metrics=clear, heavy=True)
        self.assertFalse(heavy["admit"])
        self.clock.advance(301)
        result = self.gov.admission(metrics=clear)
        self.assertEqual(result["band"], BAND_HEALTHY)

    def test_recovering_reapses_immediately(self) -> None:
        m = healthy_metrics()
        m.disk_free_pct = 5.0
        self.gov.admission(metrics=m)
        self.clock.advance(1000)
        clear = healthy_metrics()
        self.gov.admission(metrics=clear)  # -> RECOVERING
        relapse = healthy_metrics()
        relapse.load1_per_cpu = 2.0
        result = self.gov.admission(metrics=relapse)
        self.assertEqual(result["band"], BAND_PRESSED)
        critical = healthy_metrics()
        critical.disk_free_pct = 2.0
        self.gov.admission(metrics=critical)  # force CRITICAL first
        self.clock.advance(1000)
        self.gov.admission(metrics=clear)  # -> RECOVERING
        result = self.gov.admission(metrics=critical)
        self.assertEqual(result["band"], BAND_CRITICAL)

    def test_history_records_transitions_bounded(self) -> None:
        for i in range(HISTORY_CAP + 10):
            m = healthy_metrics()
            m.disk_free_pct = 15.0 if i % 2 == 0 else 5.0
            self.gov.admission(metrics=m)
            self.clock.advance(1)
        state = self.gov.load_state()
        self.assertLessEqual(len(state.history), HISTORY_CAP)


class TestConfigAndPersistence(unittest.TestCase):
    def test_config_overrides_thresholds_and_caps(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        clock = FakeClock()
        gov = HostGovernor(
            tmp.name,
            config={
                "hostGovernor": {
                    "min_dwell_seconds": 10,
                    "pressed_max_bytes": 10,
                    "thresholds": {"disk_free_pct": {"pressed": 50.0, "critical": 40.0, "exit": 55.0}},
                }
            },
            now=clock,
        )
        m = HostMetrics(disk_free_pct=45.0)
        result = gov.admission(metrics=m, bytes_needed=11)
        self.assertEqual(result["band"], BAND_PRESSED)
        self.assertFalse(result["admit"])

    def test_state_round_trip(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        gov = HostGovernor(tmp.name, now=FakeClock())
        state = GovernorState(band=BAND_RECOVERING, since="t0", last_change_at=1.0,
                              history=[{"at": "t0", "from": BAND_CRITICAL, "to": BAND_RECOVERING}])
        gov.save_state(state)
        loaded = gov.load_state()
        self.assertEqual(loaded.band, BAND_RECOVERING)
        self.assertEqual(len(loaded.history), 1)
        path = Path(tmp.name) / "governor_state.json"
        raw = json.loads(path.read_text())
        self.assertEqual(raw["band"], BAND_RECOVERING)

    def test_corrupt_state_file_falls_back_healthy(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        (Path(tmp.name) / "governor_state.json").write_text("{not json")
        gov = HostGovernor(tmp.name, now=FakeClock())
        self.assertEqual(gov.load_state().band, BAND_HEALTHY)


class TestLoopIntegration(unittest.TestCase):
    def test_delegate_refused_when_host_governor_denies(self) -> None:
        from agentos.autonomous_loop import AutonomousLoop, LoopStage

        class FakeApp:
            def __init__(self, home: str) -> None:
                self.home = Path(home)

            def host_admission(self, bytes_needed: int = 0, heavy: bool = False) -> dict:
                return {
                    "ok": True,
                    "admit": False,
                    "band": BAND_CRITICAL,
                    "reason": "host CRITICAL (disk_free_pct=1.0)",
                }

        with tempfile.TemporaryDirectory() as tmp:
            loop = AutonomousLoop(FakeApp(tmp))
            ok, detail = loop._run_stage(LoopStage.DELEGATE, "obj_test")
            self.assertFalse(ok)
            self.assertIn("host governor refused", detail)
            self.assertIn("CRITICAL", detail)

    def test_delegate_passes_when_governor_admits(self) -> None:
        from unittest import mock

        from agentos.autonomous_loop import AutonomousLoop, LoopStage

        class FakeApp:
            def __init__(self, home: str) -> None:
                self.home = Path(home)

            def host_admission(self, bytes_needed: int = 0, heavy: bool = False) -> dict:
                return {"ok": True, "admit": True, "band": BAND_HEALTHY, "reason": "ok"}

        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "agentos.adapters.janitor.JanitorOSAdapter._find_janitor", return_value=None
        ):
            loop = AutonomousLoop(FakeApp(tmp))
            ok, detail = loop._run_stage(LoopStage.DELEGATE, "obj_test")
            self.assertTrue(ok, detail)


if __name__ == "__main__":
    unittest.main()
