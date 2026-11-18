"""Project the existing authoritative blockers onto physical, actionable source tasks."""

import json
from collections import defaultdict

from .models import Location, digest
from .review_issues import issue_groups
from .v5_contracts import ReviewTask

FIELD_LABELS = {
    "sample_type": "样品类型",
    "purpose": "用途",
    "test_item": "检测项",
    "batch": "批号",
    "presentation": "规格",
    "study": "研究类型",
    "study_id": "研究编号",
    "condition": "条件",
    "time_point": "时间点",
    "method": "方法",
    "unit": "单位",
    "result": "结果",
    "criterion": "标准",
    "subresult": "子结果",
}


def project_tasks(preview, flights, sheets):
    """No qualification is calculated here: cover every preflight reference instead."""
    tasks = {}
    by_candidate = defaultdict(list)
    candidates = {c.id: c for c in preview.candidates}
    for group in issue_groups(preview):
        locations = tuple(
            Location(format="xlsx", section=s, row=r, column=c) for s, r, c in group["evidence"]
        )
        proposal = group.get("proposal", {})
        if group['code'] == 'source_comment' and any(
            i.source_kind == 'system_hint' and i.detail == group['detail']
            for m in group['members'] for i in candidates[m['candidate_id']].issues
        ):
            proposal = {**proposal, 'source_kind': 'system_hint'}
        members = tuple(group["members"])
        exception = None
        if proposal and proposal.get("eligible"):
            if proposal.get("role") == "result" and any(
                any(i.code == "source_comment" for i in candidates[m["candidate_id"]].issues) for m in members
            ):
                exception = "该结果同时有未核对批注，请先明确批注与新值的关系"
        elif proposal and 'guidance' not in proposal:
            exception = proposal.get("reason")
        batch_key = digest(
            [
                group["code"],
                group["fields"],
                proposal.get("kind"),
                proposal.get("role"),
                proposal.get("operation"),
            ]
        )
        task = ReviewTask(
            id=group["id"],
            code=group["code"],
            section=locations[0].section,
            fields=group["fields"],
            evidence=locations,
            members=members,
            detail=group["detail"],
            actions=group["actions"],
            proposal=proposal,
            exception=exception,
            batch_key=batch_key,
            base_revision=preview.base_revision,
        )
        tasks[task.id] = task
        for member in members:
            by_candidate[member["candidate_id"]].append(task.id)
    collisions = defaultdict(list)
    for c in preview.candidates:
        if c.lifecycle == "draft" and c.issue_code == "ambiguous_file_slot":
            collisions[c.record.key.slot_id if c.record else c.id].append(c)
    for slot, scope in collisions.items():
        tid = digest([preview.preview_id, "collision", slot])
        task = ReviewTask(
            id=tid,
            code="collision",
            section=scope[0].location.section,
            fields=("test_item", "method", "time_point", "batch", "presentation"),
            evidence=tuple(c.location for c in scope),
            members=tuple({"candidate_id": c.id, "candidate_revision": c.candidate_revision} for c in scope),
            detail="这些来源解释成同一检测身份和时间点。请对照原表，修正身份或明确哪条来源不采用；不自动取最后值。",
            actions=("edit", "structure", "decision"),
            base_revision=preview.base_revision,
        )
        tasks[tid] = task
        for c in scope:
            by_candidate[c.id].append(tid)
    for c in preview.candidates:
        explained = {i.code for i in c.issues}
        if (
            c.lifecycle != "draft"
            or c.status != "needs_review"
            or c.issue_code in explained
            or c.issue_code == "ambiguous_file_slot"
        ):
            continue
        if c.issue_code in {"deleted_source", "source_revision"} and "source_revision" in explained:
            continue
        if c.issue_code == "unsupported_structure" and "structure" in explained:
            continue
        fields = tuple(k for k, label in FIELD_LABELS.items() if label in c.detail) or (
            "test_item",
            "sample_type",
            "batch",
            "time_point",
        )
        origins = []
        for field in fields:
            origin = c.evidence.get(field)
            if origin is None and field in {'sample_type', 'purpose', 'study'}:
                origin = c.evidence.get('study_title') or c.evidence.get('batch')
            origins.append(origin or c.location)
        # The batch header is also a scope anchor: a shared page title does not
        # license changing another batch block with an independently missing field.
        if c.evidence.get('batch'):
            origins.append(c.evidence['batch'])
        evidence = tuple(dict.fromkeys(origins))
        effective = tuple(c.corrections.get(k, c.interpreted_fields.get(k, "")) for k in fields)
        tid = digest(
            [
                preview.preview_id,
                "interpretation",
                fields,
                [e.model_dump() for e in evidence],
                effective,
                c.issue_code,
                c.detail,
            ]
        )
        member = {"candidate_id": c.id, "candidate_revision": c.candidate_revision}
        old = tasks.get(tid)
        tasks[tid] = ReviewTask(
            id=tid,
            code="identity",
            section=c.location.section,
            fields=fields,
            evidence=evidence,
            members=(*old.members, member) if old else (member,),
            detail=c.detail,
            actions=("edit", "structure"),
            batch_key=digest(["identity", fields]),
            diagnostics=(
                *(old.diagnostics if old else ()),
                {
                    "candidate_id": c.id,
                    "code": c.issue_code,
                    "detail": c.detail,
                    "basis": "same physical field origins and effective interpretation; never text-only",
                },
            ),
            base_revision=preview.base_revision,
        )
        by_candidate[c.id].append(tid)
    from .review_suggestions import compose, compose_layout

    tasks = compose_layout(tasks, candidates, sheets, preview.preview_id)
    tasks = compose(tasks, candidates, preview.preview_id, preview.base_revision)
    by_candidate = defaultdict(list)
    for task in tasks.values():
        for member in task.members:
            if member.get("candidate_id") and task.id not in by_candidate[member["candidate_id"]]:
                by_candidate[member["candidate_id"]].append(task.id)
    regions = {r["id"]: (s, r) for s in sheets for r in s["regions"]}
    blocker_tasks = {}
    for flight in flights:
        for blocker in (*flight.unresolved, *("approval:" + cid for cid in flight.review_required)):
            kind, _, subject = blocker.partition(":")
            if kind == "candidate" and by_candidate[subject]:
                ids = by_candidate[subject]
            else:
                c = candidates.get(subject)
                if kind in {"region", "unprojected_region"}:
                    sheet, region = regions[subject]
                    tid = digest([preview.preview_id, kind, subject])
                    location = []
                    from openpyxl.utils.cell import coordinate_from_string, column_index_from_string
                    from openpyxl.utils.exceptions import CellCoordinatesException

                    for coordinate in region["coordinates"]:
                        try:
                            col, row = coordinate_from_string(coordinate)
                            location.append(
                                Location(
                                    format="xlsx",
                                    section=sheet["section"],
                                    row=row,
                                    column=column_index_from_string(col),
                                )
                            )
                        except (ValueError, CellCoordinatesException):
                            pass
                    region_proposal = (
                        {
                            "eligible": True,
                            "operation": "classify",
                            "role": "auxiliary",
                            "reason": "确认该表尾非结果区域为绘图辅助说明 · 不入库",
                        }
                        if kind == "region" and region.get("role") == "unclassified"
                        else {}
                    )
                    task = ReviewTask(
                        id=tid,
                        code="region",
                        section=sheet["section"],
                        evidence=tuple(location),
                        detail="请核对这些来源格/对象的用途；含检测结果时应补录或重新解释区域，不能作为备注跳过。",
                        members=({"region_id": subject, "coordinates": region["coordinates"]},),
                        actions=("region_parse", "manual")
                        if kind == "unprojected_region"
                        else ("classify", "region_parse", "manual"),
                        proposal=region_proposal,
                        base_revision=preview.base_revision,
                    )
                elif c:
                    code = (
                        "approval"
                        if kind == "approval"
                        else "history"
                        if kind in {"missing_intent", "stale_decision", "previous_changed"}
                        else "identity"
                    )
                    fields = (
                        tuple(k for k, label in FIELD_LABELS.items() if label in c.detail)
                        if code == "identity"
                        else ()
                    )
                    fields = (
                        fields or ("test_item", "sample_type", "batch", "time_point")
                        if code == "identity"
                        else fields
                    )
                    tid = digest([preview.preview_id, kind, c.id, c.issue_code])
                    task = ReviewTask(
                        id=tid,
                        code=code,
                        section=c.location.section,
                        fields=fields,
                        evidence=tuple(c.evidence[k] for k in fields if k in c.evidence) or (c.location,),
                        members=({"candidate_id": c.id, "candidate_revision": c.candidate_revision},),
                        detail=c.detail
                        if code == "identity"
                        else "草稿决定须必要权限批准，正式值保持不变。"
                        if code == "approval"
                        else "请核对当前历史版本，选择保留旧值、更正或独立复测。",
                        actions=("approve",)
                        if code == "approval"
                        else ("decision",)
                        if code == "history"
                        else ("edit", "structure"),
                        base_revision=preview.base_revision,
                    )
                else:
                    tid = digest([preview.preview_id, flight.section, blocker])
                    task = ReviewTask(
                        id=tid,
                        code="receipt" if kind == "not_received" else "unresolved",
                        section=flight.section,
                        detail="该来源尚未接收，请保存来源后继续核对。"
                        if kind == "not_received"
                        else "当前资格依据发生变化，请刷新并核对本表来源。",
                        actions=("receive",) if kind == "not_received" else ("refresh",),
                        base_revision=preview.base_revision,
                    )
                tasks[task.id] = task
                ids = [task.id]
            blocker_tasks[flight.section + "|" + blocker] = ids
            for tid in ids:
                task = tasks[tid]
                tasks[tid] = task.model_copy(
                    update={
                        "blockers": tuple(dict.fromkeys((*task.blockers, flight.section + "|" + blocker)))
                    }
                )
    from .review_guidance import guide_tasks
    guide_tasks(tasks, candidates)
    return list(tasks.values()), blocker_tasks


