"""NOAERTH resource contract: read-only host resource snapshot (Phase 4B).

Publishes ONE bounded JSON document with the contract fields defined in
NOAERTH_RESOURCE_CONTRACT.json. Every field is read from an EXISTING
authority (AutonomousOS governor/scheduler, JanitorOS, REALM Studio, the
ServerOS host governor, the kernel) — nothing is simulated, sampled in a
loop, or invented. When a source cannot be read the field is the explicit
string "UNKNOWN" and `_meta.unknown_fields` lists it.

Sources (read-only, O(1) file reads / one bounded /proc scan / one janitor
call with an injectable runner for tests):
  /srv/noaerth/autonomousos/state/resource.json   AutonomousOS governor
  /srv/noaerth/autonomousos/data/autonomousos.sqlite3   scheduler demands
  /srv/noaerth/realm-studio/state/studio.sqlite3 executions + provider_wait
  janitor space status --json                    JanitorOS storage truth
  agentos host governor (host_governor)          ServerOS band
  /proc, os.getloadavg, os.statvfs               kernel fallbacks

No heavyweight telemetry dependency is installed: stdlib only.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

UNKNOWN = "UNKNOWN"
SCHEMA = "noaerth.resource-snapshot/1"

# Contract fields, in contract order (kept exactly as the mission defines).
CONTRACT_FIELDS = (
    "cpu_usage",
    "load_averages",
    "available_memory",
    "memory_pressure",
    "swap_pressure",
    "disk_free",
    "disk_growth",
    "disk_io_pressure",
    "active_processes",
    "active_workers",
    "queued_demand",
    "storage_admission",
    "resource_band",
    "recommended_worker_capacity",
    "updated_utc",
)

# Mesh DEFAULT_CAPS (existing authority, mirrored for advice only — we never
# raise an existing cap; see mesh/admission.py).
CPU_HEAVY_BASE_CAP = 2
TEST_CAP = 3
MAX_ADDITIONAL_HEAVY_CODING = 2

# Snapshot freshness for consumers (admission treats older as UNKNOWN).
SNAPSHOT_MAX_AGE_S = 180

# Bounded disk-growth history (Phase 5A): raw samples for the short rate,
# hourly aggregates for the long rate. Total file stays ~15KB.
GROWTH_RAW_CAP = 180           # ~6h at the 2-min publish cadence
GROWTH_HOURLY_CAP = 168        # 7 days of hourly buckets
GROWTH_MIN_SAMPLES = 3         # below this a rate is UNKNOWN, never 0
GROWTH_MIN_SPAN_S = 600.0
GROWTH_SHORT_WINDOW_S = 7200.0
GROWTH_LONG_MIN_SPAN_S = 43200.0
GROWTH_WARN_FLOOR_BYTES = 20 * 1024**3   # janitor warn threshold (20 GiB)

# Infrastructure signals (Phase 4 stacking/stuck-scan detection).
STACKING_JANITOR_LIMIT = 2
STACKING_WATCH_LIMIT = 1
STUCK_FIND_ETIME_S = 600

Runner = Callable[[list[str], float], tuple[int, str]]  # (argv, timeout) -> (rc, stdout)


@dataclass(frozen=True)
class ResourcePaths:
    aos_resource: str
    aos_db: str
    studio_db: str
    janitor: str
    snapshot_out: str
    governor_state_dir: str

    @classmethod
    def from_env(cls) -> "ResourcePaths":
        home = os.environ.get("AGENTOS_HOME") or str(Path.home() / ".agentos")
        return cls(
            aos_resource=os.environ.get(
                "AGENTOS_RES_AOS_RESOURCE",
                "/srv/noaerth/autonomousos/state/resource.json",
            ),
            aos_db=os.environ.get(
                "AGENTOS_RES_AOS_DB",
                "/srv/noaerth/autonomousos/data/autonomousos.sqlite3",
            ),
            studio_db=os.environ.get(
                "AGENTOS_RES_STUDIO_DB",
                "/srv/noaerth/realm-studio/state/studio.sqlite3",
            ),
            janitor=os.environ.get("AGENTOS_RES_JANITOR", "/usr/local/bin/janitor"),
            snapshot_out=os.environ.get(
                "AGENTOS_RES_SNAPSHOT", "/var/lib/agentos/resource-snapshot.json"
            ),
            governor_state_dir=home,
        )


def default_runner(argv: list[str], timeout: float) -> tuple[int, str]:
    """Bounded subprocess runner (the only subprocess in the snapshot path)."""
    try:
        proc = subprocess.run(
            argv, text=True, capture_output=True, timeout=timeout, check=False
        )
        return proc.returncode, proc.stdout
    except (OSError, subprocess.SubprocessError):
        return -1, ""


# ---------------------------------------------------------------------------
# raw source loaders (all failure-tolerant; return None on any problem)
# ---------------------------------------------------------------------------

def _read_json(path: str) -> dict[str, Any] | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _load_aos_resource(path: str) -> dict[str, Any] | None:
    return _read_json(path)


def _load_scheduler(path: str) -> dict[str, Any] | None:
    """Authoritative scheduler demand/worker registry (read-only)."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
        try:
            queued = active = 0
            for _system, depth, workers in conn.execute(
                "SELECT system_id, queue_depth, active_workers FROM demands"
            ):
                queued += int(depth or 0)
                active += int(workers or 0)
            leases = conn.execute(
                "SELECT COUNT(*) FROM admission_leases WHERE expires_utc > ?",
                (int(time.time()),),
            ).fetchone()[0]
            by_class = {
                row[0]: row[1]
                for row in conn.execute(
                    "SELECT resource_class, COUNT(*) FROM admission_leases "
                    "WHERE expires_utc > ? GROUP BY resource_class",
                    (int(time.time()),),
                )
            }
            return {
                "queued_demand": queued,
                "scheduler_active_workers": active,
                "active_leases": int(leases or 0),
                "active_leases_by_class": {str(k): int(v) for k, v in by_class.items()},
            }
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return None


