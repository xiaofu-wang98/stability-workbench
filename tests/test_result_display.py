"""Read-only real result presentation, adopted values, exact exports and gaps."""
import json
import sqlite3
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from stability_hub.api import create_app
from stability_hub.result_display import display_results, trend_results
from stability_hub.result_versions import ResultVersions
from stability_hub.workflow_contracts import ResultFilter, ExportRequest, ResultChange
from stability_hub.workflow_excel import ExcelExports
from stability_hub.permissions import actor_for_role, Role
from stability_hub.workflow_common import WorkflowError
from test_v5_regressions import TECH, preview, confirm_initial

LEAD = actor_for_role('lead','Lead',Role.PROJECT_LEAD)


def table_rows(store):
    with store.read() as conn:
        return {r[0]:sorted(repr(tuple(x)) for x in conn.execute('SELECT * FROM "'+r[0]+'"'))
                for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def data(tmp_path, xlsx, context):
    flow,p = preview(tmp_path,xlsx([['Purity','0M',99,'%'],['Purity','3M','ND','%'],
        ['Purity','6M','<0.1','%'],['Purity','12M',98,'%'],['Appearance','0M','Clear','']]),context)
    confirm_initial(flow,p)
    return flow,p


def test_get_display_and_trend_do_not_write_or_reparse(tmp_path,xlsx,context,monkeypatch):
    flow,_ = data(tmp_path,xlsx,context)
    app = create_app(flow.store,[],TECH)
    def fail(*a,**k):
        raise AssertionError('Presentation must not parse sources or run intake preview')
    monkeypatch.setattr(app.state.workflow,'_source_rows',fail)
    monkeypatch.setattr(app.state.workflow,'_preview',fail)
    before=table_rows(flow.store)
    with TestClient(app) as client:
        response=client.get('/api/v1/projects/PROJECT-P/result-display')
        assert response.status_code==200,response.text
        shown=response.json()
        assert shown['total']==5
        group=next(g for g in shown['analysis_groups'] if g['key']['item']=='Purity')
        url='/api/v1/projects/PROJECT-P/result-trend'
        params={'group_id':group['id'],'base_revision':shown['base_revision'],'batches':['B001A1']}
        response=client.get(url,params=params)
        assert response.status_code==200,response.text
        trend=response.json()
        assert [p['value'] for p in trend['series'][0]['values']]==['99',None,None,'98']
        assert [p['x'] for p in trend['series'][0]['values']]==[0,3,6,12]
        assert all(p['slot_id'] and p['version_id'] for p in trend['points'])
        assert client.get(url,params={**params,'base_revision':0}).status_code==409
        assert client.get(url,params={**params,'batches':['UNKNOWN']}).status_code==422
        assert client.get(url,params={**params,'group_id':'bad'}).status_code==409
    assert table_rows(flow.store)==before
    with flow.store.read() as conn,pytest.raises(sqlite3.OperationalError):
        conn.execute("UPDATE workflow_projects SET revision=revision+1")


def test_pending_display_keeps_adopted_plot_and_export_selection(tmp_path,xlsx,context):
    flow,p=data(tmp_path,xlsx,context)
    first=flow.results('PROJECT-P',actor=TECH)
    slot=next(c for c in first.cells if c.slot_key.item=='Purity' and c.slot_key.month==0)
    ResultVersions(flow).change(slot.display.result_version_id,ResultChange(base_revision=first.base_revision,
        idempotency_key=uuid4().hex,result='97',reason='Independent pending correction'),actor=TECH)
    with flow.store.read() as conn:
        shown=display_results(conn,p.project,ResultFilter())
        group=next(g for g in shown['analysis_groups'] if g['key']['item']=='Purity')
        trend=trend_results(conn,p.project,ResultFilter(),group_id=group['id'],
                            revision=shown['base_revision'],batches=['B001A1'])
    current=next(c for c in shown['cells'] if c['slot_id']==slot.slot_id)
    assert current['display']['pending_confirmation'] and current['display']['text']=='97%'
    assert current['adopted']['text']=='99%'
    assert trend['points'][0]['version_id']==slot.adopted.result_version_id
    export=ExcelExports(flow)
    result=export.create(ExportRequest(project=p.project,base_revision=shown['base_revision'],
        slot_ids=tuple(group['slot_ids']),include_png=True),actor=LEAD)
    png=export.download_image(result.export_id,actor=LEAD)
    with Image.open(png) as img:
        metadata=json.loads(img.info['Description'])
    assert metadata['selection_id']==trend['selection_id']
    assert metadata['series']==trend['series']
    snap=json.loads((png.parent/'snapshot.json').read_text(encoding='utf-8'))
    assert len(snap['entries'])==4 and all(e['display']['slot_id'] in group['slot_ids'] for e in snap['entries'])
    with pytest.raises(WorkflowError,match='所选结果'):
        export.create(ExportRequest(project=p.project,base_revision=shown['base_revision'],slot_ids=('fake',)),actor=LEAD)


def test_groups_keep_scope_and_qualitative_results(tmp_path,xlsx,context):
    flow,p=data(tmp_path,xlsx,context)
    with flow.store.read() as conn:
        shown=display_results(conn,p.project,ResultFilter())
        group=next(g for g in shown['analysis_groups'] if g['key']['item']=='Appearance')
        trend=trend_results(conn,p.project,ResultFilter(),group_id=group['id'],
                            revision=shown['base_revision'],batches=['B001A1'])
    assert not trend['points'] and trend['omitted'][0]['raw']=='Clear'
    assert trend['series'][0]['values'][0]['value'] is None


def test_selected_export_keeps_only_related_draft_context(tmp_path,xlsx,context):
    from stability_hub.result_snapshot import create_snapshot
    flow,p=data(tmp_path,xlsx,context)
    other=context.model_copy(update={'defaults':{**context.defaults,'batch':'OTHER'}})
    flow.upload(p.project,'other.xlsx',xlsx([['Purity','18M',97,'%']]).read_bytes(),other,'other',actor=TECH)
    related=flow.upload(p.project,'related.xlsx',xlsx([['Purity','18M',97,'%']]).read_bytes(),context,'related',actor=TECH)
    with flow.store.read() as conn:
        shown=display_results(conn,p.project,ResultFilter())
        group=next(g for g in shown['analysis_groups'] if g['key']['item']=='Purity')
    with flow.store.connect() as conn:
        snapshot=create_snapshot(flow,conn,ExportRequest(project=p.project,base_revision=shown['base_revision'],slot_ids=tuple(group['slot_ids'])))
    assert [d['id'] for d in snapshot['drafts']]==[related.candidates[0].id]
