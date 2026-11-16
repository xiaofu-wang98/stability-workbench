"""Positive structural proof and counterexamples; source and results remain intact."""

from datetime import datetime
from uuid import uuid4

import pytest
from openpyxl import Workbook

from stability_hub.matrix_fields import presentation_lines
from stability_hub.review_issues import comment_requires_review
from stability_hub.source_notes import SUMMARY_INSTRUCTIONS, review_text
from stability_hub.worksheet_regions import structural_roles, coverage
from stability_hub.xlsx_matrix import source_cells
from stability_hub import source_structure
from stability_hub.v5_contracts import SourceStructureApply
from stability_hub.workflow_common import WorkflowError
from test_v5_regressions import TECH, preview


def test_bag_equivalence_keeps_distinct_strength_and_unknown_container():
    assert presentation_lines("3ml/袋\n3ml/bag") == ["3ml/袋"]
    assert len(presentation_lines("3ml/袋\n4ml/bag")) == 2
    assert len(presentation_lines("3ml/支\n3ml/vial")) == 2


def test_only_complete_template_is_nonblocking_mixed_fact_remains():
    template = "\n".join(SUMMARY_INSTRUCTIONS)
    assert not comment_requires_review("表后说明（未自动修改结果）：\nA67: " + template)
    actual = template + "\n3M结果更正为98%，发起AE-123调查。"
    assert comment_requires_review(actual)
    assert review_text(actual) == "3M结果更正为98%，发起AE-123调查。"
    assert comment_requires_review("稳定性总结：结果异常，请调查OOS。")


def test_two_physical_comments_can_be_resolved_independently(tmp_path, xlsx, context):
    from stability_hub.models import RawObservation
    from stability_hub.review_issues import source_issues, resolved_raw

    flow, p = preview(tmp_path, xlsx([["Purity", "0M", 99, "%"]]), context)
    with flow.store.connect() as conn:
        raw = RawObservation.model_validate_json(
            conn.execute("SELECT raw_json FROM workflow_candidates").fetchone()[0]
        )
    raw = raw.model_copy(update={"comment": "C8: 本次3M结果需要更正。\nD9: 另一检项需要复测。"})
    issues = [i for i in source_issues("candidate", raw) if i.code == "source_comment"]
    assert len(issues) == 2
    assert [(i.evidence[0].row, i.evidence[0].column) for i in issues] == [(8, 3), (9, 4)]
    effective, remaining = resolved_raw(
        None, "candidate", raw, [{"issue_id": issues[0].id, "action": "keep_body"}], corrections={}
    )
    assert [i.id for i in remaining if i.code == "source_comment"] == [issues[1].id]
    assert "需要更正" not in effective.comment and "需要复测" in effective.comment


def test_date_location_and_criteria_need_structural_proof():
    s = Workbook().active
    for cell, value in {
        "A1": "开始日期\nStarting Date",
        "B1": datetime(2026, 1, 1),
        "C1": "稳定性开展地点\nStability Location",
        "D1": "地点X",
        "A3": "检验项目",
        "B3": "可接受标准",
        "C3": "考察时间（月）",
        "C4": "0M",
        "D4": 3,
        "E4": 6,
        "D5": "=EDATE(B1,D4)",
        "E5": "=SUM(1,2)",
        "A6": "pH",
        "B6": "报告结果",
        "C6": 5.4,
        "A8": "备注\nRemark",
        "B8": "判断标准：报告结果。",
    }.items():
        s[cell] = value
    summary = {
        "blocks": [{"header_row": 3, "end_row": 6, "time_columns": [3, 4, 5]}, {"planning_date_row": 5}]
    }
    roles = structural_roles(s, source_cells(s), summary)
    assert roles[1, 2][0] == roles[1, 4][0] == "identity"
    assert roles[5, 4][0] == "auxiliary"
    assert (5, 5) not in roles  # An unknown formula remains unresolved.
    assert roles[8, 2][0] == "standard"
    s["B8"] = "判断标准：报告结果。本次结果异常，需要复测。"
    assert (8, 2) not in structural_roles(s, source_cells(s), summary)
    # The same labels in the result body cannot consume adjacent numeric facts.
    s["A6"] = "开始日期"
    assert (6, 3) not in structural_roles(s, source_cells(s), summary)


def test_saved_structure_version_is_explicit_revision_guarded_and_audited(
    tmp_path, xlsx, context, monkeypatch
):
    flow, p = preview(tmp_path, xlsx([["Purity", "0M", 99, "%"]]), context)
    with flow.store.connect() as conn:
        upload = flow._upload(conn, p.preview_id)
        before = coverage(conn, upload)
        region = next(r for s in before for r in s["regions"] if r["role"] == "identity")
        # Model a pre-V5.2 unknown caption, keeping the frozen source and candidates.
        import stability_hub.worksheet_regions as wr

        real_coverage = wr.coverage

        def old_coverage(conn, upload):
            sheets = real_coverage(conn, upload)
            for sheet in sheets:
                for r in sheet["regions"]:
                    if (
                        r["id"] == region["id"]
                        and not conn.execute(
                            "SELECT 1 FROM workflow_events WHERE action='source_structure'"
                        ).fetchone()
                    ):
                        r["role"] = "unclassified"
            return sheets

        monkeypatch.setattr(wr, "coverage", old_coverage)
        info = source_structure.preview(flow, conn, p.preview_id)
        assert any(c["region_id"] == region["id"] for c in info["changes"])
        raw_before = [tuple(r) for r in conn.execute("SELECT id,raw_json,lifecycle FROM workflow_candidates")]
    bad = SourceStructureApply(
        base_revision=info["base_revision"], idempotency_key="bad", fingerprint="wrong", reason="核对来源用途"
    )
    with pytest.raises(WorkflowError, match="来源用途预览"):
        source_structure.apply(flow, p.preview_id, bad, actor=TECH)
    req = bad.model_copy(update={"idempotency_key": uuid4().hex, "fingerprint": info["fingerprint"]})
    result = source_structure.apply(flow, p.preview_id, req, actor=TECH)
    assert source_structure.apply(flow, p.preview_id, req, actor=TECH) == result
    with flow.store.connect() as conn:
        assert [
            tuple(r) for r in conn.execute("SELECT id,raw_json,lifecycle FROM workflow_candidates")
        ] == raw_before
        assert conn.execute("SELECT count(*) FROM workflow_versions").fetchone()[0] == 0
        after = real_coverage(conn, flow._upload(conn, p.preview_id))
        assert next(r for s in after for r in s["regions"] if r["id"] == region["id"])["role"] == "identity"
    from stability_hub.worksheet_regions import classify
    from stability_hub.v5_contracts import RegionDecision

    classify(
        flow,
        p.preview_id,
        RegionDecision(
            base_revision=result.base_revision,
            idempotency_key=uuid4().hex,
            reason="人工重新要求核对用途",
            region_id=region["id"],
            role="unclassified",
        ),
        actor=TECH,
    )
    with flow.store.connect() as conn:
        assert not any(
            c["region_id"] == region["id"]
            for c in source_structure.preview(flow, conn, p.preview_id)["changes"]
        )
