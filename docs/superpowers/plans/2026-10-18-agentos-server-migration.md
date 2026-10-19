# AgentOS Server Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy AgentOS 0.5.0 as a Linux service on Contabo integrated with AutonomousOS, Hive, AI Mesh, and JanitorOS.

**Architecture:** AgentOS registers as AutonomousOS system (priority 60), submits tasks via Hive manifest to REALM Studio, routes providers via AI Mesh, requests storage admission via JanitorOS, runs as systemd service.

**Tech Stack:** Python 3.12+, stdlib only, SQLite WAL, systemd, shell scripts

## Global Constraints

- Python 3.12+ compatibility (stdlib only, no new dependencies)
- SQLite WAL mode for persistence
- No Redis/PostgreSQL/Kubernetes
- No competing hive scheduler
- No replacement for provider routing
- No duplication of JanitorOS
- No automatic GitHub pushes
- No paid model calls without authorization
- No public network exposure
- Preserve existing AgentOS functionality (710 tests)
- Preserve existing AutonomousOS functionality
- Server: 100.127.182.126 (Ubuntu 24.04.5, Python 3.12.3)
- SSH: `ssh -i ~/.ssh/noaerth_cloud root@100.127.182.126`
- Server destination: `/srv/noaerth/agentos`

---

### Task 1: Server Edition — Linux Path Configuration

**Files:**
- Modify: `src/agentos/services.py` (default_home function)
- Create: `tests/test_linux_paths.py`

**Interfaces:**
- Consumes: Nothing (first task)
- Produces: `AGENTOS_HOME` environment variable support, configurable paths

- [ ] **Step 1: Write the failing test**

```python
# tests/test_linux_paths.py
import os
import tempfile
import unittest
from pathlib import Path

class TestLinuxPaths(unittest.TestCase):
    def test_agentos_home_env_var(self):
        """AGENTOS_HOME env var overrides default home."""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["AGENTOS_HOME"] = tmpdir
            from agentos.services import default_home
            result = default_home()
            self.assertEqual(str(result), tmpdir)

    def test_default_home_is_dot_agentos(self):
        """Default home is ~/.agentos when AGENTOS_HOME not set."""
        old = os.environ.pop("AGENTOS_HOME", None)
        try:
            from agentos.services import default_home
            result = default_home()
            self.assertEqual(str(result), str(Path.home() / ".agentos"))
        finally:
            if old:
                os.environ["AGENTOS_HOME"] = old

    def test_custom_home_for_server(self):
        """Server can use /srv/noaerth/agentos as home."""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["AGENTOS_HOME"] = tmpdir
            from agentos.services import AgentOS
            app = AgentOS()
            self.assertEqual(str(app.home), tmpdir)
            self.assertTrue(app.db_path.name == "agentos.db")

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests/test_linux_paths.py -v`
Expected: FAIL (AgentOS constructor may not accept env var properly yet)

- [ ] **Step 3: Verify implementation already works**

The `default_home()` function in `services.py` already checks `AGENTOS_HOME` env var. Run the test again to confirm it passes.

Run: `python3 -m unittest tests/test_linux_paths.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_linux_paths.py
git commit -m "test: add Linux path configuration tests"
```

---

### Task 2: Server Edition — SQLite WAL Mode

**Files:**
- Modify: `src/agentos/store.py` (connection setup)
- Create: `tests/test_sqlite_wal.py`

**Interfaces:**
- Consumes: Nothing
- Produces: SQLite WAL mode enabled by default

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sqlite_wal.py
import os
import sqlite3
import tempfile
import unittest

