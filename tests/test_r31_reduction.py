"""R3.1 regression and guardrail tests: RC-1, RC-2, A-1..A-6, B-1..B-5."""

from decimal import Decimal
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from openpyxl.cell.rich_text import CellRichText, InlineFont, TextBlock

from stability_hub.api import create_app
from stability_hub.matrix_fields import item_text, method_text, normalize_presentation, presentation_lines
from stability_hub.models import ImportContext, Location, ProjectConfig, RawObservation
from stability_hub.normalize import NeedsReview, normalize_month
from stability_hub.numeric_formats import VERSION as NUMERIC_VERSION, interpret_result
from stability_hub.permissions import Role, actor_for_role
from stability_hub.storage import Store
from stability_hub.trend_export import write_png
from stability_hub.workflow import Workflow
from stability_hub.workflow_common import WorkflowError
from stability_hub.worksheet_confirmation import workbook_rows

TECH = actor_for_role("tech", "technician", Role.TECHNICIAN)


# ---------------------------------------------------------------------------
# RC-1: Cross-assay method pollution ("Cap Removal Force" -> R CE-SDS)
# ---------------------------------------------------------------------------
def test_rc1_cap_removal_force_never_inherits_r_ce_sds():
    assert method_text("笔帽拔除力\n（多功能检测仪）\nCap Removal Force\n(Multi-function Tester)", embedded=True) == ""
    assert method_text("Cap Removal Force", embedded=True) == ""
    assert method_text("Injection Force (N)", embedded=True) == ""
    # Valid CE-SDS methods still extract accurately
    assert method_text("纯度（还原CE-SDS法）", embedded=True) == "R CE-SDS"
    assert method_text("纯度（非还原CE-SDS法）", embedded=True) == "NR CE-SDS"
    assert method_text("rCE-SDS", embedded=True) == "R CE-SDS"
    assert method_text("nrCE-SDS", embedded=True) == "NR CE-SDS"
    assert method_text("R CE-SDS", embedded=True) == "R CE-SDS"
    assert method_text("NR CE-SDS", embedded=True) == "NR CE-SDS"


# ---------------------------------------------------------------------------
# RC-2: IP166E PPQ false collisions (particle sizes 2/5/10/25 & CN/US-EU filing)
# ---------------------------------------------------------------------------
def test_rc2_particle_sizes_and_filing_jurisdiction_stay_distinct():
    combos = [
        ("不溶性微粒\n（光阻法）", "≥2μm（中国申报）：报告结果"),
        ("不溶性微粒\n（光阻法）", "≥5μm（中国申报）：报告结果"),
        ("不溶性微粒\n（光阻法）", "≥10μm（中国申报）：≤6000个/容器"),
        ("不溶性微粒\n（光阻法）", "≥25μm（中国申报）：≤600个/容器"),
        ("不溶性微粒\n（光阻法）", "≥2μm（美国申报和欧洲申报）：报告结果"),
        ("不溶性微粒\n（光阻法）", "≥5μm（美国申报和欧洲申报）：报告结果"),
        ("不溶性微粒\n（光阻法）", "≥10μm（美国申报和欧洲申报）：≤6000个/容器"),
        ("不溶性微粒\n（光阻法）", "≥25μm（美国申报和欧洲申报）：≤600个/容器"),
    ]
    names = [item_text(parent, crit, method="光阻") for parent, crit in combos]
    assert len(set(names)) == 8, f"Expected 8 distinct names, got {names}"

    # Dose accuracy with nested parenthetical jurisdiction inside test_item
    cn_parent = "剂量准度\n（称重法（中国申报））"
    eu_parent = "剂量准度\n（称重法（美国申报和欧洲申报））"
    cn_method = method_text(cn_parent, embedded=True)
    eu_method = method_text(eu_parent, embedded=True)
    assert cn_method == "称重"
    assert eu_method == "称重"
    cn_item = item_text(cn_parent, "±10%", method=cn_method)
    eu_item = item_text(eu_parent, "±10%", method=eu_method)
    assert cn_item != eu_item
    assert "中国申报" in cn_item
    assert "美国/欧洲申报" in eu_item


