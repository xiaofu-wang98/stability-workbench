"""Persistent plans and leases for business worksheets."""

SCHEMA = (
    """CREATE TABLE business_worksheets (
        id TEXT PRIMARY KEY,
        project TEXT NOT NULL REFERENCES workflow_projects(project),
        name TEXT NOT NULL,
        identity_json TEXT NOT NULL,
        start_date TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0),
        plan_version INTEGER NOT NULL DEFAULT 1 CHECK(plan_version > 0),
        layout_json TEXT NOT NULL DEFAULT '{}',
        UNIQUE(project, identity_json)
    )""",
    "CREATE INDEX business_worksheets_project ON business_worksheets(project, name)",
    """CREATE TABLE business_worksheet_slots (
        slot_id TEXT PRIMARY KEY,
        sheet_id TEXT NOT NULL REFERENCES business_worksheets(id)
    )""",
    """CREATE TABLE business_worksheet_plans (
        sheet_id TEXT NOT NULL REFERENCES business_worksheets(id),
        version INTEGER NOT NULL CHECK(version > 0),
        plan_json TEXT NOT NULL,
        basis TEXT NOT NULL,
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(sheet_id, version)
    )""",
    """CREATE TABLE business_worksheet_leases (
        sheet_id TEXT PRIMARY KEY REFERENCES business_worksheets(id),
        actor_id TEXT NOT NULL,
        actor_name TEXT NOT NULL,
        token_hash TEXT NOT NULL,
        expires_at INTEGER NOT NULL
    )""",
    """CREATE TABLE workflow_result_clock (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        sequence INTEGER NOT NULL CHECK(sequence>=0)
    )""",
    """INSERT INTO workflow_result_clock
        SELECT 1, max(
            coalesce((SELECT seq FROM sqlite_sequence WHERE name='workflow_uploads'), 0),
            coalesce((SELECT max(upload_sequence) FROM workflow_versions), 0)
        )""",
    """INSERT INTO schema_migrations(version,description)
        VALUES (10,'Business worksheet plans, leases and shared result ordering')""",
)
