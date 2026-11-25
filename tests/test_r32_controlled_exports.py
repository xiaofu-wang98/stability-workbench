"""R3.2 verification: controlled confirmation Excel + TPL-07 parity, Changshu summary enhancements, pH axis & Chinese PNG."""
from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import load_workbook
from PIL import Image

from stability_hub.api import create_app
from stability_hub.permissions import Role, actor_for_role
from stability_hub.plot_axes import axis_range
from stability_hub.stability_summary import (
    controlled_block,
    controlled_conditions,
    format_location_checkboxes,
    write_controlled_workbook,
)
from stability_hub.storage import Store
from stability_hub.template_library import render_template
from stability_hub.worksheet_confirmation import (
    verify_confirmation,
    workbook_rows,
    write_confirmation,
)

ADMIN = actor_for_role("admin", "管理员", Role.ADMIN)


def test_confirmation_and_tpl07_have_identical_controlled_layout(tmp_path: Path):
    _, _, tpl_bytes = render_template("confirmation_xlsx")
    tpl_wb = load_workbook(BytesIO(tpl_bytes), data_only=False)
    try:
        ws = tpl_wb["稳定性确认表"]
        assert not getattr(ws, "_images", [])  # Logo-free pure Excel
        assert ws.page_setup.orientation == "landscape"
        assert str(ws.page_setup.paperSize) == str(ws.PAPERSIZE_A4)
        assert ws.page_setup.fitToWidth == 1
        assert ws.freeze_panes == "E9"
        assert "IPXXX 稳定性试验数据确认表 / Stability Study Data Verification Form" == ws["A1"].value
        assert ws["D4"].value == "□相城工厂X  ☑常熟工厂C  □园区工厂S"
        assert "☑长期试验 Long-term study" in ws["H4"].value
        assert "□加速试验 Accelerated study" in ws["H4"].value
        cond = ws["B5"].value
        assert "温度 Temperature：" in cond and "☑5℃±3℃" in cond
        assert "相对湿度 Relative humidity：" in cond and "☑其他 Others：避光" in cond
        assert "放置方式 Placement method：" in cond and "☑倒置 Inverted" in cond
        assert [ws.cell(7, c).value for c in range(1, 5)] == [
            "大类\nCategory",
            "检验项目（方法）\nTest Item (Method)",
            "子检项/单位\nSub-item & Unit",
            "可接受标准\nAcceptance Criteria",
        ]
        assert ws["E7"].value == "0M" and ws["E8"].value == "2026-06-01"
        assert ws["A9"].value == "1.鉴别"
        assert ws["D9"].value  # Criteria displayed inline in Col D
        assert ws["E9"].value == "与参考品图谱一致"

        # Verify 6-category ordering, zero formulas, and QC-运营 footer blocks
        all_rows = list(ws.iter_rows(values_only=False))
        assert not any(c.data_type == "f" for r in all_rows for c in r)
        first_col = [r[0].value or "" for r in all_rows]
        assert "备注\nRemark" in first_col
        assert any(v.startswith("稳定性总结") for v in first_col)
        assert "起草人/日期\nAuthor/Date" in first_col
        assert "QC批准人/日期\nQC Approver/Date" in first_col
        assert "变更历史 / Change History" in first_col
        assert "版本号\nVersion" in first_col
        assert "00" in first_col
    finally:
        tpl_wb.close()

    # Real snapshot export goes through workbook_rows -> write_confirmation -> verify_confirmation
    snapshot = {
        "snapshot_id": "snap-r32-check",
        "project_name": "IP281 注射液",
        "worksheet": {
            "project": "IP281",
            "name": "IP281 24M 确认",
            "product_name": "IP281 注射液",
            "plan_version": 2,
            "start_date": "2024-08-02",
            "location": "常熟工厂",
            "protocol_number": "STB(C)-QC-IP281-P-2-003",
            "specification_text": "100 mg/瓶",
            "equipment": "A-8111N",
            "storage_location": "5℃箱-01",
            "basis": "方案 V2",
            "identity": {
                "batch": "IP281DPC20240801",
                "sample_type": "DP",
                "presentation": "100 mg/瓶",
                "study": "accelerated",
                "study_id": "STB(C)-QC-IP281-P-2-003",
                "condition": "25±2℃ · 60±5%RH",
                "orientation": "正立",
                "reporting_basis": "",
            },
            "points": [
                {"id": "p0", "label": "0M", "planned_date": "2024-08-02", "required_row_ids": ["r1", "r2"]},
                {"id": "p3", "label": "3M", "planned_date": "2024-11-02", "required_row_ids": ["r1", "r2"]},
            ],
            "rows": [
                {
                    "id": "r2",
                    "label": "pH值",
                    "method": "pH计法",
                    "method_version": "",
                    "unit": "",
                    "criteria_text": "5.5~6.5",
                    "category_code": "physicochemical_other",
                    "category_name_zh": "6.理化与其它",
                    "subcategory_name_zh": "pH",
                    "category_order": 6,
                    "display_order": 601,
                },
                {
                    "id": "r1",
                    "label": "纯度（SEC-HPLC）",
                    "method": "SEC-HPLC",
                    "method_version": "",
                    "unit": "%",
                    "criteria_text": "≥95.0%",
                    "category_code": "purity_impurity",
                    "category_name_zh": "2.纯度和杂质",
                    "subcategory_name_zh": "单体",
                    "category_order": 2,
                    "display_order": 201,
                },
            ],
            "cells": [
                {"row_id": "r1", "point_id": "p0", "adopted": {"result_version_id": "v1"}},
                {"row_id": "r2", "point_id": "p0", "adopted": {"result_version_id": "v2"}},
            ],
            "events": [
                {
                    "category": "异常调查",
                    "item_label": "纯度（SEC-HPLC）",
                    "point_label": "3M",
                    "reference": "AR-C-IP281-001",
                    "description": "复核确认无影响",
                    "scan_id": "",
                }
            ],
        },
        "results": {
            "v1": {"result": {"status": "numeric", "comparator": "eq", "value": "99.2", "raw_text": "99.2", "unit": "%"}},
            "v2": {"result": {"status": "numeric", "comparator": "eq", "value": "6.0", "raw_text": "6.0", "unit": ""}},
        },
    }
    rows = tuple(workbook_rows(snapshot))
    out = tmp_path / "real_confirmation.xlsx"
    write_confirmation(out, rows)
    verify_confirmation(out, rows)

    wb = load_workbook(out, data_only=False)
    try:
        ws = wb["稳定性确认表"]
        # Row 9 must be sorted 2.纯度和杂质 BEFORE 6.理化与其它, and first value at E9!
        assert ws["A9"].value == "2.纯度和杂质"
        assert ws["D9"].value == "≥95.0%"
        assert ws["E9"].value == "99.2%"
        assert ws["A10"].value == "6.理化与其它"
        assert ws["E10"].value == "6.0"
        assert ws["D4"].value == "□相城工厂X  ☑常熟工厂C  □园区工厂S"
        assert "☑加速试验 Accelerated study" in ws["H4"].value
        assert "☑25℃±2℃" in ws["B5"].value and "☑60%RH±5%RH" in ws["B5"].value and "☑正置 Upright" in ws["B5"].value
        # Remark automatically includes event reference
        remark_row = next(r for r in ws.iter_rows(values_only=True) if r[0] == "备注\nRemark")
        assert "AR-C-IP281-001" in remark_row[1]
    finally:
        wb.close()


