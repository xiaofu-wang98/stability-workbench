"""Shared source scopes and atomic row decisions retain independent disputes/history."""

from uuid import uuid4

import pytest
from openpyxl import load_workbook
from openpyxl.styles import Font
from openpyxl.comments import Comment

from stability_hub.matrix_fields import selected_condition
from stability_hub.v5_contracts import TaskBatchScope, TaskBatchUndo
from stability_hub.review_tasks import project_tasks
from stability_hub.review_batches import undo_batch
from test_v52_review_tasks import matrix_file, view, apply
from test_v5_regressions import TECH, preview
from test_v51_interpretation import receive


def test_celsius_notation_preserves_placement_and_rejects_true_ambiguity():
    assert selected_condition("实验条件：-80°C±10°C\nExperimental conditions:-80°C±10°C") == "-80℃±10℃"
    assert selected_condition("-80°C±10°C，正置\n-80°C±10°C, upright") == "-80℃±10℃ · 正置"
    assert selected_condition("-80°C / -20°C") == ""


def test_identity_groups_use_origins_not_candidate_or_equal_text(tmp_path, context):
    path = matrix_file(tmp_path, other=True)
    b = load_workbook(path, rich_text=True)
    for s in b:
        s["A1"] = "DP长期稳定性实验数据"
        s["B2"] = "条件待确认"
    b.save(path)
    flow, p = preview(tmp_path, path, context.model_copy(update={"defaults": {}}))
    p = receive(flow, p)
    current, sheets, flights, _, _ = view(flow, p)
    # Isolate the source-condition diagnostic rather than other missing fields.
    changed = []
    for c in current.candidates:
        origin = c.location.model_copy(update={"row": 2, "column": 2})
        changed.append(
            c.model_copy(
                update={
                    "status": "needs_review",
                    "issue_code": "missing_identity",
                    "detail": "请核对或补充：条件",
                    "issues": (),
                    "evidence": {**c.evidence, "condition": origin},
                }
            )
        )
    tasks, links = project_tasks(current.model_copy(update={"candidates": tuple(changed)}), flights, sheets)
    shared = [t for t in tasks if t.code == "identity" and t.fields == ("condition",)]
    assert len(shared) == 2 and all(len(t.members) == 4 for t in shared)
    assert all(len(t.diagnostics) == 4 for t in shared)
    assert links


def deletion_file(tmp_path, *, active=False, note=False, partial=False, merged=False):
    path = matrix_file(tmp_path)
    b = load_workbook(path, rich_text=True)
    s = b.active
    s.delete_rows(6)
    s["A5"] = "纯度"
    s["B5"] = "报告结果"
    s["C5"] = 99
    s["D5"] = 98
    if not partial:
        s["A5"].font = Font(strike=True)
    s["C5"].font = Font(strike=True)
    if not active:
        s["D5"].font = Font(strike=True)
    if note:
        s["C5"].comment = Comment("复测结果需保留，不能删除", "source")
    if merged:
        s.merge_cells("C5:D5")
    b.save(path)
    return path


def test_one_row_proposal_excludes_all_members_and_undo_reopens(tmp_path, context):
    flow, p = preview(tmp_path, deletion_file(tmp_path), context)
    p = receive(flow, p)
    before, _, _, tasks, _ = view(flow, p)
    row = next(t for t in tasks if t.code == "row_exclusion")
    assert len(row.members) == 2 and row.diagnostics
    assert not any(t.code == "source_revision" for t in tasks)
    scope = TaskBatchScope(section=row.section, task_ids=(row.id,), operation="suggested")
    result, _, info = apply(flow, p, scope)
    assert len(info["changes"]) == 1 and len(info["members"]) == 2
    after, _, _, _, _ = view(flow, p)
    assert all(c.lifecycle == "withdrawn" for c in after.candidates)
    assert [c.raw_fields for c in after.candidates] == [c.raw_fields for c in before.candidates]
    undo_batch(
        flow,
        p.preview_id,
        TaskBatchUndo(
            base_revision=after.base_revision,
            idempotency_key=uuid4().hex,
            reason="撤销行级草稿",
            batch_id=result["batch_id"],
        ),
        actor=TECH,
    )
    assert any(t.code == "row_exclusion" for t in view(flow, p)[3])


@pytest.mark.parametrize("option", ["active", "note", "partial", "merged"])
def test_row_deletion_exceptions_remain_explicit(tmp_path, context, option):
    flow, p = preview(tmp_path, deletion_file(tmp_path, **{option: True}), context)
    p = receive(flow, p)
    assert not any(t.code == "row_exclusion" for t in view(flow, p)[3])


