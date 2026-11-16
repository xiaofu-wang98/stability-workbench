import pytest
from openpyxl import Workbook, load_workbook

from stability_hub.matrix_fields import interpret_matrix_fields, item_text, month_token
from stability_hub.models import ImportContext, ItemDefinition, ProjectConfig
from stability_hub.parsing import parse_source
from stability_hub.permissions import Role, actor_for_role
from stability_hub.storage import Store
from stability_hub.verification import check_original_fields, reread_source_rows
from stability_hub.workflow import Workflow
from stability_hub.workflow_contracts import CandidateMapping


def test_clarity_component_uses_agreeing_explicit_result_qualifiers():
    assert item_text("澄清度", "不得高于3号", "目视", result="小于1号（中国）").endswith(" · ChP")
    assert item_text("Clarity", "No higher than No.3", "Visual", result="小于Ⅰ号（美国）\nLess than No.Ⅰ(USP)").endswith(" · USP")
    mixed = item_text("Clar ity", "", "Visual", result="小于1号（中国）\nLess than No.Ⅰ(USP)")
    assert mixed == "Clar ity · Visual"
    assert item_text("Clarity", "", "Visual", result="小于1号（中国）\nLess than No.Ⅰ(USP)") == "Clarity · Visual"


def test_appearance_component_names_come_from_explicit_forms():
    solid = "应为白色至微黄色块状疏松体。 White or yellow powder or porous mass."
    liquid = "复溶后溶液，呈澄清至微乳光。 The recovered solution is clear to opalescent."
    assert item_text("外观", solid, "目视法", solid).endswith(" · 固体外观")
    assert item_text("外观", liquid, "目视法", liquid).endswith(" · 复溶液外观")
    assert not item_text("外观", solid, "目视法").endswith(" · 固体外观")
    assert not item_text("外观", solid + liquid, "目视法", solid + liquid).endswith(" · 固体外观")


def test_inline_batch_and_stress_keep_original_cell_evidence(bilingual):
    book = load_workbook(bilingual)
    sheet = book.active
    sheet.title = "DS 强力破坏"
    sheet["A1"] = "原液强力破坏稳定性研究实验数据"
    sheet["A2"] = "样品批号：LOT-123"
    sheet["B2"] = None
    book.save(bilingual)
    book.close()
    raw = parse_source(bilingual).observations[0]
    assert raw.fields["batch"] == "样品批号：LOT-123"
    assert (raw.evidence["batch"].row, raw.evidence["batch"].column) == (2, 1)
    fields = interpret_matrix_fields(raw.fields, raw.location.section)
    assert (fields["batch"], fields["study"], fields["sample_type"]) == ("LOT-123", "stress", "DS")


@pytest.fixture
def bilingual(tmp_path):
    path = tmp_path / 'bilingual.xlsx'
    book = Workbook()
    sheet = book.active
    sheet.title = 'DS 加速'
    for coord, value in {'A1':'原液加速稳定性研究实验数据',
                         'A2':'样品批号：\nSample lot number：', 'B2':'LOT-\n202401-DS',
                         'C2':'实验条件：\n5℃±3℃,避光\nExperimental condition: 5℃±3℃',
                         'D2':'存放位置：', 'E2':'FREEZER-1',
                         'A3':'检测项目\nSample lot number', 'C3':'方法\nMethod',
                         'D3':'质量标准\nQuality standard',
                         'E3':'考察时段（计划取样时间）\nSurvey period (planned sampling time)',
                         'E4':'0个月', 'F4':'1个月\n2024.02.01\n已签报告', 'G4':'3个月\n3M',
                         'A5':'纯度和杂质', 'B5':'纯度', 'C5':'SEC-HPLC法\nSEC-HPLC Method',
                         'D5':'单体（%）：≥95.0%\nMonomer（%）',
                         'D6':'聚合体（%）：≤3.5%\nHMW（%）',
                         'D7':'单体（%）：≥95.0%\nHMW（%）'}.items():
        sheet[coord] = value
    for area in ('A1:G1','A3:B4','C3:C4','D3:D4','E3:G3','A5:A7','B5:B7','C5:C7'):
        sheet.merge_cells(area)
    for row in (5,6,7):
        for col in (5,6,7):
            sheet.cell(row,col,98.1 if row != 6 else 1.2)
    book.save(path)
    book.close()
    return path