def test_changshu_controlled_summary_enhancements(tmp_path: Path):
    assert format_location_checkboxes("") == "□相城工厂X  □常熟工厂C  □园区工厂S"
    assert format_location_checkboxes("相城工厂") == "☑相城工厂X  □常熟工厂C  □园区工厂S"
    assert format_location_checkboxes("☑常熟工厂") == "□相城工厂X  ☑常熟工厂C  □园区工厂S"
    assert format_location_checkboxes("智享生物技术（苏州）有限公司 S") == "□相城工厂X  □常熟工厂C  ☑园区工厂S"
    assert format_location_checkboxes("外部实验室") == "□相城工厂X  □常熟工厂C  □园区工厂S  ☑其他：外部实验室"

    cond = controlled_conditions("-70±10℃ · 避光", "正立")
    assert "☑其他 Others：-70℃±10℃" in cond
    assert "☑其他 Others：避光" in cond
    assert "☑正置 Upright" in cond
    assert "☑5℃±3℃" in controlled_conditions("5±3℃ · 避光", "正置")

    sheet = {
        "id": "s1",
        "plan_version": 1,
        "project": "IP281",
        "product_name": "IP281 原液",
        "protocol_number": "STB(C)-QC-IP281-P-2-001",
        "specification_text": "10 mg/mL",
        "location": "园区工厂S",
        "equipment": "A-8111N",
        "start_date": "2024-08-02",
        "identity": {
            "sample_type": "DS",
            "study": "long_term",
            "batch": "IP281DSC20240801",
            "presentation": "10 mg/mL",
            "study_id": "STB(C)-QC-IP281-P-2-001",
            "condition": "5℃±3℃ · 避光",
            "orientation": "正置",
            "reporting_basis": "",
        },
        "points": [{"id": "p0", "label": "0M", "amount": 0, "unit": "M", "planned_date": "2024-08-02", "required_row_ids": ["r1", "r2"]}],
        "rows": [
            {
                "id": "r2",
                "item_id": "ph",
                "label": "pH",
                "method": "pH计法",
                "method_version": "not_reported",
                "unit": "",
                "criteria_text": "6.0±0.3",
                "category_code": "physicochemical_other",
                "category_name_zh": "6.理化与其它",
                "category_order": 6,
                "display_order": 601,
            },
            {
                "id": "r1",
                "item_id": "peptide",
                "label": "肽图",
                "method": "RP-UPLC",
                "method_version": "not_reported",
                "unit": "",
                "criteria_text": "与参考品一致",
                "category_code": "identification",
                "category_name_zh": "1.鉴别",
                "category_order": 1,
                "display_order": 101,
            },
        ],
        "cells": [],
    }
    block = controlled_block(sheet)
    assert block["header"][2][9] == "□相城工厂X  □常熟工厂C  ☑园区工厂S"
    assert "☑5℃±3℃" in block["header"][2][2]
    # Body sorted by 6 categories: 1.鉴别 before 6.理化与其它, with category clearly displayed
    assert "1.鉴别" in block["body"][0][0] and "肽图" in block["body"][0][0]
    assert "6.理化与其它" in block["body"][1][0] and "pH" in block["body"][1][0]
    out = tmp_path / "changshu.xlsx"
    assert write_controlled_workbook(out, [{"sample_type": "DS", "condition": "5±3℃", "blocks": [block]}]) == []


