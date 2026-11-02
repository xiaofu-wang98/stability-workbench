from openpyxl import load_workbook
from openpyxl.comments import Comment
from fastapi.testclient import TestClient

from stability_hub.api import create_app
from stability_hub.v5_contracts import IssueDecision
from stability_hub.workflow_contracts import CandidateEdit
from test_v5_regressions import TECH, preview


def test_a04_issue_decisions_do_not_clear_other_evidence(tmp_path,xlsx,context):
    path = xlsx([['Purity','0M','=98+1','%']])
    book = load_workbook(path)
    book.active['C2'].comment = Comment('原结果录入错误，更正为98.5','synthetic')
    book.save(path)
    flow,p = preview(tmp_path,path,context)
    client = TestClient(create_app(flow.store,[],TECH))
    c = p.candidates[0]
    assert {i.code for i in c.issues} == {'formula_result','source_comment'}
    flow.edit(c.id,CandidateEdit(base_revision=p.base_revision,idempotency_key='generic',reason='generic acknowledgement',
        candidate_revision=c.candidate_revision,fields={'result':'98.5'},acknowledge_source_warning=True),actor=TECH)
    p = flow.preview(p.preview_id,actor=TECH)
    assert len(p.candidates[0].issues) == 2
    comment = next(i for i in c.issues if i.code == 'source_comment')
    response = client.post(f'/api/v1/previews/{p.preview_id}/v5/issues',json=IssueDecision(base_revision=p.base_revision,idempotency_key='comment',reason='use stated correction',
        candidate_id=c.id,candidate_revision=p.candidates[0].candidate_revision,issue_id=comment.id,action='manual',fields={'result':'98.5'}).model_dump(mode='json'))
    assert response.status_code==200,response.text
    p = flow.preview(p.preview_id,actor=TECH)
    assert {i.code for i in p.candidates[0].issues} == {'formula_result'}
    formula = p.candidates[0].issues[0]
    response = client.post(f'/api/v1/previews/{p.preview_id}/v5/issues',json=IssueDecision(base_revision=p.base_revision,idempotency_key='formula',reason='independently checked formula output',
        candidate_id=c.id,candidate_revision=p.candidates[0].candidate_revision,issue_id=formula.id,action='manual',fields={'result':'98.5'}).model_dump(mode='json'))
    assert response.status_code==200,response.text
    p = flow.preview(p.preview_id,actor=TECH)
    assert p.candidates[0].status == 'new' and not p.candidates[0].issues
    assert p.candidates[0].record.result.value == __import__('decimal').Decimal('98.5')
    assert p.candidates[0].raw_fields['result'] == '=98+1'
    assert p.candidates[0].comment == '原结果录入错误，更正为98.5'


def test_a07_multiline_method_and_spec_require_separate_explicit_choices(tmp_path,xlsx,context):
    path = xlsx([['Purity','0M',99,'%','UV\nSEC-HPLC','1mg\n2mg']],
        headers=['Test item','Time point','Result','Unit','Method','Presentation'])
    flow,p = preview(tmp_path,path,context)
    client = TestClient(create_app(flow.store,[],TECH))
    c = p.candidates[0]
    assert len(c.issues)==2
    for field,value in [('method','SEC-HPLC'),('presentation','1mg')]:
        current = flow.preview(p.preview_id,actor=TECH)
        c = current.candidates[0]
        issue = next(i for i in c.issues if i.fields==(field,))
        response = client.post(f'/api/v1/previews/{p.preview_id}/v5/issues',json=IssueDecision(
            base_revision=current.base_revision,idempotency_key=field,reason='synthetic human chooses explicit identity',
            candidate_id=c.id,candidate_revision=c.candidate_revision,issue_id=issue.id,action='manual',fields={field:value}).model_dump(mode='json'))
        assert response.status_code==200,response.text
    after = flow.preview(p.preview_id,actor=TECH).candidates[0]
    assert after.status=='new' and after.record.key.method=='SEC-HPLC' and after.record.key.presentation=='1mg'
    assert after.raw_fields['method']=='UV\nSEC-HPLC' and after.raw_fields['presentation']=='1mg\n2mg'


def test_a10_known_dictionary_name_never_approves_new_correction_comment(tmp_path,xlsx,context,config):
    from stability_hub.storage import Store
    path = xlsx([['Purity','0M',99,'%']])
    book = load_workbook(path)
    book.active['C2'].comment = Comment('原结果录入错误，更正为98.5','synthetic')
    book.save(path)
    client = TestClient(create_app(Store(tmp_path/'known-name'),[config],TECH))
    response = client.post('/api/v1/projects/PROJECT-P/previews',headers={'Idempotency-Key':'known-name'},
        files={'file':('known.xlsx',path.read_bytes())},data={'context':context.model_dump_json()})
    assert response.status_code==200,response.text
    assert response.json()['candidates'][0]['issue_code']=='source_comment'


def test_a12_issue_group_preview_is_atomic_and_preserves_member_values(tmp_path,xlsx,context):
    from stability_hub.v5_contracts import IssueBatchPreview,IssueSelection,IssueBatchApply
    path = xlsx([['Purity','0M',99,'%'],['Purity','3M',98,'%']])
    book = load_workbook(path)
    for coordinate in ('C2','C3'):
        book.active[coordinate].comment = Comment('请核对本次结果是否需要更正','synthetic')
    book.save(path)
    flow,p = preview(tmp_path,path,context)
    client = TestClient(create_app(flow.store,[],TECH))
    scope = IssueBatchPreview(issues=tuple(IssueSelection(candidate_id=c.id,candidate_revision=c.candidate_revision,
        issue_id=c.issues[0].id,action='keep_body') for c in p.candidates))
    response = client.post(f'/api/v1/previews/{p.preview_id}/v5/issues/preview',json=scope.model_dump(mode='json'))
    assert response.status_code==200,response.text
    assert [m['original_result'] for m in response.json()['members']]==['99','98']
    request = IssueBatchApply(base_revision=p.base_revision,idempotency_key='group',reason='synthetic human keeps each original body',
        fingerprint=response.json()['fingerprint'],scope=scope)
    response = client.post(f'/api/v1/previews/{p.preview_id}/v5/issues/apply',json=request.model_dump(mode='json'))
    assert response.status_code==200,response.text
    after = flow.preview(p.preview_id,actor=TECH)
    assert all(not c.issues and c.status=='new' for c in after.candidates)
    assert [str(c.record.result.value) for c in after.candidates]==['99','98']
    assert client.post(f'/api/v1/previews/{p.preview_id}/v5/issues/apply',json=request.model_dump(mode='json')).json()==response.json()
