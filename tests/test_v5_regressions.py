"""V5 target behavior, synthetic sources; never depend on ignored audit scripts."""
from decimal import Decimal

import pytest
from openpyxl import load_workbook
from openpyxl.comments import Comment

from stability_hub.mapping import map_corrected
from stability_hub.matrix_fields import interpret_matrix_fields
from stability_hub.models import ItemDefinition, Location, ProjectConfig, RawObservation
from stability_hub.parsing import parse_source
from stability_hub.permissions import Role, actor_for_role
from stability_hub.storage import Store
from stability_hub.workflow import Workflow
from stability_hub.workflow_contracts import NewItems

TECH = actor_for_role('v5-test', 'V5 synthetic technician', Role.TECHNICIAN)


def preview(tmp_path, path, context):
    flow = Workflow(Store(tmp_path / 'isolated'))
    flow.configure(ProjectConfig(project='PROJECT-P'))
    return flow, flow.upload('PROJECT-P', path.name, path.read_bytes(), context, 'upload', actor=TECH)


def confirm_initial(flow,p):
    from stability_hub.ledger_review import LedgerReview
    from stability_hub.workflow_contracts import ReceiveRequest
    from stability_hub.worksheet_commit import preflight,commit
    from stability_hub.v5_contracts import SheetCommit
    LedgerReview(flow).receive(p.preview_id,ReceiveRequest(base_revision=p.base_revision,idempotency_key='receive-'+p.preview_id,
        candidate_ids=tuple(c.id for c in p.candidates)),actor=TECH)
    with flow.store.connect() as conn:
        v,_ = preflight(flow,conn,flow._upload(conn,p.preview_id),p.candidates[0].location.section,TECH)
    return commit(flow,p.preview_id,SheetCommit(base_revision=v.base_revision,idempotency_key='commit-'+p.preview_id,
        reason='synthetic worksheet review',section=v.section,fingerprint=v.fingerprint),actor=TECH)


def test_a01_percentage_format(tmp_path, xlsx, context):
    path = xlsx([['Purity', '0M', .995, '%']])
    book = load_workbook(path)
    book.active['C2'].number_format = '0.0%'
    book.save(path)
    _, p = preview(tmp_path, path, context)
    assert p.candidates[0].record.result.value == Decimal('99.5')
    assert p.candidates[0].raw_fields['result'] == '0.995'


def test_a01_confirmed_percentage_export(tmp_path, xlsx, context):
    from stability_hub.workflow_excel import ExcelExports
    from stability_hub.workflow_contracts import ExportRequest
    path = xlsx([['Purity','0M',.995,'%']])
    book = load_workbook(path)
    book.active['C2'].number_format = '0.0%'
    book.save(path)
    flow, p = preview(tmp_path, path, context)
    result = confirm_initial(flow,p)
    lead = actor_for_role('lead','lead',Role.PROJECT_LEAD)
    exports = ExcelExports(flow)
    output = exports.create(ExportRequest(project='PROJECT-P',base_revision=result.base_revision),actor=lead)
    book = load_workbook(exports.download(output.export_id,actor=lead))
    assert book['Records']['M2'].value == '99.500%'
    assert book['Records']['S2'].value == '99.500'
    assert flow.results('PROJECT-P',actor=TECH).cells[0].display.text == book['Records']['M2'].value
    book.close()


@pytest.mark.parametrize('fmt,unit', [('0.0%','mg/ml'), ('0.0,,',''), ('yyyy-mm-dd','')])
def test_numeric_format_ambiguity_blocks(tmp_path, xlsx, context, fmt, unit):
    path = xlsx([['Purity','0M',.995,unit]])
    book = load_workbook(path)
    book.active['C2'].number_format = fmt
    book.save(path)
    _, p = preview(tmp_path, path, context)
    assert p.candidates[0].issue_code == 'numeric_format'


@pytest.mark.parametrize('value,unit,expected', [('99.5%','%', '99.5'), (99.5,'%', '99.5'),
    (.995,'','0.995'), ('1E-3','','0.001'), ('<0.1','','0.1'), (0,'','0')])
def test_a02_numeric_forms(tmp_path, xlsx, context, value, unit, expected):
    _, p = preview(tmp_path, xlsx([['Purity','0M',value,unit]]), context)
    assert p.candidates[0].record.result.value == Decimal(expected)


