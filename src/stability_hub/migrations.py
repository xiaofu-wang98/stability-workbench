"""Small, ordered SQLite migrations. Never rewrite a published migration."""

import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4
from .workflow_schema import STATEMENTS

from .source_schema import SCHEMA as SOURCE_SCHEMA
from .worksheet_schema import SCHEMA as WORKSHEET_SCHEMA

CURRENT_VERSION = 18
MIGRATIONS = {
    18: (
        # P0-3: 创建独立的结果作废留痕事件审计表与作废版本登记
        """CREATE TABLE IF NOT EXISTS workflow_invalidated_versions (
            version_id TEXT PRIMARY KEY REFERENCES workflow_versions(id),
            project TEXT NOT NULL REFERENCES workflow_projects(project),
            slot_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            invalidated_by TEXT NOT NULL,
            invalidated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE INDEX IF NOT EXISTS idx_invalidated_versions_slot ON workflow_invalidated_versions(project, slot_id)""",
        """CREATE TABLE IF NOT EXISTS project_aliases (
            alias TEXT PRIMARY KEY,
            canonical_project TEXT NOT NULL REFERENCES workflow_projects(project),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE INDEX IF NOT EXISTS idx_project_aliases_canonical ON project_aliases(canonical_project)""",
        "INSERT INTO schema_migrations(version, description) VALUES (18, 'Result invalidations and project alias control')",
    ),
    17: (
        # 扩展子结果字典：支持单位、定性/定量类型、药典体系、归一化匹配签名与别名列表
        "ALTER TABLE assay_components ADD COLUMN unit TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE assay_components ADD COLUMN result_type TEXT NOT NULL DEFAULT 'numeric'",
        "ALTER TABLE assay_components ADD COLUMN reporting_basis TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE assay_components ADD COLUMN norm_signature TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE assay_components ADD COLUMN aliases_json TEXT NOT NULL DEFAULT '[]'",
        "CREATE INDEX IF NOT EXISTS idx_assay_components_sig ON assay_components(project, norm_signature)",

        # 建立独立于 Word 行号的标准化样品放行结果映射表（打通 Word/Excel CoA 与稳定性 0M）
        """CREATE TABLE IF NOT EXISTS sample_release_results (
            sample_id TEXT NOT NULL REFERENCES sample_entities(id),
            project TEXT NOT NULL REFERENCES workflow_projects(project),
            item_id TEXT NOT NULL,
            specification_id TEXT REFERENCES specifications(id),
            coa_version INTEGER NOT NULL DEFAULT 1,
            raw_value TEXT NOT NULL,
            unit TEXT NOT NULL DEFAULT '',
            reporting_basis TEXT NOT NULL DEFAULT '',
            source_id TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(sample_id, item_id, reporting_basis)
        )""",

        # 建立历史 slot_id 到标准研究表位置的别名映射表（无需篡改旧 workflow_versions 的 slot_id）
        """CREATE TABLE IF NOT EXISTS slot_identity_bridges (
            legacy_slot_id TEXT PRIMARY KEY,
            canonical_slot_id TEXT NOT NULL,
            project TEXT NOT NULL REFERENCES workflow_projects(project),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        "CREATE INDEX IF NOT EXISTS idx_slot_bridges_canonical ON slot_identity_bridges(project, canonical_slot_id)",
        "INSERT INTO schema_migrations(version, description) VALUES (17, 'Unified component signatures, dual-format COA release mapping and slot bridges')",
    ),
    16: (
        """CREATE TABLE assay_definitions (
            id TEXT PRIMARY KEY, project TEXT NOT NULL REFERENCES workflow_projects(project),
            name_zh TEXT NOT NULL, name_en TEXT NOT NULL, method TEXT NOT NULL, method_version TEXT NOT NULL
        )""",
        """CREATE TABLE assay_components (
            project TEXT NOT NULL REFERENCES workflow_projects(project), item_id TEXT NOT NULL,
            assay_id TEXT NOT NULL REFERENCES assay_definitions(id), name_zh TEXT NOT NULL, name_en TEXT NOT NULL,
            PRIMARY KEY(project,item_id)
        )""",
        """CREATE TABLE specification_assay_components (
            project TEXT NOT NULL, specification_id TEXT NOT NULL REFERENCES specifications(id),
            table_number INTEGER NOT NULL, row_number INTEGER NOT NULL, item_id TEXT NOT NULL,
            PRIMARY KEY(project,specification_id,table_number,row_number),
            FOREIGN KEY(project,item_id) REFERENCES assay_components(project,item_id)
        )""",
        "INSERT INTO schema_migrations(version,description) VALUES (16,'Project assay groups and versioned specification links')",
    ),
    15: (
        """CREATE TABLE worksheet_return_sources (
            id TEXT PRIMARY KEY, filename TEXT NOT NULL, source BLOB NOT NULL
        )""",
        "ALTER TABLE business_worksheets ADD COLUMN sample_id TEXT REFERENCES sample_entities(id)",
        """CREATE TABLE worksheet_result_positions (
            sheet_id TEXT NOT NULL REFERENCES business_worksheets(id),
            row_id TEXT NOT NULL, point_id TEXT NOT NULL,
            slot_id TEXT NOT NULL UNIQUE REFERENCES business_worksheet_slots(slot_id),
            PRIMARY KEY(sheet_id,row_id,point_id)
        )""",
        """CREATE TABLE worksheet_position_conflicts (
            sheet_id TEXT NOT NULL REFERENCES business_worksheets(id),
            row_id TEXT NOT NULL, point_id TEXT NOT NULL, slot_id TEXT NOT NULL,
            registered_sheet_id TEXT,
            PRIMARY KEY(sheet_id,row_id,point_id)
        )""",
        """INSERT INTO schema_migrations(version,description)
            VALUES (15,'Stable worksheet result positions')""",
    ),
    14: (
        """CREATE TABLE sample_entities (
            id TEXT PRIMARY KEY, project TEXT NOT NULL REFERENCES workflow_projects(project),
            batch TEXT NOT NULL, material_stage TEXT NOT NULL, sample_type TEXT NOT NULL,
            presentation TEXT NOT NULL, packaging TEXT NOT NULL, purpose TEXT NOT NULL,
            source TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        "CREATE INDEX sample_entities_batch ON sample_entities(project,batch)",
        """CREATE TABLE coa_sources (
            id TEXT PRIMARY KEY, filename TEXT NOT NULL, source BLOB NOT NULL
        )""",
        """CREATE TABLE coa_versions (
            sample_id TEXT NOT NULL REFERENCES sample_entities(id),
            specification_id TEXT NOT NULL REFERENCES specifications(id),
            version INTEGER NOT NULL, values_json TEXT NOT NULL, changed_json TEXT NOT NULL,
            source_id TEXT REFERENCES coa_sources(id), actor_id TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(sample_id,specification_id,version)
        )""",
        "INSERT INTO schema_migrations(version,description) VALUES (14,'Sample identities and versioned COA release data')",
    ),
    13: (
        """CREATE TABLE worksheet_drafts (
            id TEXT PRIMARY KEY, project TEXT NOT NULL REFERENCES workflow_projects(project),
            name TEXT NOT NULL, draft_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        "CREATE INDEX worksheet_drafts_project ON worksheet_drafts(project,created_at)",
        "INSERT INTO schema_migrations(version,description) VALUES (13,'Incomplete worksheet plans')",
    ),
    12: (
        """CREATE TABLE specifications (
            id TEXT PRIMARY KEY, filename TEXT NOT NULL, source BLOB NOT NULL, preview_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        "INSERT INTO schema_migrations(version,description) VALUES (12,'Archived quality specifications')",
    ),
    10: WORKSHEET_SCHEMA,
    11: (
        """CREATE TABLE protocol_drafts (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            source BLOB NOT NULL,
            preview_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        "INSERT INTO schema_migrations(version,description) VALUES (11,'Archived stability protocol previews')",
    ),
    9: (
        'CREATE INDEX workflow_candidates_upload ON workflow_candidates(upload_id)',
        'CREATE INDEX workflow_events_subject_action ON workflow_events(subject_id,action,sequence)',
        'CREATE INDEX workflow_events_project_action ON workflow_events(project,action,sequence)',
        "INSERT INTO schema_migrations(version,description) VALUES (9,'Scoped candidate and event query indexes')",
    ),
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
            if next_version == 15:
                from .business_worksheets import backfill_worksheet_positions
                backfill_worksheet_positions(conn)
            conn.execute(f"PRAGMA user_version={next_version}")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