def test_bilingual_caption_chinese_months_and_inline_condition(bilingual):
    parsed = parse_source(bilingual)
    assert len(parsed.observations) == 9
    raw = parsed.observations[0]
    fields = interpret_matrix_fields(raw.fields, raw.location.section)
    assert fields['batch'] == 'LOT-202401-DS'
    assert fields['method'] == 'SEC-HPLC'
    assert fields['condition'] == '5℃±3℃ · 避光'
    assert fields['time_point'] == '0M'
    assert raw.fields['batch'] == 'LOT-\n202401-DS'
    assert raw.fields['method'] == 'SEC-HPLC法\nSEC-HPLC Method'
    assert raw.evidence['condition'].row == 2 and raw.evidence['condition'].column == 3
    assert sorted({interpret_matrix_fields(o.fields,o.location.section)['time_point'] for o in parsed.observations}) == ['0M','1M','3M']
    source = reread_source_rows(bilingual)
    assert all(check_original_fields(source,o) for o in parsed.observations)


def test_conflicting_subresult_labels_remain_reviewable_and_original_is_immutable(bilingual, tmp_path):
    tech = actor_for_role('tech','实验员',Role.TECHNICIAN)
    flow = Workflow(Store(tmp_path / 'store'))
    flow.configure(ProjectConfig(project='P',items=(ItemDefinition(id='monomer',label='单体',method='SEC-HPLC',unit='%'),),
                                 presentation_defaults={'DS':'原液'}))
    before = flow.upload('P',bilingual.name,bilingual.read_bytes(),ImportContext(defaults={'study_id':'STUDY-1'}),'upload',actor=tech)
    c = next(c for c in before.candidates if c.location.row == 7 and c.location.column == 5)
    assert '标注待核对' not in c.interpreted_fields['test_item']
    assert not c.comment and any(i.source_kind=='system_hint' and i.code=='identity_conflict' and '中英文子结果标签不一致' in i.detail for i in c.issues)
    flow.map_items(before.preview_id,CandidateMapping(base_revision=before.base_revision,idempotency_key='map',
                   candidate_ids=(c.id,),item_id='monomer',reason='人工核对检项'),actor=tech)
    preview = flow.preview(before.preview_id,actor=tech)
    c = next(x for x in preview.candidates if x.id == c.id)
    assert c.issue_code == 'identity_conflict' and 'confirm_initial' not in c.allowed_actions
    from stability_hub.review_issues import resolve_issue
    from stability_hub.v5_contracts import IssueDecision
    issue = next(i for i in c.issues if i.code == 'identity_conflict')
    resolve_issue(flow,before.preview_id,IssueDecision(base_revision=preview.base_revision,candidate_revision=c.candidate_revision,
        idempotency_key='resolve',candidate_id=c.id,issue_id=issue.id,action='manual',
        fields={'test_item':'monomer'},reason='人工核对原格，确认单体含量'),actor=tech)
    after = next(x for x in flow.preview(before.preview_id,actor=tech).candidates if x.id == c.id)
    assert not any(i.code == 'identity_conflict' for i in after.issues)
    assert after.raw_fields['result'] == '98.1'
    assert after.status == 'new'
    assert after.record.result.raw_text == '98.1'
    assert after.raw_fields['criterion'] == '单体（%）：≥95.0%\nHMW（%）'


