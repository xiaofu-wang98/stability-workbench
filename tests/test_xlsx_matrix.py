"""Matrix adapters preserve physical source evidence and reuse the existing workflow."""

import pytest
from openpyxl import Workbook, load_workbook

from stability_hub.matrix_fields import interpret_matrix_fields, selected_condition
from stability_hub.models import ImportContext, ItemDefinition, ProjectConfig
from stability_hub.parsing import UnsupportedFormat, parse_source
from stability_hub.permissions import Role, actor_for_role
from stability_hub.result_versions import ResultVersions
from stability_hub.storage import Store
from stability_hub.verification import check_original_fields, reread_source_rows
from stability_hub.workflow import Workflow
from stability_hub.workflow_common import WorkflowError
from stability_hub.workflow_contracts import CandidateMapping, ConfirmItem, ConfirmRequest, ExportRequest
from stability_hub.workflow_excel import ExcelExports

TECH = actor_for_role("matrix-tech", "Matrix technician", Role.TECHNICIAN)
LEAD = actor_for_role("lead", "Lead", Role.PROJECT_LEAD)


def test_merged_condition_caption_keeps_actual_value_evidence(tmp_path):
    book = Workbook()
    sheet = book.active
    add_block(sheet)
    sheet['C3'] = sheet['B3'].value
    sheet['B3'] = None
    sheet.merge_cells('A3:B3')
    sheet['A2'] = None
    sheet['B2'] = None
    path = tmp_path / 'merged-caption.xlsx'
    book.save(path)
    raw = parse_source(path).observations[0]
    assert raw.evidence['condition'].column == 3
    assert interpret_matrix_fields(raw.fields, raw.location.section)['condition'] == '5℃±3℃'
    flow = Workflow(Store(tmp_path / 'missing-protocol'))
    flow.configure(ProjectConfig(project='P'))
    preview = flow.upload('P',path.name,path.read_bytes(),ImportContext(),'merged',actor=TECH)
    assert preview.candidates[0].status == 'new'
    assert preview.candidates[0].record.key.study_id.startswith('原文未注明@' + preview.preview_id + ':')


def add_block(sheet, offset=0, batch="B1"):
    values = {
        "A1": "项目编号\nProject No.",
        "B1": "P",
        "C1": "产品名称\nProduct Name",
        "D1": "产品原液 DS",
        "F1": "产品批号\nProduct Batch No.",
        "G1": batch,
        "I1": "规格\nSpecification",
        "J1": "原液",
        "A2": "稳定性试验方案编号",
        "B2": "STB(C)-QC-P-1",
        "I2": "稳定性类型",
        "J2": "□加速试验 R长期试验",
        "A3": "考察条件",
        "B3": "□25℃±2℃ R5℃±3℃ □其他Others:___",
        "A4": "检验项目\nTest Item",
        "B4": "可接受标准",
        "C4": "考察时间（月）",
        "C5": 0,
        "D5": "3\n2025.08.24\n已签",
        "E5": "6M",
        "F5": "12(2026.05.24)",
        "A6": "纯度\n（SEC-HPLC法）",
        "B6": "单体：≥95.0%",
        "C6": 99.5,
        "E6": 99.4,
        "F6": 99.3,
        "B7": "HMW: ≤4.5%",
        "C7": 0.5,
        "D7": "ND",
        "E7": "/",
        "F7": "N/A",
        "B8": "LMW: ≤0.5%",
        "C8": 0,
        "D8": 0,
        "E8": 0,
        "F8": 0,
        "A9": "备注",
        "B9": "来源标准仅供核对，不是检测结果",
        "A11": "Lower limit",
        "C11": 95,
    }
    for coordinate, value in values.items():
        c = sheet[coordinate]
        sheet.cell(c.row + offset, c.column, value)
    sheet.merge_cells(start_row=4 + offset, start_column=3, end_row=4 + offset, end_column=6)
    sheet.merge_cells(start_row=6 + offset, start_column=1, end_row=8 + offset, end_column=1)


