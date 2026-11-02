import json

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from stability_hub.api import create_app
from stability_hub.models import ItemDefinition
from stability_hub.identity_links import apply_link
from stability_hub.permissions import Role, actor_for_role
from stability_hub.v5_contracts import DraftDecision, DraftReview, SheetCommit, IdentityLink
from stability_hub.workflow_contracts import ExportRequest, ReceiveRequest, ResultFilter, NewItems
from stability_hub.workflow_excel import ExcelExports
from stability_hub.ledger_review import LedgerReview
from stability_hub.worksheet_commit import save_decision, review_decision, preflight, commit
from test_v5_regressions import TECH, preview, confirm_initial

SENIOR = actor_for_role('senior','Senior',Role.SENIOR_TECHNICIAN)
LEAD = actor_for_role('lead','Lead',Role.PROJECT_LEAD)


def test_history_collision_explicit_adoption_and_future_correction(tmp_path,xlsx,context):
    flow,p = preview(tmp_path,xlsx([['Old purity','0M',99,'%']]),context)
    first = confirm_initial(flow,p).result_version_ids[0]
    p2 = flow.upload('PROJECT-P','second.xlsx',xlsx([['Other label','0M',98,'%']]).read_bytes(),context,'second',actor=TECH)
    second = confirm_initial(flow,p2).result_version_ids[0]
    with flow.store.connect() as conn:
        original = [tuple(r) for r in conn.execute('SELECT id,slot_id,record_json,source_json,detection_id FROM workflow_versions ORDER BY id')]
    slots = [c.slot_id for c in flow.results('PROJECT-P',actor=TECH).cells]
    client = TestClient(create_app(flow.store,[],SENIOR))
    root = '/api/v1/projects/PROJECT-P/v5/identity-associations'
    scope = {'slot_ids':slots,'fields':{'item':'stable-purity','method':'SEC'}}
    response = client.post(root+'/preview',json=scope)
    assert response.status_code == 200,response.text
    info = response.json()
    assert len(info['groups']) == 1 and info['groups'][0]['collision']
    assert set(info['groups'][0]['adoption_options']) == {first,second}
    sid = info['groups'][0]['slot_id']
    command = {'base_revision':info['base_revision'],'idempotency_key':'associate','reason':'synthetic human confirms same assay and keeps 99',
               'scope':scope,'fingerprint':info['fingerprint'],'adoptions':{sid:first}}
    assert client.post(root+'/apply',json={**command,'adoptions':{}}).status_code == 422
    tech_client = TestClient(create_app(flow.store,[],TECH))
    assert tech_client.post(root+'/apply',json=command).status_code == 403
    result = client.post(root+'/apply',json=command)
    assert result.status_code == 200,result.text
    assert client.post(root+'/apply',json=command).json() == result.json()
    cells = flow.results('PROJECT-P',actor=TECH).cells
    assert len(cells) == 1 and cells[0].display.result_version_id == first
    assert cells[0].adopted.result_version_id == first and cells[0].slot_key.method == 'SEC'
    assert not flow.results('PROJECT-P',actor=TECH,filters=ResultFilter(method='未注明')).cells
    with flow.store.connect() as conn:
        assert [tuple(r) for r in conn.execute('SELECT id,slot_id,record_json,source_json,detection_id FROM workflow_versions ORDER BY id')] == original
    out = ExcelExports(flow).create(ExportRequest(project='PROJECT-P',base_revision=result.json()['base_revision']),actor=LEAD)
    book = load_workbook(ExcelExports(flow).download(out.export_id,actor=LEAD))
    assert book['IdentityLinks'].max_row == 2
    assert book['Records']['M2'].value == '99%'
    book.close()
    from stability_hub.workflow_excel_verify import verify_workbook
    path = ExcelExports(flow).download(out.export_id,actor=LEAD)
    snapshot = json.loads((path.parent/'snapshot.json').read_text(encoding='utf-8'))
    tampered = load_workbook(path)
    tampered['IdentityLinks']['F2'] = 'unrecorded reason'
    changed = tmp_path/'tampered.xlsx'
    tampered.save(changed)
    tampered.close()
    assert 'Identity association output differs' in verify_workbook(changed,snapshot)
    # Same original physical identity now maps through the explicit overlay;
    # the higher-sequence, different value still needs the worksheet decision.
    p3 = flow.upload('PROJECT-P','third.xlsx',xlsx([['Old purity','0M',97,'%']]).read_bytes(),context,'third',actor=TECH)
    c = p3.candidates[0]
    assert c.status == 'different_value' and c.record.key.item == 'stable-purity'
    r = LedgerReview(flow).receive(p3.preview_id,ReceiveRequest(base_revision=p3.base_revision,idempotency_key='receive3',candidate_ids=(c.id,)),actor=TECH)
    r = save_decision(flow,p3.preview_id,DraftDecision(base_revision=r.base_revision,idempotency_key='correct3',reason='synthetic correction',
        candidate_id=c.id,candidate_revision=c.candidate_revision,action='correction',previous_version_id=first),actor=TECH)
    with flow.store.connect() as conn:
        v,_ = preflight(flow,conn,flow._upload(conn,p3.preview_id),'Results',TECH)
    r = review_decision(flow,p3.preview_id,DraftReview(base_revision=r.base_revision,idempotency_key='review3',reason='review synthetic correction',
        candidate_id=c.id,decision_fingerprint=v.decision_fingerprints[c.id],action='adopt'),actor=SENIOR)
    with flow.store.connect() as conn:
        v,_ = preflight(flow,conn,flow._upload(conn,p3.preview_id),'Results',TECH)
    assert v.can_commit
    r = commit(flow,p3.preview_id,SheetCommit(base_revision=r.base_revision,idempotency_key='commit3',reason='synthetic sheet',section='Results',fingerprint=v.fingerprint),actor=TECH)
    cells = flow.results('PROJECT-P',actor=TECH).cells
    assert len(cells) == 1 and cells[0].adopted.text == '97%'
    assert cells[0].adopted.result_version_id == r.result_version_ids[0]
    out = ExcelExports(flow).create(ExportRequest(project='PROJECT-P',base_revision=r.base_revision),actor=LEAD)
    assert out.state == 'verified'
    with flow.store.connect() as conn:
        source = json.loads(conn.execute('SELECT source_json FROM workflow_versions WHERE id=?',(r.result_version_ids[0],)).fetchone()[0])[0]
        assert source['identity_association']['actor_id'] == SENIOR.id
        assert conn.execute('SELECT previous_version_id FROM workflow_identity_lineage WHERE version_id=?',(r.result_version_ids[0],)).fetchone()[0] == first
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
        assert [tuple(v) for v in conn.execute('SELECT id,slot_id,record_json,source_json,detection_id FROM workflow_versions WHERE id IN (?,?) ORDER BY id',(first,second))] == original


