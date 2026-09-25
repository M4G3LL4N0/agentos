"""Tests for Backup & Reconstruction - portable state export/import (excludes secrets)."""

from __future__ import annotations

import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path

from agentos.models import SkillDefinition
from agentos.store import Store
from agentos.backup_reconstruction import (
    BackupManifest,
    sanitize_row,
    create_backup,
    inspect_backup,
    dry_run_restore,
    restore_backup,
    reconstruct_workforce_from_backup,
    BackupManager,
    BACKUP_TABLES,
    EXCLUDE_TABLES,
)


class TestBackupManifest(unittest.TestCase):
    def test_backup_manifest_creation(self):
        manifest = BackupManifest(
            id="backup_1",
            version="1.0",
            created_at="2026-09-24T10:00:00Z",
            source_db="/path/to/db",
            tables_included=["objectives", "capabilities"],
            tables_excluded=["credentials"],
            row_counts={"objectives": 10, "capabilities": 5},
        )
        self.assertEqual(manifest.id, "backup_1")
        self.assertEqual(len(manifest.tables_included), 2)

    def test_backup_manifest_to_from_dict(self):
        manifest = BackupManifest(
            id="backup_1",
            version="1.0",
            created_at="2026-09-24T10:00:00Z",
            source_db="/path/to/db",
            tables_included=["objectives"],
            tables_excluded=["credentials"],
            row_counts={"objectives": 10},
        )
        d = manifest.to_dict()
        self.assertEqual(d["id"], "backup_1")
        self.assertEqual(d["version"], "1.0")

        restored = BackupManifest.from_dict(d)
        self.assertEqual(restored.id, "backup_1")
        self.assertEqual(restored.tables_included, ["objectives"])


class TestSanitizeRow(unittest.TestCase):
    def test_sanitize_removes_secret_columns(self):
        row = {
            "id": "obj1",
            "name": "test",
            "password": "secret123",
            "api_key": "key123",
            "access_token": "token123",
            "normal_field": "value",
        }
        sanitized = sanitize_row("objectives", row)
        self.assertEqual(sanitized["password"], "[REDACTED]")
        self.assertEqual(sanitized["api_key"], "[REDACTED]")
        self.assertEqual(sanitized["access_token"], "[REDACTED]")
        self.assertEqual(sanitized["normal_field"], "value")

    def test_sanitize_capability_config(self):
        row = {
            "id": "cap1",
            "config_json": '{"api_key": "secret", "normal": "value"}',
        }
        sanitized = sanitize_row("capabilities", row)
        config = json.loads(sanitized["config_json"])
        self.assertEqual(config["api_key"], "[REDACTED]")
        self.assertEqual(config["normal"], "value")

    def test_sanitize_federation_cell(self):
        row = {
            "id": "cell1",
            "cell_json": '{"credential": "secret", "endpoint": "http://localhost"}',
        }
        sanitized = sanitize_row("federation_cells", row)
        cell = json.loads(sanitized["cell_json"])
        self.assertEqual(cell["credential"], "[REDACTED]")
        self.assertEqual(cell["endpoint"], "http://localhost")