def test_separate_pharmacopoeial_reports_are_distinct_and_adopted_together(tmp_path):
    from stability_hub.ledger_review import LedgerReview
    from stability_hub.workflow_contracts import ReceiveRequest, SheetConfirmRequest
    book = Workbook()
    sheet = book.active
    add_block(sheet)
    sheet['A6'] = '颜色（目视比色法）\nColor (Visual Colorimetric Method )'
    sheet['B6'] = sheet['B7'] = '报告数据\nReport Result'
    for row in sheet.iter_rows(min_row=6,max_row=8,min_col=3,max_col=6):
        for cell in row:
            cell.value = None
    sheet['C6'] = 'ChP：几乎无色液体\nAlmost colorless, ChP Y0.5'
    sheet['C7'] = 'EP：几乎无色液体\nAlmost colorless, EP Y7'
    path = tmp_path / 'two-colour-reports.xlsx'
    book.save(path)
    flow = Workflow(Store(tmp_path / 'two-reports'))
    flow.configure(ProjectConfig(project='P'))
    preview = flow.upload('P',path.name,path.read_bytes(),ImportContext(),'upload',actor=TECH)
    assert len(preview.candidates) == 2
    assert all(c.status=='new' for c in preview.candidates)
    assert preview.candidates[0].record.key.item.endswith('ChP')
    assert preview.candidates[1].record.key.item.endswith('EP')
    assert preview.candidates[0].record.key.slot_id != preview.candidates[1].record.key.slot_id
    service = LedgerReview(flow)
    received = service.receive(preview.preview_id,ReceiveRequest(base_revision=preview.base_revision,
        idempotency_key='receive',candidate_ids=tuple(c.id for c in preview.candidates),defer_confirmation=True),actor=TECH)
    confirmed = service.confirm_sheet(preview.preview_id,SheetConfirmRequest(base_revision=received.base_revision,
        idempotency_key='sheet',section=sheet.title),actor=TECH)
    assert len(confirmed.result_version_ids) == 2 and not confirmed.pending_confirmation_ids
    assert flow.results('P',actor=TECH).total == 2


def test_report_basis_needs_an_explicit_unambiguous_label():
    from stability_hub.matrix_fields import item_text
    assert item_text('Color','Report Result',result='ChP: colour').endswith('ChP')
    assert item_text('Color','Report Result',result='USP: colour').endswith('USP')
    assert item_text('Color','Report Result',result='one report mentions EP and ChP') == 'Color'
    assert item_text('Color','Report Result',result='ChP: first\nEP: second') == 'Color'


@pytest.fixture
def matrix(tmp_path):
    path = tmp_path / "matrix.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "Matrix "
    add_block(sheet)
    sheet.cell(225, 15255).number_format = "0.00"  # Formatting must not create a huge rectangle.
    book.save(path)
    book.close()
    return path


def test_matrix_time_subitems_blank_states_and_sparse_source_identity(matrix):
    parsed = parse_source(matrix)
    assert len(parsed.observations) == 11
    assert parsed.coverage[0]["blank_cells"] == 1
    assert parsed.coverage[0]["blocks"][0]["header_row"] == 4
    assert all(o.location.section == "Matrix " and o.location.row in (6, 7, 8) for o in parsed.observations)
    assert {o.fields["result"] for o in parsed.observations} >= {"0", "ND", "/", "N/A"}
    source = reread_source_rows(matrix)
    assert all(check_original_fields(source, o) for o in parsed.observations)
    first = interpret_matrix_fields(parsed.observations[0].fields, "Matrix ")
    assert first["project"] == "P" and first["sample_type"] == "DS" and first["batch"] == "B1"
    assert first["study_id"] == "STB(C)-QC-P-1" and first["condition"] == "5℃±3℃"
    assert first["study"] == "long_term" and first["time_point"] == "0M"
    assert first["test_item"] == "纯度 · SEC-HPLC · Monomer"
    assert any(
        "HMW" in interpret_matrix_fields(o.fields, o.location.section)["test_item"]
        for o in parsed.observations
    )
    raw = parsed.observations[0].model_copy(
        update={"fields": {**parsed.observations[0].fields, "result": "100"}}
    )
    assert not check_original_fields(source, raw)


