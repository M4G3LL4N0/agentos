"""Backup & Reconstruction - portable state export/import (excludes secrets)."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentos.store import Store
from agentos.models import utc_now_iso, new_id
from agentos.policies import redact_secrets, redact_value


# Tables to include in backup (non-secret)
BACKUP_TABLES = [
    "objectives",
    "capabilities",
    "executions",
    "events",
    "verification_results",
    "failures",
    "plans",
    "patterns",
    "gaps",
    "quality",
    "federation_jobs",
    "federation_results",
    "federation_cells",
    "mcp_candidates",
    "ecosystem_candidates",
    "workflow_catalog",
    "benchmark_runs",
    "usage_snapshots",
    "usage_ledger",
    "routing_outcomes",
    "job_verifications",
    "intelligence_cache",
    "task_fingerprints",
    "efficiency_counters",
    "verification_records",
    "lessons",
    "workflows",
    "skills",
    "incidents",
    "executor_metrics",
    "autonomy_records",
    "routines",
    "job_traces",
    "persistence_decisions",
]

# Tables to EXCLUDE (contain secrets/credentials)
EXCLUDE_TABLES = [
    "api_keys",
    "credentials",
    "secrets",
    "browser_sessions",
    "oauth_tokens",
    "recovery_codes",
    "private_keys",
]


@dataclass
class BackupManifest:
    """Manifest describing a backup."""
    id: str
    version: str
    created_at: str
    source_db: str
    tables_included: list[str]
    tables_excluded: list[str]
    row_counts: dict[str, int]
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "created_at": self.created_at,
            "source_db": self.source_db,
            "tables_included": self.tables_included,
            "tables_excluded": self.tables_excluded,
            "row_counts": self.row_counts,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BackupManifest":
        return cls(
            id=str(data["id"]),
            version=str(data.get("version", "1.0")),
            created_at=str(data.get("created_at") or utc_now_iso()),
            source_db=str(data.get("source_db", "")),
            tables_included=list(data.get("tables_included", [])),
            tables_excluded=list(data.get("tables_excluded", [])),
            row_counts=dict(data.get("row_counts", {})),
            schema_version=str(data.get("schema_version", "1.0")),
        )


def sanitize_row(table: str, row: dict[str, Any]) -> dict[str, Any]:
    """Remove secret fields from a row before backup."""
    sanitized = dict(row)

    # Remove known secret columns
    secret_columns = [
        "password", "passwd", "secret", "credential", "token", "api_key",
        "apikey", "access_token", "refresh_token", "session_token",
        "client_secret", "private_key", "secret_key", "recovery_code",
        "cookie", "session", "bearer", "authorization",
    ]

    for col in list(sanitized.keys()):
        col_lower = col.lower()
        if any(s in col_lower for s in secret_columns):
            sanitized[col] = "[REDACTED]"
        elif isinstance(sanitized[col], str):
            if col_lower.endswith("_json"):
                try:
                    sanitized[col] = json.dumps(
                        redact_value(json.loads(sanitized[col]))
                    )
                except (json.JSONDecodeError, TypeError):
                    sanitized[col] = redact_secrets(sanitized[col])
            else:
                sanitized[col] = redact_secrets(sanitized[col])

    # Table-specific sanitization
    if table == "capabilities":
        # Sanitize capability config that might have secrets
        config = sanitized.get("config_json") or sanitized.get("config", {})
        if isinstance(config, str):
            try:
                config = json.loads(config)
            except Exception:
                config = {}
        if isinstance(config, dict):
            for k in list(config.keys()):
                if any(s in k.lower() for s in ["secret", "token", "key", "password"]):
                    config[k] = "[REDACTED]"
            sanitized["config_json"] = json.dumps(config)

    if table == "federation_cells":
        # Cell JSON might have credentials
        cell_json = sanitized.get("cell_json", {})
        if isinstance(cell_json, str):
            try:
                cell_json = json.loads(cell_json)
            except Exception:
                cell_json = {}
        if isinstance(cell_json, dict):
            for k in list(cell_json.keys()):
                if any(s in k.lower() for s in ["credential", "token", "key", "secret"]):
                    cell_json[k] = "[REDACTED]"
            sanitized["cell_json"] = json.dumps(cell_json)

    return sanitized


def create_backup(source_db_path: str, output_dir: str | None = None) -> str:
    """Create a portable backup of the database (excluding secrets)."""
    source = Path(source_db_path)
    if not source.exists():
        raise FileNotFoundError(f"Database not found: {source_db_path}")

    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="agentos_backup_")
    else:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    backup_id = new_id("backup")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_dir = Path(output_dir) / f"agentos_backup_{timestamp}_{backup_id}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    # Connect to source
    import sqlite3
    src_conn = sqlite3.connect(str(source))
    src_conn.row_factory = sqlite3.Row

    # Get table list
    tables = [r[0] for r in src_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]

    row_counts = {}
    tables_included = []

    for table in tables:
        if table in EXCLUDE_TABLES:
            continue

        if table not in BACKUP_TABLES:
            continue

        tables_included.append(table)

        # Export table
        rows = src_conn.execute(f"SELECT * FROM {table}").fetchall()
        row_counts[table] = len(rows)

        if rows:
            # Write as JSONL
            output_file = backup_dir / f"{table}.jsonl"
            with open(output_file, "w") as f:
                for row in rows:
                    row_dict = dict(row)
                    sanitized = sanitize_row(table, row_dict)
                    f.write(json.dumps(sanitized) + "\n")

    src_conn.close()

    # Create manifest
    manifest = BackupManifest(
        id=new_id("manifest"),
        version="1.0",
        created_at=datetime.now(timezone.utc).isoformat(),
        source_db=source.name,
        tables_included=tables_included,
        tables_excluded=EXCLUDE_TABLES,
        row_counts=row_counts,
    )

    manifest_path = backup_dir / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest.to_dict(), f, indent=2)

    # Create archive
    archive_path = shutil.make_archive(
        str(backup_dir), "gztar", backup_dir.parent, backup_dir.name
    )

    # Cleanup temp directory
    shutil.rmtree(backup_dir)

    return archive_path


def restore_backup(archive_path: str, target_db_path: str,
                   verify_manifest: bool = True) -> BackupManifest:
    inspection = inspect_backup(archive_path)
    if not inspection["valid"]:
        raise ValueError("backup archive failed validation")
    with tempfile.TemporaryDirectory() as tmpdir:
        backup_dir = _extract_backup_archive(Path(archive_path), Path(tmpdir))
        manifest = BackupManifest.from_dict(inspection["manifest"])
        import sqlite3
        target = Path(target_db_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target_conn = sqlite3.connect(str(target))
        target_conn.row_factory = sqlite3.Row
        try:
            target_tables = {
                str(row["name"])
                for row in target_conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            for table in manifest.tables_included:
                if table not in target_tables:
                    raise ValueError(f"target database is missing table: {table}")
                jsonl_path = backup_dir / f"{table}.jsonl"
                if not jsonl_path.exists():
                    continue
                allowed_columns = {
                    str(row["name"])
                    for row in target_conn.execute(
                        f"PRAGMA table_info({table})"
                    ).fetchall()
                }
                rows = [
                    json.loads(line)
                    for line in jsonl_path.read_text().splitlines()
                    if line
                ]
                for row in rows:
                    unknown = set(row) - allowed_columns
                    if unknown:
                        raise ValueError(
                            f"backup contains unknown columns for {table}: "
                            + ", ".join(sorted(unknown))
                        )
                if verify_manifest and len(rows) != manifest.row_counts[table]:
                    raise ValueError(
                        f"backup row count mismatch for {table}: "
                        f"manifest={manifest.row_counts[table]}, actual={len(rows)}"
                    )
                target_conn.execute(f"DELETE FROM {table}")
                for row in rows:
                    columns = list(row)
                    placeholders = ",".join("?" for _ in columns)
                    values = [
                        json.dumps(row[column])
                        if isinstance(row[column], (dict, list))
                        else row[column]
                        for column in columns
                    ]
                    target_conn.execute(
                        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
                        values,
                    )
            target_conn.commit()
        except Exception:
            target_conn.rollback()
            raise
        finally:
            target_conn.close()
    return manifest


def _contains_secret_value(value: Any) -> bool:
    if isinstance(value, dict):
        secret_names = (
            "password", "passwd", "secret", "credential", "token", "api_key",
            "apikey", "access_token", "refresh_token", "session_token",
            "client_secret", "private_key", "recovery_code", "cookie",
            "authorization", "bearer",
        )
        for key, item in value.items():
            if any(marker in str(key).lower() for marker in secret_names):
                if item not in (None, "", [], {}, "[REDACTED]", "***redacted***"):
                    return True
            if _contains_secret_value(item):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_secret_value(item) for item in value)
    if isinstance(value, str):
        lowered = value.lower()
        if "-----begin" in lowered and "private key" in lowered:
            return True
        return bool(re.search(
            r"(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|session[_-]?token|client[_-]?secret|password|passwd|bearer)\s*[:=]\s*['\"]?[^\s'\";,}]+",
            value,
        ))
    return False


def _extract_backup_archive(archive: Path, destination: Path) -> Path:
    root = destination.resolve()
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        for member in members:
            member_path = (destination / member.name).resolve()
            if member_path != root and root not in member_path.parents:
                raise ValueError("backup archive contains an unsafe path")
            if not (member.isdir() or member.isfile()):
                raise ValueError("backup archive contains an unsafe member")
        bundle.extractall(destination, filter="data")
    roots = [entry for entry in destination.iterdir() if entry.is_dir()]
    if len(roots) != 1:
        raise ValueError("invalid backup archive structure")
    return roots[0]


def _validate_archive_members(root: Path, manifest: BackupManifest) -> None:
    allowed = {"manifest.json"} | {
        f"{table}.jsonl" for table in manifest.tables_included
    }
    entries = [path for path in root.rglob("*") if path.is_file()]
    unlisted = sorted(
        str(path.relative_to(root)) for path in entries if path.name not in allowed
    )
    nested = sorted(
        str(path.relative_to(root)) for path in entries if path.parent != root
    )
    if unlisted:
        raise ValueError("backup contains unlisted files: " + ", ".join(unlisted))
    if nested:
        raise ValueError("backup contains nested files: " + ", ".join(nested))


def _target_schema_report(
    manifest: BackupManifest, target_db_path: Path, archive_root: Path
) -> dict[str, Any]:
    if not target_db_path.exists():
        return {
            "targetCompatible": None,
            "targetMissingTables": [],
            "targetUnknownColumns": {},
            "targetError": None,
        }
    try:
        with sqlite3.connect(str(target_db_path)) as connection:
            target_tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            missing = sorted(set(manifest.tables_included) - target_tables)
            unknown: dict[str, list[str]] = {}
            for table in manifest.tables_included:
                if table not in target_tables:
                    continue
                allowed_columns = {
                    str(row[1])
                    for row in connection.execute(
                        f"PRAGMA table_info({table})"
                    ).fetchall()
                }
                rows_path = archive_root / f"{table}.jsonl"
                if not rows_path.exists():
                    continue
                for line in rows_path.read_text(encoding="utf-8").splitlines():
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(row, dict):
                        continue
                    extra = sorted(set(row) - allowed_columns)
                    if extra:
                        unknown.setdefault(table, [])
                        unknown[table].extend(extra)
            unknown = {
                table: sorted(set(columns)) for table, columns in unknown.items()
            }
        return {
            "targetCompatible": not missing and not unknown,
            "targetMissingTables": missing,
            "targetUnknownColumns": unknown,
            "targetError": None,
        }
    except sqlite3.Error as exc:
        return {
            "targetCompatible": False,
            "targetMissingTables": [],
            "targetUnknownColumns": {},
            "targetError": str(exc),
        }


def _validate_manifest(manifest: BackupManifest) -> None:
    seen: set[str] = set()
    for table in manifest.tables_included:
        if table not in BACKUP_TABLES:
            raise ValueError(f"backup manifest contains an unlisted table: {table}")
        if table in seen:
            raise ValueError(f"backup manifest contains duplicate table: {table}")
        seen.add(table)
        count = manifest.row_counts.get(table)
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError(f"backup manifest has an invalid row count for {table}")


def inspect_backup(archive_path: str) -> dict[str, Any]:
    """Validate an archive without mutating a target database."""
    archive = Path(archive_path)
    if not archive.exists():
        raise FileNotFoundError(str(archive))
    with tempfile.TemporaryDirectory() as tmpdir:
        root = _extract_backup_archive(archive, Path(tmpdir))
        manifest_path = root / "manifest.json"
        if not manifest_path.exists():
            raise ValueError("backup manifest is missing")
        manifest = BackupManifest.from_dict(json.loads(manifest_path.read_text()))
        _validate_manifest(manifest)
        _validate_archive_members(root, manifest)
        missing: list[str] = []
        for table in manifest.tables_included:
            path = root / f"{table}.jsonl"
            expected = manifest.row_counts[table]
            if expected > 0 and not path.exists():
                missing.append(table)
                continue
            if not path.exists():
                continue
            rows = [line for line in path.read_text().splitlines() if line]
            if len(rows) != expected:
                raise ValueError(
                    f"backup row count mismatch for {table}: "
                    f"manifest={expected}, actual={len(rows)}"
                )
            for line in rows:
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"backup contains invalid JSON for {table}") from exc
                if not isinstance(parsed, dict):
                    raise ValueError(f"backup contains a non-object row for {table}")
                if _contains_secret_value(parsed):
                    raise ValueError(f"backup contains secret data in {table}")
        if missing:
            raise ValueError("backup is missing tables: " + ", ".join(missing))
        return {
            "valid": True,
            "manifest": manifest.to_dict(),
            "tablesIncluded": list(manifest.tables_included),
            "missingTables": [],
            "secretLeaks": [],
        }


def dry_run_restore(archive_path: str, target_db_path: str) -> dict[str, Any]:
    """Inspect an archive and target without writing any rows."""
    result = inspect_backup(archive_path)
    target = Path(target_db_path)
    with tempfile.TemporaryDirectory() as tmpdir:
        root = _extract_backup_archive(Path(archive_path), Path(tmpdir))
        manifest = BackupManifest.from_dict(result["manifest"])
        target_report = _target_schema_report(manifest, target, root)
    compatible = target_report["targetCompatible"]
    ok = bool(result["valid"]) and compatible is not False
    return {
        "ok": ok,
        "dryRun": True,
        "archive": str(archive_path),
        "target": str(target),
        "targetExists": target.exists(),
        "manifest": result["manifest"],
        "missingTables": result["missingTables"],
        "secretLeaks": result["secretLeaks"],
        **target_report,
    }


def reconstruct_workforce_from_backup(backup_dir: str) -> dict[str, Any]:
    """Reconstruct workforce state from backup (roles, skills, workflows, autonomy)."""
    result = {
        "roles": [],
        "skills": [],
        "workflows": [],
        "autonomy": [],
        "lessons": [],
        "incidents": [],
    }

    backup_path = Path(backup_dir)

    # Load skills
    skills_file = backup_path / "skills.jsonl"
    if skills_file.exists():
        with open(skills_file) as f:
            for line in f:
                if line.strip():
                    result["skills"].append(json.loads(line))

    # Load workflows
    wf_file = backup_path / "workflows.jsonl"
    if wf_file.exists():
        with open(wf_file) as f:
            for line in f:
                if line.strip():
                    result["workflows"].append(json.loads(line))

    # Load autonomy records
    autonomy_file = backup_path / "autonomy_records.jsonl"
    if autonomy_file.exists():
        with open(autonomy_file) as f:
            for line in f:
                if line.strip():
                    result["autonomy"].append(json.loads(line))

    # Load lessons
    lessons_file = backup_path / "lessons.jsonl"
    if lessons_file.exists():
        with open(lessons_file) as f:
            for line in f:
                if line.strip():
                    result["lessons"].append(json.loads(line))

    # Load incidents
    inc_file = backup_path / "incidents.jsonl"
    if inc_file.exists():
        with open(inc_file) as f:
            for line in f:
                if line.strip():
                    result["incidents"].append(json.loads(line))

    return result


class BackupManager:
    """Manages backup and restoration."""

    def __init__(self, store: Store):
        self.store = store

    def create_backup(self, output_dir: str | None = None) -> str:
        """Create backup of current database."""
        return create_backup(str(self.store.path), output_dir)

    def restore_backup(self, archive_path: str,
                       verify_manifest: bool = True) -> BackupManifest:
        """Restore database from backup."""
        # Close current connections
        self.store.close()
        try:
            manifest = restore_backup(archive_path, str(self.store.path), verify_manifest)
            # Reconnect
            self.store = Store(str(self.store.path))
            return manifest
        except Exception as e:
            # Try to reconnect
            self.store = Store(str(self.store.path))
            raise

    def inspect(self, archive_path: str) -> dict[str, Any]:
        return inspect_backup(archive_path)

    def restore_dry_run(self, archive_path: str) -> dict[str, Any]:
        return dry_run_restore(archive_path, str(self.store.path))

    def reconstruct_workforce(self, backup_dir: str) -> dict[str, Any]:
        """Reconstruct workforce state from backup."""
        return reconstruct_workforce_from_backup(backup_dir)