def saved_changes(conn, upload, preview):
    """Count effective source-field changes, not affected results or save events."""
    changes = {}
    candidates = {c.id: c for c in preview.candidates}
    undone = {
        json.loads(r[0])["batch_id"]
        for r in conn.execute(
            "SELECT detail_json FROM workflow_events WHERE subject_id=? AND action='undo_review_batch'",
            (upload["id"],),
        )
    }
    for c in preview.candidates:
        for field, value in c.corrections.items():
            if value == c.raw_fields.get(field, ""):
                continue
            loc = c.evidence.get(field, c.location)
            key = (loc.section, loc.row, loc.column, field)
            changes[key] = {
                "section": loc.section,
                "row": loc.row,
                "column": loc.column,
                "field": field,
                "before": c.raw_fields.get(field, ""),
                "after": value,
                "candidate_ids": [],
                "reason": "",
            }
    # Exclusion is an effective draft decision too, even without a field edit.
    # Mark the item origin only when its entire source scope is withdrawn.
    superseded = {
        r[0]
        for r in conn.execute(
            "SELECT subject_id FROM workflow_events WHERE project=? AND action IN ('split_source','supersede_projection')",
            (upload["project"],),
        )
    }
    # Index physical item origins once. Comparing Location models inside a full
    # candidate scan for every withdrawn result made this path quadratic.
    origin_scopes = defaultdict(list)
    for c in preview.candidates:
        if c.id not in superseded and c.raw_fields.get("result", "").strip():
            origin = c.evidence.get("test_item", c.location)
            origin_scopes[(origin.format, origin.section, origin.row, origin.column)].append(c)
    whole_origins = {
        key for key, scope in origin_scopes.items()
        if all(c.lifecycle == "withdrawn" for c in scope)
    }
    for c in preview.candidates:
        if c.lifecycle != "withdrawn" or c.id in superseded:
            continue
        origin = c.evidence.get("test_item", c.location)
        whole = (origin.format, origin.section, origin.row, origin.column) in whole_origins
        loc = origin if whole else c.location
        key = (loc.section, loc.row, loc.column, "source_decision")
        changes.setdefault(
            key,
            {
                "section": loc.section,
                "row": loc.row,
                "column": loc.column,
                "field": "source_decision",
                "before": c.raw_fields.get("test_item", "") if whole else c.raw_fields.get("result", ""),
                "after": "本次不采用；原文和历史正式值保留",
                "candidate_ids": [],
                "reason": "已保存的明确草稿范围决定",
            },
        )
        if c.id not in changes[key]["candidate_ids"]:
            changes[key]["candidate_ids"].append(c.id)
    raw_rows = {
        r["id"]: r["raw_json"]
        for r in conn.execute(
            "SELECT id,raw_json FROM workflow_candidates WHERE upload_id=?", (upload["id"],)
        )
    }
    for row in conn.execute(
        "SELECT e.subject_id,e.reason,e.detail_json FROM workflow_events e JOIN workflow_candidates c ON e.subject_id=c.id WHERE c.upload_id=? AND e.action='resolve_issue' ORDER BY e.sequence",
        (upload["id"],),
    ):
        data = json.loads(row["detail_json"])
        if data.get("batch_id") in undone:
            continue
        c = candidates[row["subject_id"]]
        from .models import RawObservation
        from .review_issues import source_issues

        raw = RawObservation.model_validate_json(raw_rows[c.id])
        issue = next((i for i in source_issues(c.id, raw) if i.id == data["issue_id"]), None)
        if issue:
            # Acknowledging evidence does not turn system interpretation into a human edit.
            for field in data.get('fields', {}):
                loc = c.evidence.get(field, c.location)
                key = (loc.section, loc.row, loc.column, field)
                after = c.corrections.get(field, c.interpreted_fields.get(field, c.raw_fields.get(field, "")))
                if after == c.raw_fields.get(field, ""):
                    continue
                changes.setdefault(
                    key,
                    {
                        "section": loc.section,
                        "row": loc.row,
                        "column": loc.column,
                        "field": field,
                        "before": c.raw_fields.get(field, ""),
                        "after": after,
                        "candidate_ids": [],
                    },
                )
                changes[key]["reason"] = row["reason"]
    projections = {}
    for e in conn.execute("SELECT subject_id,reason,detail_json FROM workflow_events WHERE project=? AND action='standard_projection' ORDER BY sequence",(upload['project'],)):
        c=candidates.get(e['subject_id'])
        if c is None or c.lifecycle=='withdrawn':
            continue
        key=(c.location.section,c.location.row,c.location.column,'reporting_basis')
        change=projections.setdefault(key,{'section':c.location.section,'row':c.location.row,'column':c.location.column,
            'field':'reporting_basis','before':c.raw_fields.get('result',''),'after':'','candidate_ids':[],'reason':e['reason']})
        change['candidate_ids'].append(c.id)
        change['after'] += (c.interpreted_fields.get('reporting_basis','')+'：'+c.interpreted_fields.get('result','')+'；')
    changes.update(projections)
    field_members = defaultdict(list)
    for field in {change['field'] for change in changes.values()}:
        for c in preview.candidates:
            loc = c.evidence.get(field, c.location)
            field_members[(loc.section, loc.row, loc.column, field)].append(c.id)
    for key, change in changes.items():
        known = set(change['candidate_ids'])
        change['candidate_ids'].extend(cid for cid in field_members[key] if cid not in known)
    return list(changes.values())


