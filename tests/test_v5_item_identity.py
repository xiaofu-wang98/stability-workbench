from fastapi.testclient import TestClient

from stability_hub.api import create_app
from stability_hub.models import ProjectConfig
from stability_hub.permissions import Role,actor_for_role
from stability_hub.workflow_contracts import CandidateMapping
from test_v5_regressions import TECH,preview,confirm_initial


def test_generated_item_identity_survives_retry_display_rename_and_future_source(tmp_path,xlsx,context):
    flow,p = preview(tmp_path,xlsx([['Unknown label','0M',99,'%']]),context)
    client = TestClient(create_app(flow.store,[],TECH))
    command = {'base_revision':p.base_revision,'idempotency_key':'create-without-id','reason':'explicit independent test item',
               'items':[{'label':'Purity assay','method':'SEC','unit':'%'}]}
    response = client.post('/api/v1/projects/PROJECT-P/items',json=command)
    assert response.status_code == 200,response.text
    assert client.post('/api/v1/projects/PROJECT-P/items',json=command).json() == response.json()
    config = ProjectConfig.model_validate(flow.configuration('PROJECT-P',actor=TECH)['config'])
    assert len(config.items) == 1 and config.items[0].id.startswith('item_')
    iid = config.items[0].id
    r = flow.map_items(p.preview_id,CandidateMapping(base_revision=response.json()['base_revision'],idempotency_key='file-map',
        reason='map this file only',candidate_ids=(p.candidates[0].id,),item_id=iid),actor=TECH)
    first = confirm_initial(flow,flow.preview(p.preview_id,actor=TECH)).result_version_ids[0]
    with flow.store.connect() as conn:
        original = tuple(conn.execute('SELECT id,slot_id,record_json,source_json FROM workflow_versions WHERE id=?',(first,)).fetchone())
    senior = actor_for_role('senior','Senior',Role.SENIOR_TECHNICIAN)
    rename = {'base_revision':flow.results('PROJECT-P',actor=TECH).base_revision,'idempotency_key':'rename',
              'reason':'display spelling change','label':'Purity by SEC'}
    endpoint = f'/api/v1/projects/PROJECT-P/items/{iid}/display-name'
    assert client.post(endpoint,json=rename).status_code == 403
    client = TestClient(create_app(flow.store,[],senior))
    response = client.post(endpoint,json=rename)
    assert response.status_code == 200,response.text
    assert client.post(endpoint,json=rename).json() == response.json()
    item = ProjectConfig.model_validate(flow.configuration('PROJECT-P',actor=TECH)['config']).items[0]
    assert item.id == iid and item.label == 'Purity by SEC' and 'Purity assay' in item.aliases
    assert item.method == 'SEC' and item.unit == '%'
    p2 = flow.upload('PROJECT-P','new-name.xlsx',xlsx([['Purity by SEC','0M',99,'%']]).read_bytes(),context,'new-name',actor=TECH)
    assert p2.candidates[0].record.key.item == iid and p2.candidates[0].status == 'same_value'
    p3 = flow.upload('PROJECT-P','old-name.xlsx',xlsx([['Purity assay','0M',99,'%']]).read_bytes(),context,'old-name',actor=TECH)
    assert p3.candidates[0].record.key.item == iid and p3.candidates[0].status == 'same_value'
    with flow.store.connect() as conn:
        assert tuple(conn.execute('SELECT id,slot_id,record_json,source_json FROM workflow_versions WHERE id=?',(first,)).fetchone()) == original
    assert flow.preview(p.preview_id,actor=TECH).candidates[0].record.key.item == iid
    assert r.base_revision < response.json()['base_revision']