def test_selected_exclusion_undo_does_not_leave_a_hidden_exclude_intent(tmp_path, xlsx, context):
    flow, p = preview(tmp_path, xlsx([["Purity", "0M", 99, "%"]]), context)
    p = receive(flow, p)
    scope = TaskBatchScope(
        section="Results", candidate_ids=(p.candidates[0].id,), operation="exclude_selected"
    )
    result, _, _ = apply(flow, p, scope)
    current = flow.preview(p.preview_id, actor=TECH)
    undo_batch(
        flow,
        p.preview_id,
        TaskBatchUndo(
            base_revision=current.base_revision,
            idempotency_key=uuid4().hex,
            reason="撤销所选范围排除",
            batch_id=result["batch_id"],
        ),
        actor=TECH,
    )
    from stability_hub.worksheet_commit import decisions

    with flow.store.connect() as conn:
        assert not decisions(conn, p.preview_id)
    assert flow.preview(p.preview_id, actor=TECH).candidates[0].lifecycle == "draft"


def test_same_cell_standard_and_subresult_share_preview_but_validate_both(tmp_path, context):
    path = matrix_file(tmp_path)
    b = load_workbook(path, rich_text=True)
    s = b.active
    s.merge_cells("A5:A6")
    s["B6"] = "HMW≤1%"
    s["C6"] = 0.3
    s["D6"] = 0.4
    b.save(path)
    flow, p = preview(tmp_path, path, context)
    p = receive(flow, p)
    tasks = view(flow, p)[3]
    task = next(t for t in tasks if set(t.fields) == {"criterion", "subresult"})
    _, _, info = apply(
        flow, p, TaskBatchScope(section=task.section, task_ids=(task.id,), operation="suggested")
    )
    assert len(info["changes"]) == 1
    assert {list(m["fields"])[0] for m in info["members"]} == {"criterion", "subresult"}
    assert not any(set(t.fields) == {"criterion", "subresult"} for t in view(flow, p)[3])


def test_current_sheet_business_notes_preserve_full_original_and_distinct_origins():
    from types import SimpleNamespace
    from stability_hub.review_notes import collect, annotate

    notes = collect(
        [
            {
                "name": "A",
                "cells": [
                    {"row": 10, "column": 1, "text": "AR-C-001：复测结果需要明确采用范围", "comment": ""},
                    {"row": 11, "column": 1, "text": "AR-C-001：复测结果需要明确采用范围", "comment": ""},
                    {"row": 12, "column": 1, "text": "填表说明：异常时填写调查编号", "comment": ""},
                    {"row": 13, "column": 2, "text": "95", "comment": "OOS-2026：待调查，不得自动采用"},
                ],
            },
            {
                "name": "B",
                "cells": [
                    {"row": 10, "column": 1, "text": "AR-C-001：复测结果需要明确采用范围", "comment": ""}
                ],
            },
        ]
    )
    assert len(notes) == 3
    shared = next(n for n in notes if n["section"] == "A" and n["text"].startswith("AR"))
    assert len(shared["evidence"]) == 2 and shared["original"].endswith("采用范围")
    result = annotate(notes, SimpleNamespace(candidates=[]), [])
    assert all(n["scope"] == "需确认作用范围" for n in result)
    assert not any("填表说明" in n["text"] for n in result)


def test_shared_identity_manual_save_is_local_and_new_conflict_is_rechecked(tmp_path, context):
    path = matrix_file(tmp_path, other=True)
    b = load_workbook(path, rich_text=True)
    for sheet in b:
        sheet["A1"] = "DP长期稳定性实验数据"
        sheet["B2"] = "条件待确认"
    b.save(path)
    flow, p = preview(tmp_path, path, context.model_copy(update={"defaults": {}}))
    p = receive(flow, p)
    before, _, _, tasks, _ = view(flow, p)
    task = next(t for t in tasks if t.code == "identity" and "condition" in t.fields)
    ids = {m["candidate_id"] for m in task.members}
    # Fill only missing condition; independent method/revision decisions remain live.
    apply(
        flow,
        p,
        TaskBatchScope(
            section=task.section, task_ids=(task.id,), operation="manual", fields={"condition": "5℃"}
        ),
    )
    after, _, flights, remaining, _ = view(flow, p)
    assert all(c.corrections.get("condition") == "5℃" for c in after.candidates if c.id in ids)
    assert all("condition" not in c.corrections for c in after.candidates if c.id not in ids)
    assert all(
        c.raw_fields == next(old.raw_fields for old in before.candidates if old.id == c.id)
        for c in after.candidates
    )
    assert remaining and not all(f.can_commit for f in flights)


def test_bare_ar_is_a_sheet_reminder_without_guessing_scope_but_actionable_text_blocks():
    from stability_hub.source_notes import informational_reference
    from stability_hub.review_issues import comment_requires_review
    from stability_hub.worksheet_regions import structural_roles
    from stability_hub.xlsx_matrix import source_cells
    from openpyxl import Workbook

    assert informational_reference("AR-C-IP275-2024003")
    assert not comment_requires_review("F30: AR-C-IP275-2024003")
    assert comment_requires_review("AR-C-IP275-2024003：0M结果需要更正")
    b = Workbook()
    s = b.active
    s["F30"] = "AR-C-IP275-2024003"
    s["F12"] = "AR-C-IP275-2024003"
    roles = structural_roles(
        s, source_cells(s), {"blocks": [{"header_row": 3, "end_row": 28, "time_columns": [5, 6]}]}
    )
    assert roles[30, 6][0] == "note"
    assert (12, 6) not in roles


