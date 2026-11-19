"""Source semantics, full blocker coverage and explicitly scoped draft batches."""

from decimal import Decimal
from uuid import uuid4

import pytest
from openpyxl import Workbook
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont

from stability_hub.review_batches import preview_batch, apply_batch, undo_batch
from stability_hub.review_tasks import current_review
from stability_hub.v5_contracts import TaskBatchScope, TaskBatchApply, TaskBatchUndo
from stability_hub.workflow_common import WorkflowError
from test_v5_regressions import TECH, preview
from test_v51_interpretation import receive


def revised(old, new):
    return CellRichText(
        TextBlock(InlineFont(strike=True), old), TextBlock(InlineFont(strike=False), "\n" + new)
    )


def matrix_file(tmp_path, *, other=False):
    book = Workbook()
    s = book.active
    s.title = "DP长期"
    for coord, value in {
        "A1": "DP长期稳定性实验数据（5℃）",
        "A2": "B1",
        "A3": "检测项目",
        "B3": "质量标准",
        "C3": "考察时间（月）",
        "C4": 0,
        "D4": 3,
        "A5": "纯度",
        "B5": revised("单体≥95%", "单体≥96%"),
        "C5": 99,
        "D5": 98,
        "A6": "pH",
        "B6": "报告结果",
        "C6": revised("5.5", "5.49"),
        "D6": revised("5.4", "5.54"),
    }.items():
        s[coord] = value
    s.merge_cells("C3:D3")
    if other:
        copied = book.copy_worksheet(s)
        copied.title = "另一张原表"
        copied["A2"] = "B2"
    path = tmp_path / "revision.xlsx"
    book.save(path)
    return path


def view(flow, p):
    with flow.store.connect() as conn:
        return current_review(flow, conn, flow._upload(conn, p.preview_id), TECH)


def apply(flow, p, scope, key=None):
    with flow.store.connect() as conn:
        info = preview_batch(flow, conn, p.preview_id, scope, TECH)
    req = TaskBatchApply(
        base_revision=info["base_revision"],
        idempotency_key=key or uuid4().hex,
        reason="核对来源保留文本及明确例外",
        scope=scope,
        fingerprint=info["fingerprint"],
    )
    return apply_batch(flow, p.preview_id, req, actor=TECH), req, info


def test_all_collisions_have_tasks_even_with_empty_source_issues(tmp_path, xlsx, context):
    flow, p = preview(tmp_path, xlsx([["Purity", "0M", 99, "%"], ["Purity", "0M", 98, "%"]]), context)
    p = receive(flow, p)
    previewed, sheets, flights, tasks, links = view(flow, p)
    assert all(not c.issues for c in previewed.candidates)
    collision = next(t for t in tasks if t.code == "collision")
    assert len(collision.members) == 2 and collision.actions
    refs = {f.section + "|" + b for f in flights for b in f.unresolved}
    assert refs == set(links) and all(links.values())


def test_formal_source_snapshot_does_not_replay_an_undone_batch(tmp_path, xlsx, context):
    import json
    from test_v5_commit import send

    flow, p = preview(tmp_path, xlsx([["Purity", "0M", revised("99", "98"), "%"]]), context)
    p = receive(flow, p)
    _, _, _, tasks, _ = view(flow, p)
    scope = TaskBatchScope(section="Results", task_ids=(tasks[0].id,), operation="keep_active")
    result, _, _ = apply(flow, p, scope)
    current, _, _, _, _ = view(flow, p)
    undo_batch(
        flow,
        p.preview_id,
        TaskBatchUndo(
            base_revision=current.base_revision,
            idempotency_key=uuid4().hex,
            reason="重新核对草稿解释",
            batch_id=result["batch_id"],
        ),
        actor=TECH,
    )
    _, _, _, tasks, _ = view(flow, p)
    apply(flow, p, scope.model_copy(update={"task_ids": (tasks[0].id,)}))
    send(flow, p, "formal-after-undo")
    with flow.store.connect() as conn:
        source = json.loads(conn.execute("SELECT source_json FROM workflow_versions").fetchone()[0])[0]
        assert all(d.get("batch_id") != result["batch_id"] for d in source["issue_decisions"])
        assert len(source["issue_decisions"]) == 1


def test_standard_decision_is_one_origin_and_does_not_approve_subresult(tmp_path, context):
    flow, p = preview(tmp_path, matrix_file(tmp_path), context.model_copy(update={"defaults": {}}))
    p = receive(flow, p)
    before, _, _, tasks, _ = view(flow, p)
    standard = next(t for t in tasks if t.fields == ("criterion",))
    assert len(standard.members) == 2 and len(standard.evidence) == 1
    assert {m["candidate_id"] for m in standard.members} == {
        c.id for c in before.candidates if c.location.row == 5
    }
    scope = TaskBatchScope(section="DP长期", task_ids=(standard.id,), operation="keep_active")
    _, _, info = apply(flow, p, scope)
    assert info["summary"]["sources"] == 1 and info["summary"]["results"] == 2
    after, _, _, remaining, _ = view(flow, p)
    assert not any(t.fields == ("criterion",) for t in remaining)
    assert len([t for t in remaining if t.fields == ("result",)]) == 2
    assert [c.raw_fields["result"] for c in after.candidates] == [
        c.raw_fields["result"] for c in before.candidates
    ]