class TestCreateBackup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.store = Store(str(self.db_path))

        # Add some test data
        self.store.conn.execute(
            "INSERT INTO objectives (id, title, description, status, priority, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            ("obj1", "Test Objective", "Description", "CREATED", "HIGH", "2026-09-24T10:00:00Z", "2026-09-24T10:00:00Z")
        )
        self.store.conn.execute(
            "INSERT INTO capabilities (id, name, type, description) VALUES (?,?,?,?)",
            ("cap1", "Test Capability", "CLI", "Description")
        )
        self.store.conn.commit()

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_backup_creates_archive(self):
        archive_path = create_backup(str(self.db_path), self.tmp.name)
        self.assertTrue(os.path.exists(archive_path))
        self.assertTrue(archive_path.endswith(".tar.gz"))

    def test_create_backup_manifest(self):
        archive_path = create_backup(str(self.db_path), self.tmp.name)

        # Extract and check manifest
        import tarfile
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(self.tmp.name, filter="data")

        # Find extracted directory
        extracted_dirs = [d for d in Path(self.tmp.name).iterdir() if d.is_dir() and d.name.startswith("agentos_backup_")]
        self.assertEqual(len(extracted_dirs), 1)

        manifest_path = extracted_dirs[0] / "manifest.json"
        self.assertTrue(manifest_path.exists())

        with open(manifest_path) as f:
            manifest_data = json.load(f)

        self.assertEqual(manifest_data["version"], "1.0")
        self.assertIn("objectives", manifest_data["tables_included"])
        self.assertIn("capabilities", manifest_data["tables_included"])
        self.assertIn("credentials", manifest_data["tables_excluded"])
        self.assertEqual(manifest_data["row_counts"]["objectives"], 1)
        self.assertEqual(manifest_data["row_counts"]["capabilities"], 1)

    def test_backup_recursively_sanitizes_nested_json(self):
        marker = "DO_NOT_LEAK"
        self.store.conn.execute(
            "UPDATE objectives SET context_json=? WHERE id=?",
            (json.dumps({"nested": {"access_token": marker}}), "obj1"),
        )
        self.store.conn.commit()
        archive_path = create_backup(str(self.db_path), self.tmp.name)
        self.assertTrue(inspect_backup(archive_path)["valid"])
        extracted = Path(self.tmp.name) / "nested_extract"
        extracted.mkdir()
        with tarfile.open(archive_path, "r:gz") as bundle:
            bundle.extractall(extracted, filter="data")
        persisted = "\n".join(
            path.read_text(encoding="utf-8")
            for path in extracted.rglob("*.jsonl")
        )
        self.assertNotIn(marker, persisted)
        self.assertIn("[REDACTED]", persisted)

    def test_backup_excludes_secret_tables(self):
        self.store.conn.execute(
            "CREATE TABLE credentials (id TEXT PRIMARY KEY, credential_json TEXT)"
        )
        self.store.conn.execute(
            "INSERT INTO credentials (id, credential_json) VALUES (?,?)",
            ("cred1", '{"password": "secret"}')
        )
        self.store.conn.commit()

        archive_path = create_backup(str(self.db_path), self.tmp.name)

        import tarfile
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(self.tmp.name, filter="data")

        extracted_dirs = [d for d in Path(self.tmp.name).iterdir() if d.is_dir() and d.name.startswith("agentos_backup_")]
        manifest_path = extracted_dirs[0] / "manifest.json"

        with open(manifest_path) as f:
            manifest_data = json.load(f)

        # credentials table should be in excluded
        self.assertIn("credentials", manifest_data["tables_excluded"])
        # And not in included
        self.assertNotIn("credentials", manifest_data["tables_included"])


class TestRestoreBackup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.store = Store(str(self.db_path))

        # Add test data
        self.store.conn.execute(
            "INSERT INTO objectives (id, title, description, status, priority, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            ("obj1", "Test Objective", "Description", "CREATED", "HIGH", "2026-09-24T10:00:00Z", "2026-09-24T10:00:00Z")
        )
        self.store.conn.execute(
            "INSERT INTO capabilities (id, name, type, description) VALUES (?,?,?,?)",
            ("cap1", "Test Capability", "CLI", "Description")
        )
        self.store.conn.commit()

    def tearDown(self):
        self.tmp.cleanup()

    def test_restore_backup(self):
        # Create backup
        archive_path = create_backup(str(self.db_path), self.tmp.name)

        # Create new empty database
        new_db_path = Path(self.tmp.name) / "restored.db"
        new_store = Store(str(new_db_path))
        new_store.close()

        # Restore
        manifest = restore_backup(archive_path, str(new_db_path))

        self.assertIsInstance(manifest, BackupManifest)
        # Verify the restored data matches
        restored_store = Store(str(new_db_path))
        # Check objectives table has the data
        objectives = restored_store.conn.execute("SELECT * FROM objectives").fetchall()
        self.assertEqual(len(objectives), 1)
        self.assertEqual(objectives[0]["id"], "obj1")
        # Check capabilities table has the data
        capabilities = restored_store.conn.execute("SELECT * FROM capabilities").fetchall()
        self.assertEqual(len(capabilities), 1)
        self.assertEqual(capabilities[0]["id"], "cap1")


