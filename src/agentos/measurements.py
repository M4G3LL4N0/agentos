"""Efficiency measurements for AgentOS — real numbers, no estimates.

Measures AgentOS construction time, import footprint, storage size,
and per-objective run cost against a real temporary home directory.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


@dataclass
class EfficiencyReport:
    startup_seconds: float
    import_seconds: float
    db_bytes: int
    home_bytes: int
    objective_count: int
    event_count: int
    run_seconds: float
    python_version: str

    def to_flat(self) -> dict[str, Any]:
        return asdict(self)


def _dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                continue
    return total


def measure_import_seconds() -> float:
    start = time.perf_counter()
    import agentos.services  # noqa: F401

    return round(time.perf_counter() - start, 4)


def measure_startup_seconds(home: Path) -> float:
    from agentos.services import AgentOS

    start = time.perf_counter()
    app = AgentOS(home=str(home))
    elapsed = time.perf_counter() - start
    try:
        app.close()
    except Exception:
        pass
    return round(elapsed, 4)


def measure_run_seconds(home: Path) -> float:
    from agentos.services import AgentOS

    app = AgentOS(home=str(home))
    try:
        obj = app.create_objective("efficiency probe", operation="echo", params={"message": "x"})
        start = time.perf_counter()
        app.run(obj.id, force=True, timeout_seconds=30.0)
        return round(time.perf_counter() - start, 4)
    finally:
        try:
            app.close()
        except Exception:
            pass


def full_report(home: Path) -> EfficiencyReport:
    import_seconds = measure_import_seconds()
    startup_seconds = measure_startup_seconds(home)
    run_seconds = measure_run_seconds(home)

    from agentos.services import AgentOS

    app = AgentOS(home=str(home))
    try:
        objective_count = app.store.count_objectives()
        event_count = app.store.count_events()
    finally:
        try:
            app.close()
        except Exception:
            pass

    db_path = home / "agentos.db"
    db_bytes = db_path.stat().st_size if db_path.exists() else 0
    home_bytes = _dir_size(home)

    return EfficiencyReport(
        startup_seconds=startup_seconds,
        import_seconds=import_seconds,
        db_bytes=db_bytes,
        home_bytes=home_bytes,
        objective_count=objective_count,
        event_count=event_count,
        run_seconds=run_seconds,
        python_version=sys.version.split()[0],
    )