def _load_studio(path: str) -> dict[str, Any] | None:
    """REALM Studio executions + persisted provider cooldowns (read-only)."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
        try:
            started = conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE started IS NOT NULL AND finished IS NULL"
            ).fetchone()[0]
            cooldowns: dict[str, int] = {}
            try:
                for key, value in conn.execute(
                    "SELECT k, v FROM kv WHERE k LIKE 'provider_wait:%'"
                ):
                    try:
                        cooldowns[str(key)] = int(value)
                    except (TypeError, ValueError):
                        continue
            except sqlite3.Error:
                pass
            return {
                "studio_active": int(started or 0),
                "provider_waits": cooldowns,
            }
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return None


def _load_janitor(paths: ResourcePaths, runner: Runner) -> dict[str, Any] | None:
    rc, out = runner([paths.janitor, "space", "status", "--json"], 20.0)
    if rc != 0 or not out.strip():
        return None
    try:
        data = json.loads(out)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _load_proc() -> dict[str, Any]:
    """One bounded /proc scan: process count, stuck finds, stacking signals."""
    out: dict[str, Any] = {
        "active_processes": UNKNOWN,
        "stuck_finds": UNKNOWN,
        "janitor_processes": UNKNOWN,
        "disk_watch_processes": UNKNOWN,
        "boot_start_ticks": None,
        "clock_ticks": 100,
    }
    proc = Path("/proc")
    if not proc.is_dir():
        return out
    try:
        ticks = os.sysconf("SC_CLK_TCK")
        out["clock_ticks"] = int(ticks) or 100
    except (ValueError, OSError):
        pass
    count = janitors = watches = stuck = 0
    boot = _boot_time()
    try:
        for entry in os.listdir(proc):
            if not entry.isdigit():
                continue
            count += 1
            p = proc / entry
            try:
                comm = (p / "comm").read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if comm == "noaerth-storage-janitor":
                janitors += 1
            elif comm == "noaerth-disk-watch":
                watches += 1
            elif comm == "find":
                # age via starttime in /proc/pid/stat (field 22)
                try:
                    stat = (p / "stat").read_text(encoding="utf-8")
                    start_ticks = int(stat.rsplit(")", 1)[1].split()[19])
                    if boot:
                        started_s = boot + start_ticks / out["clock_ticks"]
                        if time.time() - started_s >= STUCK_FIND_ETIME_S:
                            stuck += 1
                except (OSError, IndexError, ValueError):
                    pass
    except OSError:
        return out
    out["active_processes"] = count
    out["stuck_finds"] = stuck
    out["janitor_processes"] = janitors
    out["disk_watch_processes"] = watches
    return out


def _boot_time() -> float | None:
    try:
        for line in Path("/proc/stat").read_text(encoding="utf-8").splitlines():
            if line.startswith("btime"):
                return float(line.split()[1])
    except (OSError, IndexError, ValueError):
        return None
    return None


def _psi(kind: str) -> float | None:
    try:
        first = Path(f"/proc/pressure/{kind}").read_text(encoding="utf-8").splitlines()[0]
        for token in first.split()[1:]:
            if token.startswith("avg10="):
                return float(token.split("=", 1)[1])
    except (OSError, IndexError, ValueError):
        return None
    return None


def _meminfo() -> dict[str, int]:
    fields: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts:
                fields[key] = int(parts[0])
    except (OSError, ValueError):
        pass
    return fields


def _cpu_usage_from_proc(sample_s: float = 0.1) -> float | None:
    """Two-sample /proc/stat delta (bounded, no polling loop)."""

    def read() -> tuple[int, int] | None:
        try:
            parts = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()
            if parts[0] != "cpu":
                return None
            nums = [int(x) for x in parts[1:]]
            idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
            return sum(nums), idle
        except (OSError, IndexError, ValueError):
            return None

    a = read()
    if a is None:
        return None
    time.sleep(sample_s)
    b = read()
    if b is None:
        return None
    dtot, didle = b[0] - a[0], b[1] - a[1]
    if dtot <= 0:
        return None
    return round(100.0 * (dtot - didle) / dtot, 2)


def collect_sources(paths: ResourcePaths, runner: Runner = default_runner) -> dict[str, Any]:
    """Gather every raw source. Pure IO; every piece may be None."""
    return {
        "aos": _load_aos_resource(paths.aos_resource),
        "scheduler": _load_scheduler(paths.aos_db),
        "studio": _load_studio(paths.studio_db),
        "janitor": _load_janitor(paths, runner),
        "proc": _load_proc(),
        "growth_history": load_growth_history(_growth_path(paths.snapshot_out)),
    }


# ---------------------------------------------------------------------------
# composition: raw sources -> contract snapshot (deterministic given sources)
# ---------------------------------------------------------------------------

def recommend_capacity(
    band: str,
    *,
    aos_allow: bool | None,
    storage_admission: str,
    cpu_heavy_remaining: int | None = None,
) -> dict[str, Any]:
    """Advice only: never raises an existing cap. 0..2 additional heavyweight
    coding workers, gated by every measured authority (fail closed)."""
    constraints: list[str] = []
    if band == UNKNOWN:
        constraints.append("resource_band_unknown")
    if band == "CRITICAL":
        constraints.append("band_critical")
    if band in ("PRESSED", "RECOVERING"):
        constraints.append(f"band_{band.lower()}")
    if aos_allow is False:
        constraints.append("autonomousos_allow_new_work_false")
    if storage_admission == "DENY":
        constraints.append("storage_denied")
    if storage_admission == UNKNOWN:
        constraints.append("storage_unknown")

    if band == "HEALTHY" and aos_allow is not False and storage_admission == "ALLOW":
        additional = MAX_ADDITIONAL_HEAVY_CODING
    elif band in ("PRESSED", "RECOVERING") and aos_allow is not False and storage_admission == "ALLOW":
        additional = 1  # slowed admissions under pressure (hysteresis-held band)
    else:
        additional = 0
    if cpu_heavy_remaining is not None and additional > max(0, cpu_heavy_remaining):
        additional = max(0, cpu_heavy_remaining)
        constraints.append("cpu_heavy_budget_remaining")
    return {
        "additional_heavy_coding": additional,
        "max_absolute": MAX_ADDITIONAL_HEAVY_CODING,
        "constraints": constraints,
        "basis": (
            f"band={band} aos_allow={aos_allow} storage={storage_admission}"
            + (f" cpu_heavy_remaining={cpu_heavy_remaining}"
               if cpu_heavy_remaining is not None else "")
        ),
    }


def compose_snapshot(
    sources: dict[str, Any],
    *,
    now: float | None = None,
    band: str = UNKNOWN,
) -> dict[str, Any]:
    """Map raw sources onto the contract. Deterministic given sources+now."""
    now = time.time() if now is None else now
    aos = sources.get("aos") or {}
    sched = sources.get("scheduler")
    studio = sources.get("studio")
    jan = sources.get("janitor") or {}
    proc = sources.get("proc") or {}

    meta_unknown: list[str] = []
    field_sources: dict[str, str] = {}

    def pick(field: str, value: Any, source: str) -> Any:
        if value is None:
            meta_unknown.append(field)
            field_sources[field] = UNKNOWN
            return UNKNOWN
        field_sources[field] = source
        return value

    host = aos.get("host") or {}
    a_cpu = host.get("cpu") or {}
    a_mem = host.get("memory") or {}
    a_disk = host.get("disk") or {}
    pressure = aos.get("pressure") or {}

    # cpu_usage: governor first, bounded /proc delta fallback
    cpu = a_cpu.get("usage")
    cpu_source = "autonomousos.governor"
    if cpu is None:
        cpu = _cpu_usage_from_proc()
        cpu_source = "proc.stat"
    cpu_usage = pick("cpu_usage", cpu, cpu_source)

    # load_averages
    load = None
    load_source = "autonomousos.governor"
    if all(k in host for k in ("load1", "load5", "load15")):
        load = [host["load1"], host["load5"], host["load15"]]
    else:
        try:
            load = list(os.getloadavg())
        except (OSError, AttributeError):
            load = None
        load_source = "os.getloadavg"
    load_averages = pick("load_averages", load, load_source)

    # available_memory (bytes)
    avail = None
    avail_source = "autonomousos.governor"
    if isinstance(a_mem.get("available_gb"), (int, float)):
        avail = int(a_mem["available_gb"] * 1024**3)
    else:
        mi = _meminfo()
        if mi.get("MemAvailable"):
            avail = int(mi["MemAvailable"] * 1024)
            avail_source = "proc.meminfo"
    available_memory = pick("available_memory", avail, avail_source)

    # memory_pressure (PSI some avg10, %)
    mem_p = pressure.get("memory", {}).get("some_avg10")
    mem_p_source = "autonomousos.governor"
    if mem_p is None:
        mem_p = _psi("memory")
        mem_p_source = "proc.pressure"
    memory_pressure = pick("memory_pressure", mem_p, mem_p_source)

    # swap_pressure (%)
    mi = _meminfo()
    swap = None
    if mi.get("SwapTotal"):
        swap = round((mi["SwapTotal"] - mi.get("SwapFree", 0)) * 100.0 / mi["SwapTotal"], 2)
    swap_pressure = pick("swap_pressure", swap, "proc.meminfo")

    # disk_free (bytes): janitor -> governor -> statvfs
    disk_free = None
    disk_source = "janitoros.space_status"
    if isinstance((jan.get("disk") or {}).get("avail_bytes"), int):
        disk_free = jan["disk"]["avail_bytes"]
    elif isinstance(a_disk.get("free"), int):
        disk_free = a_disk["free"]
        disk_source = "autonomousos.governor"
    else:
        try:
            disk_free = shutil.disk_usage("/").free
            disk_source = "shutil.disk_usage"
        except OSError:
            disk_free = None
    disk_free = pick("disk_free", disk_free, disk_source)

    # disk_growth (bytes/day free-space change): measured short rate first,
    # janitor forecast only as fallback; UNKNOWN until real samples exist.
    forecast = jan.get("forecast") or {}
    measured = measure_growth(
        sources.get("growth_history") or {},
        now=now,
        current_free=disk_free if isinstance(disk_free, int) else None,
        warn_floor_bytes=(jan.get("disk") or {}).get("warn_floor_bytes"),
    )
    growth = measured["short_bytes_per_day"]
    growth_source = "agentos.growth_history"
    if growth is None and forecast.get("trend") not in (None, "insufficient data") and isinstance(
        forecast.get("avail_change_bytes_per_day"), int
    ):
        growth = forecast["avail_change_bytes_per_day"]
        growth_source = "janitoros.space_status"
    disk_growth = pick("disk_growth", growth, growth_source)

    # disk_io_pressure (PSI some avg10, %)
    io_p = pressure.get("io", {}).get("some_avg10")
    io_source = "autonomousos.governor"
    if io_p is None:
        io_p = _psi("io")
        io_source = "proc.pressure"
    disk_io_pressure = pick("disk_io_pressure", io_p, io_source)

    # active_processes / stacking signals
    active_processes = pick("active_processes", proc.get("active_processes"), "proc.scan")

    # active_workers + queued_demand: authoritative scheduler registry
    if sched is None:
        active_workers = queued_demand = UNKNOWN
        meta_unknown.extend(["active_workers", "queued_demand"])
        field_sources["active_workers"] = field_sources["queued_demand"] = UNKNOWN
        scheduler_active = leases = leases_by = None
    else:
        active_workers = sched["scheduler_active_workers"]
        queued_demand = sched["queued_demand"]
        field_sources["active_workers"] = "autonomousos.demands"
        field_sources["queued_demand"] = "autonomousos.demands"
        scheduler_active = sched["scheduler_active_workers"]
        leases = sched["active_leases"]
        leases_by = sched["active_leases_by_class"]

    # storage_admission
    adm_default = jan.get("admission_default") or {}
    if isinstance(adm_default.get("admit"), bool):
        storage_admission = "ALLOW" if adm_default["admit"] else "DENY"
        field_sources["storage_admission"] = "janitoros.space_status"
    else:
        storage_admission = UNKNOWN
        meta_unknown.append("storage_admission")
        field_sources["storage_admission"] = UNKNOWN

    field_sources["resource_band"] = "agentos.host_governor"
    if band == UNKNOWN:
        meta_unknown.append("resource_band")

    # infrastructure + verification signals (meta, bounded)
    janitors = proc.get("janitor_processes", UNKNOWN)
    watches = proc.get("disk_watch_processes", UNKNOWN)
    stuck = proc.get("stuck_finds", UNKNOWN)
    infra_unknown = [n for n, v in
                     (("janitor_processes", janitors), ("disk_watch_processes", watches),
                      ("stuck_finds", stuck)) if v == UNKNOWN]
    stacking = (
        isinstance(janitors, int) and isinstance(watches, int)
        and (janitors > STACKING_JANITOR_LIMIT or watches > STACKING_WATCH_LIMIT)
    )
    stuck_scans = isinstance(stuck, int) and stuck > 0
    verification = {
        "active_test_leases": (leases_by or {}).get("test", 0) if leases_by is not None else UNKNOWN,
        "test_cap": TEST_CAP,
        "overloaded": (
            isinstance(leases_by, dict) and int(leases_by.get("test", 0)) >= TEST_CAP
        ) if leases_by is not None else UNKNOWN,
    }

    cpu_budget = aos.get("cpu_budget") or {}
    remaining = cpu_budget.get("cpu_heavy_remaining")
    capacity = recommend_capacity(
        band,
        aos_allow=aos.get("allow_new_work") if aos else None,
        storage_admission=storage_admission,
        cpu_heavy_remaining=remaining if isinstance(remaining, int) else None,
    )
    # A blocked infrastructure surface also zeroes the recommendation
    # (fail closed — admission and capacity must never disagree).
    if stacking or stuck_scans:
        if capacity["additional_heavy_coding"]:
            capacity["additional_heavy_coding"] = 0
        if stacking:
            capacity["constraints"].append("process_stacking_detected")
        if stuck_scans:
            capacity["constraints"].append("stuck_filesystem_scan")
        capacity["basis"] = (
            f"band={band} aos_allow={aos.get('allow_new_work') if aos else None} "
            f"storage={storage_admission} infrastructure=blocked"
        )

    studio_active = (studio or {}).get("studio_active") if studio else None
    cooldowns = (studio or {}).get("provider_waits") if studio else None
    now_i = int(now)
    active_cooldowns = {}
    if cooldowns is None:
        provider_cooldown = UNKNOWN
    else:
        active_cooldowns = {
            k: v for k, v in cooldowns.items() if isinstance(v, int) and v > now_i
        }
        provider_cooldown = active_cooldowns or "NONE"

    snap: dict[str, Any] = {
        "schema": SCHEMA,
        "cpu_usage": cpu_usage,
        "load_averages": load_averages,
        "available_memory": available_memory,
        "memory_pressure": memory_pressure,
        "swap_pressure": swap_pressure,
        "disk_free": disk_free,
        "disk_growth": disk_growth,
        "disk_io_pressure": disk_io_pressure,
        "active_processes": active_processes,
        "active_workers": active_workers,
        "queued_demand": queued_demand,
        "storage_admission": storage_admission,
        "resource_band": band,
        "recommended_worker_capacity": capacity,
        "updated_utc": _iso(now),
        "_meta": {
            "schema": SCHEMA,
            "generated_epoch": now,
            "unknown_fields": meta_unknown,
            "field_sources": field_sources,
            "autonomousos": {
                "mode": aos.get("mode", UNKNOWN) if aos else UNKNOWN,
                "allow_new_work": aos.get("allow_new_work") if aos else None,
                "cpu_heavy_cap": cpu_budget.get("cpu_heavy_cap"),
                "cpu_heavy_remaining": remaining,
            } if aos else {"mode": UNKNOWN, "allow_new_work": None,
                           "cpu_heavy_cap": None, "cpu_heavy_remaining": None},
            "scheduler": {
                "active_leases": leases,
                "active_leases_by_class": leases_by,
                "studio_active_executions": studio_active,
            } if sched is not None else {"active_leases": UNKNOWN,
                                         "active_leases_by_class": UNKNOWN,
                                         "studio_active_executions": UNKNOWN},
            "infrastructure": {
                "janitor_processes": janitors,
                "disk_watch_processes": watches,
                "stuck_finds": stuck,
                "stacking_detected": stacking,
                "unknown": infra_unknown,
            },
            "verification": verification,
            "provider_cooldown": {
                "state": provider_cooldown,
                "active": active_cooldowns if cooldowns is not None else UNKNOWN,
            },
            "janitor": {
                "health": jan.get("health", UNKNOWN) if jan else UNKNOWN,
                "disk_band": (jan.get("disk") or {}).get("band", UNKNOWN) if jan else UNKNOWN,
                "storage_band": adm_default.get("band"),
                "storage_reason": adm_default.get("reason"),
            },
            "growth": measured,
        },
    }
    return snap


def _iso(epoch: float) -> str:
    import datetime as _dt

    return (
        _dt.datetime.fromtimestamp(epoch, _dt.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


# ---------------------------------------------------------------------------
# bounded disk-growth history (Phase 5A): real samples only, never invented
# ---------------------------------------------------------------------------

def _growth_path(snapshot_out: str | Path) -> Path:
    out = Path(snapshot_out)
    return out.parent / "resource-growth.json"


def load_growth_history(path: str | Path) -> dict[str, Any]:
    """Read the bounded growth store; {} when absent or corrupt.

    Shape: {"raw": [[epoch, free_bytes], ...], "hourly": [[epoch, free_bytes], ...]}
    with newest entries last. Both lists are pruned to their caps on write.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, Any] = {}
    for key in ("raw", "hourly"):
        rows = data.get(key)
        if isinstance(rows, list):
            clean = []
            for row in rows[-max(GROWTH_RAW_CAP, GROWTH_HOURLY_CAP):]:
                if (isinstance(row, (list, tuple)) and len(row) == 2
                        and isinstance(row[0], (int, float))
                        and isinstance(row[1], (int, float))):
                    clean.append([float(row[0]), int(row[1])])
            clean.sort(key=lambda r: r[0])
            out[key] = clean
    return out


