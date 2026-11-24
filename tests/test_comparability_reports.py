"""Real multi-project studies, explicit alignment and shared plot/export scales."""
import json
from uuid import uuid4
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from stability_hub.api import create_app
from stability_hub.comparability import inventory, save, compare, StudySave, Member, StudyQuery
from stability_hub.models import ImportContext
from stability_hub.permissions import Role, actor_for_role
from stability_hub.plot_axes import axis_range
from stability_hub.study_reports import ReportRequest, create_report
from stability_hub.workflow_common import WorkflowError
from test_v5_regressions import TECH, preview, confirm_initial
from test_result_display import table_rows

LEAD = actor_for_role('lead','Lead',Role.PROJECT_LEAD)


def study_data(tmp_path,xlsx,context):
    flow,p = preview(tmp_path,xlsx([['HMW','0M',.3,'%'],['HMW','3M',.6,'%'],['HMW','6M','ND','%']],name='before.xlsx'),context)
    confirm_initial(flow,p)
    ctx = ImportContext(defaults={**context.defaults,'batch':'B002','presentation':'unknown-source-B','study_id':'study-B'})
    path=xlsx([['HMW','0M',.4,'%'],['HMW','3M',.5,'%']],name='after.xlsx')
    p=flow.upload('PROJECT-P',path.name,path.read_bytes(),ctx,'second',actor=TECH)
    confirm_initial(flow,p)
    with flow.store.read() as conn:
        members = inventory(conn,'PROJECT-P')
    revision = flow.results('PROJECT-P',actor=TECH).base_revision
    cmd=StudySave(base_revision=revision,idempotency_key=uuid4().hex,title='明确研究',reason='人工指定工艺前后',
        members=tuple(Member(sample_id=m['id'],role='before' if m['batch']=='B001A1' else 'after') for m in members))
    return flow,cmd


def test_axis_profiles_and_manual_clipping():
    assert axis_range([.3,.6],'纯度 SEC-HPLC HMW','%')['max']==5
    assert axis_range([.3,8],'HMW','%')['max']>=8
    assert axis_range([99.3,99.6],'单体 Monomer','%')['min']==90
    ph_scale = axis_range([7, 7.1], 'pH', '')
    assert ph_scale['family'] == 'ph' and 5.5 <= ph_scale['min'] <= 6.0 and 8.0 <= ph_scale['max'] <= 8.5
    assert axis_range([99,105],'相对活性','%')['max']>105
    detail=axis_range([.3,.6],'HMW','%','detail')
    assert detail['max']-detail['min']<1
    with pytest.raises(WorkflowError,match='裁掉'):
        axis_range([.3,.6],'HMW','%','manual',.4,1)
    for a,b in [(1,0),(float('nan'),1),(0,float('inf'))]:
        with pytest.raises(WorkflowError):
            axis_range([.3],'HMW','%','manual',a,b)


def test_explicit_alignment_is_local_audited_idempotent_and_does_not_change_facts(tmp_path,xlsx,context):
    flow,cmd=study_data(tmp_path,xlsx,context)
    before=table_rows(flow.store)
    saved=save(flow,'PROJECT-P','new',cmd,TECH)
    assert save(flow,'PROJECT-P','new',cmd,TECH)==saved
    sid=saved['study']['id']
    with flow.store.read() as conn:
        view=compare(conn,'PROJECT-P',sid,StudyQuery(base_revision=saved['base_revision']))
        assert len(view['groups'])==2
    aligned=cmd.model_copy(update={'base_revision':saved['base_revision'],'idempotency_key':uuid4().hex,
        'aligned_fields':('presentation','study_id'),'alignment_reason':'本研究指定两份来源属于同一比较条件'})
    saved=save(flow,'PROJECT-P',sid,aligned,LEAD)
    with flow.store.read() as conn:
        view=compare(conn,'PROJECT-P',sid,StudyQuery(base_revision=saved['base_revision']))
        assert len(view['groups'])==1
        assert {c['slot_key']['presentation'] for c in view['cells']}=={'pen-1mg','unknown-source-B'}
        view=compare(conn,'PROJECT-P',sid,StudyQuery(base_revision=saved['base_revision'],group_id=view['groups'][0]['id']))
        assert len(view['trend']['series'])==2
        assert view['trend']['axis']['max']==5
        assert len(view['trend']['omitted'])==1
    after=table_rows(flow.store)
    assert {k:v for k,v in before.items() if k not in {'workflow_events','workflow_requests','workflow_projects','sqlite_sequence'}}=={
        k:v for k,v in after.items() if k not in {'workflow_events','workflow_requests','workflow_projects','sqlite_sequence'}}
    with pytest.raises(WorkflowError,match='修订|变化'):
        save(flow,'PROJECT-P',sid,cmd.model_copy(update={'idempotency_key':'stale'}),TECH)