@pytest.mark.parametrize("active_child", [False, True])
def test_merged_item_requires_each_logical_row_then_combines_only_complete_blocks(
    tmp_path, context, active_child
):
    path = matrix_file(tmp_path)
    b = load_workbook(path, rich_text=True)
    sheet = b.active
    sheet.merge_cells("A5:A6")
    sheet["A5"].font = Font(strike=True)
    sheet["B5"] = "主峰"
    sheet["B6"] = "杂质"
    for coord in ["C5", "D5", "C6", "D6"]:
        sheet[coord] = 99
        sheet[coord].font = Font(strike=True)
    if active_child:
        sheet["D6"].font = Font(strike=False)
    b.save(path)
    flow, p = preview(tmp_path, path, context)
    p = receive(flow, p)
    tasks = view(flow, p)[3]
    rows = [t for t in tasks if t.code == "row_exclusion"]
    assert len(rows) == 1
    assert len(rows[0].members) == (2 if active_child else 4)
    assert rows[0].proposal["kind"] == ("row" if active_child else "row_block")
    if active_child:
        assert rows[0].proposal["row"] == 5
        assert any(
            t.code == "source_revision" and t.fields == ("test_item",) and not t.proposal["eligible"]
            for t in tasks
        )


def test_partial_structure_note_keeps_other_unknown_cells_blocking_and_manual_scope_local(tmp_path, context):
    from stability_hub import source_structure
    from stability_hub.worksheet_regions import coverage, classify
    from stability_hub.v5_contracts import SourceStructureApply, RegionDecision

    path = matrix_file(tmp_path)
    book = load_workbook(path, rich_text=True)
    sheet = book.active
    sheet["A8"] = "=SUM(1,2)"
    sheet["B8"] = "备注"
    sheet["F8"] = "AR-C-IP275-2024003"
    book.save(path)
    from stability_hub.parsing import parse_source
    from stability_hub.workflow import Workflow
    from stability_hub.storage import Store
    from stability_hub.models import ProjectConfig

    # Build the historical inventory BEFORE upload; immutable audit history is
    # never edited or bypassed, even in the fixture.
    parsed = parse_source(path)
    inventory = parsed.coverage[0]
    for region in inventory["regions"]:
        region["coordinates"] = [c for c in region["coordinates"] if c not in {"A8", "B8", "F8"}]
    inventory["regions"] = [r for r in inventory["regions"] if r["coordinates"]]
    inventory["regions"].append(
        {
            "id": "legacy-row-8",
            "section": sheet.title,
            "coordinates": ["A8", "B8", "F8"],
            "role": "unclassified",
            "revision": 1,
            "hidden": False,
            "basis": "old physical row",
        }
    )
    flow = Workflow(Store(tmp_path / "historical-fixture"))
    flow.configure(ProjectConfig(project="PROJECT-P"))
    p = flow.upload(
        "PROJECT-P", path.name, path.read_bytes(), context, uuid4().hex, actor=TECH, _parsed=parsed
    )
    p = receive(flow, p)
    with flow.store.connect() as conn:
        before = [tuple(r) for r in conn.execute("SELECT id,raw_json,lifecycle FROM workflow_candidates")]
        info = source_structure.preview(flow, conn, p.preview_id)
        change = next(c for c in info["changes"] if c["region_id"] == "legacy-row-8")
        assert change["coordinates"] == ["B8", "F8"] and change["remaining_coordinates"] == ["A8"]
    result = source_structure.apply(
        flow,
        p.preview_id,
        SourceStructureApply(
            base_revision=info["base_revision"],
            idempotency_key=uuid4().hex,
            fingerprint=info["fingerprint"],
            reason="核对明确备注范围",
        ),
        actor=TECH,
    )
    with flow.store.connect() as conn:
        regions = coverage(conn, flow._upload(conn, p.preview_id))[0]["regions"]
        assert next(r for r in regions if r["id"] == "legacy-row-8")["coordinates"] == ["A8"]
        note = next(r for r in regions if "F8" in r["coordinates"])
        assert note["role"] == "note"
        assert [
            tuple(r) for r in conn.execute("SELECT id,raw_json,lifecycle FROM workflow_candidates")
        ] == before
    classify(
        flow,
        p.preview_id,
        RegionDecision(
            base_revision=result.base_revision,
            idempotency_key=uuid4().hex,
            region_id="legacy-row-8",
            role="auxiliary",
            reason="fixture explicitly reviewed remaining source",
        ),
        actor=TECH,
    )
    with flow.store.connect() as conn:
        regions = coverage(conn, flow._upload(conn, p.preview_id))[0]["regions"]
        assert next(r for r in regions if "F8" in r["coordinates"])["role"] == "note"
        assert next(r for r in regions if "A8" in r["coordinates"])["role"] == "auxiliary"
