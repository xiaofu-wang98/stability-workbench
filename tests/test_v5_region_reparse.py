from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import Workbook
from openpyxl.comments import Comment

from stability_hub.api import create_app
from stability_hub.models import ProjectConfig
from stability_hub.permissions import Role, actor_for_role
from stability_hub.storage import Store
from stability_hub.v5_contracts import RegionDecision
from stability_hub.workflow_contracts import ExportRequest
from stability_hub.workflow_excel import ExcelExports
from stability_hub.worksheet_regions import coverage, classify
from test_v5_regressions import TECH, confirm_initial


def setup(tmp_path, context, *, uncertain=False):
    book = Workbook()
    s = book.active
    s.title = 'Unrecognized'
    s.append(['Label x', 'Period x', 'Reading x', 'Unused note'])
    s.append(['Synthetic assay', '0M', .995, 'Do not silently consume'])
    s.append(['Synthetic assay', '3M', .994])
    s['C2'].number_format = s['C3'].number_format = '0.0%'
    if uncertain:
        s['C2'].comment = Comment('Possible replacement requires review', 'synthetic')
    data = BytesIO()
    book.save(data)
    app = create_app(Store(tmp_path/'isolated'), [ProjectConfig(project='PROJECT-P')], TECH)
    flow = app.state.workflow
    p = flow.upload('PROJECT-P', 'odd.xlsx', data.getvalue(), context, 'upload', actor=TECH)
    assert not p.candidates
    return flow, TestClient(app), p


def scope():
    return {'section':'Unrecognized','range_ref':'A1:D3','header_row':1,
            'columns':{'test_item':'A','time_point':'B','result':'C'},
            'common_fields':{'method':'UV','presentation':'10mg','study':'long_term','study_id':'SYNTH','unit':'%'}}


def test_region_reparse_percent_preserves_source_and_reaches_verified_export(tmp_path, context):
    flow, client, p = setup(tmp_path, context)
    root = f'/api/v1/previews/{p.preview_id}/v5/region-parse'
    res = client.post(root+'/preview', json=scope())
    assert res.status_code == 200, res.text
    info = res.json()
    assert info['unmapped_coordinates'] == ['D1','D2']
    assert [r['coordinate'] for r in info['rows']] == ['C2','C3']
    command = {'base_revision':p.base_revision,'idempotency_key':'region','reason':'synthetic explicit columns',
               'fingerprint':info['fingerprint'],'scope':scope()}
    res = client.post(root+'/apply', json=command)
    assert res.status_code == 200, res.text
    assert client.post(root+'/apply', json=command).json() == res.json()
    p = flow.preview(p.preview_id, actor=TECH)
    assert len(p.candidates) == 2
    assert [str(c.record.result.value) for c in p.candidates] == ['99.500','99.400']
    assert all('result' not in c.corrections for c in p.candidates)
    with flow.store.connect() as conn:
        sheets = coverage(conn, flow._upload(conn,p.preview_id))
        assert conn.execute('SELECT COUNT(*) FROM workflow_versions').fetchone()[0] == 0
    roles = {c:r['role'] for r in sheets[0]['regions'] for c in r['coordinates']}
    assert roles['C2'] == 'result' and roles['A2'] == 'identity' and roles['D2'] == 'unclassified'
    for index, region in enumerate(sheets[0]['regions']):
        if region['role'] == 'unclassified':
            p = flow.preview(p.preview_id, actor=TECH)
            classify(flow,p.preview_id,RegionDecision(base_revision=p.base_revision,idempotency_key=f'note-{index}',
                reason='synthetic explicit note',region_id=region['id'],role='note'),actor=TECH)
    confirm_initial(flow, flow.preview(p.preview_id,actor=TECH))
    out = ExcelExports(flow).create(ExportRequest(project='PROJECT-P',base_revision=flow.results('PROJECT-P',actor=TECH).base_revision),
        actor=actor_for_role('lead','Lead',Role.PROJECT_LEAD))
    assert out.state == 'verified'
    assert client.post(root+'/preview', json=scope()).status_code == 409


def test_region_reparse_rejects_stale_duplicate_and_implicit_result_copy(tmp_path, context):
    flow, client, p = setup(tmp_path, context, uncertain=True)
    root = f'/api/v1/previews/{p.preview_id}/v5/region-parse'
    info = client.post(root+'/preview', json=scope()).json()
    assert info['rows'][0]['issues'][0]['code'] == 'source_comment'
    command = {'base_revision':p.base_revision,'idempotency_key':'region','reason':'explicit',
               'fingerprint':'stale','scope':scope()}
    assert client.post(root+'/apply', json=command).status_code == 409
    assert not flow.preview(p.preview_id,actor=TECH).candidates
    command['fingerprint'] = info['fingerprint']
    assert client.post(root+'/apply', json=command).status_code == 200
    p = flow.preview(p.preview_id,actor=TECH)
    assert p.candidates[0].record is None
    assert client.post(root+'/preview',json=scope()).status_code == 409
    assert client.post(root+'/apply',json={**command,'idempotency_key':'different'}).status_code == 409
    assert len(flow.preview(p.preview_id,actor=TECH).candidates) == 2
    invalid = scope()
    invalid['common_fields']['result'] = '99'
    assert client.post(root+'/preview',json=invalid).status_code == 422
