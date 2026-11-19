"""File-local, explicitly selected source decisions; never performs formal adoption."""

import json
from collections import Counter

from .models import digest
from .permissions import Capability, require
from .review_tasks import current_review
from .review_issues import resolve_issue
from .v5_contracts import IssueDecision
from .workflow_common import (
    WorkflowError,
    bump,
    check_revision,
    encoded,
    event,
    project_row,
    replay,
    remember,
    uid,
)


def preview_batch(flow, conn, upload_id, scope, actor):
    upload = flow._upload(conn, upload_id)
    p, sheets, flights, tasks, _ = current_review(flow, conn, upload, actor, _shared=True)
    by_id = {t.id: t for t in tasks}
    if len(set(scope.task_ids)) != len(scope.task_ids) or any(tid not in by_id for tid in scope.task_ids):
        raise WorkflowError("task_changed", "所选来源问题已变化，请刷新原表。", 409)
    sections = {scope.section, *scope.include_sections}
    if not sections <= {s["section"] for s in sheets}:
        raise WorkflowError("scope_outside", "所选工作表不属于本文件。")
    selected = [by_id[tid] for tid in scope.task_ids]
    candidates = {c.id: c for c in p.candidates}
    if scope.operation == "exclude_selected":
        from .v5_contracts import ReviewTask

        if not scope.candidate_ids or len(set(scope.candidate_ids)) != len(scope.candidate_ids):
            raise WorkflowError("empty_selection", "请先选择明确的原表行或结果。")
        selected = []
        grouped = {}
        for cid in scope.candidate_ids:
            c = candidates.get(cid)
            if not c or c.lifecycle != "draft" or c.location.section not in sections:
                raise WorkflowError("scope_outside", "所选来源已变化或不属于明确范围。", 409)
            grouped.setdefault((c.location.section, c.logical_row or c.location.row), []).append(c)
        for (section, row), members in grouped.items():
            selected.append(
                ReviewTask(
                    id=digest(["selected", section, row, sorted(c.id for c in members)]),
                    code="selected_exclusion",
                    section=section,
                    detail=f"本次明确不采用第 {row} 行所选 {len(members)} 个来源结果；历史值保留",
                    evidence=tuple(c.location for c in members),
                    members=tuple(
                        {"candidate_id": c.id, "candidate_revision": c.candidate_revision} for c in members
                    ),
                    actions=("exclude",),
                    proposal={"eligible": True, "operation": "exclude"},
                    base_revision=p.base_revision,
                    batch_key="selected",
                )
            )
    elif scope.candidate_ids or not selected:
        raise WorkflowError("empty_scope", "请先选择待核对内容。")
    members_in_scope = {m["candidate_id"] for t in selected for m in t.members if "candidate_id" in m}
    if not set(scope.exclude_candidate_ids) <= members_in_scope:
        raise WorkflowError("scope_outside", "例外成员必须属于明确所选来源范围。")
    if scope.operation == "manual" and "result" in scope.fields and len(members_in_scope - set(scope.exclude_candidate_ids)) > 1:
        raise WorkflowError("shared_result", "不能将同一人工结果复制到不同检测格，请逐格核对。")
    if scope.operation not in {"suggested", "classify"} and len({t.batch_key for t in selected}) > 1:
        raise WorkflowError(
            "mixed_semantics", "本次只能处理相同字段语义和修改方式；标准、结果和全删除须分别预览。"
        )
    plans, changes, exceptions = [], [], []
    for task in selected:
        affected = {
            candidates[m["candidate_id"]].location.section for m in task.members if "candidate_id" in m
        }
        if not affected <= sections or task.section not in sections:
            raise WorkflowError("scope_outside", "跨表范围须由用户明确选择，本次未执行。")
        reason = None
        fields = scope.fields
        operation = task.proposal.get("operation") if scope.operation == "suggested" else scope.operation
        kind = (
            "decision"
            if task.code == "selected_exclusion"
            else "edit"
            if task.code == "identity"
            else "region"
            if task.code == "region"
            else "issue"
        )
        if kind == "region":
            role = scope.role or (task.proposal.get("role") if task.proposal else None) or "auxiliary"
            for member in task.members:
                plans.append({
                    **member,
                    "kind": "region",
                    "action": "classify",
                    "role": role,
                    "task_id": task.id,
                    "section": task.section,
                })
            changes.append({
                "task_id": task.id,
                "section": task.section,
                "fields": {"role": {"before": "unclassified", "after": role}},
                "operation": "classify",
                "explanation": task.proposal.get("reason", task.detail),
                "evidence": [e.model_dump(mode="json") for e in task.evidence],
                "affected_results": 0,
            })
            continue
        if kind == "decision":
            operation = "exclude"
            fields = {}
        elif kind == "edit" and operation == "manual":
            from .bulk_drafts import preview as edit_preview
            from .v5_contracts import BulkPreview

            if not fields or set(fields) - set(task.fields):
                raise WorkflowError("issue_scope", "请仅填写当前共享来源缺少的字段。")
            edit_preview(
                flow,
                conn,
                upload_id,
                BulkPreview(candidate_ids=tuple(m["candidate_id"] for m in task.members), fields=fields),
            )
        elif not task.members or any("issue_id" not in m for m in task.members):
            reason = "请在原表补充身份/范围或办理历史处置，此任务不能使用来源批量决定"
        elif operation in {"keep_active", "exclude"}:
            if not task.proposal.get("eligible") or task.proposal.get("operation") != operation:
                reason = task.proposal.get("reason", "该来源不满足此修改方式的前提")
            elif task.exception:
                reason = task.exception
            fields = (
                (
                    {k: v["after"] for k, v in task.proposal["fields"].items()}
                    if task.proposal.get("fields")
                    else {task.fields[0]: task.proposal.get("after", "")}
                )
                if operation == "keep_active"
                else {}
            )
        elif operation == "manual":
            if not fields or set(fields) - set(task.fields):
                raise WorkflowError("issue_scope", "只允许填写当前争议字段。")
        elif operation == "keep_body":
            fields = {}
            if "keep_body" not in task.actions:
                reason = "该修改形态需要明确新值或删除范围，不能笼统保留正文"
        elif operation == 'keep_single':
            if scope.fields:
                raise WorkflowError('issue_scope', '排版沿用不能同时改写结果或身份字段。')
            fields = {}
            if task.code != 'structure' or task.proposal.get('operation') != 'keep_single' or not task.proposal.get('eligible'):
                reason = '该来源没有明确的单一结果排版证据，请按真实逻辑结果解释。'
        else:
            reason = "本处没有可直接采用的建议，请单独明确当前解释。"
        if reason:
            exceptions.append(
                {
                    "task_id": task.id,
                    "section": task.section,
                    "reason": reason,
                    "evidence": [e.model_dump(mode="json") for e in task.evidence],
                }
            )
            continue
        for member in task.members:
            if member["candidate_id"] in scope.exclude_candidate_ids:
                continue
            member_fields = fields
            if member.get("source_field"):
                member_fields = (
                    {member["source_field"]: fields[member["source_field"]]}
                    if member["source_field"] in fields
                    else {}
                )
            plans.append(
                {**member, "kind": kind, "action": operation, "fields": member_fields, "task_id": task.id}
            )
        values = {
            field: {
                "before": task.proposal.get(
                    "before", candidates[task.members[0]["candidate_id"]].raw_fields.get(field, "")
                ),
                "after": value,
            }
            for field, value in fields.items()
        }
        applied_ids = {m["candidate_id"] for m in task.members} - set(scope.exclude_candidate_ids)
        if not applied_ids:
            continue
        changes.append(
            {
                "task_id": task.id,
                "section": task.section,
                "fields": values,
                "operation": operation,
                "explanation": task.proposal.get("reason", task.detail),
                "evidence": [e.model_dump(mode="json") for e in task.evidence],
                "affected_results": len(applied_ids),
                "source_revisions": {
                    field: candidates[task.members[0]["candidate_id"]].revisions[field]
                    for field in task.fields
                    if field in candidates[task.members[0]["candidate_id"]].revisions
                },
                "retained_results": [
                    {'candidate_id': cid, 'location': candidates[cid].location.model_dump(mode='json'),
                     'range': candidates[cid].result_anchor.get('range'),
                     'fields': {k: candidates[cid].corrections.get(k, candidates[cid].interpreted_fields.get(k, candidates[cid].raw_fields.get(k, '')))
                                for k in ('test_item', 'method', 'time_point', 'result', 'unit', 'batch', 'condition')},
                     'source_value': candidates[cid].source_value}
                    for cid in sorted(applied_ids, key=lambda cid: (
                        candidates[cid].location.section, candidates[cid].location.row, candidates[cid].location.column))
                ] if operation == 'keep_single' else [],
                "review_results": [
                    {'candidate_id': cid, 'location': candidates[cid].location.model_dump(mode='json'),
                     'logical_row': candidates[cid].logical_row,
                     'original_result': candidates[cid].raw_fields.get('result', ''),
                     'current_result': candidates[cid].corrections.get('result', candidates[cid].interpreted_fields.get('result', candidates[cid].raw_fields.get('result', ''))),
                     'time_point': candidates[cid].interpreted_fields.get('time_point', candidates[cid].raw_fields.get('time_point', '')),
                     'result_changed': 'result' in fields}
                    for cid in sorted(applied_ids)
                ] if task.proposal.get('guidance') or task.code in {'identity_conflict', 'method_conflict'} else [],
            }
        )
    for change in changes:
        if change['operation'] == 'keep_single':
            from .structural_review import projection_seed
            for retained in change['retained_results']:
                seed = projection_seed(flow, conn, upload_id, retained['candidate_id'], actor)
                retained['display_result'] = seed['fields'].get('result', '')
                retained['display_unit'] = seed['fields'].get('unit', '')
                retained['warnings'] = seed['warnings']
            retained = change['retained_results']
            change['common_fields'] = {k: retained[0]['fields'][k] for k in retained[0]['fields']
                                       if all(r['fields'].get(k) == retained[0]['fields'][k] for r in retained)
                                       and retained[0]['fields'][k]}
    # Source actions validate per member too; task summaries cannot relax the command.
    from .review_issues import preview_issue_batch
    from .v5_contracts import IssueBatchPreview, IssueSelection

    issue_plans = [plan for plan in plans if plan["kind"] == "issue"]
    if issue_plans:
        preview_issue_batch(
            flow,
            conn,
            upload_id,
            IssueBatchPreview(
                issues=tuple(
                    IssueSelection(
                        **{
                            k: plan[k]
                            for k in ["candidate_id", "candidate_revision", "issue_id", "action", "fields"]
                        }
                    )
                    for plan in issue_plans
                )
            ),
        )
    result_ids = {m["candidate_id"] for m in plans if "candidate_id" in m}
    by_sheet = Counter(candidates[cid].location.section for cid in result_ids)
    for m in plans:
        if m.get("kind") == "region" and m.get("section"):
            by_sheet.setdefault(m["section"], 0)
    summary = {
        "sources": len({(e["section"], e["row"], e["column"]) for c in changes for e in c["evidence"]}),
        "tasks": len(changes),
        "results": len(result_ids),
        "exceptions": len(exceptions),
        "by_sheet": dict(by_sheet),
        "remaining_independent_tasks": sum(
            bool(t.blockers) and any(b.split("|", 1)[0] in sections for b in t.blockers)
            for t in tasks
            if t.id not in {c["task_id"] for c in changes}
        ),
    }
    basis = {
        "scope": scope.model_dump(mode="json"),
        "plans": plans,
        "base_revision": p.base_revision,
        "changes": changes,
        "exceptions": exceptions,
    }
    return {
        "base_revision": p.base_revision,
        "fingerprint": digest(basis),
        "summary": summary,
        "changes": changes,
        "exceptions": exceptions,
        "members": plans,
        "notice": "仅保存明确范围的草稿决定；保存后重新检查身份碰撞及整表资格，不自动提交其他表。",
    }