def test_repeated_blocks_do_not_reuse_previous_batch(matrix):
    book = load_workbook(matrix)
    add_block(book.active, 15, "B2")
    book.save(matrix)
    book.close()
    parsed = parse_source(matrix)
    assert len(parsed.observations) == 22
    assert {o.fields["batch"] for o in parsed.observations} == {"B1", "B2"}
    assert len(parsed.coverage[0]["blocks"]) == 2


def test_unknown_time_column_stays_unresolved_instead_of_disappearing(matrix):
    book = load_workbook(matrix)
    book.active["E5"] = "7 days"
    book.save(matrix)
    book.close()
    parsed = parse_source(matrix)
    unresolved = [o for o in parsed.observations if o.location.column == 5]
    assert len(unresolved) == 3
    assert all(interpret_matrix_fields(o.fields, o.location.section)["time_point"] == "7D" for o in unresolved)


def test_merged_result_is_not_repeated_as_independent_results(matrix):
    book = load_workbook(matrix)
    book.active.merge_cells("C6:D6")
    book.save(matrix)
    book.close()
    parsed = parse_source(matrix)
    same = [o for o in parsed.observations if o.location.row == 6 and o.location.column == 3]
    assert len(same) == 1 and same[0].hazards


def test_selected_condition_does_not_choose_unchecked_temperature():
    assert (
        selected_condition("□-70℃±10℃ R25℃±2℃ 相对湿度：R60%RH±5%RH R其他Others:避光")
        == "25℃±2℃ · 60%RH±5%RH · 避光"
    )
    assert selected_condition("R5℃±3℃ R25℃±2℃") == ""
    assert selected_condition("□5℃±3℃ □25℃±2℃") == ""


def test_legacy_columns_footer_and_missing_identity(tmp_path):
    p = tmp_path / "legacy.xlsx"
    book = Workbook()
    s = book.active
    for coordinate, value in {
        "A1": "DP长期稳定性研究（5℃±3℃）实验数据",
        "A2": "OLD-1",
        "A3": "检测项目",
        "C3": "方法",
        "D3": "质量标准",
        "E3": "时间（月）（取样时间）",
        "E4": 0,
        "F4": "3（2025.03.01）",
        "G4": 6,
        "H4": 12,
        "A5": "纯度",
        "C5": "SEC",
        "D5": "单体：≥95.0%",
        "D6": "HMW",
        "D7": "LMW",
        "E5": 99,
        "E6": 1,
        "E7": 0,
        "A8": "取样量（支）",
        "E8": 100,
    }.items():
        s[coordinate] = value
    s.merge_cells("E3:H3")
    s.merge_cells("A5:B7")
    s.merge_cells("C5:C7")
    book.save(p)
    book.close()
    parsed = parse_source(p)
    assert len(parsed.observations) == 3
    fields = interpret_matrix_fields(parsed.observations[0].fields, s.title)
    assert fields["sample_type"] == "DP" and fields["study_id"] == "" and fields["presentation"] == ""
    assert fields["method"] == "SEC"
    assert all(o.location.row < 8 for o in parsed.observations)


def test_annotation_formula_and_correction_notes_require_review(matrix):
    from openpyxl.comments import Comment

    book = load_workbook(matrix)
    s = book.active
    s["C7"].comment = Comment("人工核对", "tester")
    s["C8"] = "=1-1"
    s["A10"] = "3M SEC结果修改，须核对"
    book.save(matrix)
    book.close()
    observations = parse_source(matrix).observations
    assert next(o for o in observations if o.location.row == 8 and o.location.column == 3).formula == "=1-1"
    assert next(o for o in observations if o.location.row == 7 and o.location.column == 3).comment
    assert next(o for o in observations if o.location.row == 7 and o.location.column == 4).comment
    assert not next(o for o in observations if o.location.row == 6 and o.location.column == 3).comment
    tables = reread_source_rows(matrix)
    assert all(check_original_fields(tables, o) for o in observations)


