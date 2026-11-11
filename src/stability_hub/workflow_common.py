"""Small shared helpers for workflow transactions and provenance."""

import json
import time
from uuid import uuid4

from .models import digest


class WorkflowError(ValueError):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.status = code, status


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def encoded(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def next_result_sequence(conn):
    """Order spreadsheet entry and file imports on one transactional clock."""
    return conn.execute(
        "UPDATE workflow_result_clock SET sequence=sequence+1 WHERE singleton=1 RETURNING sequence"
    ).fetchone()[0]


def ensure_worksheet_slot_unlocked(conn, slot_id):
    if conn.execute("""SELECT 1 FROM business_worksheet_slots s
                       JOIN business_worksheet_leases l ON l.sheet_id=s.sheet_id
                       WHERE s.slot_id=? AND l.expires_at>?""", (slot_id, int(time.time()))).fetchone():
        raise WorkflowError("worksheet_occupied", "此结果所在的研究工作表正在编辑，请待占用释放后再提交。", 409)


def project_row(conn, project):
    row = conn.execute("SELECT * FROM workflow_projects WHERE project=?", (project,)).fetchone()
    if row is None:
        raise WorkflowError("not_found", "项目不存在。", 404)
    return row


def check_revision(conn, project, expected):
    if project_row(conn, project)["revision"] != expected:
        raise WorkflowError("stale_revision", "项目数据或候选已变化，请刷新并重新核对。", 409)


def bump(conn, project):
    conn.execute("UPDATE workflow_projects SET revision=revision+1 WHERE project=?", (project,))
    return project_row(conn, project)["revision"]


def event(conn, project, subject, action, actor, reason, detail):
    conn.execute(
        """INSERT INTO workflow_events(project,subject_id,action,actor_id,reason,detail_json)
        VALUES (?,?,?,?,?,?)""", (project, subject, action, actor.id, reason, encoded(detail)),
    )


def replay(conn, project, actor, key, payload):
    request_hash = digest(payload)
    row = conn.execute(
        "SELECT * FROM workflow_requests WHERE project=? AND actor_id=? AND client_key=?",
        (project, actor.id, key),
    ).fetchone()
    if row:
        if row["request_hash"] != request_hash:
            raise WorkflowError("idempotency_conflict", "同一幂等键不能提交不同内容。", 409)
        return json.loads(row["response_json"])
    return None


def remember(conn, project, actor, key, payload, response):
    conn.execute("INSERT INTO workflow_requests VALUES (?,?,?,?,?)",
                 (project, actor.id, key, digest(payload), encoded(response)))