def apply_batch(flow, upload_id, request, *, actor):
    require(actor, Capability.CONFIRM_FACT)
    with flow.store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        upload = flow._upload(conn, upload_id)
        project = upload["project"]
        payload = {"review_batch": upload_id, **request.model_dump(mode="json")}
        prior = replay(conn, project, actor, request.idempotency_key, payload)
        if prior:
            return prior
        check_revision(conn, project, request.base_revision)
        info = preview_batch(flow, conn, upload_id, request.scope, actor)
        if info["fingerprint"] != request.fingerprint:
            raise WorkflowError("stale_scope", "来源、例外或解释已变化，整批未保存。", 409)
        if not info["members"]:
            raise WorkflowError("no_applicable_members", "没有满足该处理方式的成员，请处理列出的例外。")
        batch_id = uid("draft_batch")
        # Frozen physical evidence is read once for the atomic scope. Each issue
        # still validates against its current row revision and effective decisions.
        issue_rows = {
            m["candidate_id"]: flow._candidate(conn, m["candidate_id"])
            for m in info["members"]
            if m["kind"] == "issue" and "candidate_id" in m
        }
        source_raws = flow._source_rows(conn, list(issue_rows.values()), upload) if issue_rows else {}
        before = {}
        for index, member in enumerate(info["members"]):
            if member["kind"] == "region":
                from .worksheet_regions import classify
                from .v5_contracts import RegionDecision

                classify(
                    flow,
                    upload_id,
                    RegionDecision(
                        base_revision=project_row(conn, project)["revision"],
                        idempotency_key=request.idempotency_key + ":" + str(index),
                        reason=request.reason,
                        region_id=member["region_id"],
                        role=member.get("role", "auxiliary"),
                    ),
                    actor=actor,
                    _conn=conn,
                    _batch_id=batch_id,
                )
                continue
            row = flow._candidate(conn, member["candidate_id"])
            before.setdefault(
                row["id"], {"corrections": json.loads(row["corrections_json"]), "lifecycle": row["lifecycle"]}
            )
            if member["kind"] == "edit":
                from .bulk_drafts import apply as apply_edit, preview as preview_edit
                from .v5_contracts import BulkApply, BulkPreview

                edit_scope = BulkPreview(candidate_ids=(row["id"],), fields=member["fields"])
                edit_info = preview_edit(flow, conn, upload_id, edit_scope)
                apply_edit(
                    flow,
                    upload_id,
                    BulkApply(
                        base_revision=project_row(conn, project)["revision"],
                        idempotency_key=request.idempotency_key + ":" + str(index),
                        reason=request.reason,
                        scope=edit_scope,
                        fingerprint=edit_info["fingerprint"],
                    ),
                    actor=actor,
                    _conn=conn,
                    batch_id=batch_id,
                )
            elif member["kind"] == "decision":
                from .worksheet_commit import save_decision
                from .v5_contracts import DraftDecision

                save_decision(
                    flow,
                    upload_id,
                    DraftDecision(
                        base_revision=project_row(conn, project)["revision"],
                        idempotency_key=request.idempotency_key + ":" + str(index),
                        reason=request.reason,
                        candidate_id=row["id"],
                        candidate_revision=row["revision"],
                        action="exclude",
                    ),
                    actor=actor,
                    _conn=conn,
                    _batch_id=batch_id,
                )
            else:
                resolve_issue(
                    flow,
                    upload_id,
                    IssueDecision(
                        candidate_id=row["id"],
                        candidate_revision=row["revision"],
                        base_revision=project_row(conn, project)["revision"],
                        idempotency_key=request.idempotency_key + ":" + str(index),
                        issue_id=member["issue_id"],
                        action=member["action"],
                        fields=member["fields"],
                        reason=request.reason,
                        batch_id=batch_id,
                    ),
                    actor=actor,
                    _conn=conn,
                    _raws=source_raws,
                )
        after = {
            cid: {
                "corrections": json.loads(flow._candidate(conn, cid)["corrections_json"]),
                "lifecycle": flow._candidate(conn, cid)["lifecycle"],
            }
            for cid in before
        }
        event(
            conn,
            project,
            upload_id,
            "review_batch",
            actor,
            request.reason,
            {
                "batch_id": batch_id,
                "scope": request.scope.model_dump(mode="json"),
                "before": before,
                "after": after,
                "summary": info["summary"],
                "regions": [m["region_id"] for m in info["members"] if m.get("kind") == "region"],
            },
        )
        _, _, flights, tasks, _ = current_review(flow, conn, upload, actor, _shared=True)
        sections = {request.scope.section, *request.scope.include_sections}
        remaining = {s: sum(any(b.startswith(s + "|") for b in t.blockers) for t in tasks) for s in sections}
        result = {
            "project": project,
            "base_revision": project_row(conn, project)["revision"],
            "batch_id": batch_id,
            "summary": info["summary"],
            "remaining_tasks": sum(remaining.values()),
            "remaining_by_sheet": remaining,
            "preflights": [f.model_dump(mode="json") for f in flights],
        }
        remember(conn, project, actor, request.idempotency_key, payload, result)
        return result


