"""Compose physical source/row decisions without replacing authoritative diagnostics."""

from collections import defaultdict

from .models import digest
from .v5_contracts import ReviewTask


def compose_layout(tasks, candidates, sheets, preview_id):
    from .merge_layout import MERGE_HAZARD, inventory, can_keep_single
    proof = inventory(sheets)
    groups = defaultdict(list)
    for task in tasks.values():
        if task.code != 'structure' or task.detail != MERGE_HAZARD or len(task.members) != 1:
            continue
        c = candidates[task.members[0]['candidate_id']]
        if not can_keep_single(c, proof):
            continue
        anchor = c.result_anchor['range']
        key = (c.location.section, c.logical_row or c.location.row, anchor[2] - anchor[0],
               *(c.evidence.get(field) for field in ('batch', 'test_item', 'method', 'criterion')))
        groups[key].append(task)
    for group in groups.values():
        first = group[0]
        reason = '合并范围的延伸行没有独立来源内容；每个时间点仍沿用自己的一条结果，已有值、格式和决定不重填。'
        task = first.model_copy(update={
            'id': digest([preview_id, 'layout_single', sorted(t.id for t in group)]),
            'members': tuple(m for t in group for m in t.members),
            'evidence': tuple(dict.fromkeys(e for t in group for e in t.evidence)),
            'proposal': {'eligible': True, 'operation': 'keep_single', 'kind': 'layout', 'reason': reason},
            'detail': reason, 'actions': ('keep_single', 'structure'),
            'batch_key': digest(['layout_single']),
            'diagnostics': tuple(t.model_dump(mode='json') for t in group),
        })
        for old in group:
            tasks.pop(old.id)
        tasks[task.id] = task
    return tasks