def test_unrecognized_populated_sheet_is_not_silently_accepted(matrix):
    book = load_workbook(matrix)
    book.create_sheet("unknown")["A1"] = "Unrecognized data"
    book.save(matrix)
    book.close()
    with pytest.raises(UnsupportedFormat):
        parse_source(matrix)


def test_group_mapping_confirmation_export_and_original_evidence(matrix, tmp_path):
    flow = Workflow(Store(tmp_path / "store"))
    flow.configure(
        ProjectConfig(
            project="P", items=(ItemDefinition(id="main", label="Monomer", method="SEC-HPLC", unit="%"),)
        )
    )
    # V4 partial-import compatibility; V5 worksheet boundaries have separate tests.
    preview = flow.upload("P", matrix.name, matrix.read_bytes(), ImportContext(), "upload", actor=TECH,_legacy_compatibility=True)
    selected = [c for c in preview.candidates if "Monomer" in c.interpreted_fields["test_item"]]
    request = CandidateMapping(
        base_revision=preview.base_revision,
        idempotency_key="map",
        reason="核对单体项目",
        candidate_ids=tuple(c.id for c in selected),
        item_id="main",
    )
    mapped = flow.map_items(preview.preview_id, request, actor=TECH)
    assert flow.map_items(preview.preview_id, request, actor=TECH) == mapped
    preview = flow.preview(preview.preview_id, actor=TECH)
    selected = [c for c in preview.candidates if c.record and c.record.key.item == "main"]
    assert len(selected) == 3
    assert preview.parsing_summary[0]["result_cells"] == 11
    confirmed = ResultVersions(flow).confirm(
        preview.preview_id,
        ConfirmRequest(
            base_revision=preview.base_revision,
            idempotency_key="confirm",
            items=tuple(
                ConfirmItem(
                    candidate_id=c.id,
                    candidate_revision=c.candidate_revision,
                    detection_id=c.detection_id,
                    identity_confirmed=True,
                    intent="initial",
                    reason="逐项核对来源",
                )
                for c in selected
            ),
        ),
        actor=TECH,
    )
    assert len(confirmed.result_version_ids) == 3
    exported = ExcelExports(flow).create(
        ExportRequest(project="P", base_revision=confirmed.base_revision), actor=LEAD
    )
    assert exported.state == "verified"
    still = flow.preview(preview.preview_id, actor=TECH)
    assert sum(c.lifecycle == "draft" for c in still.candidates) == 8
    assert flow.results("P", actor=TECH).total == 3


def test_mapping_transaction_rejects_foreign_or_submitted_candidates(matrix, tmp_path):
    flow = Workflow(Store(tmp_path / "store"))
    flow.configure(
        ProjectConfig(
            project="P", items=(ItemDefinition(id="main", label="Monomer", method="SEC-HPLC", unit="%"),)
        )
    )
    first = flow.upload("P", matrix.name, matrix.read_bytes(), ImportContext(), "first", actor=TECH)
    second = flow.upload("P", matrix.name, matrix.read_bytes(), ImportContext(), "second", actor=TECH)
    request = CandidateMapping(
        base_revision=second.base_revision,
        idempotency_key="bad-map",
        reason="核对",
        candidate_ids=(second.candidates[0].id, first.candidates[0].id),
        item_id="main",
    )
    with pytest.raises(WorkflowError):
        flow.map_items(second.preview_id, request, actor=TECH)
    assert flow.preview(first.preview_id, actor=TECH).candidates[0].corrections == {}
    assert flow.preview(second.preview_id, actor=TECH).candidates[0].corrections == {}


def test_source_method_abbreviation_is_not_silently_merged_with_another_method():
    fields = interpret_matrix_fields({"test_item": "纯度", "method": "SEC", "criterion": "HMW"}, "DS长期")
    assert fields["method"] == "SEC"
    assert fields["sample_type"] == "DS"


