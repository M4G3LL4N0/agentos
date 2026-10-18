"""Tests for SQLite WAL mode and concurrent access configuration."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest


class TestSQLiteWAL(unittest.TestCase):
    def test_wal_mode_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from agentos.store import Store

            db_path = os.path.join(tmpdir, "test.db")
            store = Store(db_path)
            mode = store.conn.execute("PRAGMA journal_mode").fetchone()[0]
            self.assertEqual(mode.lower(), "wal")

    def test_busy_timeout_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from agentos.store import Store

            db_path = os.path.join(tmpdir, "test.db")
            store = Store(db_path)
            timeout = store.conn.execute("PRAGMA busy_timeout").fetchone()[0]
            self.assertGreater(timeout, 0)

    def test_foreign_keys_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from agentos.store import Store

            db_path = os.path.join(tmpdir, "test.db")
            store = Store(db_path)
            fk = store.conn.execute("PRAGMA foreign_keys").fetchone()[0]
            self.assertEqual(fk, 1)

    def test_synchronous_normal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from agentos.store import Store

            db_path = os.path.join(tmpdir, "test.db")
            store = Store(db_path)
            sync = store.conn.execute("PRAGMA synchronous").fetchone()[0]
            self.assertEqual(sync, 1)

    def test_concurrent_connections(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from agentos.store import Store

            db_path = os.path.join(tmpdir, "test.db")
            store = Store(db_path)
            store.conn.execute("INSERT INTO objectives (id, title, created_at, updated_at) VALUES ('t1', 'test', '2024-01-01', '2024-01-01')")
            store.conn.commit()
            conn2 = sqlite3.connect(db_path, timeout=30.0)
            conn2.execute("PRAGMA journal_mode=WAL")
            row = conn2.execute("SELECT title FROM objectives WHERE id = 't1'").fetchone()
            conn2.close()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "test")


if __name__ == "__main__":
    unittest.main()
