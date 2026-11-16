"""Explicit scoped rules; versions are frozen into each new upload's corrections."""

import json

from .matrix_fields import interpret_raw
from .models import ProjectConfig, RawObservation, digest
from .permissions import Capability, require
from .workflow_common import (
    WorkflowError,
    bump,
    check_revision,
    encoded,
    event,
    project_row,
    replay,
    remember,
)
from .workflow_contracts import MutationResult


def source_signature(raw, *, interpreted=None):
    f = (
        interpreted
        if interpreted is not None
        else interpret_raw(raw, raw.fields)
        if raw.adapter == "stability_matrix"
        else raw.fields
    )
    return {
        "source_name": f.get("test_item", ""),
        "method": f.get("method", ""),
        "method_version": f.get("method_version", ""),
        "unit": f.get("unit", ""),
        "subresult": raw.subresult_hint,
    }


def list_rules(conn, project):
    project_row(conn, project)
    retired = {
        json.loads(r[0])["rule_version"]
        for r in conn.execute(
            "SELECT detail_json FROM workflow_events WHERE project=? AND action='retire_project_rule'",
            (project,),
        )
    }
    rules = {}
    for row in conn.execute(
        "SELECT detail_json,actor_id FROM workflow_events WHERE project=? AND action='save_project_rule' ORDER BY sequence",
        (project,),
    ):
        data = json.loads(row["detail_json"])
        rules[data["rule_version"]] = {
            **data,
            "retired": data["rule_version"] in retired,
            "saved_by": row["actor_id"],
        }
    return list(rules.values())


def rule_corrections(conn, project, raw):
    signature = source_signature(raw)
    matches = []
    retired = {
        json.loads(r[0])["rule_version"]
        for r in conn.execute(
            "SELECT detail_json FROM workflow_events WHERE project=? AND action='retire_project_rule'",
            (project,),
        )
    }
    for r in conn.execute(
        "SELECT sequence,detail_json FROM workflow_events WHERE project=? AND action='save_project_rule' ORDER BY sequence",
        (project,),
    ):
        data = json.loads(r["detail_json"])
        if data["signature"] == signature and data["rule_version"] not in retired:
            matches.append((r["sequence"], data))
    dimensions = ("id", "method", "method_version", "unit", "result_type")
    if len({encoded({k: d["target"][k] for k in dimensions}) for _, d in matches}) > 1:
        return {}, ("项目规则存在同范围不同目标碰撞，须明确文件映射。",), {}
    if matches:
        _, matched = matches[-1]
        current = ProjectConfig.model_validate_json(project_row(conn, project)["config_json"])
        item = next((i for i in current.items if i.id == matched["target"]["id"]), None)
        if item is None or any(getattr(item, k) != matched["target"][k] for k in dimensions):
            return {}, ("项目规则目标方法/版本/单位已变化，请明确文件映射或停用后重建规则。",), {}
        return (
            {"test_item": matched["target"]["id"]},
            (),
            {"rule_version": matched["rule_version"], "target": matched["target"]},
        )
    return {}, (), {}


def rule_impact(conn, project, request):
    upload = conn.execute(
        "SELECT * FROM workflow_uploads WHERE id=? AND project=?", (request.upload_id, project)
    ).fetchone()
    row = conn.execute(
        "SELECT * FROM workflow_candidates WHERE id=? AND upload_id=?",
        (request.candidate_id, request.upload_id),
    ).fetchone()
    if not upload or not row:
        raise WorkflowError("unknown_source", "必须选择本项目的来源映射。")
    raw = RawObservation.model_validate_json(row["raw_json"])
    signature = source_signature(raw)
    expected = {k: getattr(request, k) for k in signature}
    if signature != expected:
        raise WorkflowError(
            "rule_scope_changed", "规则范围必须与来源名称、方法、版本、单位和子项完全一致。", 409
        )
    config = ProjectConfig.model_validate_json(project_row(conn, project)["config_json"])
    item = next((i for i in config.items if i.id == request.item_id), None)
    if not item or json.loads(row["corrections_json"]).get("test_item") != item.id:
        raise WorkflowError("unconfirmed_mapping", "先保存本文件明确映射，再单独保存项目规则。")
    drafts = []
    for c in conn.execute(
        "SELECT id,upload_id,raw_json FROM workflow_candidates WHERE project=? AND lifecycle='draft'",
        (project,),
    ):
        if source_signature(RawObservation.model_validate_json(c["raw_json"])) == signature:
            drafts.append({"candidate_id": c["id"], "upload_id": c["upload_id"], "automatic_change": False})
    return {
        "signature": signature,
        "target": item.model_dump(mode="json"),
        "drafts": drafts,
        "rule_version": digest([signature, item.model_dump(mode="json")]),
    }


