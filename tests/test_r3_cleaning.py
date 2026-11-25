"""R3-B verification: Tier-A deterministic cleaning, Tier-B GMP redlines, and targeted footer notes."""
from __future__ import annotations

from decimal import Decimal

from stability_hub.business_notes import note_scope
from stability_hub.cleaning import (
    clean_observation_events,
    extract_exception_refs,
    repair_english_wrap_spaces,
    summarize_cleaning_audit,
)
from stability_hub.models import Location, RawObservation
from stability_hub.numeric_formats import interpret_result
from stability_hub.review_issues import source_issues


def _raw(fields: dict[str, str], *, comment: str = "", raw_type: str = "") -> RawObservation:
    return RawObservation(
        adapter="stability_matrix",
        fields=fields,
        location=Location(format="xlsx", section="长期DS", row=10, column=6),
        comment=comment,
        source_value={"raw_type": raw_type},
    )


def test_a1_comparator_numeric_does_not_crash():
    for text, expected_cmp, expected_val in [
        ("<0.2", "lt", Decimal("0.2")),
        ("＜0.45", "lt", Decimal("0.45")),
        ("≤10", "le", Decimal("10")),
        (">100", "gt", Decimal("100")),
        ("＜0.01", "lt", Decimal("0.01")),
    ]:
        raw = _raw({"test_item": "细菌内毒素", "result": text, "unit": "EU/mL"}, raw_type="")
        res = interpret_result(raw, text, "EU/mL", "numeric")
        assert res.status == "numeric"
        assert res.comparator == expected_cmp
        assert res.value == expected_val
        assert res.raw_text == text


def test_a2_a3_a6_cleaning_events_preserve_raw_and_extract_refs():
    repaired, changed = repair_english_wrap_spaces("Theabsolute\ndifference is0.0 for main peak pl")
    assert changed
    assert repaired == "The absolute difference is 0.0 for main peak pI"

    refs = extract_exception_refs("暂未出结果（详见AR-C-IP318-2026003，关联AE－2026-01）")
    assert refs == ["AR-C-IP318-2026003", "AE-2026-01"]

    obs = _raw({
        "test_item": "细菌内毒素",
        "method": "无菌检查法\n薄膜过滤法",
        "criterion": "Thedifference between main peak pI is≤0.2",
        "result": "＜0.45 (AR-C-IP318-2026003)",
        "unit": "EU/ml",
    })
    summary = summarize_cleaning_audit([obs])
    codes = {entry["rule_code"] for entry in summary["by_rule"]}
    assert "comparator_numeric_fix" in codes
    assert "unit_and_width_normalize" in codes
    assert "english_wrap_space_repair" in codes
    assert "catalog_synonym_canonicalize" in codes
    assert "exception_ref_extraction" in codes


def test_a5_footer_note_targeted_no_broadcast():
    obs = [
        _raw({"test_item": "纯度", "method": "非还原型CE-SDS法", "criterion": "低分子量组分LMW(%): ≤5.0", "time_point": "3M"}),
        _raw({"test_item": "纯度", "method": "非还原型CE-SDS法", "criterion": "主峰Main Peak(%): ≥90.0", "time_point": "3M"}),
        _raw({"test_item": "等电点", "method": "icIEF法", "criterion": "主峰pI差值≤0.2", "time_point": "3M"}),
        _raw({"test_item": "电荷变异体", "method": "icIEF法", "criterion": "酸性峰(%): 报告数据", "time_point": "3M"}),
    ]
    # Glued shorthand nrceLMW targets ONLY nrCE + LMW (index 0), never broadcasting to all 4 rows
    assert note_scope('nrceLMW:"0.2"修改为"0.3"', obs) == {0}
    # Targeted charge variant note hits ONLY index 3, never 等电点 (index 2)
    assert note_scope("长期3M电荷变异体：发起AR-C-IP318-2026003复测", obs) == {3}
    # Generic unaddressed narrative remark never broadcasts to all cells
    assert note_scope("检测组提供本次复核修订说明", obs) == set()


