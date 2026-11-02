"""Migration 3: workflow tables. Existing P0/P1 tables remain intact."""

STATEMENTS = (
    """CREATE TABLE workflow_projects (
        project TEXT PRIMARY KEY, config_json TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0))""",
    """CREATE TABLE workflow_uploads (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
        project TEXT NOT NULL REFERENCES workflow_projects(project),
        source_hash TEXT NOT NULL REFERENCES documents(sha256), filename TEXT NOT NULL,
        context_json TEXT NOT NULL, uploaded_by TEXT NOT NULL,
        uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, client_key TEXT NOT NULL, request_hash TEXT NOT NULL,
        UNIQUE(project,uploaded_by,client_key), UNIQUE(id,project))""",
    """CREATE TABLE workflow_detections (
        id TEXT PRIMARY KEY, project TEXT NOT NULL REFERENCES workflow_projects(project), source_number TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(id,project))""",
    """CREATE TABLE workflow_candidates (
        id TEXT PRIMARY KEY, project TEXT NOT NULL, upload_id TEXT NOT NULL,
        raw_json TEXT NOT NULL, corrections_json TEXT NOT NULL DEFAULT '{}',
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
        detection_id TEXT NOT NULL, acknowledge_warning INTEGER NOT NULL DEFAULT 0 CHECK(acknowledge_warning IN (0,1)),
        lifecycle TEXT NOT NULL DEFAULT 'draft' CHECK(lifecycle IN ('draft','submitted','withdrawn')),
        FOREIGN KEY(upload_id,project) REFERENCES workflow_uploads(id,project),
        FOREIGN KEY(detection_id,project) REFERENCES workflow_detections(id,project), UNIQUE(id,project))""",
    """CREATE TABLE workflow_versions (
        id TEXT PRIMARY KEY, project TEXT NOT NULL REFERENCES workflow_projects(project),
        slot_id TEXT NOT NULL, record_json TEXT NOT NULL, detection_id TEXT NOT NULL,
        candidate_id TEXT UNIQUE, source_json TEXT NOT NULL, upload_sequence INTEGER NOT NULL CHECK(upload_sequence>=0),
        intent TEXT NOT NULL CHECK(intent IN ('initial','repeat_source','retest','correction','legacy')),
        reason TEXT NOT NULL, references_json TEXT NOT NULL, submitted_by TEXT NOT NULL,
        submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, previous_version_id TEXT,
        FOREIGN KEY(detection_id,project) REFERENCES workflow_detections(id,project),
        FOREIGN KEY(candidate_id,project) REFERENCES workflow_candidates(id,project),
        UNIQUE(project,slot_id,id),
        FOREIGN KEY(project,slot_id,previous_version_id) REFERENCES workflow_versions(project,slot_id,id))""",
    "CREATE INDEX workflow_versions_slot ON workflow_versions(project,slot_id,upload_sequence)",
    """CREATE TABLE workflow_version_states (
        version_id TEXT PRIMARY KEY REFERENCES workflow_versions(id),
        state TEXT NOT NULL CHECK(state IN ('pending','confirmed','rejected','withdrawn','legacy')))""",
    """CREATE TABLE workflow_adoptions (
        project TEXT NOT NULL, slot_id TEXT NOT NULL, version_id TEXT NOT NULL,
        PRIMARY KEY(project,slot_id),
        FOREIGN KEY(project,slot_id,version_id) REFERENCES workflow_versions(project,slot_id,id))""",
    """CREATE TABLE workflow_events (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL REFERENCES workflow_projects(project),
        subject_id TEXT NOT NULL, action TEXT NOT NULL, actor_id TEXT NOT NULL, reason TEXT NOT NULL,
        detail_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE workflow_requests (
        project TEXT NOT NULL REFERENCES workflow_projects(project), actor_id TEXT NOT NULL,
        client_key TEXT NOT NULL, request_hash TEXT NOT NULL, response_json TEXT NOT NULL,
        PRIMARY KEY(project,actor_id,client_key))""",
    """CREATE TABLE workflow_exports (
        id TEXT PRIMARY KEY, project TEXT NOT NULL REFERENCES workflow_projects(project),
        revision INTEGER NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL, snapshot_json TEXT NOT NULL,
        created_by TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TRIGGER workflow_versions_immutable BEFORE UPDATE ON workflow_versions
        BEGIN SELECT RAISE(ABORT,'Result versions are immutable'); END""",
    """CREATE TRIGGER workflow_versions_no_delete BEFORE DELETE ON workflow_versions
        BEGIN SELECT RAISE(ABORT,'Result history cannot be deleted'); END""",
    """CREATE TRIGGER workflow_events_immutable BEFORE UPDATE ON workflow_events
        BEGIN SELECT RAISE(ABORT,'Decision history is immutable'); END""",
    """CREATE TRIGGER workflow_events_no_delete BEFORE DELETE ON workflow_events
        BEGIN SELECT RAISE(ABORT,'Decision history cannot be deleted'); END""",
    """CREATE TRIGGER workflow_adoption_insert BEFORE INSERT ON workflow_adoptions
        WHEN COALESCE((SELECT state FROM workflow_version_states WHERE version_id=NEW.version_id),'')
             NOT IN ('confirmed','legacy')
        BEGIN SELECT RAISE(ABORT,'Only confirmed results may be adopted'); END""",
    """CREATE TRIGGER workflow_adoption_update BEFORE UPDATE ON workflow_adoptions
        WHEN COALESCE((SELECT state FROM workflow_version_states WHERE version_id=NEW.version_id),'')
             NOT IN ('confirmed','legacy')
        BEGIN SELECT RAISE(ABORT,'Only confirmed results may be adopted'); END""",
    """CREATE TRIGGER workflow_state_adopted BEFORE UPDATE ON workflow_version_states
        WHEN NEW.state NOT IN ('confirmed','legacy') AND EXISTS
             (SELECT 1 FROM workflow_adoptions WHERE version_id=OLD.version_id)
        BEGIN SELECT RAISE(ABORT,'Cannot invalidate adopted result'); END""",
    """CREATE TRIGGER workflow_state_identity BEFORE UPDATE OF version_id ON workflow_version_states
        BEGIN SELECT RAISE(ABORT,'State identity is immutable'); END""",
    """CREATE TRIGGER workflow_state_history BEFORE DELETE ON workflow_version_states
        BEGIN SELECT RAISE(ABORT,'Result state history cannot be deleted'); END""",
    """CREATE TRIGGER workflow_state_final BEFORE UPDATE OF state ON workflow_version_states
        WHEN OLD.state IN ('confirmed','legacy','rejected','withdrawn') AND NEW.state<>OLD.state
        BEGIN SELECT RAISE(ABORT,'Final result states cannot be reversed'); END""",
    "INSERT INTO schema_migrations(version,description) VALUES (3,'Human-confirmed workflow and result versions')",
)