class TestReconstructWorkforceFromBackup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.store = Store(str(self.db_path))

        # Add workforce data
        self.store.save_skill(SkillDefinition(
            id="skill_test",
            objective="Test skill",
            inputs={},
            constraints=[],
            decision_rules=[],
            tools=[],
            outputs={},
            verification="",
            approval_boundary="",
            failure_handling={},
            context_refs=[],
            version=1,
        ))

        from agentos.models import WorkflowDefinition, WorkflowStatus, WorkflowMaturity
        wf = WorkflowDefinition(
            id="wf_test",
            name="test_wf",
            task_class="test",
            status=WorkflowStatus.VERIFIED,
            maturity=WorkflowMaturity.MATURE,
        )
        self.store.save_workflow(wf)

        self.store.save_autonomy_record({
            "id": "auto1",
            "entity_type": "worker",
            "entity_id": "worker_1",
            "state": "CERTIFIED_AUTONOMY",
            "successful_verified_tasks": 100,
            "failures": 0,
            "security_incidents": 0,
            "human_corrections": 0,
            "scope_violations": 0,
            "verification_pass_rate": 1.0,
        })

        from agentos.models import LessonCandidate, LessonStatus
        lesson = LessonCandidate(
            id="lesson1",
            task_class="test",
            scope="test",
            symptom="test",
            root_cause="test",
            lesson="test lesson",
            status=LessonStatus.ADOPTED,
        )
        self.store.save_lesson(lesson)

        from agentos.models import IncidentRecord
        incident = IncidentRecord(
            id="inc1",
            signature="test",
            affected_jobs=[],
            affected_executors=[],
            probable_cause="test",
            confidence=1.0,
        )
        self.store.save_incident(incident)

        self.store.conn.commit()

    def tearDown(self):
        self.tmp.cleanup()

    def test_reconstruct_workforce(self):
        # Create backup
        archive_path = create_backup(str(self.db_path), self.tmp.name)

        # Extract
        import tarfile
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(self.tmp.name, filter="data")

        extracted_dirs = [d for d in Path(self.tmp.name).iterdir() if d.is_dir() and d.name.startswith("agentos_backup_")]
        backup_dir = extracted_dirs[0]

        result = reconstruct_workforce_from_backup(str(backup_dir))

        self.assertIn("skills", result)
        self.assertIn("workflows", result)
        self.assertIn("autonomy", result)
        self.assertIn("lessons", result)
        self.assertIn("incidents", result)

        self.assertGreaterEqual(len(result["skills"]), 1)
        self.assertGreaterEqual(len(result["workflows"]), 1)
        self.assertGreaterEqual(len(result["autonomy"]), 1)
        self.assertGreaterEqual(len(result["lessons"]), 1)
        self.assertGreaterEqual(len(result["incidents"]), 1)