class TestSQLiteWAL(unittest.TestCase):
    def test_wal_mode_enabled(self):
        """Store enables WAL mode for concurrent access."""
        with tempfile.TemporaryDirectory() as tmpdir:
            from agentos.store import Store
            db_path = os.path.join(tmpdir, "test.db")
            store = Store(db_path)
            conn = sqlite3.connect(db_path)
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            conn.close()
            self.assertEqual(mode.lower(), "wal")

    def test_busy_timeout_set(self):
        """Store sets busy_timeout for concurrent access."""
        with tempfile.TemporaryDirectory() as tmpdir:
            from agentos.store import Store
            db_path = os.path.join(tmpdir, "test.db")
            store = Store(db_path)
            conn = sqlite3.connect(db_path)
            timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
            conn.close()
            self.assertGreater(timeout, 0)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python3 -m unittest tests/test_sqlite_wal.py -v`
Expected: PASS (WAL already implemented in store.py)

- [ ] **Step 3: Commit**

```bash
git add tests/test_sqlite_wal.py
git commit -m "test: add SQLite WAL mode tests"
```

---

### Task 3: Hive Adapter — Core Module

**Files:**
- Create: `src/agentos/adapters/hive.py`
- Create: `tests/test_hive_adapter.py`

**Interfaces:**
- Consumes: Nothing
- Produces: `HiveAdapter` class with `discover_workers()`, `submit_task()`, `get_task_status()`, `get_task_logs()`, `cancel_task()`, `collect_artifacts()`, `collect_verification()`, `inspect_resource_usage()`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hive_adapter.py
import json
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

class TestHiveAdapter(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.hive_dir = os.path.join(self.temp_dir, "autonomousos", "mesh", "hive")
        os.makedirs(self.hive_dir, exist_ok=True)
        self.state_dir = os.path.join(self.temp_dir, "autonomousos", "state")
        os.makedirs(self.state_dir, exist_ok=True)
        self.resource_json = os.path.join(self.state_dir, "resource.json")
        with open(self.resource_json, "w") as f:
            json.dump({"allow_new_work": True, "mode": "HEALTHY", "disk_pct": 50}, f)

    def test_discover_workers_empty(self):
        """discover_workers returns empty list when no workers."""
        from agentos.adapters.hive import HiveAdapter
        adapter = HiveAdapter(hive_dir=self.hive_dir, state_dir=self.state_dir)
        workers = adapter.discover_workers()
        self.assertIsInstance(workers, list)

    def test_submit_task_requires_admission(self):
        """submit_task checks resource admission before submitting."""
        from agentos.adapters.hive import HiveAdapter
        adapter = HiveAdapter(hive_dir=self.hive_dir, state_dir=self.state_dir)
        with patch.object(adapter, '_check_resource_admission') as mock_admission:
            mock_admission.return_value = (False, "disk full")
            result = adapter.submit_task({"id": "t1", "title": "test", "prompt": "do something"})
            self.assertFalse(result["ok"])
            self.assertIn("disk full", result["error"])

    def test_submit_task_success(self):
        """submit_task succeeds when admission passes."""
        from agentos.adapters.hive import HiveAdapter
        adapter = HiveAdapter(hive_dir=self.hive_dir, state_dir=self.state_dir)
        with patch.object(adapter, '_check_resource_admission') as mock_admission:
            mock_admission.return_value = (True, "ok")
            with patch.object(adapter, '_submit_to_studio') as mock_submit:
                mock_submit.return_value = {"task_id": "task_123"}
                result = adapter.submit_task({"id": "t1", "title": "test", "prompt": "do something"})
                self.assertTrue(result["ok"])
                self.assertEqual(result["task_id"], "task_123")

    def test_get_task_status_not_found(self):
        """get_task_status returns None for unknown task."""
        from agentos.adapters.hive import HiveAdapter
        adapter = HiveAdapter(hive_dir=self.hive_dir, state_dir=self.state_dir)
        status = adapter.get_task_status("nonexistent")
        self.assertIsNone(status)

    def test_inspect_resource_usage(self):
        """inspect_resource_usage reads resource.json."""
        from agentos.adapters.hive import HiveAdapter
        adapter = HiveAdapter(hive_dir=self.hive_dir, state_dir=self.state_dir)
        usage = adapter.inspect_resource_usage()
        self.assertTrue(usage["ok"])
        self.assertEqual(usage["mode"], "HEALTHY")

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests/test_hive_adapter.py -v`
Expected: FAIL (HiveAdapter not defined)

- [ ] **Step 3: Write minimal implementation**