def compose(tasks, candidates, preview_id, revision):
    positions = defaultdict(list)
    active_slots = defaultdict(set)
    slots = {}
    origin_members = defaultdict(set)
    for c in candidates.values():
        loc = c.location
        positions[loc.format, loc.section, loc.row, loc.column].append(c)
        if c.record:
            slots[c.id] = c.record.key.slot_id
        if c.raw_fields.get('result', '').strip():
            origin_members[c.evidence.get('test_item', loc)].add(c.id)
        if (c.lifecycle == 'draft' and c.record and c.raw_fields.get('result', '').strip()
                and not c.revisions.get('result', {}).get('all_deleted')):
            active_slots[loc.section, slots[c.id]].add(c.id)
    physical = defaultdict(list)
    for task in tasks.values():
        if task.code == "source_revision" and task.fields in {("criterion",), ("subresult",)}:
            key = (
                tuple(task.evidence),
                task.proposal.get("before"),
                task.proposal.get("after"),
                task.proposal.get("kind"),
            )
            physical[key].append(task)
    for group in physical.values():
        if {t.fields[0] for t in group} != {"criterion", "subresult"}:
            continue
        first = group[0]
        members = tuple({**m, "source_field": t.fields[0]} for t in group for m in t.members)
        fields = tuple(sorted({t.fields[0] for t in group}))
        proposal = {
            "kind": first.proposal["kind"],
            "role": "standard_subresult",
            "operation": "keep_active",
            "eligible": all(
                t.proposal.get("eligible") and t.proposal.get("operation") == "keep_active" for t in group
            ),
            "fields": {t.fields[0]: t.proposal for t in group},
            "reason": "同一单元格同时包含质量标准与分项结果，请核对是否采纳",
            "before": first.proposal["before"],
            "after": first.proposal["after"],
        }
        task = first.model_copy(
            update={
                "id": digest([preview_id, "physical_revision", sorted(t.id for t in group)]),
                "fields": fields,
                "members": members,
                "proposal": proposal,
                "detail": proposal["reason"],
                "batch_key": digest(["physical_revision", fields, proposal["kind"]]),
                "exception": None if proposal["eligible"] else "该原格含不明确的分项删除，须填写当前有效解释",
                "diagnostics": tuple(t.model_dump(mode="json") for t in group),
            }
        )
        for old in group:
            tasks.pop(old.id)
        tasks[task.id] = task
    task_members = defaultdict(set)
    task_order = {key: index for index, key in enumerate(tasks)}
    for key, task in tasks.items():
        for member in task.members:
            if member.get('candidate_id'):
                task_members[member['candidate_id']].add(key)
    rows = defaultdict(list)
    for c in candidates.values():
        if c.raw_fields.get("result", "").strip():
            rows[c.location.section, c.logical_row or c.location.row].append(c)
    for (section, row), scope in rows.items():
        if not all(
            c.lifecycle == "draft"
            and c.revisions.get("test_item", {}).get("all_deleted")
            and c.revisions.get("result", {}).get("all_deleted")
            for c in scope
        ):
            continue
        if any(i.code in {"source_comment", "structure"} for c in scope for i in c.issues):
            continue
        if any(
            not any(i.code == "source_revision" and i.source_field == "result" for i in c.issues)
            for c in scope
        ):
            continue  # An old bundled diagnostic is not a new field-scoped action.
        # A physical result shared across logical subrows is not a row-local deletion.
        if any(
            any(
                other.id != c.id
                and (other.logical_row or other.location.row) != row
                for other in positions[c.location.format, c.location.section, c.location.row, c.location.column]
            )
            for c in scope
        ):
            continue
        # A live alternative for the same result identity is an exception.
        # Other logical subrows under one merged name remain independent.
        ids = {c.id for c in scope}
        if any(active_slots[section, slots[c.id]] - ids for c in scope if c.record):
            continue
        diagnostics = []
        touched = {key for cid in ids for key in task_members[cid]}
        for key in sorted(touched, key=task_order.__getitem__):
            task = tasks.get(key)
            if task is None:
                continue
            kept = tuple(m for m in task.members if m.get("candidate_id") not in ids)
            removed = tuple(m for m in task.members if m.get("candidate_id") in ids)
            if not removed:
                continue
            diagnostics.append({"id": task.id, "code": task.code, "detail": task.detail, "members": removed})
            if kept:
                tasks[key] = task.model_copy(update={"members": kept})
            else:
                tasks.pop(key)
        members = []
        for c in scope:
            issue = next(i for i in c.issues if i.code == "source_revision" and i.source_field == "result")
            members.append(
                {"candidate_id": c.id, "candidate_revision": c.candidate_revision, "issue_id": issue.id}
            )
        evidence = tuple(
            dict.fromkeys(
                [scope[0].evidence.get("test_item", scope[0].location), *(c.location for c in scope)]
            )
        )
        reason = "本行检测项及对应结果原表已划线删除，建议排除整行不入库；保留历史数据"
        task = ReviewTask(
            id=digest([preview_id, "row_exclusion", section, row, sorted(ids)]),
            code="row_exclusion",
            section=section,
            fields=("test_item", "result"),
            evidence=evidence,
            members=tuple(members),
            detail=reason,
            actions=("exclude",),
            proposal={
                "kind": "row",
                "role": "row",
                "operation": "exclude",
                "eligible": True,
                "reason": reason,
                "row": row,
                "before": scope[0].raw_fields.get("test_item", ""),
                "after": "",
            },
            batch_key=digest(["row_exclusion"]),
            base_revision=revision,
            diagnostics=tuple(diagnostics),
        )
        tasks[task.id] = task
    # A merged item is one block suggestion only when EVERY nonblank logical
    # subrow passed the same checks. A mixed block keeps its row exceptions.
    blocks = defaultdict(list)
    for task in tasks.values():
        if task.code == "row_exclusion":
            blocks[task.evidence[0]].append(task)
    for origin, group in blocks.items():
        if len(group) < 2:
            continue
        expected = origin_members[origin]
        represented = {m["candidate_id"] for t in group for m in t.members}
        if represented != expected:
            continue
        first = group[0]
        rows = sorted(t.proposal["row"] for t in group)
        reason = "该检测项在原表中已整体划线作废，建议批量排除不入库"
        merged = first.model_copy(
            update={
                "id": digest([preview_id, "deleted_item_block", sorted(t.id for t in group)]),
                "members": tuple(m for t in group for m in t.members),
                "evidence": tuple(dict.fromkeys(e for t in group for e in t.evidence)),
                "proposal": {**first.proposal, "kind": "row_block", "rows": rows, "reason": reason},
                "detail": reason,
                "diagnostics": tuple(t.model_dump(mode="json") for t in group),
            }
        )
        for old in group:
            tasks.pop(old.id)
        tasks[merged.id] = merged
    return tasks
