import pytest
from fastapi.testclient import TestClient

from stability_hub.api import create_app
from stability_hub.ledger_review import LedgerReview
from stability_hub.permissions import Role,actor_for_role
from stability_hub.result_versions import ResultVersions
from stability_hub.v5_contracts import DraftDecision,DraftReview,SheetCommit,SheetCommitResult
from stability_hub.workflow_common import WorkflowError
from stability_hub.workflow_contracts import ReceiveRequest
from stability_hub.worksheet_commit import preflight,save_decision,review_decision,commit
from test_v5_regressions import TECH,preview,confirm_initial

SENIOR = actor_for_role('v5-senior','Senior',Role.SENIOR_TECHNICIAN)


def inspect(flow,p,section='Results'):
    with flow.store.connect() as conn:
        return preflight(flow,conn,flow._upload(conn,p.preview_id),section,TECH)[0]


def send(flow,p,key):
    v = inspect(flow,p)
    return commit(flow,p.preview_id,SheetCommit(base_revision=v.base_revision,idempotency_key=key,
        reason='worksheet checked',section=v.section,fingerprint=v.fingerprint),actor=TECH)


def test_a14_a15_a16_mixed_sheet_no_facts_before_commit(tmp_path,xlsx,context):
    flow,p = preview(tmp_path,xlsx([[name,'0M',99,'%'] for name in ['Keep','Correct','Retest','Same']]),context)
    confirm_initial(flow,p)
    before = flow.results('PROJECT-P',actor=TECH)
    path = xlsx([[name,'0M',value,'%'] for name,value in [('Keep',98),('Correct',98.5),('Retest',98.5),('Same',99),('New',5),('Exclude',2)]])
    p = flow.upload('PROJECT-P','mixed.xlsx',path.read_bytes(),context,'mixed',actor=TECH)
    app = create_app(flow.store,[],TECH)
    client = TestClient(app)
    received = client.post(f'/api/v1/previews/{p.preview_id}/receive',json=ReceiveRequest(base_revision=p.base_revision,idempotency_key='mixed-receive',
        candidate_ids=tuple(c.id for c in p.candidates),defer_confirmation=False).model_dump(mode='json'))
    assert received.status_code==200 and not received.json()['result_version_ids']
    assert flow.results('PROJECT-P',actor=TECH).cells == before.cells
    actions = ['keep_old','correction','retest','repeat_source','initial','exclude']
    for c,action in zip(p.candidates,actions,strict=True):
        view = inspect(flow,p)
        response = client.post(f'/api/v1/previews/{p.preview_id}/v5/decisions',json=DraftDecision(base_revision=view.base_revision,idempotency_key='decide-'+c.id,reason=action,
            candidate_id=c.id,candidate_revision=c.candidate_revision,action=action,previous_version_id=view.previous_versions.get(c.id)).model_dump(mode='json'))
        assert response.status_code==200,response.text
    view = inspect(flow,p)
    assert len(view.review_required) == 3 and not view.can_commit
    assert flow.results('PROJECT-P',actor=TECH).cells == before.cells
    for cid in view.review_required:
        action = 'adopt' if cid == p.candidates[1].id else 'confirm_fact'
        v = inspect(flow,p)
        req = DraftReview(base_revision=v.base_revision,idempotency_key='review-'+cid,reason='senior checked',candidate_id=cid,
            decision_fingerprint=v.decision_fingerprints[cid],action=action)
        assert client.post(f'/api/v1/previews/{p.preview_id}/v5/review',json=req.model_dump(mode='json')).status_code==403
        app.state.actor = SENIOR
        response = client.post(f'/api/v1/previews/{p.preview_id}/v5/review',json=req.model_dump(mode='json'))
        app.state.actor = TECH
        assert response.status_code==200,response.text
    assert flow.results('PROJECT-P',actor=TECH).cells == before.cells
    v = inspect(flow,p)
    assert v.can_commit
    request = SheetCommit(base_revision=v.base_revision,idempotency_key='mixed-commit',reason='all decisions complete',section='Results',fingerprint=v.fingerprint)
    response = client.post(f'/api/v1/previews/{p.preview_id}/v5/commit',json=request.model_dump(mode='json'))
    assert response.status_code==200,response.text
    result = SheetCommitResult.model_validate(response.json())
    assert client.post(f'/api/v1/previews/{p.preview_id}/v5/commit',json=request.model_dump(mode='json')).json() == response.json()
    assert result.counts == {'initial':1,'repeat_source':1,'keep_old':1,'correction':1,'retest':1,'exclude':1,'blank':0}
    assert len(result.result_version_ids) == 3
    cells = {c.slot_key.item:c for c in flow.results('PROJECT-P',actor=TECH).cells}
    assert cells['Keep'].display.text == '99%'
    assert cells['Correct'].adopted.text == '98.5%'
    assert cells['Retest'].display.text == '98.5%' and cells['Retest'].adopted.text == '99%'
    with flow.store.connect() as conn:
        rows = list(conn.execute('SELECT intent,detection_id,previous_version_id FROM workflow_versions WHERE upload_sequence=?',(p.upload_sequence,)))
        for row in rows:
            if row['intent'] in {'correction','repeat_source'}:
                old = conn.execute('SELECT detection_id FROM workflow_versions WHERE id=?',(row['previous_version_id'],)).fetchone()
                assert old[0] == row['detection_id']
        assert conn.execute("SELECT count(*) FROM workflow_version_states WHERE state='pending'").fetchone()[0] == 0