```python
# src/agentos/adapters/hive.py
"""Hive adapter: connect AgentOS to the existing NOAERTH hive scheduler.

Uses the existing Hive infrastructure at /srv/noaerth/autonomousos/mesh/hive/
for task submission, worker discovery, and status tracking. Does NOT create
a second scheduler — delegates to the existing Hive.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any


class HiveAdapter:
    """Adapter to the existing NOAERTH hive scheduler."""

    def __init__(
        self,
        hive_dir: str | None = None,
        state_dir: str | None = None,
        studio_db: str | None = None,
    ) -> None:
        self.hive_dir = Path(hive_dir) if hive_dir else Path("/srv/noaerth/autonomousos/mesh/hive")
        self.state_dir = Path(state_dir) if state_dir else Path("/srv/noaerth/autonomousos/state")
        self.studio_db = studio_db or "/srv/noaerth/realm-studio/state/studio.sqlite3"

    def discover_workers(self) -> list[dict[str, Any]]:
        """Discover active workers from the hive."""
        workers = []
        resource_path = self.state_dir / "resource.json"
        if resource_path.exists():
            try:
                data = json.loads(resource_path.read_text())
                for system in data.get("systems", []):
                    if system.get("active_workers", 0) > 0:
                        workers.append({
                            "system_id": system.get("system_id"),
                            "active_workers": system.get("active_workers"),
                            "priority": system.get("priority"),
                        })
            except (json.JSONDecodeError, OSError):
                pass
        return workers

    def inspect_resource_usage(self) -> dict[str, Any]:
        """Read current resource state from AutonomousOS."""
        resource_path = self.state_dir / "resource.json"
        if not resource_path.exists():
            return {"ok": False, "error": "resource.json not found"}
        try:
            data = json.loads(resource_path.read_text())
            return {
                "ok": True,
                "mode": data.get("mode", "UNKNOWN"),
                "disk_pct": data.get("disk_pct", 0),
                "allow_new_work": data.get("allow_new_work", False),
                "systems": data.get("systems", []),
            }
        except (json.JSONDecodeError, OSError) as exc:
            return {"ok": False, "error": str(exc)}

    def _check_resource_admission(self) -> tuple[bool, str]:
        """Check if resource state allows new work."""
        usage = self.inspect_resource_usage()
        if not usage["ok"]:
            return False, usage.get("error", "resource check failed")
        if usage.get("mode") in ("SEVERE", "CRITICAL", "ERROR"):
            return False, f"resource mode is {usage['mode']}"
        if not usage.get("allow_new_work", False):
            return False, "allow_new_work is false"
        if usage.get("disk_pct", 0) >= 90:
            return False, f"disk usage {usage['disk_pct']}% >= 90%"
        return True, "ok"

    def _submit_to_studio(self, task: dict[str, Any]) -> dict[str, Any]:
        """Submit task to REALM Studio's task registry."""
        if not os.path.exists(self.studio_db):
            return {"error": f"studio db not found: {self.studio_db}"}
        try:
            conn = sqlite3.connect(self.studio_db, timeout=10)
            cursor = conn.cursor()
            task_id = f"task_{os.urandom(6).hex()}"
            cursor.execute(
                """INSERT INTO tasks (id, title, prompt, level, agent, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
                (task_id, task.get("title", ""), task.get("prompt", ""),
                 task.get("level", "MEDIUM"), task.get("agent", "opencode"), "PENDING"),
            )
            conn.commit()
            conn.close()
            return {"task_id": task_id}
        except sqlite3.Error as exc:
            return {"error": str(exc)}

    def submit_task(self, task: dict[str, Any]) -> dict[str, Any]:
        """Submit a task to the hive after resource admission."""
        allowed, reason = self._check_resource_admission()
        if not allowed:
            return {"ok": False, "error": reason}
        result = self._submit_to_studio(task)
        if "error" in result:
            return {"ok": False, "error": result["error"]}
        return {"ok": True, "task_id": result["task_id"]}

    def get_task_status(self, task_id: str) -> dict[str, Any] | None:
        """Get status of a submitted task."""
        if not os.path.exists(self.studio_db):
            return None
        try:
            conn = sqlite3.connect(self.studio_db, timeout=10)
            cursor = conn.cursor()
            cursor.execute("SELECT id, status, agent FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()
            conn.close()
            if row:
                return {"task_id": row[0], "status": row[1], "agent": row[2]}
            return None
        except sqlite3.Error:
            return None

    def get_task_logs(self, task_id: str) -> str:
        """Get logs for a task."""
        return f"logs for {task_id} not yet implemented"

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        """Cancel a pending/running task."""
        if not os.path.exists(self.studio_db):
            return {"ok": False, "error": "studio db not found"}
        try:
            conn = sqlite3.connect(self.studio_db, timeout=10)
            cursor = conn.cursor()
            cursor.execute("UPDATE tasks SET status = 'CANCELLED' WHERE id = ?", (task_id,))
            conn.commit()
            conn.close()
            return {"ok": True}
        except sqlite3.Error as exc:
            return {"ok": False, "error": str(exc)}

    def collect_artifacts(self, task_id: str) -> list[str]:
        """Collect artifacts produced by a task."""
        return []

    def collect_verification(self, task_id: str) -> dict[str, Any]:
        """Collect verification evidence for a task."""
        return {"task_id": task_id, "verified": False, "evidence": []}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests/test_hive_adapter.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentos/adapters/hive.py tests/test_hive_adapter.py
git commit -m "feat: add Hive adapter for existing scheduler integration"
```

---

### Task 4: JanitorOS Adapter — Storage Admission

**Files:**
- Create: `src/agentos/adapters/janitor.py`
- Create: `tests/test_janitor_adapter.py`

**Interfaces:**
- Consumes: Nothing
- Produces: `JanitorOSAdapter` class with `request_admission()`, `check_protected_path()`, `get_storage_forecast()`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_janitor_adapter.py
import json
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