def test_tier_b_substantive_conflicts_always_intercepted():
    # Bilingual method contradiction MUST still raise method_conflict
    raw_method = _raw({
        "test_item": "纯度",
        "method": "还原型CE-SDS法(NR CE-SDS Method)",
        "criterion": "主峰≥90.0%",
        "result": "97.5",
    })
    assert "method_conflict" in {i.code for i in source_issues("c1", raw_method)}

    # Bilingual purity subresult contradiction MUST still raise identity_conflict
    raw_purity = _raw({
        "test_item": "纯度",
        "method": "SEC-HPLC法",
        "criterion": "单体(%) / HMW(%)",
        "result": "99.1",
    })
    assert "identity_conflict" in {i.code for i in source_issues("c2", raw_purity)}

    # Genuine dual-method switch MUST still raise identity_ambiguity
    raw_dual = _raw({
        "test_item": "生物学活性",
        "method": "电化学发光法\n报告基因法",
        "criterion": "70%~130%",
        "result": "102",
    })
    assert "identity_ambiguity" in {i.code for i in source_issues("c3", raw_dual)}


def test_r3_fix1_custom_platform_alias_passthrough_conn(tmp_path):
    from stability_hub.mapping import map_corrected, map_observation
    from stability_hub.models import ImportContext, ItemDefinition, ProjectConfig
    from stability_hub.platform_catalog import add_platform_alias, create_platform_assay
    from stability_hub.storage import Store

    store = Store(tmp_path / "custom_alias.sqlite")
    with store.connect() as conn:
        created = create_platform_assay(
            conn,
            category_code="purity_impurity",
            subcategory_code="adc_conjugation",
            name_zh="游离巯基含量",
            name_en="Free Thiol Content",
            default_unit="mol/mol",
            methods=[{"name_zh": "Ellman法"}],
        )
        add_platform_alias(conn, entity_type="assay", entity_id=created["id"], raw_alias="DTNB游离巯基")

    config = ProjectConfig(
        project="IP901",
        name="IP901",
        items=(
            ItemDefinition(
                id="free_thiol",
                label="游离巯基含量",
                unit="mol/mol",
                result_type="numeric",
                method="Ellman法",
                category_code="purity_impurity",
                subcategory_code="adc_conjugation",
                platform_assay_id=created["id"],
            ),
        ),
    )
    context = ImportContext(
        defaults={
            "sample_type": "DS",
            "batch": "B1",
            "presentation": "10 mg/mL",
            "study": "long_term",
            "study_id": "STB-901",
            "condition": "5±3℃",
        }
    )
    obs = RawObservation(
        fields={"test_item": "DTNB游离巯基", "method": "Ellman法", "time_point": "0M", "result": "0.12", "unit": "mol/mol"},
        location=Location(format="xlsx", section="Sheet1", row=4, column=3),
    )
    with store.read() as conn:
        rec1 = map_observation(obs, context, config, conn=conn)
        rec2 = map_corrected(obs, context, config, {}, conn=conn)
    assert rec1.key.item == "free_thiol"
    assert rec2.key.item == "free_thiol"


def test_r3_fix2_confirmation_and_results_expose_categories():
    from stability_hub.worksheet_confirmation import workbook_rows

    snapshot = {
        "snapshot_id": "snap-1",
        "worksheet": {
            "name": "长期研究",
            "project": "IP501",
            "basis": "方案",
            "start_date": "2026-01-01",
            "plan_version": 1,
            "identity": {"batch": "B1", "study_id": "STB-1", "condition": "5±3℃", "orientation": "正置"},
            "points": [{"id": "p0", "label": "0M", "planned_date": "2026-01-01", "required_row_ids": ["r1", "r2"]}],
            "rows": [
                {
                    "id": "r2", "label": "纯度（SEC） · 单体", "method": "SEC-HPLC", "method_version": "V1", "unit": "%",
                    "category_code": "purity_impurity", "category_name_zh": "2.纯度和杂质",
                    "subcategory_name_zh": "分子大小变异体", "category_order": 2, "display_order": 201,
                },
                {
                    "id": "r1", "label": "肽图", "method": "RP-UPLC", "method_version": "V1", "unit": "",
                    "category_code": "identification", "category_name_zh": "1.鉴别",
                    "subcategory_name_zh": "肽图", "category_order": 1, "display_order": 101,
                },
            ],
            "cells": [],
        },
        "results": {},
    }
    rows = list(workbook_rows(snapshot))
    assert rows[6] == (
        "大类\nCategory",
        "检验项目（方法）\nTest Item (Method)",
        "子检项/单位\nSub-item & Unit",
        "可接受标准\nAcceptance Criteria",
        "0M",
    )
    assert rows[8][0] == "1.鉴别"
    assert "肽图" in rows[8][1]
    assert rows[8][2] == "肽图"
    assert rows[9][0] == "2.纯度和杂质"
    assert "纯度（SEC） · 单体" in rows[9][1]
    assert rows[9][2] == "分子大小变异体 / %"


