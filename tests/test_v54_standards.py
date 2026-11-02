"""Source/identity/export checks for common and separately reported standards."""
import json
from uuid import uuid4

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont

from stability_hub.models import ImportContext, ProjectConfig
from stability_hub.parsing import parse_source
from stability_hub.storage import Store
from stability_hub.workflow import Workflow
from stability_hub.standards import segments
from test_v5_regressions import TECH, confirm_initial
from test_v51_interpretation import receive


def matrix(tmp_path, kind='split', *, reverse=False, offset=0, hidden=False):
    book=Workbook();s=book.active;s.title='任意来源表'
    values={'A1':'项目编号','B1':'P','C1':'产品名称','D1':'原液 DS','F1':'批号','G1':'LOT-X',
            'I1':'规格','J1':'原液','A2':'稳定性试验方案编号','B2':'STB(C)-GEN-1',
            'I2':'稳定性类型','J2':'长期','A3':'考察条件','B3':'5℃',
            'A4':'检验项目','B4':'可接受标准','C4':'考察时间（月）','C5':'0M','D5':'3M',
            'A6':'颜色\nColor'}
    cn='不深于Y3（中国药典）\nNo more colored than Y3 (ChP)'
    ep='不深于Y5（欧洲药典）\nNo more colored than Y5 (EP)'
    a='Y0.5（中国药典）\nY0.5 (ChP)';b='Y7（欧洲药典）\nY7 (EP)'
    if reverse:cn,ep,a,b=ep,cn,b,a
    if kind=='rows':
        values.update(B6=cn,C6=a,D6=a,B7=ep,C7=b,D7=b)
    elif kind=='shared':
        values.update(B6=cn+'\n'+ep,C6=5.6,D6=5.7)
    else:
        if kind=='equal':b=a.replace('中国药典','欧洲药典').replace('ChP','EP') if '中国药典' in a else a.replace('欧洲药典','中国药典').replace('EP','ChP')
        value=('EP: Y7; ChP: Y0.5' if reverse else 'ChP: Y0.5; EP: Y7') if kind=='inline' else a+'\n'+b
        values.update(B6=cn+'\n'+ep,C6=value,D6=value)
    from openpyxl.utils.cell import coordinate_to_tuple
    for coordinate,value in values.items():
        r,c=coordinate_to_tuple(coordinate);s.cell(r+offset,c+offset,value)
    s.merge_cells(start_row=4+offset,start_column=3+offset,end_row=4+offset,end_column=4+offset)
    if kind=='rows':s.merge_cells(start_row=6+offset,start_column=1+offset,end_row=7+offset,end_column=1+offset)
    s.row_dimensions[6+offset].hidden=hidden
    p=tmp_path/(kind+str(offset)+str(reverse)+'.xlsx');book.save(p);book.close();return p


def upload(tmp_path,path,parsed=None):
    flow=Workflow(Store(tmp_path/'home'));flow.configure(ProjectConfig(project='P'))
    p=flow.upload('P',path.name,path.read_bytes(),ImportContext(),uuid4().hex,actor=TECH,_parsed=parsed)
    return flow,p


@pytest.mark.parametrize('kind,count',[('rows',4),('split',4),('equal',4),('shared',2),('inline',4)])
@pytest.mark.parametrize('offset,reverse,hidden',[(0,False,False),(3,True,True)])
def test_reported_values_and_common_value_remain_distinct_and_export(tmp_path,kind,count,offset,reverse,hidden):
    from stability_hub.workflow_excel import ExcelExports
    from stability_hub.workflow_contracts import ExportRequest
    from test_xlsx_matrix import LEAD
    path=matrix(tmp_path,kind,offset=offset,reverse=reverse,hidden=hidden)
    flow,p=upload(tmp_path,path)
    assert len(p.candidates)==count
    assert all(c.status=='new' and not c.issues for c in p.candidates),[(c.detail,c.issues) for c in p.candidates]
    assert len({c.record.key.slot_id for c in p.candidates})==count
    if kind=='shared':
        assert all(len(c.record.standards)==2 and not c.record.key.reporting_basis for c in p.candidates)
    else:
        assert {c.record.key.reporting_basis for c in p.candidates}=={'CN','EP'}
        assert all(len(c.record.standards)==1 and c.record.standards[0].basis==c.record.key.reporting_basis for c in p.candidates)
    assert all(s.version=='' and s.effective_status=='not_reported' for c in p.candidates for s in c.record.standards)
    result=confirm_initial(flow,p)
    ex=ExcelExports(flow)
    out=ex.create(ExportRequest(project='P',base_revision=result.base_revision),actor=LEAD)
    book=load_workbook(ex.download(out.export_id,actor=LEAD))
    assert book['Records'].max_row==count+1
    assert all(json.loads(r[27]) for r in book['Records'].iter_rows(min_row=2,values_only=True))
    book.close()