def checked_sources(conn, upload, preview):
    """Human acknowledgements remain visible without inventing field edits."""
    if preview is None:
        return []
    from .review_issues import source_issues
    from .models import RawObservation
    candidates = {c.id: c for c in preview.candidates}
    undone = {json.loads(r[0])['batch_id'] for r in conn.execute(
        "SELECT detail_json FROM workflow_events WHERE subject_id=? AND action='undo_review_batch'", (upload['id'],))}
    restored = {r[0]: r[1] for r in conn.execute(
        "SELECT e.subject_id,MAX(e.sequence) FROM workflow_events e JOIN workflow_candidates c ON c.id=e.subject_id WHERE c.upload_id=? AND e.action='restore_draft' GROUP BY e.subject_id", (upload['id'],))}
    checks = {}
    for row in conn.execute(
        "SELECT e.subject_id,e.sequence,e.reason,e.detail_json,c.raw_json FROM workflow_events e JOIN workflow_candidates c ON c.id=e.subject_id WHERE c.upload_id=? AND e.action='resolve_issue' ORDER BY e.sequence", (upload['id'],)):
        data = json.loads(row['detail_json'])
        if (data.get('action') not in {'keep_body', 'keep_single'} or data.get('fields') or data.get('batch_id') in undone
                or row['sequence'] <= restored.get(row['subject_id'], 0)):
            continue
        c = candidates[row['subject_id']]
        raw = RawObservation.model_validate_json(row['raw_json'])
        issue = next((i for i in source_issues(c.id, raw) if i.id == data['issue_id']), None)
        evidence = issue.evidence if issue else ()
        if data['action'] == 'keep_single':
            evidence = (c.location,)
        if not evidence:
            for hint in raw.system_hints:
                origin = Location.model_validate(hint['source'])
                if digest([c.id, 'source_comment', hint['text'], [origin.model_dump()]]) == data['issue_id']:
                    evidence = (origin,)
                    break
        evidence = evidence or (c.location,)
        key = tuple((e.section, e.row, e.column) for e in evidence)
        layout = data['action'] == 'keep_single'
        if layout:
            key = ('layout', c.location.section, c.logical_row or c.location.row,
                   *(c.evidence.get(k) for k in ('batch', 'test_item', 'method', 'criterion')))
        check = checks.setdefault(key, {'kind': 'checked_layout' if layout else 'checked_original',
            'label': '人工核对排版，沿用各处现有结果' if layout else '人工核对保留',
            'section': evidence[0].section, 'evidence': [e.model_dump(mode='json') for e in evidence],
            'candidate_ids': [], 'reason': row['reason'], 'identity_pending': False})
        if c.id not in check['candidate_ids']:
            check['candidate_ids'].append(c.id)
        for e in evidence:
            loc = e.model_dump(mode='json')
            if loc not in check['evidence']:
                check['evidence'].append(loc)
        check['identity_pending'] |= any(i.code in {'identity_conflict', 'method_conflict'} for i in c.issues)
    return list(checks.values())