def test_a03_dictionary_does_not_create_new_fact(tmp_path, xlsx, context):
    flow, p = preview(tmp_path, xlsx([['Purity','0M',99,'%']]), context)
    confirm_initial(flow,p)
    flow.add_items('PROJECT-P', NewItems(base_revision=flow.results('PROJECT-P',actor=TECH).base_revision,
        idempotency_key='dictionary',reason='explicit dictionary addition',items=(
        ItemDefinition(id='purity',label='Purity',method='未注明',unit='%'),)),actor=TECH)
    p2 = flow.upload('PROJECT-P','second.xlsx',xlsx([['Purity','0M',99,'%']]).read_bytes(),context,'second',actor=TECH)
    assert p2.candidates[0].status != 'new'


def test_a04_correction_comment_blocks(tmp_path, xlsx, context):
    path = xlsx([['Purity','0M',99,'%']])
    book = load_workbook(path)
    book.active['C2'].comment = Comment('原结果录入错误，更正为98.5', 'synthetic')
    book.save(path)
    _, p = preview(tmp_path, path, context)
    assert p.candidates[0].issue_code == 'source_comment'


def test_a05_merge_has_specific_resolution_path(context,tmp_path,xlsx):
    raw = RawObservation(fields={'test_item':'Purity','result':'99','time_point':'0M'},
        location=Location(format='xlsx',section='S',row=2,column=3),
        hazards=('结果格跨行或跨时间点合并，须先明确每个检测结果，不能复制计入。',))
    # A generic acknowledgement must continue to block. T06 adds a scoped command.
    from stability_hub.normalize import NeedsReview
    with pytest.raises(NeedsReview):
        map_corrected(raw,context,ProjectConfig(project='PROJECT-P'),{'result':'99'},True,source_facts=True)
    from stability_hub.structural_review import resolve_structure
    from stability_hub.v5_contracts import StructuralDecision
    path = xlsx([['Purity','0M',99,'']])
    book = load_workbook(path)
    book.active.merge_cells('C2:D2')
    book.save(path)
    flow,p = preview(tmp_path,path,context)
    c = p.candidates[0]
    hazard = c.issues[0].detail
    cmd = StructuralDecision(base_revision=p.base_revision,idempotency_key='split',reason='explicit two-month attribution',
        candidate_id=c.id,candidate_revision=c.candidate_revision,section='Results',coordinate='C2',hazard=hazard,
        results=({'test_item':'Purity','time_point':'0M','result':'99'},
                 {'test_item':'Purity','time_point':'3M','result':'98'}))
    result = resolve_structure(flow,p.preview_id,cmd,actor=TECH)
    assert resolve_structure(flow,p.preview_id,cmd,actor=TECH) == result
    p = flow.preview(p.preview_id,actor=TECH)
    assert p.candidates[0].lifecycle == 'withdrawn'
    children = p.candidates[1:]
    assert len(children) == 2 and all(c.status == 'new' for c in children)
    assert {c.record.key.month for c in children} == {0,3}
    assert all(c.location == p.candidates[0].location and c.raw_fields['result']=='99' for c in children)
    from stability_hub.structural_review import restore
    from stability_hub.v5_contracts import DraftDecision
    restore(flow,p.preview_id,DraftDecision(base_revision=p.base_revision,idempotency_key='undo-split',reason='review attribution again',
        candidate_id=c.id,candidate_revision=p.candidates[0].candidate_revision,action='restore'),actor=TECH)
    p = flow.preview(p.preview_id,actor=TECH)
    assert p.candidates[0].lifecycle == 'draft'
    assert all(x.lifecycle == 'withdrawn' for x in p.candidates[1:])
    resolve_structure(flow,p.preview_id,cmd.model_copy(update={'base_revision':p.base_revision,
        'candidate_revision':p.candidates[0].candidate_revision,'idempotency_key':'split-again'}),actor=TECH)
    p = flow.preview(p.preview_id,actor=TECH)
    assert sum(c.lifecycle == 'draft' for c in p.candidates) == 2


def test_a06_footer_extra_result_is_accounted_for(tmp_path):
    from openpyxl import Workbook
    from test_xlsx_matrix import add_block
    book = Workbook()
    add_block(book.active)
    book.active['A13'], book.active['C13'] = '额外检测', 123
    path = tmp_path / 'footer.xlsx'
    book.save(path)
    parsed = parse_source(path)
    assert any(o.location.row == 13 for o in parsed.observations) or any(
        r.get('role') == 'unclassified' and 'C13' in r.get('coordinates', [])
        for s in parsed.coverage for r in s.get('regions', []))


def test_a07_multiline_identity_is_not_truncated():
    fields = interpret_matrix_fields({'test_item':'含量','method':'UV\nSEC-HPLC',
        'presentation':'1mg\n2mg','time_point':'0M'},'S')
    assert 'SEC-HPLC' in fields['method']
    assert '2mg' in fields['presentation']