def record_growth_sample(
    snapshot_out: str | Path, now: float, free_bytes: int
) -> dict[str, Any]:
    """Append one (epoch, free_bytes) sample; roll raw into hourly buckets.

    Bounded: raw pruned to GROWTH_RAW_CAP, hourly to GROWTH_HOURLY_CAP.
    Atomic publish (tmp + replace, 0644). Returns the new history. Recording
    happens only on an actual publish with a real integer free_bytes — an
    UNKNOWN disk_free records nothing.
    """
    if not isinstance(free_bytes, int) or free_bytes < 0:
        return load_growth_history(_growth_path(snapshot_out))
    history = load_growth_history(_growth_path(snapshot_out))
    raw = history.get("raw", [])
    raw.append([float(now), int(free_bytes)])
    raw = [row for row in raw if row[0] <= now + 1][-GROWTH_RAW_CAP:]
    hourly = history.get("hourly", [])
    if hourly and now - hourly[-1][0] < 3600.0:
        # refresh the open hour bucket with the newest sample (monotone window)
        hourly[-1] = [hourly[-1][0], int(free_bytes)]
    else:
        hourly.append([float(now), int(free_bytes)])
    hourly = [row for row in hourly if row[0] <= now + 1][-GROWTH_HOURLY_CAP:]
    history = {"raw": raw, "hourly": hourly}
    target = _growth_path(snapshot_out)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(history, separators=(",", ":")) + "\n"
    if len(body) > 64 * 1024:
        raise ValueError("growth history exceeds 64KB bound")
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, target)
    return history