def test_a17_direct_http_confirm_and_receive_cannot_bypass(tmp_path,xlsx,context):
    flow,p = preview(tmp_path,xlsx([['Purity','0M',99,'%'],['Other','0M',1,'%']]),context)
    client = TestClient(create_app(flow.store,[],TECH))
    c = p.candidates[0]
    response = client.post(f'/api/v1/previews/{p.preview_id}/confirm',json={'base_revision':p.base_revision,'idempotency_key':'bypass',
        'items':[{'candidate_id':c.id,'candidate_revision':1,'detection_id':c.detection_id,'identity_confirmed':True,'intent':'initial','reason':'bypass'}]})
    assert response.status_code == 422 and response.json()['code'] == 'sheet_confirmation_required'
    response = client.post(f'/api/v1/previews/{p.preview_id}/receive',json={'base_revision':p.base_revision,'idempotency_key':'receive',
        'candidate_ids':[c.id],'defer_confirmation':False})
    assert response.status_code == 200 and response.json()['result_version_ids'] == []
    response = client.get(f'/api/v1/previews/{p.preview_id}/v5/preflight',params={'section':'Results'})
    assert response.status_code == 200 and not response.json()['can_commit']
    with flow.store.connect() as conn:
        assert conn.execute('SELECT count(*) FROM workflow_versions').fetchone()[0] == 0


def test_a17_commit_failure_rolls_back_entire_sheet(tmp_path,xlsx,context,monkeypatch):
    flow,p = preview(tmp_path,xlsx([['A','0M',1,''],['B','0M',2,'']]),context)
    LedgerReview(flow).receive(p.preview_id,ReceiveRequest(base_revision=p.base_revision,idempotency_key='receive',candidate_ids=tuple(c.id for c in p.candidates)),actor=TECH)
    original = ResultVersions.confirm
    def fail_second(self,*args,**kwargs):
        original(self,*args,**kwargs)
        raise WorkflowError('injected_failure','test failure after fact insertion')
    monkeypatch.setattr(ResultVersions,'confirm',fail_second)
    with pytest.raises(WorkflowError,match='test failure'):
        send(flow,p,'fail')
    assert flow.results('PROJECT-P',actor=TECH).total == 0
    assert all(c.lifecycle == 'draft' for c in flow.preview(p.preview_id,actor=TECH).candidates)


@pytest.mark.parametrize('value,excluded,expected', [('',False,'blank'),(99,True,'exclude')])
def test_a18_zero_new_fact_sheets_complete(tmp_path,xlsx,context,value,excluded,expected):
    flow,p = preview(tmp_path,xlsx([['Purity','0M',value,'%']]),context)
    LedgerReview(flow).receive(p.preview_id,ReceiveRequest(base_revision=p.base_revision,idempotency_key='receive',candidate_ids=tuple(c.id for c in p.candidates)),actor=TECH)
    if excluded:
        v = inspect(flow,p)
        save_decision(flow,p.preview_id,DraftDecision(base_revision=v.base_revision,idempotency_key='exclude',reason='human excludes auxiliary data',
            candidate_id=p.candidates[0].id,candidate_revision=1,action='exclude'),actor=TECH)
    result = send(flow,p,'complete-empty')
    assert result.counts[expected] == 1 and not result.result_version_ids
    assert inspect(flow,p).state == 'committed'
    assert flow.results('PROJECT-P',actor=TECH).total == 0


def test_a16_equal_value_can_be_independent_retest(tmp_path,xlsx,context):
    flow,p = preview(tmp_path,xlsx([['Purity','0M',99,'%']]),context)
    confirm_initial(flow,p)
    original = flow.results('PROJECT-P',actor=TECH).cells[0]
    p = flow.upload('PROJECT-P','same.xlsx',xlsx([['Purity','0M',99,'%']]).read_bytes(),context,'same',actor=TECH)
    LedgerReview(flow).receive(p.preview_id,ReceiveRequest(base_revision=p.base_revision,idempotency_key='receive-again',candidate_ids=(p.candidates[0].id,)),actor=TECH)
    v = inspect(flow,p)
    c = p.candidates[0]
    save_decision(flow,p.preview_id,DraftDecision(base_revision=v.base_revision,idempotency_key='same-retest',reason='independent test happened to equal original',
        candidate_id=c.id,candidate_revision=1,action='retest',previous_version_id=v.previous_versions[c.id]),actor=TECH)
    v = inspect(flow,p)
    review_decision(flow,p.preview_id,DraftReview(base_revision=v.base_revision,idempotency_key='approve-same',reason='test identity checked',
        candidate_id=c.id,decision_fingerprint=v.decision_fingerprints[c.id],action='confirm_fact'),actor=SENIOR)
    send(flow,p,'same-commit')
    after = flow.results('PROJECT-P',actor=TECH).cells[0]
    assert after.display.detection_id != original.display.detection_id
    assert after.adopted == original.adopted