def count_resolved_decisions(conn, upload, preview, sheets, changes, checks=None):
    """Count distinct resolved source decision nodes per section.

    A single source decision affecting multiple fields (e.g. criterion + subresult)
    or multiple timepoints counts as 1 resolved decision.
    Region classifications and structure resolutions also count toward resolved decisions.
    """
    resolved_by_section = defaultdict(set)
    for check in checked_sources(conn, upload, preview) if checks is None else checks:
        if check['kind'] == 'checked_layout':
            resolved_by_section[check['section']].add(('layout', tuple(check['candidate_ids'])))
            continue
        for e in check['evidence']:
            resolved_by_section[e['section']].add(('source_decision', e['section'], e['row'], e['column']))
    for c in changes:
        sec = c["section"]
        field = c.get("field", "")
        key = ("source_decision", sec, c["row"], c["column"] if field != "source_decision" else 0)
        resolved_by_section[sec].add(key)

    for sheet in sheets:
        sec = sheet["section"]
        for r in sheet.get("regions", []):
            if r.get("role") and r["role"] != "unclassified":
                if "classify_region" in str(r.get("basis", "")) or r.get("revision", 1) > 1:
                    resolved_by_section[sec].add(("region", sec, r["id"]))

    for row in conn.execute(
        "SELECT detail_json FROM workflow_events WHERE subject_id=? AND action='classify_region' ORDER BY sequence",
        (upload["id"],),
    ):
        data = json.loads(row[0])
        rid = data.get("region_id")
        for sheet in sheets:
            if any(r["id"] == rid for r in sheet.get("regions", [])):
                resolved_by_section[sheet["section"]].add(("region", sheet["section"], rid))

    for row in conn.execute(
        "SELECT detail_json FROM workflow_events WHERE subject_id=? AND action='resolve_structure' ORDER BY sequence",
        (upload["id"],),
    ):
        data = json.loads(row[0])
        sec = data.get("section")
        coord = data.get("coordinate")
        if sec and coord:
            resolved_by_section[sec].add(("structure", sec, coord))

    return {sheet["section"]: len(resolved_by_section[sheet["section"]]) for sheet in sheets}