def measure_growth(
    history: dict[str, Any],
    *,
    now: float | None = None,
    current_free: int | None = None,
    warn_floor_bytes: int | None = None,
) -> dict[str, Any]:
    """Pure: short/long free-space rates (bytes/day, negative = shrinking).

    A rate is None (UNKNOWN) until GROWTH_MIN_SAMPLES span
    GROWTH_MIN_SPAN_S / GROWTH_LONG_MIN_SPAN_S of real samples — UNKNOWN is
    never reported as 0 growth. eta_to_warn_floor_s is computed only from a
    supported short rate that is actually negative (free space shrinking)
    plus a real current_free and warn floor; otherwise it is None.
    """
    now = time.time() if now is None else now
    raw = [r for r in (history or {}).get("raw", []) if r[0] <= now + 1]
    hourly = [r for r in (history or {}).get("hourly", []) if r[0] <= now + 1]
    floor = warn_floor_bytes if isinstance(warn_floor_bytes, int) else None

    def rate(rows: list[list[float]], min_span: float) -> tuple[float | None, float]:
        if len(rows) < GROWTH_MIN_SAMPLES:
            return None, 0.0
        window = [r for r in rows if now - r[0] <= max(min_span, GROWTH_SHORT_WINDOW_S)]
        if len(window) < GROWTH_MIN_SAMPLES:
            return None, 0.0
        span = window[-1][0] - window[0][0]
        if span < GROWTH_MIN_SPAN_S:
            return None, 0.0
        return (window[-1][1] - window[0][1]) / span * 86400.0, span

    short_rate, short_span = rate(raw, GROWTH_SHORT_WINDOW_S)
    long_rate, long_span = rate(hourly, GROWTH_LONG_MIN_SPAN_S)

    eta = None
    if (
        short_rate is not None
        and short_rate < 0
        and isinstance(current_free, int)
        and floor is not None
    ):
        if current_free <= floor:
            eta = 0.0
        else:
            eta = (current_free - floor) / (-short_rate / 86400.0)

    return {
        "short_bytes_per_day": short_rate,
        "long_bytes_per_day": long_rate,
        "short_window_s": short_span,
        "long_window_s": long_span,
        "raw_samples": len(raw),
        "hourly_samples": len(hourly),
        "eta_to_warn_floor_s": eta,
        "warn_floor_bytes": floor,
        "basis": "measured" if short_rate is not None else UNKNOWN,
    }