def save_rule(flow, project, request, *, actor):
    require(actor, Capability.CONFIRM_FACT)
    with flow.store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        payload = {"save_rule": request.model_dump(mode="json")}
        prior = replay(conn, project, actor, request.idempotency_key, payload)
        if prior:
            return MutationResult.model_validate(prior)
        check_revision(conn, project, request.base_revision)
        impact = rule_impact(conn, project, request)
        event(conn, project, request.item_id, "save_project_rule", actor, request.reason, impact)
        result = MutationResult(project=project, base_revision=bump(conn, project))
        remember(conn, project, actor, request.idempotency_key, payload, result.model_dump(mode="json"))
        return result


def reinterpret_preview(flow, conn, upload, actor):
    before = flow._preview(conn, upload, actor)
    conn.execute("SAVEPOINT reinterpret_preview")
    try:
        from .numeric_formats import VERSION as numeric_version
        from .matrix_fields import IDENTITY_VERSION
        from .normalize import SEMANTIC_VERSION
        from .standards import VERSION as standards_version

        event(
            conn,
            upload["project"],
            upload["id"],
            "reinterpret_draft",
            actor,
            "只读规则差异预览；事务回滚",
            {"numeric_rule_version": numeric_version, "identity_rule_version": IDENTITY_VERSION,
             "semantic_rule_version": SEMANTIC_VERSION, "standards_rule_version": standards_version, "preview_only": True},
        )
        conn.execute(
            "UPDATE workflow_uploads SET interpretation_config_json=? WHERE id=?",
            (project_row(conn, upload["project"])["config_json"], upload["id"]),
        )
        updates, conflicts = [], []
        for row in conn.execute(
            "SELECT * FROM workflow_candidates WHERE upload_id=? AND lifecycle='draft'", (upload["id"],)
        ).fetchall():
            fields, hazards, trace = rule_corrections(
                conn, upload["project"], RawObservation.model_validate_json(row["raw_json"])
            )
            manual = False
            for e in conn.execute(
                "SELECT action,detail_json FROM workflow_events WHERE subject_id=?", (row["id"],)
            ):
                detail = json.loads(e["detail_json"])
                if (
                    detail.get("file_mapping")
                    or (
                        detail.get("after", {}).get("test_item") is not None
                        and detail["after"].get("test_item") != detail.get("before", {}).get("test_item")
                    )
                    or detail.get("fields", {}).get("test_item")
                ):
                    manual = True
            if manual:
                continue
            if hazards:
                conflicts.append({"candidate_id": row["id"], "hazards": hazards})
            if fields:
                prior = json.loads(row["corrections_json"])
                after_fields = {**prior, **fields}
                if after_fields != prior:
                    updates.append({"candidate_id": row["id"], "after": after_fields, "trace": trace})
                    conn.execute(
                        "UPDATE workflow_candidates SET corrections_json=? WHERE id=?",
                        (encoded(after_fields), row["id"]),
                    )
        from .standard_upgrade import project as project_standards
        structures, structure_exceptions = project_standards(flow, conn, upload, actor, reason='只读标准投影预览；事务回滚')
        after = flow._preview(conn, flow._upload(conn, upload["id"]), actor)
        old = {c.id:c for c in before.candidates}
        new = {c.id:c for c in after.candidates}
        differences = [{'candidate_id':cid, 'before':old[cid].model_dump(mode='json') if cid in old else None,
                        'after':new[cid].model_dump(mode='json') if cid in new else None}
                       for cid in dict.fromkeys([*old,*new]) if old.get(cid) != new.get(cid)]

        def meaning(candidate):
            if candidate is None:
                return None
            record = candidate.record
            value = (
                (
                    record.result.status,
                    str(record.result.value),
                    record.result.unit,
                    record.result.comparator,
                    record.result.interpretation.get(
                        "display_text",
                        str(record.result.value)
                        if record.result.value is not None
                        else record.result.raw_text,
                    ),
                )
                if record
                else None
            )
            return (
                record.key.model_dump() if record else None,
                value,
                [(i.code, i.source_kind, i.fields, i.detail) for i in candidate.issues],
                candidate.status, candidate.lifecycle,
                record.standards if record else None,
            )

        semantic = [change for change in differences if meaning(old.get(change['candidate_id'])) != meaning(new.get(change['candidate_id']))]
        body = {
            "base_revision": before.base_revision,
            "changes": differences,
            "numeric_rule_version": numeric_version,
            "identity_rule_version": IDENTITY_VERSION,
            "semantic_rule_version": SEMANTIC_VERSION,
            "standards_rule_version": standards_version,
            "structural_changes": structures,
            "structural_exceptions": structure_exceptions,
            "semantic_changes": semantic,
            "metadata_only_count": len(differences) - len(semantic),
            "config": project_row(conn, upload["project"])["config_json"],
            "rule_updates": updates,
            "rule_conflicts": conflicts,
        }
        return {**body, "fingerprint": digest(body)}
    finally:
        conn.execute("ROLLBACK TO reinterpret_preview")
        conn.execute("RELEASE reinterpret_preview")