def worksheet_states(preview, flights, sheets, tasks, changes, resolved_counts=None):
    resolved_counts = resolved_counts or {}
    states = []
    for sheet in sheets:
        section = sheet["section"]
        flight = next(f for f in flights if f.section == section)
        scope = [c for c in preview.candidates if c.location.section == section]
        relevant = [t for t in tasks if any(b.startswith(section + "|") for b in t.blockers)]
        field_changes_count = sum(c["section"] == section for c in changes)
        resolved_count = resolved_counts.get(section, field_changes_count)
        states.append(
            {
                "section": section,
                "hidden": sheet["hidden"],
                "state": flight.state,
                "source_only": not scope,
                "saved_changes": resolved_count,
                "remaining_tasks": len(relevant),
                "affected_results": len(
                    {m["candidate_id"] for t in relevant for m in t.members if "candidate_id" in m}
                ),
                "approval_count": len(flight.review_required),
                "can_commit": flight.can_commit,
                "diagnostics": {
                    "field_changes": field_changes_count,
                    "raw_blockers": len(flight.unresolved) + len(flight.review_required),
                },
            }
        )
    return states


def enrich_sheet_preflight(conn, upload, preview, view, physical_sheets=None, tasks=None, resolved_counts=None):
    if physical_sheets is None:
        from .worksheet_regions import coverage
        physical_sheets = coverage(conn, upload)
    if tasks is None:
        tasks, _ = project_tasks(preview, [view], physical_sheets)
    if resolved_counts is None:
        changes = saved_changes(conn, upload, preview)
        resolved_counts = count_resolved_decisions(conn, upload, preview, physical_sheets, changes)
    relevant = [t for t in tasks if any(b.startswith(view.section + "|") for b in t.blockers)]
    return view.model_copy(
        update={
            'pending_decision_count': len(relevant),
            'resolved_decision_count': resolved_counts.get(view.section, 0),
            'affected_result_count': len({m["candidate_id"] for t in relevant for m in t.members if "candidate_id" in m}),
        }
    )