def test_report_package_is_real_verified_restricted_and_tamper_evident(tmp_path,xlsx,context):
    flow,cmd=study_data(tmp_path,xlsx,context)
    saved=save(flow,'PROJECT-P','new',cmd,TECH)
    before=table_rows(flow.store)
    command=ReportRequest(project='PROJECT-P',base_revision=saved['base_revision'],kind='comparability',study_id=saved['study']['id'])
    with pytest.raises(PermissionError):
        create_report(flow,command,TECH)
    app=create_app(flow.store,[],LEAD)
    with TestClient(app) as client:
        res=client.post('/api/v1/reports',json=command.model_dump(mode='json'))
        assert res.status_code==200,res.text
        result=res.json()
        assert result['result_count']==5 and result['chart_count']==2
        package=client.get(result['download_url'])
        assert package.status_code==200
        assert client.get(result['word_download_url']).status_code==200
        path=flow.store.home/'workflow-exports'/result['export_id']/'report.zip'
        with ZipFile(path) as z:
            assert {'report.docx','results.xlsx','snapshot.json','verification.json'}<=set(z.namelist())
            snapshot=json.loads(z.read('snapshot.json'))
            assert len(snapshot['entries'])==5
            assert json.loads(z.read('verification.json'))['passed']
        for image in path.parent.glob('*.png'):
            with Image.open(image) as img:
                assert json.loads(img.info['Description'])['axis']['max']==5
        path.write_bytes(b'changed')
        assert client.get(result['download_url']).status_code==409
    after=table_rows(flow.store)
    assert {k:v for k,v in before.items() if k!='workflow_exports'}=={k:v for k,v in after.items() if k!='workflow_exports'}


def test_study_routes_validate_project_and_revision(tmp_path,xlsx,context):
    flow,cmd=study_data(tmp_path,xlsx,context)
    app=create_app(flow.store,[],TECH)
    with TestClient(app) as client:
        response=client.post('/api/v1/projects/PROJECT-P/comparison-studies/new',json=cmd.model_dump(mode='json'))
        assert response.status_code==200,response.text
        saved=response.json()
        route=f"/api/v1/projects/PROJECT-P/comparison-studies/{saved['study']['id']}/results"
        assert client.get(route,params={'base_revision':saved['base_revision']}).status_code==200
        assert client.get(route,params={'base_revision':0}).status_code==409
        assert client.get('/api/v1/projects/other/comparison-studies').status_code==404
        invalid={**cmd.model_dump(mode='json'),'aligned_fields':['presentation'],'alignment_reason':''}
        assert client.post('/api/v1/projects/PROJECT-P/comparison-studies/new',json=invalid).status_code==422


def test_release_and_stability_report_ranges_stay_separate(tmp_path,xlsx,context):
    release=ImportContext(role='release_coa',defaults={**context.defaults,'study':'release','condition':'release'})
    flow,p=preview(tmp_path,xlsx([['HMW','',.3,'%']]),release)
    confirm_initial(flow,p)
    revision=flow.results('PROJECT-P',actor=TECH).base_revision
    with flow.store.read() as conn:
        samples=inventory(conn,'PROJECT-P')
    saved=save(flow,'PROJECT-P','new',StudySave(base_revision=revision,idempotency_key=uuid4().hex,
        title='Release only',reason='Explicit selection',members=(Member(sample_id=samples[0]['id'],role='before'),)),TECH)
    with flow.store.read() as conn:
        view=compare(conn,'PROJECT-P',saved['study']['id'],StudyQuery(base_revision=saved['base_revision'],mode='release'))
        assert len(view['cells'])==1
        assert compare(conn,'PROJECT-P',saved['study']['id'],StudyQuery(base_revision=saved['base_revision']))['cells']==[]
        graph=compare(conn,'PROJECT-P',saved['study']['id'],StudyQuery(base_revision=saved['base_revision'],
            mode='release',group_id=view['groups'][0]['id']))['trend']
        assert graph['scope'][9]=='release' and graph['points'][0]['x']==0
    result=create_report(flow,ReportRequest(project='PROJECT-P',base_revision=saved['base_revision'],
        filters={'study':'release'},include_charts=False),LEAD)
    assert result['result_count']==1 and result['chart_count']==0
    with pytest.raises(WorkflowError,match='没有'):
        create_report(flow,ReportRequest(project='PROJECT-P',base_revision=saved['base_revision'],
            kind='comparability',study_id=saved['study']['id'],mode='stability'),LEAD)