def build_snapshot(
    paths: ResourcePaths | None = None,
    *,
    runner: Runner = default_runner,
    now: float | None = None,
    band: str | None = None,
    sources: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Full pipeline: collect sources (unless injected) + host band + compose."""
    paths = paths or ResourcePaths.from_env()
    if sources is None:
        sources = collect_sources(paths, runner)
    if band is None:
        band = _host_band(paths)
    return compose_snapshot(sources, now=now, band=band)


def _host_band(paths: ResourcePaths) -> str:
    try:
        from agentos.host_governor import HostGovernor, collect_metrics

        gov = HostGovernor(paths.governor_state_dir)
        metrics = collect_metrics(paths.governor_state_dir)
        state = gov.evaluate(metrics)
        return str(state.band)
    except Exception:
        return UNKNOWN


def write_snapshot(snapshot: dict[str, Any], path: str | Path | None = None) -> Path:
    """Atomic bounded publish: tmp file + os.replace. ~16KB document.

    Also records one bounded (epoch, free_bytes) sample into the growth
    history (only when disk_free is a real integer — UNKNOWN records nothing).
    """
    paths = ResourcePaths.from_env()
    out = Path(path or paths.snapshot_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(snapshot, indent=2, sort_keys=False) + "\n"
    if len(body) > 64 * 1024:
        raise ValueError("resource snapshot exceeds 64KB bound")
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    # world-readable: cross-user consumers (control collector, hive) read it
    os.chmod(tmp, 0o644)
    os.replace(tmp, out)
    free = snapshot.get("disk_free")
    meta = snapshot.get("_meta") or {}
    if isinstance(free, int) and free >= 0:
        try:
            record_growth_sample(out, float(meta.get("generated_epoch") or time.time()), free)
        except (OSError, ValueError):
            pass  # growth history is best-effort; the snapshot itself stands
    return out


def read_snapshot(path: str | Path | None = None, *, max_age_s: float | None = None,
                  now: float | None = None) -> dict[str, Any] | None:
    """Read a published snapshot; None when absent, corrupt, or stale."""
    paths = ResourcePaths.from_env()
    target = Path(path or paths.snapshot_out)
    try:
        stat = target.stat()
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if max_age_s is not None:
        now = time.time() if now is None else now
        updated = data.get("_meta", {}).get("generated_epoch") or 0
        if not updated:
            try:
                updated = stat.st_mtime
            except OSError:
                return None
        if now - float(updated) > max_age_s:
            return None
    return data