class TestBackupManager(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.store = Store(str(self.db_path))

    def tearDown(self):
        self.tmp.cleanup()

    def test_backup_manager_create(self):
        manager = BackupManager(self.store)
        archive = manager.create_backup(self.tmp.name)
        self.assertTrue(os.path.exists(archive))

    def test_backup_manager_reconstruct(self):
        # Add some data
        self.store.save_skill(SkillDefinition(
            id="skill_test",
            objective="Test",
            inputs={},
            constraints=[],
            decision_rules=[],
            tools=[],
            outputs={},
            verification="",
            approval_boundary="",
            failure_handling={},
            context_refs=[],
            version=1,
        ))
        self.store.conn.commit()

        manager = BackupManager(self.store)
        archive = manager.create_backup(self.tmp.name)

        # Extract and reconstruct
        import tarfile
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(self.tmp.name, filter="data")

        extracted_dirs = [d for d in Path(self.tmp.name).iterdir() if d.is_dir() and d.name.startswith("agentos_backup_")]
        result = manager.reconstruct_workforce(str(extracted_dirs[0]))

        self.assertIn("skills", result)
        self.assertGreaterEqual(len(result["skills"]), 1)


class TestBackupValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write_archive(self, manifest, files=None, symlinks=None):
        archive = self.root / "malicious.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            manifest_bytes = json.dumps(manifest).encode()
            manifest_info = tarfile.TarInfo("backup/manifest.json")
            manifest_info.size = len(manifest_bytes)
            bundle.addfile(manifest_info, io.BytesIO(manifest_bytes))
            for name, content in (files or {}).items():
                data = content.encode()
                info = tarfile.TarInfo(f"backup/{name}")
                info.size = len(data)
                bundle.addfile(info, io.BytesIO(data))
            for name, target in (symlinks or {}).items():
                info = tarfile.TarInfo(f"backup/{name}")
                info.type = tarfile.SYMTYPE
                info.linkname = target
                bundle.addfile(info)
        return str(archive)

    def test_restore_rejects_unknown_column(self):
        target = self.root / "target.db"
        store = Store(str(target))
        store.conn.execute(
            "INSERT INTO objectives (id, title, status, priority, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("sentinel", "keep", "CREATED", "HIGH", "2026-09-24T10:00:00Z", "2026-09-24T10:00:00Z"),
        )
        store.conn.commit()
        store.close()
        archive = self._write_archive(
            {
                "id": "manifest",
                "version": "1.0",
                "created_at": "2026-09-24T10:00:00Z",
                "source_db": "test.db",
                "tables_included": ["objectives"],
                "tables_excluded": [],
                "row_counts": {"objectives": 1},
            },
            {"objectives.jsonl": '{"id":"bad","title":"bad","status":"CREATED","not_a_column":"x"}\n'},
        )
        with self.assertRaisesRegex(ValueError, "column"):
            restore_backup(archive, str(target))
        restored = Store(str(target))
        self.assertIsNotNone(restored.get_objective("sentinel"))
        restored.close()

    def test_restore_rejects_secret_before_mutation(self):
        target = self.root / "target.db"
        store = Store(str(target))
        store.conn.execute(
            "INSERT INTO objectives (id, title, status, priority, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("sentinel", "keep", "CREATED", "HIGH", "2026-09-24T10:00:00Z", "2026-09-24T10:00:00Z"),
        )
        store.conn.commit()
        store.close()
        archive = self._write_archive(
            {
                "id": "manifest",
                "version": "1.0",
                "created_at": "2026-09-24T10:00:00Z",
                "source_db": "test.db",
                "tables_included": ["objectives"],
                "tables_excluded": [],
                "row_counts": {"objectives": 1},
            },
            {"objectives.jsonl": '{"id":"bad","title":"bad","status":"CREATED","password":"secret"}\n'},
        )
        with self.assertRaisesRegex(ValueError, "secret"):
            restore_backup(archive, str(target))
        restored = Store(str(target))
        self.assertIsNotNone(restored.get_objective("sentinel"))
        self.assertIsNone(restored.get_objective("bad"))
        restored.close()

    def test_inspect_rejects_unlisted_archive_member(self):
        archive = self._write_archive(
            {
                "id": "manifest",
                "version": "1.0",
                "created_at": "2026-09-24T10:00:00Z",
                "source_db": "test.db",
                "tables_included": ["objectives"],
                "tables_excluded": [],
                "row_counts": {"objectives": 0},
            },
            {"extra.jsonl": "secret\n"},
        )
        with self.assertRaisesRegex(ValueError, "unlisted"):
            inspect_backup(archive)

    def test_dry_run_reports_target_schema_mismatch(self):
        source = Store(str(self.root / "source.db"))
        archive = create_backup(str(source.path), str(self.root))
        source.close()
        target = Store(str(self.root / "target.db"))
        target.conn.execute("DROP TABLE capabilities")
        target.conn.commit()
        target.close()
        result = dry_run_restore(archive, str(self.root / "target.db"))
        self.assertFalse(result["ok"])
        self.assertIn("capabilities", result["targetMissingTables"])

    def test_manifest_does_not_expose_source_path(self):
        source = Store(str(self.root / "source.db"))
        archive = create_backup(str(source.path), str(self.root))
        source.close()
        result = inspect_backup(archive)
        self.assertEqual(result["manifest"]["source_db"], "source.db")

    def test_inspect_rejects_unlisted_table(self):
        archive = self._write_archive(
            {
                "id": "manifest",
                "version": "1.0",
                "created_at": "2026-09-24T10:00:00Z",
                "source_db": "test.db",
                "tables_included": ["unlisted_table"],
                "tables_excluded": [],
                "row_counts": {"unlisted_table": 0},
            }
        )
        with self.assertRaisesRegex(ValueError, "table"):
            inspect_backup(archive)

    def test_inspect_rejects_row_count_mismatch(self):
        archive = self._write_archive(
            {
                "id": "manifest",
                "version": "1.0",
                "created_at": "2026-09-24T10:00:00Z",
                "source_db": "test.db",
                "tables_included": ["objectives"],
                "tables_excluded": [],
                "row_counts": {"objectives": 2},
            },
            {"objectives.jsonl": '{"id":"one"}\n'},
        )
        with self.assertRaisesRegex(ValueError, "row count"):
            inspect_backup(archive)

    def test_inspect_rejects_archive_symlink(self):
        archive = self._write_archive(
            {
                "id": "manifest",
                "version": "1.0",
                "created_at": "2026-09-24T10:00:00Z",
                "source_db": "test.db",
                "tables_included": [],
                "tables_excluded": [],
                "row_counts": {},
            },
            symlinks={"escape": "/tmp"},
        )
        with self.assertRaisesRegex(ValueError, "unsafe"):
            inspect_backup(archive)


if __name__ == "__main__":
    unittest.main()