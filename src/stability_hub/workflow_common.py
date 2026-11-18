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


def ensure_worksheet_slot_unlocked(conn, slot_id, *, except_sheet_id=None):
    if conn.execute("""SELECT 1 FROM business_worksheet_slots s
                       JOIN business_worksheet_leases l ON l.sheet_id=s.sheet_id
                       WHERE s.slot_id=? AND l.expires_at>?
                         AND (? IS NULL OR s.sheet_id<>?)""",
                    (slot_id, int(time.time()), except_sheet_id, except_sheet_id)).fetchone():
        raise WorkflowError("worksheet_occupied", "此结果所在的研究工作表正在编辑，请待占用释放后再提交。", 409)


def resolve_canonical_project(conn, project: str) -> str:
    """P0-2: 将输入的项目编号或别名解析为标准项目主键，支持去空格、不区分大小写及别名映射。"""
    if not project:
        return ""
    p_clean = project.strip()
    # 1. 精确匹配
    row = conn.execute("SELECT project FROM workflow_projects WHERE project=?", (p_clean,)).fetchone()
    if row:
        return row["project"]
    # 2. 忽略大小写匹配
    row = conn.execute("SELECT project FROM workflow_projects WHERE UPPER(project)=UPPER(?)", (p_clean,)).fetchone()
    if row:
        return row["project"]
    # 3. 别名表匹配
    try:
        row = conn.execute("SELECT canonical_project FROM project_aliases WHERE UPPER(alias)=UPPER(?)", (p_clean,)).fetchone()
        if row:
            return row["canonical_project"]
    except Exception:
        pass
    # 4. 常见 IP 前缀别名容错匹配（如输入 501 自动解析为 IP501，或输入 IP501 解析为 501）
    if p_clean.isdigit():
        row = conn.execute("SELECT project FROM workflow_projects WHERE UPPER(project)=UPPER(?)", ("IP" + p_clean,)).fetchone()
        if row:
            return row["project"]
    elif p_clean.upper().startswith("IP") and p_clean[2:].isdigit():
        row = conn.execute("SELECT project FROM workflow_projects WHERE project=?", (p_clean[2:],)).fetchone()
        if row:
            return row["project"]
    return p_clean


def project_row(conn, project):
    canonical = resolve_canonical_project(conn, project)
    row = conn.execute("SELECT * FROM workflow_projects WHERE project=?", (canonical,)).fetchone()
    if row is None:
        raise WorkflowError("not_found", "项目不存在。", 404)
    return row


def unique_project_code(conn, project):
    """Resolve external project input and reject ambiguous legacy identities."""
    code = project.strip().upper()
    counterpart = "IP" + code if code.isascii() and code.isdecimal() else code
    if code.startswith("IP") and code[2:].isascii() and code[2:].isdecimal():
        counterpart = code[2:]
    candidates = {row[0] for row in conn.execute("""
        SELECT project FROM workflow_projects WHERE UPPER(project) IN (?,?)
        UNION
        SELECT canonical_project FROM project_aliases WHERE UPPER(alias) IN (?,?)
    """, (code, counterpart, code, counterpart))}
    if len(candidates) > 1:
        # A recorded reconciliation can keep the old project and its immutable audit history.
        resolved = {row[0] for row in conn.execute("""
            SELECT canonical_project FROM project_aliases
            WHERE UPPER(alias) IN (?,?) AND UPPER(alias) != UPPER(canonical_project)
        """, (code, counterpart))}
        if len(resolved) == 1:
            canonical = next(iter(resolved))
            if len(candidates) == 2 and {candidate.upper() for candidate in candidates} == {code, counterpart} and canonical in candidates:
                return canonical
        codes = "、".join(sorted(candidates))
        raise WorkflowError("project_identity_conflict", f"项目编号匹配多个既有项目（{codes}）。请先核对归属，再使用该项目。", 409)
    if candidates:
        return next(iter(candidates))
    return project_row(conn, project)["project"]


def active_project_code(conn, project):
    """Keep unreconciled projects visible and omit explicitly superseded codes."""
    try:
        return unique_project_code(conn, project) == project
    except WorkflowError as exc:
        if exc.code != "project_identity_conflict":
            raise
        return True


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


def replay(conn, project, actor, key, payload, *, legacy_payload=None):
    request_hash = digest(payload)
    row = conn.execute(
        "SELECT * FROM workflow_requests WHERE project=? AND actor_id=? AND client_key=?",
        (project, actor.id, key),
    ).fetchone()
    if row:
        if row["request_hash"] != request_hash and (
            legacy_payload is None or row["request_hash"] != digest(legacy_payload)
        ):
            raise WorkflowError("idempotency_conflict", "同一幂等键不能提交不同内容。", 409)
        return json.loads(row["response_json"])
    return None


def remember(conn, project, actor, key, payload, response):
    conn.execute("INSERT INTO workflow_requests VALUES (?,?,?,?,?)",
                 (project, actor.id, key, digest(payload), encoded(response)))