# ---------------------------------------------------------------------------
# A-1 & A-2: Bilingual presentation merge guardrail & spacing normalization
# ---------------------------------------------------------------------------
def test_a1_a2_presentation_merge_and_normalization():
    # Matching dose, volume, AND container word -> merge to 1 line
    assert len(presentation_lines("3mg（0.5 ml）/支\n3mg (0.5ml)/syringe")) == 1
    assert len(presentation_lines("10 ml/瓶\n10mL/vial")) == 1
    assert len(presentation_lines("100mg/瓶\n100 mg/bottle")) == 1

    # Strict container guardrail: 支 vs vial must NOT merge
    assert len(presentation_lines("3ml/支\n3ml/vial")) == 2
    assert len(presentation_lines("1mg（0.5 ml）/支\n1mg (0.5ml)/vial")) == 2
    # Different dose must NOT merge
    assert len(presentation_lines("1mg/瓶\n3mg/bottle")) == 2

    # A-2 spacing & fullwidth normalization
    assert normalize_presentation("10 ml/瓶") == "10ml/瓶"
    assert normalize_presentation("10ml/瓶") == "10ml/瓶"
    assert normalize_presentation("1mg（0.5 ml）/支") == "1mg(0.5ml)/支"


# ---------------------------------------------------------------------------
# A-3: Inline numeric + unit parsing with strict unit equivalence guardrail
# ---------------------------------------------------------------------------
def _raw_cell(val: str) -> RawObservation:
    return RawObservation(
        adapter="stability_matrix",
        location=Location(format="xlsx", section="Sheet1", row=5, column=3),
        fields={"result": val},
        source_value={"raw_value": val, "raw_type": "s", "number_format": "General", "rule_version": NUMERIC_VERSION},
    )


def test_a3_inline_unit_numeric_and_conflict_guardrail():
    # Equivalent unit -> parsed as numeric, preserving raw_text
    res = interpret_result(_raw_cell("286 mOsmol/kg"), "286 mOsmol/kg", "mOsmol/kg", "qualitative")
    assert res.status == "numeric"
    assert res.value == Decimal("286")
    assert res.unit == "mOsmol/kg"
    assert res.raw_text == "286 mOsmol/kg"
    assert res.interpretation["rule"] == "inline_unit"

    # Comparator + equivalent inline unit
    res_lt = interpret_result(_raw_cell("< 0.05 EU/mg"), "< 0.05 EU/mg", "EU/mg", "numeric")
    assert res_lt.status == "numeric"
    assert res_lt.comparator == "lt"
    assert res_lt.value == Decimal("0.05")

    # Non-equivalent / conversion-needed inline unit -> strictly intercepted
    with pytest.raises(NeedsReview) as exc1:
        interpret_result(_raw_cell("286 mg/mL"), "286 mg/mL", "mOsmol/kg", "numeric")
    assert exc1.value.code == "unit_conflict"

    with pytest.raises(NeedsReview) as exc2:
        interpret_result(_raw_cell("0.5 g/L"), "0.5 g/L", "mg/mL", "qualitative")
    assert exc2.value.code == "unit_conflict"


# ---------------------------------------------------------------------------
# A-6: Redundant method parenthetical removal + (cpB酶切) anti-regression
# ---------------------------------------------------------------------------
def test_a6_strip_redundant_method_parens_and_preserve_cpb_digest():
    # Redundant method parens stripped from item prefix (no double method text)
    assert item_text("电荷变异体（icIEF法）", "", method="icIEF") == "电荷变异体 · icIEF"
    assert item_text("纯度（SEC-HPLC法）Purity", "", method="SEC-HPLC") == "纯度 · SEC-HPLC"
    assert item_text("纯度（NR CE-SDS）", "", method="NR CE-SDS") == "纯度 · NR CE-SDS"

    # STRICT ANTI-REGRESSION: distinguishing pre-treatment/condition modifiers MUST survive!
    cpb = item_text("电荷变异体（cpB酶切）", "", method="icIEF")
    assert "cpB酶切" in cpb, f"cpB酶切 was wrongly stripped: {cpb!r}"

    cpb_mixed = item_text("电荷变异体（cpB酶切，icIEF法）", "", method="icIEF")
    assert "cpB酶切" in cpb_mixed, f"cpB酶切 was wrongly stripped: {cpb_mixed!r}"

    trypsin = item_text("肽图（胰酶酶切）", "", method="RP-HPLC")
    assert "胰酶酶切" in trypsin, f"胰酶酶切 was wrongly stripped: {trypsin!r}"


