SCHEMA = (
    '''CREATE TABLE source_imports (id TEXT PRIMARY KEY, project TEXT NOT NULL REFERENCES workflow_projects(project),
       document_hash TEXT NOT NULL REFERENCES documents(sha256), filename TEXT NOT NULL,
       package_json TEXT NOT NULL, request_hash TEXT NOT NULL, created_by TEXT NOT NULL, client_key TEXT NOT NULL,
       created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(project,created_by,client_key))''',
    "CREATE TRIGGER source_imports_immutable BEFORE UPDATE ON source_imports BEGIN SELECT RAISE(ABORT,'immutable source'); END",
    "CREATE TRIGGER source_imports_no_delete BEFORE DELETE ON source_imports BEGIN SELECT RAISE(ABORT,'immutable source'); END",
    "INSERT INTO schema_migrations(version,description) VALUES (4,'Immutable source table archives')",
)