def reinterpret(flow, upload_id, request, *, actor):
    require(actor, Capability.CONFIRM_FACT)
    with flow.store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        upload = flow._upload(conn, upload_id)
        project = upload["project"]
        payload = {"reinterpret": upload_id, **request.model_dump(mode="json")}
        prior = replay(conn, project, actor, request.idempotency_key, payload)
        if prior:
            return MutationResult.model_validate(prior)
        check_revision(conn, project, request.base_revision)
        preview = reinterpret_preview(flow, conn, upload, actor)
        if preview["fingerprint"] != request.fingerprint:
            raise WorkflowError("stale_scope", "解释预览已变化，请刷新。", 409)
        if preview["rule_conflicts"]:
            raise WorkflowError("rule_conflict", "新规则存在碰撞，先明确文件映射或停用冲突规则。", 409)
        conn.execute(
            "UPDATE workflow_uploads SET interpretation_config_json=? WHERE id=?",
            (preview["config"], upload_id),
        )
        for update in preview["rule_updates"]:
            conn.execute(
                "UPDATE workflow_candidates SET corrections_json=? WHERE id=?",
                (encoded(update["after"]), update["candidate_id"]),
            )
            event(
                conn,
                project,
                update["candidate_id"],
                "apply_project_rule",
                actor,
                request.reason,
                update["trace"],
            )
        event(conn, project, upload_id, 'reinterpret_draft', actor, request.reason, preview)
        from .standard_upgrade import project as project_standards
        structures, exceptions = project_standards(flow, conn, upload, actor, reason=request.reason)
        if structures != preview['structural_changes'] or exceptions != preview['structural_exceptions']:
            raise WorkflowError('stale_scope','标准投影范围发生变化，未保存，请重新预览。',409)
        conn.execute(
            "UPDATE workflow_candidates SET revision=revision+1 WHERE upload_id=? AND lifecycle='draft'",
            (upload_id,),
        )
        result = MutationResult(project=project, base_revision=bump(conn, project))
        remember(conn, project, actor, request.idempotency_key, payload, result.model_dump(mode="json"))
        return result


def retire_rule(flow, project, request, *, actor):
    require(actor, Capability.CONFIRM_FACT)
    with flow.store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        payload = {"retire_rule": request.model_dump(mode="json")}
        prior = replay(conn, project, actor, request.idempotency_key, payload)
        if prior:
            return MutationResult.model_validate(prior)
        check_revision(conn, project, request.base_revision)
        rules = [
            json.loads(r[0])
            for r in conn.execute(
                "SELECT detail_json FROM workflow_events WHERE project=? AND action='save_project_rule'",
                (project,),
            )
        ]
        if not any(d["rule_version"] == request.rule_version for d in rules):
            raise WorkflowError("unknown_rule", "项目规则版本不存在。")
        event(
            conn,
            project,
            request.rule_version,
            "retire_project_rule",
            actor,
            request.reason,
            request.model_dump(mode="json"),
        )
        result = MutationResult(project=project, base_revision=bump(conn, project))
        remember(conn, project, actor, request.idempotency_key, payload, result.model_dump(mode="json"))
        return result
