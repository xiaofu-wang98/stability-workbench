from fastapi.testclient import TestClient
from io import BytesIO
from openpyxl import Workbook

from stability_hub.api import create_app
from test_v5_regressions import TECH,preview


def test_file_rule_signature_and_saved_draft_state_are_authoritative(tmp_path,xlsx,context):
    flow,p = preview(tmp_path,xlsx([['Purity','0M',99,'%']]),context)
    c=p.candidates[0]
    assert c.mapping_source == {'source_name':'Purity','method':'','method_version':'','unit':'%','subresult':''}
    client=TestClient(create_app(flow.store,[],TECH))
    root=f'/api/v1/previews/{p.preview_id}/v5'
    assert client.get(root+'/draft-state').json() == {'base_revision':p.base_revision,'decisions':{}}
    command={'base_revision':p.base_revision,'idempotency_key':'exclude','reason':'synthetic exclusion',
             'candidate_id':c.id,'candidate_revision':c.candidate_revision,'action':'exclude'}
    r=client.post(root+'/decisions',json=command)
    assert r.status_code==200,r.text
    state=client.get(root+'/draft-state').json()
    assert state['base_revision']==r.json()['base_revision']
    assert state['decisions'][c.id]['action']=='exclude'
    assert state['decisions'][c.id]['reason']=='synthetic exclusion'


def test_zero_candidate_upload_can_download_hash_checked_original(tmp_path,xlsx,context):
    flow,_ = preview(tmp_path,xlsx([['Purity','0M',99,'%']]),context)
    book=Workbook()
    book.active['A1']='Unsupported synthetic note'
    data=BytesIO()
    book.save(data)
    p=flow.upload('PROJECT-P','zero.xlsx',data.getvalue(),context,'zero',actor=TECH)
    assert not p.candidates
    client=TestClient(create_app(flow.store,[],TECH))
    url=f'/api/v1/previews/{p.preview_id}/file'
    response=client.get(url)
    assert response.status_code==200 and response.content==data.getvalue()
    original=flow.store.home/'sources'/(p.source_hash+'.xlsx')
    original.write_bytes(b'corrupted isolated fixture')
    assert client.get(url).status_code==409
