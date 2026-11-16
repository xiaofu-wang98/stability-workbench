"""Explicit, auditable structural reinterpretation of the preserved source file."""

import hashlib
import json
from pathlib import Path

from .models import digest
from .permissions import Capability, require
from .workflow_common import WorkflowError, bump, check_revision, event, project_row, replay, remember
from .workflow_contracts import MutationResult

VERSION = "v53-structure-AR-note-1"


def preview(flow, conn, upload_id):
    from .parsing import parse_source
    from .worksheet_regions import coverage

    upload = flow._upload(conn, upload_id)
    if not upload["filename"].lower().endswith(".xlsx"):
        raise WorkflowError("source_type", "本次结构解释只适用于Excel工作表。")
    path = Path(
        conn.execute("SELECT stored_path FROM documents WHERE sha256=?", (upload["source_hash"],)).fetchone()[
            0
        ]
    )
    if hashlib.sha256(path.read_bytes()).hexdigest() != upload["source_hash"]:
        raise WorkflowError("source_changed", "冻结来源校验失败。", 409)
    fresh = parse_source(path, allow_partial=True)
    manual = set()
    for row in conn.execute(
        "SELECT action,detail_json FROM workflow_events WHERE subject_id=? AND action IN ('classify_region','classify_source_sheet')",
        (upload_id,),
    ):
        data = json.loads(row["detail_json"])
        manual.update(data["region_ids"] if row["action"] == "classify_source_sheet" else [data["region_id"]])
    known = {
        (s["section"], coord): (r["role"], r["basis"])
        for s in fresh.coverage
        for r in s["regions"]
        if r["role"] not in {"unclassified", "result", "excluded"}
        for coord in r["coordinates"]
    }
    changes = []
    for sheet in coverage(conn, upload):
        committed = conn.execute(
            "SELECT 1 FROM workflow_events WHERE subject_id=? AND action='confirm_sheet' AND json_extract(detail_json,'$.section')=?",
            (upload_id, sheet["section"]),
        ).fetchone()
        if committed:
            continue
        for region in sheet["regions"]:
            coordinates = region["coordinates"]
            if region["id"] in manual or region["role"] != "unclassified" or not coordinates:
                continue
            selected = [c for c in coordinates if (sheet["section"], c) in known]
            if not selected:
                continue
            facts = [known[sheet["section"], c] for c in selected]
            roles = {r for r, _ in facts}
            changes.append(
                {
                    "region_id": region["id"],
                    "section": sheet["section"],
                    "coordinates": selected,
                    "remaining_coordinates": [c for c in coordinates if c not in selected],
                    "role": next(iter(roles)) if len(roles) == 1 else "auxiliary",
                    "cell_roles": {c: known[sheet["section"], c][0] for c in selected},
                    "reason": "冻结原件重新解析的非结果用途证据；" + VERSION,
                    "region_revision": region["revision"],
                }
            )
    revision = project_row(conn, upload["project"])["revision"]
    return {
        "version": VERSION,
        "base_revision": revision,
        "changes": changes,
        "fingerprint": digest([upload_id, upload["source_hash"], VERSION, revision, changes]),
        "notice": "仅更新所列未明区域的来源用途；保留候选、人工决定和正式值。其余争议仍须原表核对。",
    }


def apply(flow, upload_id, command, *, actor):
    require(actor, Capability.CONFIRM_FACT)
    with flow.store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        upload = flow._upload(conn, upload_id)
        payload = {"source_structure": upload_id, **command.model_dump(mode="json")}
        prior = replay(conn, upload["project"], actor, command.idempotency_key, payload)
        if prior:
            return MutationResult.model_validate(prior)
        check_revision(conn, upload["project"], command.base_revision)
        info = preview(flow, conn, upload_id)
        if info["fingerprint"] != command.fingerprint:
            raise WorkflowError("preview_changed", "来源用途预览已变化，请重新核对。", 409)
        if not info["changes"]:
            raise WorkflowError("empty_scope", "没有可更新的未明来源区域。")
        event(conn, upload["project"], upload_id, "source_structure", actor, command.reason, info)
        result = MutationResult(project=upload["project"], base_revision=bump(conn, upload["project"]))
        remember(
            conn, upload["project"], actor, command.idempotency_key, payload, result.model_dump(mode="json")
        )
        return result