def test_unlabelled_or_combined_labels_are_not_guessed_as_separate_reports():
    assert segments('符合规定（中国药典/欧洲药典）')==[]
    assert segments('EP: Y1\n批注：结果更正待确认')==[]
    assert segments('生产于中国')==[]


def test_old_draft_upgrade_projects_only_unmodified_evidence_and_rolls_back_preview(tmp_path):
    from stability_hub import file_rules
    from stability_hub.v5_contracts import Reinterpret
    path=matrix(tmp_path,'split')
    parsed=parse_source(path)
    # Frozen baseline had one physical candidate per cell and the old generated hint.
    originals={}
    for raw in parsed.observations:
        originals.setdefault(raw.location,raw.model_copy(update={'reporting_basis':'','result_anchor':{},
            'source_value':{k:v for k,v in raw.source_value.items() if k!='standards_rule_version'},
            'comment':'B6: 多行子结果未提供可区分的名称，请核对并明确检测项目及结果。'}))
    parsed.observations=list(originals.values())
    flow,p=upload(tmp_path,path,parsed);p=receive(flow,p)
    assert len(p.candidates)==2 and all(c.issues for c in p.candidates)
    with flow.store.connect() as conn:
        before=[tuple(r) for r in conn.execute('SELECT id,raw_json FROM workflow_candidates')]
        info=file_rules.reinterpret_preview(flow,conn,flow._upload(conn,p.preview_id),TECH)
        assert len(info['structural_changes'])==2
        assert [tuple(r) for r in conn.execute('SELECT id,raw_json FROM workflow_candidates')]==before
    file_rules.reinterpret(flow,p.preview_id,Reinterpret(base_revision=p.base_revision,idempotency_key='upgrade',
        fingerprint=info['fingerprint'],reason='核对各标准及对应来源段后升级'),actor=TECH)
    p=flow.preview(p.preview_id,actor=TECH)
    live=[c for c in p.candidates if c.lifecycle=='draft']
    assert len(live)==4 and all(c.received and c.status=='new' and not c.issues for c in live)
    with flow.store.connect() as conn:
        assert [tuple(conn.execute('SELECT id,raw_json FROM workflow_candidates WHERE id=?',(cid,)).fetchone()) for cid,_ in before]==before
        assert conn.execute('SELECT count(*) FROM workflow_versions').fetchone()[0]==0
    from stability_hub.workflow_excel import ExcelExports
    from stability_hub.workflow_contracts import ExportRequest
    from test_xlsx_matrix import LEAD
    from stability_hub.worksheet_commit import preflight, commit
    from stability_hub.v5_contracts import SheetCommit
    with flow.store.connect() as conn:
        flight,_=preflight(flow,conn,flow._upload(conn,p.preview_id),live[0].location.section,TECH)
    assert flight.can_commit
    result=commit(flow,p.preview_id,SheetCommit(base_revision=flight.base_revision,idempotency_key='commit-upgraded',section=flight.section,fingerprint=flight.fingerprint,reason='核对来源投影后整表确认'),actor=TECH)
    assert len(result.result_version_ids)==4
    exports=ExcelExports(flow)
    out=exports.create(ExportRequest(project='P',base_revision=result.base_revision),actor=LEAD)
    assert exports.download(out.export_id,actor=LEAD).is_file()


def test_partial_deleted_ntu_is_not_converted_and_retains_standard_results(tmp_path):
    from stability_hub.review_issues import resolve_issue
    from stability_hub.v5_contracts import IssueDecision
    path=matrix(tmp_path,'split');book=load_workbook(path)
    s=book.active;s['A6']='澄清度'
    for coord,active in [('B6','≤4号浊度液（中国）\n≤Ⅲ号浊度液（美国）'),
                         ('C6','>1号，<2号浊度液（中国）\n>Ⅰ号，<Ⅱ号浊度液（美国）'),
                         ('D6','>1号，<2号浊度液（中国）\n>Ⅰ号，<Ⅱ号浊度液（美国）')]:
        s[coord]=CellRichText(TextBlock(InlineFont(strike=True),'≤30NTU'), '\n'+active)
    book.save(path);book.close()
    flow,p=upload(tmp_path,path)
    assert len(p.candidates)==4 and all(c.issues for c in p.candidates)
    for c in p.candidates:
        for issue in c.issues:
            assert issue.code=='source_revision' and issue.proposal['eligible']
            current=flow.preview(p.preview_id,actor=TECH);view=next(x for x in current.candidates if x.id==c.id)
            resolve_issue(flow,p.preview_id,IssueDecision(base_revision=current.base_revision,idempotency_key=uuid4().hex,
                candidate_id=c.id,candidate_revision=view.candidate_revision,issue_id=issue.id,
                action='keep_active',fields={issue.source_field:issue.proposal['after']},reason='确认删除NTU、分别保留两套参比原文'),actor=TECH)
    p=flow.preview(p.preview_id,actor=TECH)
    assert all(c.status=='new' and not c.issues for c in p.candidates)
    assert {c.record.key.reporting_basis for c in p.candidates}=={'CN','US'}
    assert all(c.record.result.value is None and 'NTU' not in c.record.result.raw_text for c in p.candidates)
    assert all('NTU' not in s.criterion_text for c in p.candidates for s in c.record.standards)