def undo_batch(flow, upload_id, request, *, actor):
    require(actor, Capability.CONFIRM_FACT)
    with flow.store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        upload = flow._upload(conn, upload_id)
        project = upload["project"]
        payload = {"undo_review_batch": upload_id, **request.model_dump(mode="json")}
        prior = replay(conn, project, actor, request.idempotency_key, payload)
        if prior:
            return prior
        check_revision(conn, project, request.base_revision)
        row = conn.execute(
            "SELECT detail_json, sequence FROM workflow_events WHERE subject_id=? AND action='review_batch' AND json_extract(detail_json,'$.batch_id')=?",
            (upload_id, request.batch_id),
        ).fetchone()
        if not row:
            raise WorkflowError("batch_missing", "本文件中没有该草稿批次。")
        data = json.loads(row[0])
        batch_seq = row[1]
        if conn.execute(
            "SELECT 1 FROM workflow_events WHERE subject_id=? AND action='undo_review_batch' AND json_extract(detail_json,'$.batch_id')=?",
            (upload_id, request.batch_id),
        ).fetchone():
            raise WorkflowError("already_undone", "该草稿批次已撤销。", 409)
        undone_batches = {
            json.loads(r[0])["batch_id"]
            for r in conn.execute(
                "SELECT detail_json FROM workflow_events WHERE subject_id=? AND action='undo_review_batch'",
                (upload_id,),
            )
        }
        for rid in data.get("regions", []):
            later = conn.execute(
                "SELECT detail_json FROM workflow_events WHERE subject_id=? AND action='classify_region' AND sequence > ? ORDER BY sequence DESC",
                (upload_id, batch_seq),
            ).fetchall()
            for later_row in later:
                ld = json.loads(later_row[0])
                if ld.get("region_id") == rid and ld.get("batch_id") not in undone_batches:
                    raise WorkflowError("later_change", "该区域其后已重新分类，请先核对当前解释。", 409)
        for cid, previous in data["before"].items():
            current = flow._candidate(conn, cid)
            if current["lifecycle"] == "submitted":
                raise WorkflowError("already_submitted", "已有正式结果，不能撤销草稿批次。", 409)
            changes = json.loads(current["corrections_json"])
            written = data["after"][cid]
            if previous["lifecycle"] != written["lifecycle"] and upload["uploaded_by"] != actor.id:
                require(actor, Capability.REVIEW_DUPLICATE)
            touched = {
                k
                for k in {*previous["corrections"], *written["corrections"]}
                if previous["corrections"].get(k) != written["corrections"].get(k)
            }
            if (
                any(changes.get(k) != written["corrections"].get(k) for k in touched)
                or current["lifecycle"] != written["lifecycle"]
            ):
                raise WorkflowError(
                    "later_change", "该字段/来源其后已改，请先核对当前解释；本次未覆盖后续修改。", 409
                )
            for field in touched:
                if field in previous["corrections"]:
                    changes[field] = previous["corrections"][field]
                else:
                    changes.pop(field, None)
            conn.execute(
                "UPDATE workflow_candidates SET corrections_json=?,lifecycle=?,revision=revision+1 WHERE id=?",
                (encoded(changes), previous["lifecycle"], cid),
            )
        event(
            conn,
            project,
            upload_id,
            "undo_review_batch",
            actor,
            request.reason,
            {"batch_id": request.batch_id},
        )
        result = {"project": project, "base_revision": bump(conn, project)}
        remember(conn, project, actor, request.idempotency_key, payload, result)
        return result
