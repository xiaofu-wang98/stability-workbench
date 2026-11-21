import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .migrations import migrate


class SnapshotConnection(sqlite3.Connection):
    """Publish derived read state only after the transaction has committed."""

    def execute(self, sql, parameters=()):
        # total_changes is monotonic even across ROLLBACK TO. An undo set built
        # inside a rolled-back savepoint cannot represent the restored state.
        if sql.lstrip().upper().startswith(('ROLLBACK', 'BEGIN', 'COMMIT')):
            getattr(self, 'undo_sets', {}).clear()
        return super().execute(sql, parameters)

    def rollback(self):
        getattr(self, 'undo_sets', {}).clear()
        return super().rollback()


class Store:
    def __init__(self, home: Path):
        self.home = home.resolve()
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "sources").mkdir(exist_ok=True)
        self.db = self.home / "history.sqlite"
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            # Durability is a compliance invariant: keep SQLite's default
            # synchronous=FULL. Never trade committed audit records for speed.
            migrate(conn, self.db)

    @contextmanager
    def read(self):
        """A SQLite-enforced read-only snapshot; never migrates or commits writes."""
        conn = sqlite3.connect(self.db.as_uri() + '?mode=ro', uri=True, timeout=10, factory=SnapshotConnection)
        conn.undo_sets = {}
        # Request-local reuse of derived review state. Every value is computed
        # from one committed snapshot, so it may be published immediately.
        conn.review_states = {}
        conn.read_only = True
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('BEGIN')
            yield conn
        finally:
            conn.close()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db, timeout=10, factory=SnapshotConnection)
        conn.after_commit = []
        conn.review_states = {}
        conn.undo_sets = {}
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
            for callback in conn.after_commit:
                callback()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def snapshot(self, project: str) -> list[dict]:
        with self.connect() as conn:
            # All records and source rows must belong to the same read snapshot.
            conn.execute("BEGIN")
            rows = conn.execute(
                """
                SELECT o.id, o.record_json, o.slot_id FROM effective e
                JOIN observations o ON o.id=e.observation_id
                JOIN imports i ON i.id=o.import_id WHERE i.project=? ORDER BY o.slot_id
            """,
                (project,),
            ).fetchall()
            records = []
            for row in rows:
                refs = conn.execute(
                    """
                    SELECT o.id, o.raw_json, i.document_hash, d.filename, d.stored_path,
                           i.config_json, i.context_json
                    FROM observations o JOIN imports i ON i.id=o.import_id
                    JOIN documents d ON d.sha256=i.document_hash
                    WHERE o.slot_id=? AND o.status IN ('accepted','equivalent') ORDER BY o.id
                """,
                    (row["slot_id"],),
                ).fetchall()
                records.append(
                    {
                        "slot_id": row["slot_id"],
                        "observation_id": row["id"],
                        "record": json.loads(row["record_json"]),
                        "sources": [
                            {
                                **dict(ref),
                                "raw_json": json.loads(ref["raw_json"]),
                                "config_json": json.loads(ref["config_json"]),
                                "context_json": json.loads(ref["context_json"]),
                            }
                            for ref in refs
                        ],
                    }
                )
            return records

    def issues(self, project: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT o.*, i.document_hash, d.filename FROM observations o
                JOIN imports i ON i.id=o.import_id JOIN documents d ON d.sha256=i.document_hash
                WHERE i.project=? AND o.status IN ('pending','conflict') ORDER BY o.id
            """,
                (project,),
            ).fetchall()
            return [dict(row) for row in rows]