# ---------------------------------------------------------------------------
# A-5: Confirmation export qualitative whitespace stripping (raw_json untouched)
# ---------------------------------------------------------------------------
def test_a5_confirmation_export_strips_qualitative_whitespace_display_only():
    snapshot = {
        "snapshot_id": "snap-1",
        "worksheet": {
            "name": "B1 · 长期",
            "project": "P1",
            "basis": "测试",
            "start_date": "2026-01-01",
            "plan_version": 1,
            "identity": {"batch": "B1", "study_id": "S1", "condition": "5℃", "orientation": "正立"},
            "points": [{"id": "pt0", "label": "0M", "planned_date": "2026-01-01"}],
            "rows": [
                {
                    "id": "r1",
                    "label": "外观",
                    "method": "目测法",
                    "method_version": "",
                    "unit": "",
                    "category_code": "general",
                    "category_order": 1,
                    "display_order": 1,
                    "category_name_zh": "一般检查",
                    "subcategory_name_zh": "外观",
                }
            ],
            "cells": [
                {
                    "row_id": "r1",
                    "point_id": "pt0",
                    "adopted": {"result_version_id": "v1"},
                }
            ],
        },
        "results": {
            "v1": {"result": {"status": "text", "raw_text": "  澄清无色液体 \n ", "unit": ""}}
        },
        "result_notes": {},
    }
    raw_before = snapshot["results"]["v1"]["result"]["raw_text"]
    rows = list(workbook_rows(snapshot))
    # Source raw_text untouched
    assert snapshot["results"]["v1"]["result"]["raw_text"] == raw_before
    data_row = next(r for r in rows if r and r[0] == "外观")
    assert data_row[4] == "澄清无色液体"


# ---------------------------------------------------------------------------
# B-3 (error localization) & B-5 (Trend PNG Chinese unitless + 0-point 422)
# ---------------------------------------------------------------------------
def test_b3_chinese_timepoint_error_and_b5_trend_png(tmp_path):
    from stability_hub.models import RecordKey, Result, StabilityRecord

    with pytest.raises(NeedsReview) as exc:
        normalize_month("非稳定性样品")
    assert "Expected integer months" not in str(exc.value)
    assert "时间点" in str(exc.value)

    # B-5: 0 numeric points raises 422 chart_no_numeric_points
    qual_rec = StabilityRecord(
        key=RecordKey(
            project="P1", sample_type="DP", batch="B1", presentation="1ml/支",
            study="long_term", study_id="S1", condition="5C", purpose="routine",
            reporting_basis="", month=0, day=None, item="外观", method="目测法", method_version="not_reported",
        ),
        result=Result(status="text", raw_text="澄清无色液体", unit=""),
    )
    qual_row = {"id": "v1", "slot_id": "s1", "state": "confirmed", "source_json": "[]", "record_json": qual_rec.model_dump_json()}
    empty_snapshot = {
        "snapshot_id": "snap-empty",
        "project": "P1",
        "revision": 1,
        "filters": {},
        "item_labels": {"外观": "外观"},
        "entries": [
            {"adopted": qual_row, "display": qual_row}
        ],
    }
    with pytest.raises(WorkflowError) as png_exc:
        write_png(tmp_path / "empty.png", empty_snapshot)
    assert png_exc.value.code == "chart_no_numeric_points"
    assert png_exc.value.status == 422

    # B-5: unitless pH renders cleanly with Chinese label (no 'source unit not reported')
    ph_entries = []
    for m, v in ((0, "6.2"), (3, "6.1")):
        rec = StabilityRecord(
            key=RecordKey(
                project="P1", sample_type="DP", batch="B1", presentation="1ml/支",
                study="long_term", study_id="S1", condition="5C", purpose="routine",
                reporting_basis="", month=m, day=None, item="pH值", method="pH计法", method_version="not_reported",
            ),
            result=Result(status="numeric", raw_text=v, unit="", value=Decimal(v), comparator="eq"),
        )
        row = {"id": f"v-{m}", "slot_id": f"s-{m}", "state": "confirmed", "source_json": "[]", "record_json": rec.model_dump_json()}
        ph_entries.append({"adopted": row, "display": row})
    ph_snapshot = {
        "snapshot_id": "snap-ph",
        "project": "P1",
        "revision": 1,
        "filters": {},
        "item_labels": {"pH值": "pH值"},
        "entries": ph_entries,
    }
    out = tmp_path / "ph.png"
    manifest = write_png(out, ph_snapshot)
    assert out.is_file() and out.stat().st_size > 5000
    assert manifest["item_label"] == "pH值"