class TestJanitorOSAdapter(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def test_request_admission_success(self):
        """request_admission returns ok when janitor approves."""
        from agentos.adapters.janitor import JanitorOSAdapter
        adapter = JanitorOSAdapter()
        with patch.object(adapter, '_run_janitor') as mock_run:
            mock_run.return_value = {"ok": True, "admitted": True}
            result = adapter.request_admission(1024 * 1024 * 100)  # 100MB
            self.assertTrue(result["ok"])

    def test_request_admission_denied(self):
        """request_admission returns error when janitor denies."""
        from agentos.adapters.janitor import JanitorOSAdapter
        adapter = JanitorOSAdapter()
        with patch.object(adapter, '_run_janitor') as mock_run:
            mock_run.return_value = {"ok": True, "admitted": False, "reason": "disk full"}
            result = adapter.request_admission(1024 * 1024 * 100)
            self.assertFalse(result["ok"])
            self.assertIn("disk full", result["error"])

    def test_check_protected_path_git(self):
        """check_protected_path detects .git directories."""
        from agentos.adapters.janitor import JanitorOSAdapter
        adapter = JanitorOSAdapter()
        self.assertTrue(adapter.check_protected_path("/repo/.git/config"))
        self.assertTrue(adapter.check_protected_path("/repo/.git/HEAD"))

    def test_check_protected_path_env(self):
        """check_protected_path detects .env files."""
        from agentos.adapters.janitor import JanitorOSAdapter
        adapter = JanitorOSAdapter()
        self.assertTrue(adapter.check_protected_path("/repo/.env"))
        self.assertTrue(adapter.check_protected_path("/repo/config/.env.local"))

    def test_check_protected_path_safe(self):
        """check_protected_path allows safe paths."""
        from agentos.adapters.janitor import JanitorOSAdapter
        adapter = JanitorOSAdapter()
        self.assertFalse(adapter.check_protected_path("/tmp/worktree/src/main.py"))
        self.assertFalse(adapter.check_protected_path("/srv/noaerth/agentos/state"))

    def test_get_storage_forecast(self):
        """get_storage_forecast returns disk usage info."""
        from agentos.adapters.janitor import JanitorOSAdapter
        adapter = JanitorOSAdapter()
        forecast = adapter.get_storage_forecast()
        self.assertIn("total_bytes", forecast)
        self.assertIn("used_bytes", forecast)
        self.assertIn("available_bytes", forecast)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests/test_janitor_adapter.py -v`
Expected: FAIL (JanitorOSAdapter not defined)

- [ ] **Step 3: Write minimal implementation**

```python
# src/agentos/adapters/janitor.py
"""JanitorOS adapter: storage admission and protected path enforcement.

Connects AgentOS to the existing JanitorOS storage steward. AgentOS must
request storage admission before creating substantial worktrees, build
artifacts, or temporary files. Never writes to protected paths.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

PROTECTED_PATTERNS = [
    ".git",
    ".env",
    ".env.local",
    ".env.production",
    "id_rsa",
    "id_ed25519",
    ".ssh",
    ".gnupg",
    ".aws",
    ".config/gh",
    ".key",
    ".pem",
    ".p12",
    ".netrc",
    ".npmrc",
]


class JanitorOSAdapter:
    """Adapter to JanitorOS storage admission control."""

    def __init__(self, janitor_binary: str | None = None) -> None:
        self.janitor_binary = janitor_binary or self._find_janitor()

    def _find_janitor(self) -> str | None:
        """Find the janitor binary."""
        for path in ["/home/portfolio/.local/bin/janitor", "/usr/local/bin/janitor"]:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
        return shutil.which("janitor")

    def _run_janitor(self, args: list[str]) -> dict[str, Any]:
        """Run janitor CLI and parse JSON output."""
        if not self.janitor_binary:
            return {"ok": False, "error": "janitor binary not found"}
        try:
            result = subprocess.run(
                [self.janitor_binary] + args,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                try:
                    return json.loads(result.stdout)
                except json.JSONDecodeError:
                    return {"ok": True, "raw": result.stdout}
            return {"ok": False, "error": result.stderr or f"exit {result.returncode}"}
        except (subprocess.TimeoutExpired, OSError) as exc:
            return {"ok": False, "error": str(exc)}

    def request_admission(self, bytes_needed: int) -> dict[str, Any]:
        """Request storage admission for a given number of bytes."""
        result = self._run_janitor(["space", "admit", "--bytes", str(bytes_needed), "--json"])
        if not result.get("ok"):
            return {"ok": False, "error": result.get("error", "janitor call failed")}
        if result.get("admitted", False):
            return {"ok": True, "admitted": True}
        return {"ok": False, "error": result.get("reason", "admission denied")}

    def check_protected_path(self, path: str) -> bool:
        """Check if a path is protected by JanitorOS."""
        path_obj = Path(path)
        for part in path_obj.parts:
            for pattern in PROTECTED_PATTERNS:
                if pattern in part:
                    return True
        return False

    def get_storage_forecast(self) -> dict[str, Any]:
        """Get disk usage forecast."""
        usage = shutil.disk_usage("/")
        return {
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "available_bytes": usage.free,
            "used_pct": round(usage.used / usage.total * 100, 1),
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests/test_janitor_adapter.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentos/adapters/janitor.py tests/test_janitor_adapter.py
git commit -m "feat: add JanitorOS adapter for storage admission"
```

---

### Task 5: Provider Integration — AI Mesh Connection

**Files:**
- Create: `src/agentos/adapters/ai_mesh.py`
- Create: `tests/test_ai_mesh_adapter.py`

**Interfaces:**
- Consumes: Nothing
- Produces: `AIMeshAdapter` class with `discover_providers()`, `route_task()`, `get_provider_status()`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ai_mesh_adapter.py
import json
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

class TestAIMeshAdapter(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.mesh_dir = os.path.join(self.temp_dir, "autonomousos", "mesh")
        os.makedirs(self.mesh_dir, exist_ok=True)

    def test_discover_providers_empty(self):
        """discover_providers returns empty list when no providers found."""
        from agentos.adapters.ai_mesh import AIMeshAdapter
        adapter = AIMeshAdapter(mesh_dir=self.mesh_dir)
        providers = adapter.discover_providers()
        self.assertIsInstance(providers, list)

    def test_detect_provider_states(self):
        """Provider states are correctly detected."""
        from agentos.adapters.ai_mesh import AIMeshAdapter, ProviderState
        adapter = AIMeshAdapter(mesh_dir=self.mesh_dir)
        self.assertEqual(adapter._detect_state(True, True, True), ProviderState.READY)
        self.assertEqual(adapter._detect_state(True, True, False), ProviderState.AUTH_REQUIRED)
        self.assertEqual(adapter._detect_state(True, False, False), ProviderState.CONFIGURED)
        self.assertEqual(adapter._detect_state(False, False, False), ProviderState.UNAVAILABLE)

    def test_route_task_free_first(self):
        """route_task prefers free providers."""
        from agentos.adapters.ai_mesh import AIMeshAdapter, ProviderState
        adapter = AIMeshAdapter(mesh_dir=self.mesh_dir)
        with patch.object(adapter, 'discover_providers') as mock_discover:
            mock_discover.return_value = [
                {"id": "opencode", "cost_rank": 1, "state": ProviderState.READY, "free": True},
                {"id": "claude", "cost_rank": 4, "state": ProviderState.READY, "free": False},
            ]
            result = adapter.route_task({"capability": "coding"})
            self.assertEqual(result["provider"], "opencode")

    def test_route_task_no_free(self):
        """route_task falls back to paid when no free providers."""
        from agentos.adapters.ai_mesh import AIMeshAdapter, ProviderState
        adapter = AIMeshAdapter(mesh_dir=self.mesh_dir)
        with patch.object(adapter, 'discover_providers') as mock_discover:
            mock_discover.return_value = [
                {"id": "claude", "cost_rank": 4, "state": ProviderState.READY, "free": False},
            ]
            result = adapter.route_task({"capability": "coding"})
            self.assertEqual(result["provider"], "claude")

    def test_get_provider_status(self):
        """get_provider_status returns status for a provider."""
        from agentos.adapters.ai_mesh import AIMeshAdapter
        adapter = AIMeshAdapter(mesh_dir=self.mesh_dir)
        status = adapter.get_provider_status("opencode")
        self.assertIn("id", status)
        self.assertIn("state", status)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests/test_ai_mesh_adapter.py -v`
Expected: FAIL (AIMeshAdapter not defined)

- [ ] **Step 3: Write minimal implementation**

```python
# src/agentos/adapters/ai_mesh.py
"""AI Mesh adapter: provider discovery and cost-aware routing.

Connects AgentOS to the existing AI Mesh provider intelligence system.
Discovers local CLI providers (opencode, claude, codex, cursor-agent) and
routes tasks based on cost, capability, and reliability.
"""

from __future__ import annotations

import json
import os
import shutil
from enum import StrEnum
from pathlib import Path
from typing import Any


class ProviderState(StrEnum):
    DETECTED = "DETECTED"
    CONFIGURED = "CONFIGURED"
    READY = "READY"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    RATE_LIMITED = "RATE_LIMITED"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class AIMeshAdapter:
    """Adapter to the existing AI Mesh provider routing system."""

    def __init__(self, mesh_dir: str | None = None) -> None:
        self.mesh_dir = Path(mesh_dir) if mesh_dir else Path("/srv/noaerth/autonomousos/mesh")
        self.providers_file = self.mesh_dir / "providers.py"

    def discover_providers(self) -> list[dict[str, Any]]:
        """Discover available AI providers."""
        providers = []
        for name, binary, cost_rank, caps in [
            ("opencode", "opencode", 1, ["coding", "general"]),
            ("cursor-agent", "cursor-agent", 2, ["coding"]),
            ("codex", "codex", 3, ["coding", "reasoning"]),
            ("claude", "claude", 4, ["coding", "reasoning"]),
        ]:
            path = shutil.which(binary)
            if path:
                state = self._detect_state(True, True, True)
                providers.append({
                    "id": name,
                    "binary": binary,
                    "path": path,
                    "cost_rank": cost_rank,
                    "capabilities": caps,
                    "state": state,
                    "free": cost_rank <= 2,
                })
        return providers

    def _detect_state(self, installed: bool, configured: bool, authenticated: bool) -> ProviderState:
        """Detect provider state from binary presence and config."""
        if not installed:
            return ProviderState.UNAVAILABLE
        if not configured:
            return ProviderState.DETECTED
        if not authenticated:
            return ProviderState.AUTH_REQUIRED
        return ProviderState.READY

    def route_task(self, task: dict[str, Any]) -> dict[str, Any]:
        """Route a task to the best available provider."""
        providers = self.discover_providers()
        if not providers:
            return {"ok": False, "error": "no providers available"}

        capability = task.get("capability", "coding")
        capable = [p for p in providers if capability in p.get("capabilities", [])]
        if not capable:
            return {"ok": False, "error": f"no provider for capability: {capability}"}

        free = [p for p in capable if p.get("free", False) and p["state"] == ProviderState.READY]
        if free:
            best = min(free, key=lambda p: p["cost_rank"])
            return {"ok": True, "provider": best["id"], "binary": best["path"]}

        ready = [p for p in capable if p["state"] == ProviderState.READY]
        if ready:
            best = min(ready, key=lambda p: p["cost_rank"])
            return {"ok": True, "provider": best["id"], "binary": best["path"], "paid": True}

        return {"ok": False, "error": "no ready providers"}

    def get_provider_status(self, provider_id: str) -> dict[str, Any]:
        """Get status of a specific provider."""
        providers = self.discover_providers()
        for p in providers:
            if p["id"] == provider_id:
                return p
        return {"id": provider_id, "state": ProviderState.UNKNOWN, "error": "not found"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests/test_ai_mesh_adapter.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentos/adapters/ai_mesh.py tests/test_ai_mesh_adapter.py
git commit -m "feat: add AI Mesh adapter for provider routing"
```

---

### Task 6: AutonomousOS Registration Adapter

**Files:**
- Create: `src/agentos/adapters/autonomous_os.py`
- Create: `tests/test_autonomous_os_adapter.py`

**Interfaces:**
- Consumes: Nothing
- Produces: `AutonomousOSAdapter` class with `register()`, `publish_demand()`, `read_allocation()`, `get_system_status()`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_autonomous_os_adapter.py
import json
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

class TestAutonomousOSAdapter(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.state_dir = os.path.join(self.temp_dir, "agentos", "state")
        os.makedirs(self.state_dir, exist_ok=True)

    def test_register_creates_demand(self):
        """register creates initial demand.json."""
        from agentos.adapters.autonomous_os import AutonomousOSAdapter
        adapter = AutonomousOSAdapter(state_dir=self.state_dir)
        result = adapter.register("agentos", "AgentOS", 60, 1, 4)
        self.assertTrue(result["ok"])
        demand_path = os.path.join(self.state_dir, "demand.json")
        self.assertTrue(os.path.exists(demand_path))

    def test_publish_demand(self):
        """publish_demand writes demand.json."""
        from agentos.adapters.autonomous_os import AutonomousOSAdapter
        adapter = AutonomousOSAdapter(state_dir=self.state_dir)
        adapter.publish_demand(queue_depth=2, active_workers=1, desired_model=1)
        demand_path = os.path.join(self.state_dir, "demand.json")
        with open(demand_path) as f:
            data = json.load(f)
        self.assertEqual(data["queue_depth"], 2)
        self.assertEqual(data["active_workers"], 1)

    def test_read_allocation(self):
        """read_allocation reads autonomousos-allocation.json."""
        from agentos.adapters.autonomous_os import AutonomousOSAdapter
        adapter = AutonomousOSAdapter(state_dir=self.state_dir)
        alloc_path = os.path.join(self.state_dir, "autonomousos-allocation.json")
        with open(alloc_path, "w") as f:
            json.dump({"max_workers": 2, "priority": 60}, f)
        result = adapter.read_allocation()
        self.assertEqual(result["max_workers"], 2)

    def test_read_allocation_missing(self):
        """read_allocation returns defaults when file missing."""
        from agentos.adapters.autonomous_os import AutonomousOSAdapter
        adapter = AutonomousOSAdapter(state_dir=self.state_dir)
        result = adapter.read_allocation()
        self.assertEqual(result["max_workers"], 1)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests/test_autonomous_os_adapter.py -v`
Expected: FAIL (AutonomousOSAdapter not defined)

- [ ] **Step 3: Write minimal implementation**

```python
# src/agentos/adapters/autonomous_os.py
"""AutonomousOS adapter: register as a managed system.

AgentOS registers with the existing AutonomousOS control plane to receive
resource governance. Publishes demand, reads allocation, and respects
resource limits.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class AutonomousOSAdapter:
    """Adapter to the existing AutonomousOS control plane."""

    def __init__(self, state_dir: str | None = None) -> None:
        self.state_dir = Path(state_dir) if state_dir else Path("/srv/noaerth/agentos/state")
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def register(
        self,
        system_id: str,
        name: str,
        priority: int,
        minimum: int = 1,
        maximum: int = 4,
    ) -> dict[str, Any]:
        """Register AgentOS as a new AutonomousOS system."""
        self.publish_demand(queue_depth=0, active_workers=0, desired_model=0)
        return {
            "ok": True,
            "system_id": system_id,
            "name": name,
            "priority": priority,
            "minimum": minimum,
            "maximum": maximum,
        }

    def publish_demand(
        self,
        queue_depth: int = 0,
        active_workers: int = 0,
        desired_model: int = 0,
        urgency: str = "NORMAL",
    ) -> dict[str, Any]:
        """Publish demand.json to state directory."""
        demand = {
            "system_id": "agentos",
            "queue_depth": queue_depth,
            "active_workers": active_workers,
            "desired_model": desired_model,
            "desired_build": 0,
            "desired_test": 0,
            "desired_browser": 0,
            "desired_io": 0,
            "urgency": urgency,
            "gpu_required": False,
            "render_backlog": 0,
        }
        demand_path = self.state_dir / "demand.json"
        demand_path.write_text(json.dumps(demand, indent=2))
        return {"ok": True, "path": str(demand_path)}

    def read_allocation(self) -> dict[str, Any]:
        """Read autonomousos-allocation.json from state directory."""
        alloc_path = self.state_dir / "autonomousos-allocation.json"
        if not alloc_path.exists():
            return {"max_workers": 1, "priority": 60, "ok": True}
        try:
            data = json.loads(alloc_path.read_text())
            data["ok"] = True
            return data
        except (json.JSONDecodeError, OSError):
            return {"max_workers": 1, "priority": 60, "ok": True}

    def get_system_status(self) -> dict[str, Any]:
        """Get current system status."""
        allocation = self.read_allocation()
        demand_path = self.state_dir / "demand.json"
        demand = {}
        if demand_path.exists():
            try:
                demand = json.loads(demand_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {
            "system_id": "agentos",
            "allocation": allocation,
            "demand": demand,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests/test_autonomous_os_adapter.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentos/adapters/autonomous_os.py tests/test_autonomous_os_adapter.py
git commit -m "feat: add AutonomousOS registration adapter"
```

---

### Task 7: systemd Service Unit

**Files:**
- Create: `deploy/agentos.service`
- Create: `deploy/agentos.env`
- Create: `deploy/install.sh`

**Interfaces:**
- Consumes: All previous tasks
- Produces: systemd unit file, environment config, install script

- [ ] **Step 1: Create the systemd unit file**

```ini
# deploy/agentos.service
[Unit]
Description=AgentOS Execution Service
After=network.target
Wants=network.target

[Service]
Type=simple
User=agentos
Group=agentos
WorkingDirectory=/srv/noaerth/agentos
Environment=AGENTOS_HOME=/srv/noaerth/agentos
Environment=PYTHONUNBUFFERED=1
Environment=PATH=/usr/local/bin:/usr/bin:/bin
ExecStart=/usr/bin/python3 -m agentos serve --host 127.0.0.1 --port 8765
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
RestartSec=5
StartLimitBurst=3
StartLimitIntervalSec=60
StandardOutput=journal
StandardError=journal
SyslogIdentifier=agentos

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Create environment file**

```bash
# deploy/agentos.env
AGENTOS_HOME=/srv/noaerth/agentos
PYTHONUNBUFFERED=1
```

- [ ] **Step 3: Create install script**

```bash
#!/bin/bash
# deploy/install.sh
set -euo pipefail

AGENTOS_USER="agentos"
AGENTOS_HOME="/srv/noaerth/agentos"

# Create dedicated user
if ! id "$AGENTOS_USER" &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$AGENTOS_USER"
fi

# Create directories
mkdir -p "$AGENTOS_HOME"
mkdir -p "$AGENTOS_HOME/state"
mkdir -p "$AGENTOS_HOME/logs"

# Install Python package
pip3 install -e /srv/noaerth/agentos

# Set permissions
chown -R "$AGENTOS_USER:$AGENTOS_USER" "$AGENTOS_HOME"

# Install systemd unit
cp deploy/agentos.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable agentos.service

echo "AgentOS service installed. Start with: systemctl start agentos"
```

- [ ] **Step 4: Commit**

```bash
git add deploy/
git commit -m "feat: add systemd service unit and install script"
```

---

### Task 8: CLI Integration — noaerth agentos

**Files:**
- Modify: `src/agentos/cli.py` (add server commands)
- Create: `tests/test_cli_server.py`

**Interfaces:**
- Consumes: All adapter tasks
- Produces: `agentos workers`, `agentos providers`, `agentos logs`, `agentos recover` commands

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_server.py
import unittest
from unittest.mock import patch, MagicMock

class TestCLIServer(unittest.TestCase):
    def test_workers_command(self):
        """agentos workers lists hive workers."""
        from agentos.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["workers"])
        self.assertEqual(args.command, "workers")

    def test_providers_command(self):
        """agentos providers lists AI mesh providers."""
        from agentos.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["providers"])
        self.assertEqual(args.command, "providers")

    def test_logs_command(self):
        """agentos logs shows recent events."""
        from agentos.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["logs"])
        self.assertEqual(args.command, "logs")

    def test_recover_command(self):
        """agentos recover recovers a failed objective."""
        from agentos.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["recover", "obj_123"])
        self.assertEqual(args.command, "recover")
        self.assertEqual(args.objective_id, "obj_123")

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests/test_cli_server.py -v`
Expected: FAIL (workers/providers/logs commands not in parser)

- [ ] **Step 3: Add CLI commands**

Add to `build_parser()` in `cli.py`:

```python
sub.add_parser("workers", help="list hive workers")
sub.add_parser("providers", help="list AI mesh providers")
p_logs = sub.add_parser("logs", help="show recent events")
p_logs.add_argument("--limit", type=int, default=50)
```

Add to `dispatch()` in `cli.py`:

```python
if cmd == "workers":
    return cmd_workers(app, args, as_json)