def test_each_result_uses_its_own_new_value_undo_and_idempotency(tmp_path, context):
    flow, p = preview(tmp_path, matrix_file(tmp_path), context.model_copy(update={"defaults": {}}))
    p = receive(flow, p)
    _, _, _, tasks, _ = view(flow, p)
    result_tasks = [t for t in tasks if t.fields == ("result",)]
    assert len(result_tasks) == 2
    manual_scope = TaskBatchScope(section="DP长期", task_ids=tuple(t.id for t in result_tasks),
                                  operation="manual", fields={"result": "5.50"})
    with flow.store.read() as conn:
        with pytest.raises(WorkflowError) as failure:
            preview_batch(flow, conn, p.preview_id, manual_scope, TECH)
        assert failure.value.code == "shared_result"
        skipped = result_tasks[1].members[0]["candidate_id"]
        one = preview_batch(flow, conn, p.preview_id,
                            manual_scope.model_copy(update={"exclude_candidate_ids": (skipped,)}), TECH)
        assert len(one["members"]) == 1 and one["members"][0]["candidate_id"] != skipped
    scope = TaskBatchScope(
        section="DP长期", task_ids=tuple(t.id for t in result_tasks), operation="keep_active"
    )
    saved, req, info = apply(flow, p, scope, "pH")
    assert info["summary"]["sources"] == 2
    assert apply_batch(flow, p.preview_id, req, actor=TECH) == saved
    after, _, _, _, _ = view(flow, p)
    ph = [c for c in after.candidates if c.location.row == 6]
    assert [c.record.result.value for c in ph] == [Decimal("5.49"), Decimal("5.54")]
    undo_batch(
        flow,
        p.preview_id,
        TaskBatchUndo(
            base_revision=after.base_revision,
            idempotency_key="undo",
            batch_id=saved["batch_id"],
            reason="撤销后重新核对",
        ),
        actor=TECH,
    )
    restored, _, _, tasks, _ = view(flow, p)
    assert len([t for t in tasks if t.fields == ("result",)]) == 2
    assert all(not c.corrections for c in restored.candidates)
    with flow.store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM workflow_versions").fetchone()[0] == 0


def test_cross_sheet_scope_is_explicit_and_only_saves_drafts(tmp_path, context):
    flow, p = preview(
        tmp_path, matrix_file(tmp_path, other=True), context.model_copy(update={"defaults": {}})
    )
    p = receive(flow, p)
    _, _, _, tasks, _ = view(flow, p)
    chosen = [t for t in tasks if t.fields == ("result",)]
    scope = TaskBatchScope(section="DP长期", task_ids=tuple(t.id for t in chosen), operation="keep_active")
    with pytest.raises(WorkflowError, match="跨表"):
        apply(flow, p, scope)
    explicit = scope.model_copy(update={"include_sections": ("另一张原表",)})
    result, _, info = apply(flow, p, explicit)
    assert len(info["summary"]["by_sheet"]) == 2
    with flow.store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM workflow_versions").fetchone()[0] == 0
    assert all(f["state"] != "committed" for f in result["preflights"])


def test_whole_standard_deletion_does_not_delete_result(tmp_path, context):
    from openpyxl import load_workbook
    from openpyxl.styles import Font

    path = matrix_file(tmp_path)
    book = load_workbook(path, rich_text=True)
    book.active["B5"] = "旧标准已划线"
    book.active["B5"].font = Font(strike=True)
    book.save(path)
    flow, p = preview(tmp_path, path, context.model_copy(update={"defaults": {}}))
    p = receive(flow, p)
    _, _, _, tasks, _ = view(flow, p)
    standard = next(t for t in tasks if t.fields == ("criterion",))
    assert standard.proposal["operation"] == "keep_active"
    with pytest.raises(WorkflowError, match="没有满足"):
        apply(flow, p, TaskBatchScope(section="DP长期", task_ids=(standard.id,), operation="exclude"))
    apply(flow, p, TaskBatchScope(section="DP长期", task_ids=(standard.id,), operation="keep_active"))
    after, _, _, _, _ = view(flow, p)
    assert all(
        c.lifecycle == "draft" and c.raw_fields["result"] in {"99", "98", "5.5\n5.49", "5.4\n5.54"}
        for c in after.candidates
    )


def test_multiple_new_values_are_exceptions(tmp_path, context):
    from openpyxl import load_workbook

    path = matrix_file(tmp_path)
    book = load_workbook(path, rich_text=True)
    book.active["C6"] = revised("5.5", "5.49\n5.50")
    book.save(path)
    flow, p = preview(tmp_path, path, context.model_copy(update={"defaults": {}}))
    p = receive(flow, p)
    _, _, _, tasks, _ = view(flow, p)
    ts = [t for t in tasks if t.fields == ("result",)]
    scope = TaskBatchScope(section="DP长期", task_ids=tuple(t.id for t in ts), operation="keep_active")
    _, _, info = apply(flow, p, scope)
    assert len(info["exceptions"]) == 1 and info["summary"]["results"] == 1


def test_two_semantics_at_same_source_are_resolved_independently():
    from stability_hub.models import RawObservation, Location
    from stability_hub.review_issues import source_issues, resolved_raw

    loc = Location(format="xlsx", section="Original", row=19, column=2)
    rev = {
        "segments": [{"text": "old", "struck": True}, {"text": "new", "struck": False}],
        "active_text": "new",
        "all_deleted": False,
        "deleted_word": False,
    }
    raw = RawObservation(
        location=loc,
        fields={"criterion": "oldnew", "subresult": "oldnew", "result": "99"},
        evidence={"criterion": loc, "subresult": loc},
        revisions={"criterion": rev, "subresult": rev},
    )
    issues = source_issues("c", raw)
    chosen = next(i for i in issues if i.source_field == "criterion")
    effective, remaining = resolved_raw(
        None, "c", raw, decisions=[{"issue_id": chosen.id, "action": "keep_active"}], corrections={}
    )
    assert effective.fields["criterion"] == "new" and effective.fields["subresult"] == "oldnew"
    assert len(remaining) == 1 and remaining[0].source_field == "subresult"