def test_repeated_metadata_and_separate_chart_axis_are_not_results(tmp_path):
    path = tmp_path / 'blocks.xlsx'
    book = Workbook()
    s = book.active
    for top, batch in ((1,'B-1'),(11,'B-2')):
        for coord,value in {f'A{top}':'项目编号 Project No.', f'B{top}':'P',f'F{top}':'产品批号 Product Batch No.',f'G{top}':batch,
                            f'A{top+3}':'检验项目\nTest Item', f'B{top+3}':'可接受标准\nAcceptable Criteria',
                            f'D{top+3}':'考察时间（月）\nstudy timepoints (Month)',
                            f'A{top+5}':'纯度（SEC-HPLC）',f'B{top+5}':'单体（%）≥95'}.items():
            s[coord] = value
        s.merge_cells(f'D{top+3}:F{top+3}')
        s.merge_cells(f'B{top+5}:C{top+5}')
        for col,month in ((4,0),(5,1),(6,3)):
            s.cell(top+4,col,month)
            s.cell(top+5,col,98)
    for col,month in ((4,0),(5,1),(6,3)):
        s.cell(22,col,month)
        s.cell(23,col,95)
    s['C23'] = 'Lower Limit'
    book.save(path)
    book.close()
    parsed = parse_source(path)
    assert len(parsed.observations) == 6
    assert {o.location.row for o in parsed.observations} == {6,16}
    assert {o.fields['batch'] for o in parsed.observations} == {'B-1','B-2'}


def test_line_wrapping_does_not_merge_alternative_batch_ids():
    assert interpret_matrix_fields({'batch':'LOT1\nLOT2'},'')['batch'] == ''
    assert month_token('3个月\n（2025.01.01）') == 3
    assert month_token('3天') is None


def test_generic_merged_subresults_keep_their_own_source_names(bilingual):
    book = load_workbook(bilingual)
    sheet = book.active
    sheet['B5'] = 'N糖谱'
    sheet['C5'] = 'FLD-UPLC法\nFLD-UPLC Method'
    for row,label in ((5,'G0-GN(%)：报告结果'),(6,'G0F-GN(%)：报告结果'),(7,'G0(%)：报告结果')):
        sheet.cell(row,4,label)
    book.save(bilingual)
    book.close()
    parsed = parse_source(bilingual)
    labels = {interpret_matrix_fields(o.fields,o.location.section)['test_item'] for o in parsed.observations}
    assert len(labels) == 3
    assert all(any(name in label for label in labels) for name in ('G0-GN','G0F-GN','G0'))
    assert not any(o.comment for o in parsed.observations)
    assert all(o.evidence['subresult'] == o.evidence['criterion'] for o in parsed.observations)


def test_unnamed_children_are_not_silently_classified_by_limits(bilingual):
    book = load_workbook(bilingual)
    for row,label in ((5,'报告结果'),(6,'≤3.5%'),(7,'符合规定')):
        book.active.cell(row,4,label)
    book.save(bilingual)
    book.close()
    parsed = parse_source(bilingual)
    assert all(not o.comment and any('未提供可区分的名称' in h['text'] for h in o.system_hints) for o in parsed.observations)


@pytest.mark.parametrize('mixed', [False, True])
def test_workbook_project_inheritance_has_explicit_source_and_never_guesses_mixed_projects(bilingual, mixed):
    book = load_workbook(bilingual)
    other = book.copy_worksheet(book.active)
    other.title = 'Project-labelled sheet'
    other['I1'] = '项目编号\nProject No.'
    other['J1'] = 'EXPECTED'
    if mixed:
        third = book.copy_worksheet(other)
        third.title = 'Another project'
        third['J1'] = 'DIFFERENT'
    book.save(bilingual)
    book.close()
    parsed = parse_source(bilingual)
    first = parsed.observations[0]
    if mixed:
        assert 'project' not in first.fields
    else:
        assert first.fields['project'] == 'EXPECTED'
        assert first.evidence['project'].section == other.title
        assert first.evidence['project'].column == 10
        assert all(check_original_fields(reread_source_rows(bilingual), o) for o in parsed.observations)