def test_r3_fix3_t3_subresult_col_no_false_missing_subresult():
    from io import BytesIO
    from openpyxl import Workbook
    from stability_hub.xlsx_matrix import parse_matrix_sheet

    wb = Workbook()
    ws = wb.active
    ws.title = "T3_长期"
    ws.append(["产品批号", "B202601", "产品名称", "IP501 原液", "稳定性类型", "☑长期 □加速", "考察条件", "5℃±3℃"])
    ws.append(["大类", "检验项目", "子检项/细项", "分析方法", "可接受标准", "考察时间（月）", ""])
    ws.append(["", "", "", "", "", "0M", "3M"])
    ws.merge_cells("F2:G2")
    ws.append(["2.纯度和杂质", "纯度", "单体", "SEC-HPLC法", "≥95.0%", "99.1%", "99.0%"])
    ws.append(["2.纯度和杂质", "", "高分子量聚体", "SEC-HPLC法", "≤5.0%", "0.9%", "1.0%"])
    ws.merge_cells("B4:B5")

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    from openpyxl import load_workbook
    loaded = load_workbook(buf, data_only=False)
    obs, _ = parse_matrix_sheet(loaded["T3_长期"])
    assert len(obs) == 4
    for o in obs:
        assert not any(h.get("kind") == "missing_subresult" for h in o.system_hints), o.system_hints


def test_r3_fix4_dual_checked_study_boxes_leave_study_blank():
    from stability_hub.matrix_fields import interpret_matrix_fields

    fields = interpret_matrix_fields(
        {"study": "☑长期  ☑加速  □强力破坏", "sample_type": "原液", "batch": "B01", "condition": "5℃±3℃", "time_point": "0M"},
        "Sheet1",
    )
    assert fields["study"] == ""
    single = interpret_matrix_fields(
        {"study": "☑长期  □加速  □强力破坏", "sample_type": "原液", "batch": "B01", "condition": "5℃±3℃", "time_point": "0M"},
        "Sheet1",
    )
    assert single["study"] == "long_term"


def test_r3_stress_bug1_trailing_space_section_preserved():
    from stability_hub.v5_contracts import SheetCommit, TaskBatchScope

    cmd = SheetCommit(
        base_revision=1,
        idempotency_key="k1",
        reason="commit sheet with trailing space",
        section="注射笔1mg加速 A1 ",
        fingerprint="fp-1",
    )
    assert cmd.section == "注射笔1mg加速 A1 "
    scope = TaskBatchScope(section="注射笔3mg长期 A1 ", operation="exclude")
    assert scope.section == "注射笔3mg长期 A1 "


def test_r3_stress_bug2_oversized_source_status_chunks_roundtrip(tmp_path):
    from stability_hub.models import digest
    from stability_hub.workflow_excel import write_workbook
    from stability_hub.workflow_excel_verify import verify_workbook

    big_coords = [f"A{i}" for i in range(1, 7000)]  # > 40,000 chars when JSON encoded
    snapshot = {
        "project": "IP166",
        "revision": 1,
        "orientation": "horizontal",
        "item_labels": {},
        "filters": {},
        "selected_slot_ids": [],
        "entries": [],
        "drafts": [],
        "pending_source_regions": [
            {
                "upload_id": "upload_big",
                "filename": "big.xlsx",
                "section": "Sheet1",
                "scope": "unassigned",
                "regions": [{"id": "r1", "role": "unclassified", "coordinates": big_coords}],
            }
        ],
    }
    snapshot["snapshot_id"] = digest(snapshot)
    out = tmp_path / "results.xlsx"
    write_workbook(out, snapshot)
    errors = verify_workbook(out, snapshot)
    assert errors == [], errors
