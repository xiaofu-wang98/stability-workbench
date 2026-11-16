"""Unsaved comparisons are read-only; grouping, alignment and exports share one scope."""
import json
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image

from stability_hub.api import create_app
from stability_hub.comparability import QuickCompare, StudySave, StudyQuery, compare, save, studies
from stability_hub.quick_comparison import catalog, quick_compare
from stability_hub.study_reports import ReportRequest, create_report
from test_comparability_reports import study_data, LEAD
from test_result_display import table_rows
from test_v5_regressions import TECH, preview, confirm_initial


def selection(flow):
    with flow.store.read() as conn:
        data=catalog(conn,'PROJECT-P')
    return QuickCompare(base_revision=data['base_revision'],sample_ids=tuple(s['id'] for s in data['samples']))


def test_unsaved_catalog_preview_and_graph_are_read_only(tmp_path,xlsx,context,monkeypatch):
    flow,_=study_data(tmp_path,xlsx,context)
    app=create_app(flow.store,[],TECH)
    def fail(*args,**kwargs):
        raise AssertionError('Quick comparison must not reparse sources or preview intake')
    monkeypatch.setattr(app.state.workflow,'_source_rows',fail)
    monkeypatch.setattr(app.state.workflow,'_preview',fail)
    before=table_rows(flow.store)
    with TestClient(app) as client:
        response=client.get('/api/v1/projects/PROJECT-P/comparison-catalog')
        assert response.status_code==200,response.text
        data=response.json()
        assert data['studies']==[] and len(data['samples'])==2
        assert sum(o['slot_count'] for o in data['options'])==5
        payload={'base_revision':data['base_revision'],'sample_ids':[s['id'] for s in data['samples']]}
        url='/api/v1/projects/PROJECT-P/comparison-preview'
        response=client.post(url,json=payload)
        assert response.status_code==200,response.text
        value=response.json()
        assert value['cells']==[] and value['total_slots']==5
        assert len(value['groups'])==2
        assert {c['field'] for c in value['alignment_candidates']}=={'presentation','study_id'}
        assert value['roles_count']=={'before':0,'after':0,'selected':2}
        group=value['groups'][0]
        response=client.post(url,json={**payload,'group_id':group['id'],'include_cells':True})
        assert response.status_code==200,response.text
        chart=response.json()
        assert chart['trend']['axis']['max']==5
        assert len(chart['cells'])==len(group['slot_ids'])
        assert chart['trend']['points'][0]['version_id']
        assert '变更' not in chart['trend']['series'][0]['batch']
    assert table_rows(flow.store)==before


def test_alignment_and_one_sided_grouping_do_not_hide_results(tmp_path,xlsx,context):
    flow,_=study_data(tmp_path,xlsx,context)
    command=selection(flow)
    aligned=QuickCompare(**{**command.model_dump(),'aligned_fields':['presentation','study_id'],'alignment_reason':'Explicit local alignment',
        'members':[{'sample_id':sid,'role':'after'} for sid in command.sample_ids]})
    with flow.store.read() as conn:
        view=quick_compare(conn,'PROJECT-P',aligned)
        assert len(view['groups'])==1 and view['roles_count']['after']==2
        assert view['alignment_candidates']==[]
        chart=quick_compare(conn,'PROJECT-P',aligned.model_copy(update={'group_id':view['groups'][0]['id']}))['trend']
        assert len(chart['series'])==2 and len(chart['points'])==4 and len(chart['omitted'])==1
        assert chart['axis']['max']==5
        assert studies(conn,'PROJECT-P')==[]


def test_selection_validation_revision_and_project_isolation(tmp_path,xlsx,context):
    flow,_=study_data(tmp_path,xlsx,context)
    payload=selection(flow).model_dump(mode='json')
    with TestClient(create_app(flow.store,[],TECH)) as client:
        url='/api/v1/projects/PROJECT-P/comparison-preview'
        for extra,code in [({'sample_ids':['other-project-sample']},409),({'base_revision':0},409),
                           ({'aligned_fields':['unit'],'alignment_reason':'no'},422),
                           ({'aligned_fields':['presentation'],'alignment_reason':''},422),
                           ({'sample_ids':[payload['sample_ids'][0]]*2},422),
                           ({'members':[{'sample_id':'not-selected','role':'before'}]},422),
                           ({'group_id':'old-group'},409)]:
            response=client.post(url,json={**payload,**extra})
            assert response.status_code==code,response.text
        assert client.get('/api/v1/projects/OTHER/comparison-catalog').status_code==404