# ---------------------------------------------------------------------------
# End-to-end API tests for A-4, B-1, B-2, B-3, B-4
# ---------------------------------------------------------------------------
def _make_matrix_xlsx(
    tmp_path,
    filename,
    *,
    purity_0m=99.1,
    purity_3m=98.5,
    tail_note=None,
    strike_criterion=False,
    include_start_date=True,
):
    wb = Workbook()
    ws = wb.active
    ws.title = "DP-5C"
    ws["A1"] = "DP长期稳定性数据（5℃）"
    ws["A2"] = "批号"
    ws["B2"] = "B-R31"
    if include_start_date:
        ws["C2"] = "开始日期"
        ws["D2"] = "2026-01-01"
    ws["A3"] = "检测项目"
    ws["B3"] = "质量标准"
    ws["C3"] = "考察时间（月）"
    ws.merge_cells("C3:D3")
    ws["C4"] = "0M"
    ws["D4"] = "3M"
    ws["A5"] = "纯度（SEC-HPLC法）"
    if strike_criterion:
        ws["B5"] = CellRichText(
            TextBlock(InlineFont(strike=True), "≥90%"),
            TextBlock(InlineFont(strike=False), "≥95%"),
        )
    else:
        ws["B5"] = "≥95%"
    ws["C5"] = purity_0m
    ws["D5"] = purity_3m
    if tail_note:
        ws["B9"] = tail_note
    path = tmp_path / filename
    wb.save(path)
    return path.read_bytes()