def blocker_cards(conn, upload, preview, flight, sheets, tasks):
    """Expose each preflight reference once, even when several share a decision."""
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter
    from pathlib import Path
    from .workflow_common import WorkflowError
    import hashlib

    by_blocker = defaultdict(list)
    for task in tasks:
        for blocker in task.blockers:
            by_blocker[blocker].append(task)
    candidates = {candidate.id: candidate for candidate in preview.candidates}
    regions = {region["id"]: region for sheet in sheets if sheet["section"] == flight.section
               for region in sheet["regions"]}
    references = (*flight.unresolved, *("approval:" + cid for cid in flight.review_required))
    if not references:
        return []
    path = Path(conn.execute("SELECT stored_path FROM documents WHERE sha256=?", (upload["source_hash"],)).fetchone()[0])
    if hashlib.sha256(path.read_bytes()).hexdigest() != upload["source_hash"]:
        raise WorkflowError("source_changed", "冻结 Excel 校验失败，无法核对原格。", 409)
    book = load_workbook(path, read_only=False, rich_text=True)
    try:
        sheet = book[flight.section]
        cards = []
        for reference in references:
            kind, _, subject = reference.partition(":")
            matching = by_blocker[flight.section + "|" + reference]
            candidate = candidates.get(subject)
            region = regions.get(subject)
            coordinates = list(region["coordinates"]) if region else []
            for location in [*(loc for task in matching for loc in task.evidence),
                             *([candidate.location] if candidate else [])]:
                if location.section == flight.section:
                    coordinates.append(get_column_letter(location.column) + str(location.row))
            originals = []
            for coordinate in dict.fromkeys(coordinates):
                if ":" in coordinate:
                    originals.append({"coordinate": coordinate, "text": "原件嵌入对象，请打开原件查看。"})
                    continue
                cell = sheet[coordinate]
                originals.append({"coordinate": coordinate, "text": str(cell.value) if cell.value is not None else "",
                                  "comment": cell.comment.text if cell.comment else ""})
            cards.append({"id": reference, "kind": kind, "section": flight.section, "cells": originals,
                          "candidate_id": candidate.id if candidate else None, "tasks": matching,
                          "region_summary": region.get("summary") or region.get("basis", "") if region else ""})
        return cards
    finally:
        book.close()