if cmd == "providers":
    return cmd_providers(app, args, as_json)
if cmd == "logs":
    return cmd_logs(app, args, as_json)
```

Add command implementations:

```python
def cmd_workers(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    from agentos.adapters.hive import HiveAdapter
    adapter = HiveAdapter()
    workers = adapter.discover_workers()
    if as_json:
        return emit_json({"ok": True, "workers": workers})
    if not workers:
        print("no active workers")
        return 0
    for w in workers:
        print(f"{w['system_id']:<20} workers={w['active_workers']} priority={w['priority']}")
    return 0


def cmd_providers(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    from agentos.adapters.ai_mesh import AIMeshAdapter
    adapter = AIMeshAdapter()
    providers = adapter.discover_providers()
    if as_json:
        return emit_json({"ok": True, "providers": providers})
    if not providers:
        print("no providers detected")
        return 0
    for p in providers:
        print(f"{p['id']:<15} [{p['state']}] cost_rank={p['cost_rank']} free={p['free']}")
    return 0


def cmd_logs(app: AgentOS, args: argparse.Namespace, as_json: bool) -> int:
    events = app.store.get_events(limit=args.limit)
    if as_json:
        return emit_json({"ok": True, "events": [e.to_dict() for e in events]})
    for e in events:
        print(f"{e.created_at[:19]}  {e.event_type}")
    return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests/test_cli_server.py -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -5`
Expected: OK (710+ tests pass)

- [ ] **Step 6: Commit**

```bash
git add src/agentos/cli.py tests/test_cli_server.py
git commit -m "feat: add server CLI commands (workers, providers, logs)"
```

---

### Task 9: Server Deployment

**Files:**
- Server: `/srv/noaerth/agentos/`
- Server: `/etc/systemd/system/agentos.service`

**Interfaces:**
- Consumes: All previous tasks
- Produces: Running AgentOS service on Contabo

- [ ] **Step 1: Copy AgentOS to server**

```bash
rsync -avz --exclude='.git' --exclude='tests' --exclude='__pycache__' \
  ~/startups/agentos/ root@100.127.182.126:/srv/noaerth/agentos/
```

- [ ] **Step 2: Install and start service**

```bash
ssh root@100.127.182.126 "cd /srv/noaerth/agentos && bash deploy/install.sh"
ssh root@100.127.182.126 "systemctl start agentos"
ssh root@100.127.182.126 "systemctl status agentos"
```

- [ ] **Step 3: Verify service is running**

```bash
ssh root@100.127.182.126 "systemctl is-active agentos"
ssh root@100.127.182.126 "agentos status"
```

- [ ] **Step 4: Register with AutonomousOS**

```bash
ssh root@100.127.182.126 "/home/portfolio/.local/bin/autonomousos register agentos 'AgentOS' 60 /srv/noaerth/agentos/state --minimum 1 --maximum 4"
```

---

### Task 10: Acceptance Testing

**Files:**
- Server: `/srv/noaerth/agentos/`

**Interfaces:**
- Consumes: All previous tasks
- Produces: Live execution evidence

- [ ] **Step 1: Create test objective**

```bash
ssh root@100.127.182.126 "agentos objective create 'Server smoke test' --description 'Verify AgentOS works on server'"
```

- [ ] **Step 2: Run objective**

```bash
ssh root@100.127.182.126 "agentos run <objective_id> --operation shell.run --params '{\"command\": \"echo hello from server\"}'"
```

- [ ] **Step 3: Verify objective**

```bash
ssh root@100.127.182.126 "agentos verify <objective_id>"
```

- [ ] **Step 4: Check workers**

```bash
ssh root@100.127.182.126 "agentos workers"
```

- [ ] **Step 5: Check providers**

```bash
ssh root@100.127.182.126 "agentos providers"
```

- [ ] **Step 6: Test restart persistence**

```bash
ssh root@100.127.182.126 "systemctl restart agentos"
ssh root@100.127.182.126 "agentos status"
```

- [ ] **Step 7: Test noaerth CLI integration**

```bash
ssh root@100.127.182.126 "noaerth agentos status"
```