def test_identity_association_requires_complete_scope_and_matching_units(tmp_path,xlsx,context):
    flow,p = preview(tmp_path,xlsx([['A','0M',99,'%']]),context)
    confirm_initial(flow,p)
    p2 = flow.upload('PROJECT-P','b.xlsx',xlsx([['B','0M',1,'mg/ml']]).read_bytes(),context,'b',actor=TECH)
    confirm_initial(flow,p2)
    cells = flow.results('PROJECT-P',actor=TECH).cells
    client = TestClient(create_app(flow.store,[],SENIOR))
    root = '/api/v1/projects/PROJECT-P/v5/identity-associations'
    # Omitting an existing target cannot silently hide its adopted result.
    a = next(c for c in cells if c.slot_key.item == 'A')
    b = next(c for c in cells if c.slot_key.item == 'B')
    response = client.post(root+'/preview',json={'slot_ids':[a.slot_id],'fields':{'item':'B'}})
    assert response.status_code == 409 and response.json()['code'] == 'incomplete_identity_scope'
    response = client.post(root+'/preview',json={'slot_ids':[a.slot_id,b.slot_id],'fields':{'item':'B'}})
    assert response.status_code == 409 and response.json()['code'] == 'identity_unit_conflict'
    assert len(flow.results('PROJECT-P',actor=TECH).cells) == 2