def build_action_groups(tasks, physical_sheets, section, preview):
    from .v5_contracts import ActionGroup, TaskBatchScope
    sheet_tasks = [t for t in tasks if t.section == section and t.proposal.get("eligible") and not t.exception]
    groups = []

    # 1. Standard revisions
    std_tasks = [t for t in sheet_tasks if (t.code in ("source_revision", "standard") or set(t.fields) <= {"criterion", "subresult"}) and "result" not in t.fields and t.code != "region"]
    if std_tasks:
        groups.append(ActionGroup(
            group_id=f"ag_std_{section}",
            title=f"标准删改 {len(std_tasks)} 处",
            reason="批量采用本表质量标准修改建议",
            scope=TaskBatchScope(section=section, task_ids=tuple(t.id for t in std_tasks), operation='suggested'),
            decision_count=len(std_tasks),
            affected_result_count=len({m["candidate_id"] for t in std_tasks for m in t.members if "candidate_id" in m}),
            base_revision=preview.base_revision,
        ))

    # 2. Result revisions
    res_tasks = [t for t in sheet_tasks if t.code in ("source_revision", "result") and "result" in t.fields and t.code != "region" and t not in std_tasks]
    if res_tasks:
        groups.append(ActionGroup(
            group_id=f"ag_res_{section}",
            title=f"结果删改 {len(res_tasks)} 处",
            reason="批量采用本表结果正文修改建议",
            scope=TaskBatchScope(section=section, task_ids=tuple(t.id for t in res_tasks), operation='suggested'),
            decision_count=len(res_tasks),
            affected_result_count=len({m["candidate_id"] for t in res_tasks for m in t.members if "candidate_id" in m}),
            base_revision=preview.base_revision,
        ))

    # 3. Whole row exclusions
    row_tasks = [t for t in sheet_tasks if t.code == "row_exclusion"]
    if row_tasks:
        groups.append(ActionGroup(
            group_id=f"ag_row_{section}",
            title=f"整行不采用 {len(row_tasks)} 处",
            reason="批量排除整行来源数据",
            scope=TaskBatchScope(section=section, task_ids=tuple(t.id for t in row_tasks), operation='suggested'),
            decision_count=len(row_tasks),
            affected_result_count=len({m["candidate_id"] for t in row_tasks for m in t.members if "candidate_id" in m}),
            base_revision=preview.base_revision,
        ))

    # 4. Auxiliary / unclassified tail regions
    reg_tasks = [t for t in sheet_tasks if t.code == "region" and t.proposal.get("operation") == "classify"]
    if reg_tasks:
        groups.append(ActionGroup(
            group_id=f"ag_reg_{section}",
            title=f"辅助区域 {len(reg_tasks)} 处",
            reason="确认表尾绘图/计算辅助区域不入库",
            scope=TaskBatchScope(section=section, task_ids=tuple(t.id for t in reg_tasks), operation='classify', role=reg_tasks[0].proposal.get('role', 'auxiliary')),
            decision_count=len(reg_tasks),
            affected_result_count=0,
            base_revision=preview.base_revision,
        ))

    # 5. Longitudinal isomorphic structure tasks (反例 G)
    struct_tasks = [t for t in sheet_tasks if t.code in ("structure", "unsupported_structure")]
    if struct_tasks:
        groups.append(ActionGroup(
            group_id=f"ag_struct_{section}",
            title=f"核对排版 {len(struct_tasks)} 组",
            reason="核对合并排版；各时间点仍沿用自身一条结果，不复制数值",
            scope=TaskBatchScope(section=section, task_ids=tuple(t.id for t in struct_tasks), operation='suggested'),
            decision_count=len(struct_tasks),
            affected_result_count=len({m["candidate_id"] for t in struct_tasks for m in t.members if "candidate_id" in m}),
            base_revision=preview.base_revision,
        ))

    # 6. Source comments & investigation notes review (keep_body)
    cmt_tasks = [t for t in sheet_tasks if t.code == "source_comment" and t.proposal.get("operation") == "keep_body"]
    if cmt_tasks:
        groups.append(ActionGroup(
            group_id=f"ag_cmt_{section}",
            title=f"批注说明 {len(cmt_tasks)} 处",
            reason="核对批注/调查说明后保留当前有效正文及数值",
            scope=TaskBatchScope(section=section, task_ids=tuple(t.id for t in cmt_tasks), operation='keep_body'),
            decision_count=len(cmt_tasks),
            affected_result_count=len({m["candidate_id"] for t in cmt_tasks for m in t.members if "candidate_id" in m}),
            base_revision=preview.base_revision,
        ))

    return groups

def build_review_blocks(physical_sheets, section, preview, tasks):
    from .v5_contracts import ReviewBlock
    sheet = next((s for s in physical_sheets if s.get("section") == section), None)
    if not sheet:
        return []
    blocks = []
    for region in sheet.get("regions", []):
        role = region.get("role", "unclassified")
        if role in ("auxiliary", "unclassified", "excluded"):
            coords = region.get("coordinates", [])
            span = f"{coords[0]}:{coords[-1]}" if coords else ""
            summary = region.get("summary") or f"{role} ({len(coords)} 单元格)"
            reg_tasks = [t.id for t in tasks if t.code == "region" and any(m.get("region_id") == region.get("id") for m in t.members)]
            blocks.append(ReviewBlock(
                block_id=region.get("id", ""),
                section=section,
                range_refs=(span,) if span else (),
                role=role,
                summary=summary,
                requires_human=role == "unclassified",
                collapsed=role in ("auxiliary", "excluded"),
                task_ids=tuple(reg_tasks),
            ))
    return blocks