def test_a4_b1_b2_b4_api_end_to_end(tmp_path):
    app = create_app(Store(tmp_path / "store"), [], actor=TECH)
    client = TestClient(app)
    assert client.post("/api/v1/projects", json={"project": "PR31", "name": "R31测试"}).status_code == 200

    ctx_json = ImportContext(
        role="stability_source", defaults={"presentation": "1ml/支", "study_id": "ST-R31"}
    ).model_dump_json()

    # Upload 1: has tail region (B-1) and strikethrough criterion (B-4)
    long_note = "表尾绘图与计算辅助备注区域用于测试前三十字摘要截断显示是否完整正确"
    raw1 = _make_matrix_xlsx(tmp_path, "u1.xlsx", tail_note=long_note, strike_criterion=True)
    r_up1 = client.post(
        "/api/v1/projects/PR31/previews",
        files={"file": ("u1.xlsx", raw1, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"context": ctx_json},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert r_up1.status_code == 200, r_up1.text
    p1 = r_up1.json()
    pid1 = p1["preview_id"]

    # Also upload u2 BEFORE u1 commits, saving initial decision on 0M (99.1) so u1 commit triggers previous_changed on u2's same_value 0M!
    raw2 = _make_matrix_xlsx(tmp_path, "u2.xlsx", purity_0m=99.1, purity_3m=97.0)
    r_up2 = client.post(
        "/api/v1/projects/PR31/previews",
        files={"file": ("u2.xlsx", raw2, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"context": ctx_json},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert r_up2.status_code == 200, r_up2.text
    p2 = r_up2.json()
    pid2 = p2["preview_id"]
    client.post(
        f"/api/v1/previews/{pid2}/receive",
        json={"base_revision": p2["base_revision"], "idempotency_key": uuid4().hex, "candidate_ids": [c["id"] for c in p2["candidates"]]},
    )
    c2_0m = next(c for c in p2["candidates"] if c["raw_fields"].get("time_point") == "0M")
    p2_fresh = client.get(f"/api/v1/previews/{pid2}").json()
    c2_0m_fresh = next(c for c in p2_fresh["candidates"] if c["id"] == c2_0m["id"])
    r_dec = client.post(
        f"/api/v1/previews/{pid2}/v5/decisions",
        json={
            "base_revision": p2_fresh["base_revision"],
            "idempotency_key": uuid4().hex,
            "reason": "先暂存首次决定",
            "candidate_id": c2_0m_fresh["id"],
            "candidate_revision": c2_0m_fresh["candidate_revision"],
            "action": "initial",
        },
    )
    assert r_dec.status_code == 200, r_dec.text

    # Receive sheet 1
    p1_fresh = client.get(f"/api/v1/previews/{pid1}").json()
    r_rec = client.post(
        f"/api/v1/previews/{pid1}/receive",
        json={"base_revision": p1_fresh["base_revision"], "idempotency_key": uuid4().hex, "candidate_ids": [c["id"] for c in p1_fresh["candidates"]]},
    )
    assert r_rec.status_code == 200, r_rec.text

    # Inspect review-actions for B-1 & B-4
    act1 = client.get(f"/api/v1/previews/{pid1}/v5/review-actions", params={"section": "DP-5C"}).json()
    groups_by_id = {g["group_id"]: g for g in act1["groups"]}
    assert "ag_reg_DP-5C" in groups_by_id
    assert "ag_std_DP-5C" in groups_by_id

    # B-4: task card for criterion strikethrough shows row-wide apply label
    rev_cards = [c for c in act1["task_cards"] if c["code"] == "source_revision"]
    assert rev_cards and any("按行一键应用至整行 2 个时间点" in q["label"] for q in rev_cards[0]["quick_choices"])

    # B-1: preview region batch -> must contain coordinates + <=30-char text_preview
    reg_prev = client.post(f"/api/v1/previews/{pid1}/v5/tasks/preview", json=groups_by_id["ag_reg_DP-5C"]["scope"]).json()
    assert reg_prev["changes"]
    ch0 = reg_prev["changes"][0]
    assert "B9" in ch0["coordinates"]
    assert len(ch0["text_preview"]) == 30
    assert ch0["text_truncated"] is True
    assert ch0["text_preview"] == long_note[:30]

    # Apply B-1 and B-4 batches
    for gid in ("ag_reg_DP-5C", "ag_std_DP-5C"):
        g = groups_by_id[gid]
        pv = client.post(f"/api/v1/previews/{pid1}/v5/tasks/preview", json=g["scope"]).json()
        ap = client.post(
            f"/api/v1/previews/{pid1}/v5/tasks/apply",
            json={
                "scope": g["scope"],
                "base_revision": pv["base_revision"],
                "fingerprint": pv["fingerprint"],
                "reason": f"测试批量应用 {gid}",
                "idempotency_key": uuid4().hex,
            },
        )
        assert ap.status_code == 200, ap.text

    # Commit sheet 1
    pf1 = client.get(f"/api/v1/previews/{pid1}/v5/preflight", params={"section": "DP-5C"}).json()
    assert pf1["can_commit"] is True
    cm1 = client.post(
        f"/api/v1/previews/{pid1}/v5/commit",
        json={
            "section": "DP-5C",
            "base_revision": pf1["base_revision"],
            "fingerprint": pf1["fingerprint"],
            "reason": "首次提交",
            "idempotency_key": uuid4().hex,
        },
    )
    assert cm1.status_code == 200, cm1.text

    # A-4: sync-worksheets without material_stages returns needs_input + suggestion (no silent write)
    sync_empty = client.post(f"/api/v1/previews/{pid1}/sync-worksheets", params={"section": "DP-5C"}).json()
    ws_items = sync_empty["worksheet_sync"]
    assert len(ws_items) == 1
    assert ws_items[0]["status"] == "needs_input"
    assert ws_items[0]["suggested_material_stage"] == "制剂"
    assert "样品材料阶段" in ws_items[0]["missing_fields"]

    # A-4: sync-worksheets with operator-confirmed material_stages creates worksheet cleanly
    slot_id = ws_items[0]["slot_id"]
    sync_ok = client.post(
        f"/api/v1/previews/{pid1}/sync-worksheets",
        params={"section": "DP-5C"},
        json={"material_stages": {slot_id: "制剂"}},
    ).json()
    assert sync_ok["worksheet_sync"][0]["status"] == "created"

    # B-2: Now inspect u2 (which has 1 same_value previous_changed 0M=99.1 + 1 different_value 3M=97.0)
    act2 = client.get(f"/api/v1/previews/{pid2}/v5/review-actions", params={"section": "DP-5C"}).json()
    rep_group = next((g for g in act2["groups"] if g["group_id"] == "ag_repeat_DP-5C"), None)
    assert rep_group is not None
    # STRICT GUARDRAIL: ONLY the 1 same_value task is in ag_repeat_, NOT the different_value task!
    assert rep_group["decision_count"] == 1
    assert rep_group["affected_result_count"] == 1

    # Attempting repeat_source on the different_value task MUST be rejected into exceptions
    diff_task = next(
        c for c in act2["task_cards"] if c["code"] == "history" and c["task_id"] not in rep_group["scope"]["task_ids"]
    )
    bad_prev = client.post(
        f"/api/v1/previews/{pid2}/v5/tasks/preview",
        json={"section": "DP-5C", "task_ids": [diff_task["task_id"]], "operation": "repeat_source"},
    ).json()
    assert len(bad_prev["changes"]) == 0
    assert len(bad_prev["exceptions"]) == 1

    # Applying ag_repeat_ resolves the same_value task in 1 call
    rep_prev = client.post(f"/api/v1/previews/{pid2}/v5/tasks/preview", json=rep_group["scope"]).json()
    assert len(rep_prev["changes"]) == 1
    rep_ap = client.post(
        f"/api/v1/previews/{pid2}/v5/tasks/apply",
        json={
            "scope": rep_group["scope"],
            "base_revision": rep_prev["base_revision"],
            "fingerprint": rep_prev["fingerprint"],
            "reason": "确认同值重复来源",
            "idempotency_key": uuid4().hex,
        },
    )
    assert rep_ap.status_code == 200, rep_ap.text
    # Only the 1 different_value task remains!
    assert rep_ap.json()["remaining_tasks"] == 1


def test_b3_bulk_identity_groups_batch_fill(tmp_path):
    app = create_app(Store(tmp_path / "store_b3"), [], actor=TECH)
    client = TestClient(app)
    assert client.post("/api/v1/projects", json={"project": "PB3", "name": "B3测试"}).status_code == 200

    # Workbook with 2 separate blocks missing sample_type, study, condition
    wb = Workbook()
    ws = wb.active
    ws.title = "Panel"
    for idx, r0 in enumerate((1, 8), start=1):
        ws[f"A{r0}"] = f"批号：LOT-{idx}"
        ws[f"A{r0+1}"] = "检测项目"
        ws[f"B{r0+1}"] = "质量标准"
        ws[f"C{r0+1}"] = "考察时间（月）"
        ws[f"C{r0+2}"] = "0M"
        ws[f"A{r0+3}"] = "纯度"
        ws[f"B{r0+3}"] = "≥95%"
        ws[f"C{r0+3}"] = 99.0 + idx * 0.1
    path = tmp_path / "b3.xlsx"
    wb.save(path)

    r_up = client.post(
        "/api/v1/projects/PB3/previews",
        files={"file": ("b3.xlsx", path.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"context": ImportContext(role="stability_source", defaults={"presentation": "1ml/支"}).model_dump_json()},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert r_up.status_code == 200, r_up.text
    p = r_up.json()
    pid = p["preview_id"]
    client.post(
        f"/api/v1/previews/{pid}/receive",
        json={"base_revision": p["base_revision"], "idempotency_key": uuid4().hex, "candidate_ids": [c["id"] for c in p["candidates"]]},
    )

    act = client.get(f"/api/v1/previews/{pid}/v5/review-actions", params={"section": "Panel"}).json()
    bulk_groups = act["bulk_identity_groups"]
    assert bulk_groups, f"Expected bulk_identity_groups, got {act['task_cards']}"
    bg = bulk_groups[0]
    assert bg["task_count"] >= 2

    fill_fields = {f: {"sample_type": "DP", "study": "long_term", "condition": "5℃"}[f] for f in bg["fields"] if f in {"sample_type", "study", "condition"}}
    scope = {**bg["scope"], "fields": fill_fields}
    pv = client.post(f"/api/v1/previews/{pid}/v5/tasks/preview", json=scope).json()
    assert len(pv["changes"]) == bg["task_count"]
    ap = client.post(
        f"/api/v1/previews/{pid}/v5/tasks/apply",
        json={
            "scope": scope,
            "base_revision": pv["base_revision"],
            "fingerprint": pv["fingerprint"],
            "reason": "批量补齐身份",
            "idempotency_key": uuid4().hex,
        },
    )
    assert ap.status_code == 200, ap.text
    assert ap.json()["remaining_tasks"] == 0
