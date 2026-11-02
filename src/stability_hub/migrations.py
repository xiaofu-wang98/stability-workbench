"""Small, ordered SQLite migrations. Never rewrite a published migration."""

import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4
from .workflow_schema import STATEMENTS

from .source_schema import SCHEMA as SOURCE_SCHEMA

CURRENT_VERSION = 8
MIGRATIONS = {
    8: (
        '''CREATE TABLE workflow_identity_adoptions (
            project TEXT NOT NULL REFERENCES workflow_projects(project),
            slot_id TEXT NOT NULL, version_id TEXT NOT NULL REFERENCES workflow_versions(id),
            PRIMARY KEY(project,slot_id))''',
        '''CREATE TABLE workflow_identity_lineage (
            version_id TEXT PRIMARY KEY REFERENCES workflow_versions(id),
            previous_version_id TEXT NOT NULL REFERENCES workflow_versions(id))''',
        '''CREATE TRIGGER workflow_identity_adoption_insert BEFORE INSERT ON workflow_identity_adoptions
            WHEN NOT EXISTS (SELECT 1 FROM workflow_versions v JOIN workflow_version_states s ON s.version_id=v.id
                             WHERE v.id=NEW.version_id AND v.project=NEW.project AND s.state IN ('confirmed','legacy'))
            BEGIN SELECT RAISE(ABORT,'Only confirmed project results may be logically adopted'); END''',
        '''CREATE TRIGGER workflow_identity_adoption_update BEFORE UPDATE ON workflow_identity_adoptions
            WHEN NOT EXISTS (SELECT 1 FROM workflow_versions v JOIN workflow_version_states s ON s.version_id=v.id
                             WHERE v.id=NEW.version_id AND v.project=NEW.project AND s.state IN ('confirmed','legacy'))
            BEGIN SELECT RAISE(ABORT,'Only confirmed project results may be logically adopted'); END''',
        '''CREATE TRIGGER workflow_identity_lineage_insert BEFORE INSERT ON workflow_identity_lineage
            WHEN NOT EXISTS (SELECT 1 FROM workflow_versions v JOIN workflow_versions p ON p.project=v.project
                             WHERE v.id=NEW.version_id AND p.id=NEW.previous_version_id AND v.upload_sequence>p.upload_sequence)
            BEGIN SELECT RAISE(ABORT,'Identity lineage requires an earlier version in the same project'); END''',
        '''CREATE TRIGGER workflow_identity_lineage_immutable BEFORE UPDATE ON workflow_identity_lineage
            BEGIN SELECT RAISE(ABORT,'Identity lineage is immutable'); END''',
        '''CREATE TRIGGER workflow_identity_lineage_no_delete BEFORE DELETE ON workflow_identity_lineage
            BEGIN SELECT RAISE(ABORT,'Identity lineage cannot be deleted'); END''',
        "INSERT INTO schema_migrations(version,description) VALUES (8,'Explicit logical identity adoption without rewriting historical slots')",
    ),
    7: (
        '''UPDATE workflow_uploads SET interpretation_config_json=(SELECT config_json FROM workflow_projects p
           WHERE p.project=workflow_uploads.project) WHERE interpretation_config_json IS NULL''',
        "INSERT INTO schema_migrations(version,description) VALUES (7,'Freeze legacy draft configuration at upgrade without rewriting result history')",
    ),
    6: (
        'ALTER TABLE workflow_uploads ADD COLUMN interpretation_config_json TEXT',
        'ALTER TABLE workflow_uploads ADD COLUMN workflow_version INTEGER NOT NULL DEFAULT 4',
        "INSERT INTO schema_migrations(version,description) VALUES (6,'Frozen V5 draft interpretation and worksheet boundary')",
    ),
    1: (
        "CREATE TABLE documents (sha256 TEXT PRIMARY KEY, filename TEXT NOT NULL, stored_path TEXT NOT NULL)",
        """CREATE TABLE imports (
            id TEXT PRIMARY KEY, document_hash TEXT NOT NULL REFERENCES documents(sha256),
            project TEXT NOT NULL, config_json TEXT NOT NULL, context_json TEXT NOT NULL,
            summary_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(document_hash, project))""",
        """CREATE TABLE observations (
            id TEXT PRIMARY KEY, import_id TEXT NOT NULL REFERENCES imports(id),
            raw_json TEXT NOT NULL, record_json TEXT, slot_id TEXT,
            status TEXT NOT NULL, issue_code TEXT, detail TEXT NOT NULL, prior_id TEXT)""",
        "CREATE INDEX observations_slot ON observations(slot_id)",
        """CREATE TABLE effective (
            slot_id TEXT PRIMARY KEY, observation_id TEXT NOT NULL REFERENCES observations(id))""",
        """CREATE TABLE exports (
            id TEXT PRIMARY KEY, project TEXT NOT NULL, snapshot_id TEXT NOT NULL,
            state TEXT NOT NULL, output_path TEXT NOT NULL, detail TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    ),
    2: (
        """CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY CHECK(version > 0),
            description TEXT NOT NULL, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
        "INSERT INTO schema_migrations(version, description) VALUES (1, 'Recognized legacy schema')",
        "INSERT INTO schema_migrations(version, description) VALUES (2, 'Versioned migration baseline')",
    ),
    3: STATEMENTS,
    4: SOURCE_SCHEMA,
    5: (
        "ALTER TABLE workflow_projects ADD COLUMN startup_config_json TEXT",
        "UPDATE workflow_projects SET startup_config_json=config_json",
        "INSERT INTO schema_migrations(version, description) VALUES (5, 'Audited project dictionary and presentation defaults')",
    ),
}


def migrate(conn: sqlite3.Connection, database: Path) -> None:
    """Upgrade atomically, back up an existing DB under the write lock, reject future versions."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 0 or version > CURRENT_VERSION:
            raise ValueError(f"Unsupported database schema version: {version}")
        if version == CURRENT_VERSION:
            conn.commit()
            return
        if version:
            backup_dir = database.parent / "migration-backups"
            backup_dir.mkdir(exist_ok=True)
            backup = backup_dir / f"before-v{CURRENT_VERSION}-{uuid4().hex}.sqlite"
            # Use a separate reader: backup on the connection holding a write transaction can block.
            with closing(sqlite3.connect(database)) as source, closing(sqlite3.connect(backup)) as target:
                source.backup(target)
        for next_version in range(version + 1, CURRENT_VERSION + 1):
            for statement in MIGRATIONS[next_version]:
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version={next_version}")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