def current_review(flow, conn, upload, actor, *, _shared=False):
    """Internal readers may share this snapshot; external callers receive a detached copy.

    Shared consumers only inspect frozen models and build new DTOs. They must not
    mutate nested dictionaries. No shared object is sent across the HTTP boundary.
    """
    from .worksheet_regions import coverage
    from .worksheet_commit import preflight
    from .workflow_common import project_row
    from copy import deepcopy

    if not conn.in_transaction:
        conn.execute('BEGIN')
    project = project_row(conn, upload['project'])
    key = (upload['id'], digest(dict(upload)), project['revision'], project['config_json'],
           actor.id, actor.role, actor.capabilities)
    local_key = (key, conn.total_changes)
    if local_key in getattr(conn, 'review_states', {}):
        flow.review_build_counts['request_hit'] += 1
        value = conn.review_states[local_key]
        return value if _shared else deepcopy(value)
    if conn.total_changes == 0:
        with flow._source_cache_lock:
            cached = flow._review_states.get(key)
        if cached is not None:
            flow.review_build_counts['revision_hit'] += 1
            return cached if _shared else deepcopy(cached)
    flow.review_build_counts['preview'] += 1

    preview = flow._preview(conn, upload, actor)
    sheets = coverage(conn, upload)
    flights = [
        preflight(flow, conn, upload, s["section"], actor, preview=preview, sheets=sheets)[0] for s in sheets
    ]
    tasks, links = project_tasks(preview, flights, sheets)
    updated_flights = []
    for f in flights:
        relevant = [t for t in tasks if any(b.startswith(f.section + "|") for b in t.blockers)]
        updated_flights.append(
            f.model_copy(
                update={
                    "pending_decision_count": len(relevant),
                    "affected_result_count": len({m["candidate_id"] for t in relevant for m in t.members if "candidate_id" in m}),
                }
            )
        )
    result = (preview, sheets, updated_flights, tasks, links)
    if hasattr(conn, 'review_states'):
        conn.review_states[local_key] = result
        def publish():
            # A later write in the same transaction must not publish an earlier state.
            if (project_row(conn, upload['project'])['revision'] != key[2]
                    or digest(dict(flow._upload(conn, upload['id']))) != key[1]):
                return
            with flow._source_cache_lock:
                flow._review_states[key] = result
                flow._review_states.move_to_end(key)
                while len(flow._review_states) > 4:
                    flow._review_states.popitem(last=False)
        conn.after_commit.append(publish)
    return result if _shared else deepcopy(result)


def candidate_states(preview, tasks, changes):
    """One backend projection; suggestions never stand in for unresolved facts."""
    from .v5_contracts import CandidateReviewState

    by_candidate = defaultdict(list)
    for task in tasks:
        if not task.blockers:
            continue
        for member in task.members:
            if member.get("candidate_id"):
                by_candidate[member["candidate_id"]].append(task)
    modified = {cid for change in changes for cid in change["candidate_ids"]}
    result = {}
    for candidate in preview.candidates:
        pending = by_candidate[candidate.id]
        suggestions = [t.id for t in pending if t.proposal.get("eligible") and not t.exception]
        unresolved = bool(pending or candidate.status == "needs_review" or candidate.issues)
        state = (
            "committed"
            if candidate.lifecycle == "submitted"
            else "excluded"
            if candidate.lifecycle == "withdrawn"
            else "suggested"
            if suggestions
            else "pending"
            if unresolved
            else "saved_draft"
            if candidate.id in modified
            else "normal_pending"
        )
        result[candidate.id] = CandidateReviewState(
            candidate_id=candidate.id,
            state=state,
            modified=candidate.id in modified,
            has_unresolved=unresolved and candidate.lifecycle == "draft",
            task_ids=tuple(dict.fromkeys(t.id for t in pending)),
            suggestion_ids=tuple(dict.fromkeys(suggestions)),
        ).model_dump(mode="json")
    return result