def test_same_value_with_changed_limit_requires_explicit_history_choice(tmp_path):
    path=matrix(tmp_path,'shared')
    flow,p=upload(tmp_path,path)
    confirm_initial(flow,p)
    book=load_workbook(path);book.active['B6']=book.active['B6'].value.replace('Y3','Y2');book.save(path);book.close()
    later=flow.upload('P','new-standard.xlsx',path.read_bytes(),ImportContext(),uuid4().hex,actor=TECH)
    assert all(c.status=='different_value' for c in later.candidates)
    assert all(c.existing_record.standards[0].criterion_text != c.record.standards[0].criterion_text for c in later.candidates)
    assert all(c.existing_record.result.equivalent(c.record.result) for c in later.candidates)


def test_standard_upgrade_preserves_manual_result_and_lists_exact_exception(tmp_path):
    from stability_hub import file_rules
    from stability_hub.workflow_contracts import CandidateEdit
    path=matrix(tmp_path,'split');parsed=parse_source(path)
    originals={}
    for raw in parsed.observations:
        originals.setdefault(raw.location,raw.model_copy(update={'reporting_basis':'','result_anchor':{},
            'source_value':{k:v for k,v in raw.source_value.items() if k!='standards_rule_version'}}))
    parsed.observations=list(originals.values())
    flow,p=upload(tmp_path,path,parsed)
    c=p.candidates[0]
    flow.edit(c.id,CandidateEdit(base_revision=p.base_revision,candidate_revision=c.candidate_revision,
        idempotency_key='edit',fields={'result':'经核对，结果含两套标准，原文保留'},reason='测试保留已有人工决定'),actor=TECH)
    with flow.store.connect() as conn:
        info=file_rules.reinterpret_preview(flow,conn,flow._upload(conn,p.preview_id),TECH)
    assert any(e['candidate_id']==c.id for e in info['structural_exceptions'])
    assert len(info['structural_changes'])==1
    assert flow.preview(p.preview_id,actor=TECH).candidates[0].corrections['result']=='经核对，结果含两套标准，原文保留'


def test_draft_standard_edit_revalidates_same_numeric_history_without_rewriting_source(tmp_path):
    from stability_hub.workflow_contracts import CandidateEdit
    path=matrix(tmp_path,'shared');flow,p=upload(tmp_path,path);confirm_initial(flow,p)
    later=flow.upload('P',path.name,path.read_bytes(),ImportContext(),uuid4().hex,actor=TECH)
    c=later.candidates[0];assert c.status=='same_value'
    changed=c.raw_fields['criterion'].replace('Y3','Y2')
    flow.edit(c.id,CandidateEdit(base_revision=later.base_revision,idempotency_key='standard-edit',
        candidate_revision=c.candidate_revision,fields={'criterion':changed},reason='明确本次标准更正，原标准及历史不回写'),actor=TECH)
    current=next(x for x in flow.preview(later.preview_id,actor=TECH).candidates if x.id==c.id)
    assert current.status=='different_value' and current.raw_fields==c.raw_fields
    assert current.record.result.equivalent(c.record.result)
    assert 'Y2' in current.record.standards[0].criterion_text
    assert 'Y3' in current.existing_record.standards[0].criterion_text


def test_ambiguous_repeated_standard_segments_stay_one_local_source_question(tmp_path):
    path=matrix(tmp_path,'shared');book=load_workbook(path)
    book.active['C6']='ChP: 5; EP: 6; ChP: 7';book.save(path);book.close()
    flow,p=upload(tmp_path,path)
    c=next(c for c in p.candidates if c.location.column==3)
    assert c.status=='needs_review'
    assert any(i.source_kind=='system_hint' and '各段边界' in i.detail for i in c.issues)
    assert c.raw_fields['result']=='ChP: 5; EP: 6; ChP: 7'