def test_unknown_historical_identity_requires_explicit_scoped_completion(tmp_path,xlsx,context):
    unknown = context.model_copy(update={'defaults':{k:v for k,v in context.defaults.items() if k not in {'presentation','study_id'}}})
    flow,p = preview(tmp_path,xlsx([['Assay','0M',99,'%']]),unknown)
    first = confirm_initial(flow,p).result_version_ids[0]
    cells = flow.results('PROJECT-P',actor=TECH).cells
    assert cells[0].slot_key.presentation.startswith('原文未注明')
    client = TestClient(create_app(flow.store,[],SENIOR))
    root = '/api/v1/projects/PROJECT-P/v5/identity-associations'
    scope = {'slot_ids':[cells[0].slot_id],'fields':{'presentation':'pen-1mg','study_id':'STUDY-P-01','method':'SEC'}}
    info = client.post(root+'/preview',json=scope).json()
    sid = info['groups'][0]['slot_id']
    command = {'base_revision':info['base_revision'],'idempotency_key':'complete','reason':'synthetic human supplies missing source identity',
               'scope':scope,'fingerprint':info['fingerprint'],'adoptions':{sid:first}}
    assert client.post(root+'/apply',json={**command,'fingerprint':'wrong'}).status_code == 409
    assert flow.results('PROJECT-P',actor=TECH).cells[0].slot_id == cells[0].slot_id
    assert client.post(root+'/apply',json=command).status_code == 200
    p2 = flow.upload('PROJECT-P','known.xlsx',xlsx([['Assay','0M',99,'%','SEC']],headers=['Test item','Time point','Result','Unit','Method']).read_bytes(),context,'known',actor=TECH)
    assert p2.candidates[0].status == 'same_value'
    p3 = flow.upload('PROJECT-P','unknown-again.xlsx',xlsx([['Assay','0M',99,'%']]).read_bytes(),unknown,'unknown-again',actor=TECH)
    assert p3.candidates[0].status == 'new'  # unknown in another file does not prove sameness


def test_simple_item_link_then_method_completion_preserves_future_identity(tmp_path,xlsx,context):
    flow,p = preview(tmp_path,xlsx([['Purity','0M',99,'%']]),context)
    first = confirm_initial(flow,p).result_version_ids[0]
    r = flow.add_items('PROJECT-P',NewItems(base_revision=flow.results('PROJECT-P',actor=TECH).base_revision,
        idempotency_key='dictionary',reason='stable dictionary',items=(ItemDefinition(id='purity',label='Purity',method='未注明',unit='%'),)),actor=TECH)
    apply_link(flow,'PROJECT-P',IdentityLink(base_revision=r.base_revision,idempotency_key='simple',reason='simple alias',
        item_id='purity',historical_item='Purity'),actor=SENIOR)
    client = TestClient(create_app(flow.store,[],SENIOR))
    root = '/api/v1/projects/PROJECT-P/v5/identity-associations'
    scope = {'slot_ids':[flow.results('PROJECT-P',actor=TECH).cells[0].slot_id],'fields':{'item':'purity','method':'SEC'}}
    info = client.post(root+'/preview',json=scope).json()
    command = {'base_revision':info['base_revision'],'idempotency_key':'complete','reason':'explicit method and identity',
               'scope':scope,'fingerprint':info['fingerprint'],'adoptions':{info['groups'][0]['slot_id']:first}}
    assert client.post(root+'/apply',json=command).status_code == 200
    p2 = flow.upload('PROJECT-P','completed.xlsx',xlsx([['Purity','0M',99,'%','SEC']],
        headers=['Test item','Time point','Result','Unit','Method']).read_bytes(),context,'completed',actor=TECH)
    assert p2.candidates[0].status == 'same_value'
    assert p2.candidates[0].record.key.item == 'purity'
