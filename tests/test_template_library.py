"""R3-B verification: all 7 minimal-change standard templates generate and round-trip parse cleanly."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

from openpyxl import load_workbook

from stability_hub.specification_preview import inspect_specification
from stability_hub.template_library import export_template_pack, list_templates, render_template
from stability_hub.xlsx_matrix import parse_matrix_sheet


def test_all_standard_templates_generate_and_export(tmp_path: Path):
    templates = list_templates()
    assert len(templates) == 7
    written = export_template_pack(tmp_path / "pack")
    assert len(written) == 7
    assert all(p.is_file() and p.stat().st_size > 2000 for p in written)


def test_spec_templates_roundtrip_six_categories():
    for tpl_id in ("spec_xlsx", "spec_docx"):
        filename, _, content = render_template(tpl_id)
        parsed = inspect_specification(content, filename=filename)
        assert len(parsed["rows"]) >= 15
        cats = {r["category_code"] for r in parsed["rows"]}
        assert cats == {
            "identification",
            "purity_impurity",
            "potency",
            "content",
            "safety",
            "physicochemical_other",
        }


def test_t1_t5_worksheet_and_summary_templates_roundtrip_parse():
    for tpl_id, expected_sheets in [("worksheet_xlsx", 1), ("summary_xlsx", 4)]:
        _, _, content = render_template(tpl_id)
        wb = load_workbook(BytesIO(content), data_only=False)
        parsed_sheets = 0
        for ws in wb.worksheets:
            if ws.title == "项目与批次索引":
                continue
            res = parse_matrix_sheet(ws)
            assert res is not None, f"Failed to parse sheet {ws.title}"
            obs, info = res
            assert len(obs) >= 30
            # T1 split condition combined cleanly without Wingdings2 R checkbox glitch
            assert all("℃" in o.fields.get("condition", "") and "HorizontalR" not in o.fields.get("condition", "") for o in obs)
            # T2 explicit 6-category preserved
            assert all(o.fields.get("category", "") for o in obs)
            # T4 signing status row excluded (none of the observations is '已签报告' or '待检')
            assert all(o.fields.get("result", "") not in {"已签报告", "进行中", "待检"} for o in obs)
            # T5 footer mini-table excluded from observations
            assert all("AR-C-IPXXX" not in o.fields.get("test_item", "") for o in obs)
            # Zero hazards on clean standard template
            assert all(not o.hazards for o in obs)
            parsed_sheets += 1
        assert parsed_sheets == expected_sheets
