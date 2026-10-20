"""Host resource governor: adaptive admission with hysteresis (Phase 3 §9).

Watches *measured* host signals only (disk, load, memory, swap) and gates new
work through four bands:

    HEALTHY     — admit everything
    PRESSED     — admit small, non-heavy work only
    CRITICAL    — admit nothing (fail closed)
    RECOVERING  — like PRESSED, until signals stay clear past a dwell time

Hysteresis rules (no flapping):
- degraded bands are entered immediately (safety first)
- upgrades require BOTH stricter exit thresholds AND a minimum dwell time
- CRITICAL only ever exits into RECOVERING, never straight into HEALTHY
- a relapse from RECOVERING re-enters the measured band immediately

No value is ever invented: unavailable signals are recorded as None and
excluded from band computation; the band is only as trustworthy as the
signals that were actually measured.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from agentos.models import utc_now_iso

BAND_HEALTHY = "HEALTHY"
BAND_PRESSED = "PRESSED"
BAND_CRITICAL = "CRITICAL"
BAND_RECOVERING = "RECOVERING"

_BAND_RANK = {
    BAND_HEALTHY: 0,
    BAND_PRESSED: 1,
    BAND_RECOVERING: 1,
    BAND_CRITICAL: 2,
}

# per-metric: entry thresholds (worse-or-equal to this => metric is in band)
# and exit thresholds (must be strictly better than this to leave the band).
DEFAULT_THRESHOLDS: dict[str, dict[str, float]] = {
    # lower free % is worse
    "disk_free_pct": {"pressed": 20.0, "critical": 10.0, "exit": 25.0},
    # higher load-per-cpu is worse
    "load1_per_cpu": {"pressed": 1.5, "critical": 4.0, "exit": 1.0},
    # lower available % is worse
    "mem_available_pct": {"pressed": 15.0, "critical": 8.0, "exit": 20.0},
    # higher swap used % is worse
    "swap_used_pct": {"pressed": 60.0, "critical": 85.0, "exit": 40.0},
}

# metrics where "worse" means higher value
_HIGHER_IS_WORSE = {"load1_per_cpu", "swap_used_pct"}

DEFAULT_MIN_DWELL_SECONDS = 300.0
DEFAULT_PRESSED_MAX_BYTES = 100 * 1024 * 1024
HISTORY_CAP = 50


@dataclass
class HostMetrics:
    disk_free_pct: float | None = None
    load1_per_cpu: float | None = None
    mem_available_pct: float | None = None
    swap_used_pct: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GovernorState:
    band: str = BAND_HEALTHY
    since: str = ""
    last_change_at: float = 0.0
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GovernorState":
        known = {f: data.get(f) for f in cls.__dataclass_fields__}
        history = known.get("history") or []
        known["history"] = history[-HISTORY_CAP:] if isinstance(history, list) else []
        return cls(**known)


def collect_metrics(state_dir: str | Path) -> HostMetrics:
    """Measure host signals. Unavailable signals stay None (never invented)."""
    metrics = HostMetrics()
    try:
        st = os.statvfs(str(state_dir))
        total = st.f_blocks * st.f_frsize
        if total > 0:
            metrics.disk_free_pct = round(st.f_bavail * st.f_frsize * 100.0 / total, 2)
    except OSError:
        pass
    try:
        load1, _, _ = os.getloadavg()
        metrics.load1_per_cpu = round(load1 / max(os.cpu_count() or 1, 1), 2)
    except (OSError, AttributeError):
        pass
    try:
        meminfo = Path("/proc/meminfo").read_text(encoding="utf-8")
        fields: dict[str, int] = {}
        for line in meminfo.splitlines():
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts:
                fields[key] = int(parts[0])
        total_mem = fields.get("MemTotal")
        avail_mem = fields.get("MemAvailable")
        if total_mem and avail_mem is not None:
            metrics.mem_available_pct = round(avail_mem * 100.0 / total_mem, 2)
        swap_total = fields.get("SwapTotal")
        swap_free = fields.get("SwapFree")
        if swap_total and swap_free is not None:
            metrics.swap_used_pct = round((swap_total - swap_free) * 100.0 / swap_total, 2)
    except (OSError, ValueError):
        pass
    return metrics


class HostGovernor:
    def __init__(
        self,
        home: str | Path,
        config: dict[str, Any] | None = None,
        now: Any = time.time,
    ) -> None:
        self.home = Path(home)
        self.state_path = self.home / "governor_state.json"
        self._now = now
        cfg = ((config or {}).get("hostGovernor")) or {}
        self.min_dwell_seconds = float(cfg.get("min_dwell_seconds", DEFAULT_MIN_DWELL_SECONDS))
        self.pressed_max_bytes = int(cfg.get("pressed_max_bytes", DEFAULT_PRESSED_MAX_BYTES))
        self.thresholds: dict[str, dict[str, float]] = {}
        overrides = cfg.get("thresholds") or {}
        for metric, base in DEFAULT_THRESHOLDS.items():
            merged = dict(base)
            if isinstance(overrides, dict) and isinstance(overrides.get(metric), dict):
                for key in ("pressed", "critical", "exit"):
                    if key in overrides[metric]:
                        merged[key] = float(overrides[metric][key])
            self.thresholds[metric] = merged

    # -- persistence ----------------------------------------------------
    def load_state(self) -> GovernorState:
        if self.state_path.exists():
            try:
                return GovernorState.from_dict(
                    json.loads(self.state_path.read_text(encoding="utf-8"))
                )
            except (json.JSONDecodeError, OSError, TypeError, ValueError):
                pass
        return GovernorState(band=BAND_HEALTHY, since=utc_now_iso(), last_change_at=self._now())

    def save_state(self, state: GovernorState) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        state.history = state.history[-HISTORY_CAP:]
        self.state_path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")

    # -- band computation ----------------------------------------------
    def _metric_band(self, metric: str, value: float | None) -> str | None:
        if value is None:
            return None
        thresholds = self.thresholds.get(metric)
        if not thresholds:
            return None
        if metric in _HIGHER_IS_WORSE:
            if value >= thresholds["critical"]:
                return BAND_CRITICAL
            if value >= thresholds["pressed"]:
                return BAND_PRESSED
            return BAND_HEALTHY
        if value <= thresholds["critical"]:
            return BAND_CRITICAL
        if value <= thresholds["pressed"]:
            return BAND_PRESSED
        return BAND_HEALTHY

    def _metrics_clear_of_exit(self, metrics: HostMetrics) -> bool:
        """True when every measured metric is strictly better than its exit threshold."""
        values = metrics.to_dict()
        for metric, value in values.items():
            if value is None:
                continue
            exit_at = self.thresholds[metric]["exit"]
            if metric in _HIGHER_IS_WORSE:
                if value > exit_at:
                    return False
            else:
                if value < exit_at:
                    return False
        return True

    def _any_measured(self, metrics: HostMetrics) -> bool:
        return any(v is not None for v in metrics.to_dict().values())

    def _measured_band(self, metrics: HostMetrics) -> str:
        """Worst band across measured signals (entry thresholds)."""
        band = BAND_HEALTHY
        for metric, value in metrics.to_dict().items():
            mb = self._metric_band(metric, value)
            if mb and _BAND_RANK[mb] > _BAND_RANK[band]:
                band = mb
        return band

    def _driving_metrics(self, metrics: HostMetrics) -> list[str]:
        driving = []
        for metric, value in metrics.to_dict().items():
            if value is not None and self._metric_band(metric, value) != BAND_HEALTHY:
                driving.append(f"{metric}={value}")
        return driving

    # -- state machine ---------------------------------------------------
    def evaluate(self, metrics: HostMetrics, state: GovernorState | None = None) -> GovernorState:
        state = state or self.load_state()
        if not self._any_measured(metrics):
            # nothing measured: keep the current band (never invent recovery)
            return state
        measured = self._measured_band(metrics)
        clear = self._metrics_clear_of_exit(metrics)
        dwell_ok = (self._now() - state.last_change_at) >= self.min_dwell_seconds
        previous = state.band
        band = previous

        if previous == BAND_HEALTHY:
            band = measured if measured != BAND_HEALTHY else BAND_HEALTHY
        elif previous == BAND_PRESSED:
            if measured == BAND_CRITICAL:
                band = BAND_CRITICAL
            elif clear and dwell_ok:
                band = BAND_HEALTHY
        elif previous == BAND_CRITICAL:
            if measured != BAND_CRITICAL:
                band = BAND_RECOVERING
        elif previous == BAND_RECOVERING:
            if measured in (BAND_PRESSED, BAND_CRITICAL):
                band = measured
            elif clear and dwell_ok:
                band = BAND_HEALTHY

        if band != previous:
            state.history.append(
                {
                    "at": utc_now_iso(),
                    "from": previous,
                    "to": band,
                    "metrics": metrics.to_dict(),
                }
            )
            state.band = band
            state.since = utc_now_iso()
            state.last_change_at = self._now()
            self.save_state(state)
        elif state.band != previous:
            state.band = previous
            self.save_state(state)
        return state

    # -- admission -------------------------------------------------------
    def admission(
        self,
        metrics: HostMetrics | None = None,
        state: GovernorState | None = None,
        bytes_needed: int = 0,
        heavy: bool = False,
    ) -> dict[str, Any]:
        if metrics is None:
            metrics = collect_metrics(self.home)
        state = self.evaluate(metrics, state)
        band = state.band
        driving = self._driving_metrics(metrics)
        admit = True
        reason = "host resources healthy"
        if band == BAND_CRITICAL:
            admit = False
            reason = f"host CRITICAL ({', '.join(driving) or 'prior measurement'})"
        elif band in (BAND_PRESSED, BAND_RECOVERING):
            if heavy:
                admit = False
                reason = f"host {band}: heavy work refused ({', '.join(driving)})"
            elif bytes_needed > self.pressed_max_bytes:
                admit = False
                reason = (
                    f"host {band}: {bytes_needed} bytes exceeds "
                    f"pressed cap {self.pressed_max_bytes} ({', '.join(driving)})"
                )
            else:
                reason = f"host {band}: small work admitted ({', '.join(driving)})"
        return {
            "ok": True,
            "admit": admit,
            "band": band,
            "reason": reason,
            "driving_metrics": driving,
            "metrics": metrics.to_dict(),
            "pressed_max_bytes": self.pressed_max_bytes,
        }