def test_save_neutral_study_keeps_current_filters_and_no_result_changes(tmp_path,xlsx,context):
    flow,_=study_data(tmp_path,xlsx,context)
    command=selection(flow)
    before=table_rows(flow.store)
    saved=save(flow,'PROJECT-P','new',StudySave(base_revision=command.base_revision,idempotency_key=uuid4().hex,
        title='Neutral comparison',reason='Save exactly this time range',
        members=tuple({'sample_id':sid,'role':'selected'} for sid in command.sample_ids),filters={'month':0}),TECH)
    with flow.store.read() as conn:
        value=compare(conn,'PROJECT-P',saved['study']['id'],StudyQuery(base_revision=saved['base_revision']))
        assert len(value['cells'])==2
        assert all(c['slot_key']['month']==0 and c['study_role']=='selected' for c in value['cells'])
    after=table_rows(flow.store)
    assert all(after[k]==v for k,v in before.items() if k not in {'workflow_events','workflow_projects','workflow_requests','sqlite_sequence'})


def test_quick_report_uses_current_scope_and_axis_without_saving_study(tmp_path,xlsx,context):
    flow,_=study_data(tmp_path,xlsx,context)
    cmd=selection(flow)
    cmd=QuickCompare(**{**cmd.model_dump(),'filters':{'month':0},'aligned_fields':['presentation','study_id'],
        'alignment_reason':'Explicit local scope'})
    before=table_rows(flow.store)
    with flow.store.read() as conn:
        view=quick_compare(conn,'PROJECT-P',cmd)
    result=create_report(flow,ReportRequest(project='PROJECT-P',base_revision=cmd.base_revision,kind='quick',quick=cmd,
        axis_mode='manual',axis_min=0,axis_max=2),LEAD)
    assert result['result_count']==2 and result['chart_count']==1
    folder=flow.store.home/'workflow-exports'/result['export_id']
    image=next(folder.glob('*.png'))
    with Image.open(image) as picture:
        data=json.loads(picture.info['Description'])
        assert data['axis']['max']==2 and len(data['series'])==2
        assert data['filters']['month']==0
        assert set(p['slot_id'] for p in data['points'])==set(view['groups'][0]['slot_ids'])
    with flow.store.read() as conn:
        assert studies(conn,'PROJECT-P')==[]
    after=table_rows(flow.store)
    assert all(after[k]==v for k,v in before.items() if k!='workflow_exports')


def test_same_batch_different_sources_keep_separate_curves_after_alignment(tmp_path,xlsx,context):
    from stability_hub.models import ImportContext
    flow,p=preview(tmp_path,xlsx([['HMW','0M',.3,'%']],name='first.xlsx'),context)
    confirm_initial(flow,p)
    second=ImportContext(defaults={**context.defaults,'presentation':'independent-source','study_id':'second-study'})
    path=xlsx([['HMW','0M',.4,'%']],name='second.xlsx')
    p=flow.upload('PROJECT-P',path.name,path.read_bytes(),second,'second',actor=TECH)
    confirm_initial(flow,p)
    command=selection(flow)
    command=QuickCompare(**{**command.model_dump(),'aligned_fields':['presentation','study_id'],
        'alignment_reason':'Explicitly compare two independent sources for the same batch'})
    with flow.store.read() as conn:
        view=quick_compare(conn,'PROJECT-P',command)
        assert len(view['groups'])==1
        chart=quick_compare(conn,'PROJECT-P',command.model_copy(update={'group_id':view['groups'][0]['id']}))['trend']
    assert len(chart['series'])==2 and len(chart['points'])==2
    assert len(set(s['batch'] for s in chart['series']))==2
    assert {p['value'] for p in chart['points']}=={'0.3','0.4'}