def test_ph_axis_overview_smart_span_and_export_axis_passthrough(tmp_path: Path, config, context, xlsx):
    # Smart pH overview scaling (never flat-lined to 0~14)
    narrow = axis_range([7.4, 7.5], "pH", "")
    assert narrow["family"] == "ph"
    assert narrow["min"] == 6.0 and narrow["max"] == 8.5

    with_limits = axis_range([6.0, 6.1, 5.7, 6.3], "pH值", "")
    assert with_limits["min"] == 4.5 and with_limits["max"] == 7.5

    low_edge = axis_range([0.2, 0.4], "pH", "")
    assert low_edge["min"] == 0.0 and low_edge["max"] == 1.5

    high_edge = axis_range([13.5, 13.8], "pH", "")
    assert high_edge["min"] == 12.5 and high_edge["max"] == 14.0

    # End-to-end API parity between /result-trend and POST /exports PNG across overview, detail, and manual
    store = Store(tmp_path / "store")
    app = create_app(store, [config], ADMIN)
    flow = app.state.workflow
    from test_workflow import confirm, upload

    confirm(
        flow,
        upload(
            flow,
            xlsx([["pH", "0M", 7.4, ""], ["pH", "3M", 7.5, ""], ["pH", "6M", "ND", ""]]),
            context,
        ),
    )
    with TestClient(app) as client:
        base = f"/api/v1/projects/{config.project}"
        display = client.get(base + "/result-display").json()
        group = display["analysis_groups"][0]
        rev = display["base_revision"]
        for params in (
            {"axis_mode": "overview"},
            {"axis_mode": "detail"},
            {"axis_mode": "manual", "axis_min": 6.5, "axis_max": 8.0},
        ):
            trend = client.get(
                base + "/result-trend",
                params={"group_id": group["id"], "base_revision": rev, "batches": group["batches"], **params},
            ).json()
            exp = client.post(
                "/api/v1/exports",
                json={
                    "project": config.project,
                    "base_revision": rev,
                    "slot_ids": group["slot_ids"],
                    "include_png": True,
                    **params,
                },
            )
            assert exp.status_code == 200, exp.text
            png_bytes = client.get(exp.json()["image_download_url"]).content
            with Image.open(BytesIO(png_bytes)) as img:
                assert img.size == (1540, 840)
                meta = json.loads(img.info["Description"])
            assert meta["axis"] == trend["axis"]