def test_sampling_plan_and_chart_limit_blocks_never_become_results(matrix):
    book = load_workbook(matrix)
    s = book.active
    s["A9"] = None
    s["B9"] = None
    s["A11"] = None
    s["C11"] = 0
    s["D11"] = 3
    s["E11"] = 6
    s["B12"] = "Lower Limit: ≥95.0%"
    s["C12"] = 95
    s["D12"] = 95
    s["E12"] = 95
    s["A15"] = "STB(C)-QC-P-1 稳定性研究考察方案"
    s.merge_cells("A15:F15")
    s["A16"] = "考察项目"
    s["B16"] = "时间节点"
    s["E17"] = 30
    book.save(matrix)
    book.close()
    parsed = parse_source(matrix)
    assert len(parsed.observations) == 11
    assert all(o.location.row < 9 for o in parsed.observations)


def test_result_panels_preserve_two_distinct_sample_info_columns(tmp_path):
    p = tmp_path / "panels.xlsx"
    book = Workbook()
    s = book.active
    s.append(["样品批号", "样品信息", "N端HG缺失比例（%）", None, "样品批号", "样品信息", "N端缺失比例（%）"])
    s.append(["B1", "长期 -70℃±10℃，24M", 2.64, None, "B2", "加速 25℃±2℃，6M", 4.39])
    book.save(p)
    book.close()
    parsed = parse_source(p)
    assert len(parsed.observations) == 2
    values = [interpret_matrix_fields(o.fields, o.location.section) for o in parsed.observations]
    assert [v["batch"] for v in values] == ["B1", "B2"]
    assert [v["time_point"] for v in values] == ["24M", "6M"]
    assert all(v["sample_type"] == "" and v["method"] == "" for v in values)
    assert all(check_original_fields(reread_source_rows(p), o) for o in parsed.observations)


def test_ambiguous_conditions_remain_correctable_candidates(matrix, tmp_path):
    book = load_workbook(matrix)
    book.active["B3"] = "R5℃±3℃ R25℃±2℃"
    book.save(matrix)
    book.close()
    flow = Workflow(Store(tmp_path / "missing-store"))
    flow.configure(
        ProjectConfig(
            project="P",
            items=(
                ItemDefinition(id="main", label="纯度 · SEC-HPLC · Monomer", method="SEC-HPLC", unit="%"),
            ),
        )
    )
    preview = flow.upload(
        "P", matrix.name, matrix.read_bytes(), ImportContext(), "unknown-condition", actor=TECH
    )
    selected = next(c for c in preview.candidates if c.location.row == 6)
    assert selected.issue_code == "missing_identity"
    assert selected.interpreted_fields["condition"] == ""
    assert not any("confirm_initial" in c.allowed_actions for c in preview.candidates)


def test_frozen_original_download_checks_hash_and_view_permission(matrix, tmp_path):
    from fastapi.testclient import TestClient
    from stability_hub.api import create_app, get_actor
    from stability_hub.permissions import Actor

    config = ProjectConfig(
        project="P", items=(ItemDefinition(id="main", label="Monomer", method="SEC", unit="%"),)
    )
    store = Store(tmp_path / "download")
    app = create_app(store, [config], TECH)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/projects/P/previews",
            files={"file": ("source.xlsx", matrix.read_bytes())},
            headers={"Idempotency-Key": "upload"},
        )
        assert response.status_code == 200
        candidate_id = response.json()["candidates"][0]["id"]
        url = f"/api/v1/sources/{candidate_id}/file"
        download = client.get(url)
        assert download.status_code == 200 and download.content == matrix.read_bytes()
        app.dependency_overrides[get_actor] = lambda: Actor(
            id="none", display_name="None", capabilities=frozenset()
        )
        assert client.get(url).status_code == 403
        app.dependency_overrides.clear()
        path = next((store.home / "sources").glob("*.xlsx"))
        path.write_bytes(b"tampered")
        assert client.get(url).status_code == 409


def test_native_excel_date_header_preserves_source_but_requires_time_correction(matrix):
    from datetime import datetime

    book = load_workbook(matrix)
    book.active["D5"] = datetime(2025, 8, 24)
    book.save(matrix)
    book.close()
    parsed = parse_source(matrix)
    raw = next(o for o in parsed.observations if o.location.row == 7 and o.location.column == 4)
    assert interpret_matrix_fields(raw.fields, raw.location.section)["time_point"] == ""
    assert check_original_fields(reread_source_rows(matrix), raw)
