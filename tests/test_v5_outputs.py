import json

import pytest
from openpyxl import load_workbook
from PIL import Image

from stability_hub.permissions import Role,actor_for_role
from stability_hub.result_snapshot import create_snapshot
from stability_hub.trend_export import chart_data
from stability_hub.workflow_common import WorkflowError
from stability_hub.workflow_contracts import ExportRequest,ResultFilter
from stability_hub.workflow_excel import ExcelExports
from test_v5_regressions import TECH,preview,confirm_initial

LEAD = actor_for_role('lead','Lead',Role.PROJECT_LEAD)


def test_a22_a23_same_snapshot_excel_and_real_png(tmp_path,xlsx,context):
    flow,p = preview(tmp_path,xlsx([['Purity','0M',99,'%'],['Purity','3M','ND','%'],
        ['Purity','6M','<0.1','%'],['Purity','12M',98,'%'],['Purity','18M','不汇报结果','%']]),context)
    confirm_initial(flow,p)
    request = ExportRequest(project='PROJECT-P',base_revision=flow.results('PROJECT-P',actor=TECH).base_revision,
                            filters=ResultFilter(item='Purity'),include_png=True)
    exports = ExcelExports(flow)
    out = exports.create(request,actor=LEAD)
    png = exports.download_image(out.export_id,actor=LEAD)
    book = load_workbook(exports.download(out.export_id,actor=LEAD))
    with Image.open(png) as img:
        assert img.size == (1540,840)
        metadata = json.loads(img.info['Description'])
    snapshot = json.loads((png.parent/'snapshot.json').read_text(encoding='utf-8'))
    assert metadata['snapshot_id'] == snapshot['snapshot_id']
    assert {point['x'] for point in metadata['points']} == {0,12}
    assert [point['value'] for point in metadata['points']] == ['99','98']
    assert {point['raw'] for point in metadata['omitted']} == {'ND','<0.1','不汇报结果'}
    assert book['Records'].max_row-1 == len(metadata['points']) + len(metadata['omitted'])
    assert out.image_download_url.endswith('/image')
    assert all(point['unit']=='%' for point in metadata['points'])
    book.close()
    png.write_bytes(png.read_bytes()+b'tampered')
    with pytest.raises(WorkflowError,match='已变化'):
        exports.download_image(out.export_id,actor=LEAD)


def test_a22_drafts_follow_exact_filters_and_unknown_scope_is_explicit(tmp_path,xlsx,context):
    flow,p = preview(tmp_path,xlsx([['Purity','0M',99,'%']]),context)
    confirm_initial(flow,p)
    other = context.model_copy(update={'defaults':{**context.defaults,'batch':'OTHER'}})
    flow.upload('PROJECT-P','other.xlsx',xlsx([['Purity','0M',98,'%']]).read_bytes(),other,'other',actor=TECH)
    draft = flow.upload('PROJECT-P','same-scope.xlsx',xlsx([['Purity','3M',97,'%']]).read_bytes(),context,'same-scope',actor=TECH)
    req = ExportRequest(project='PROJECT-P',base_revision=draft.base_revision,filters=ResultFilter(batch='B001A1'))
    with flow.store.connect() as conn:
        snapshot = create_snapshot(flow,conn,req)
    assert [d['id'] for d in snapshot['drafts']] == [draft.candidates[0].id]
    assert snapshot['drafts'][0]['scope'] == 'in_scope'
    uncertain = context.model_copy(update={'defaults':{k:v for k,v in context.defaults.items() if k!='presentation'}})
    unknown = flow.upload('PROJECT-P','unknown-spec.xlsx',xlsx([['Purity','6M',96,'%']]).read_bytes(),uncertain,'unknown-spec',actor=TECH)
    req = ExportRequest(project='PROJECT-P',base_revision=unknown.base_revision,
                        filters=ResultFilter(batch='B001A1',presentation='pen-1mg'))
    with flow.store.connect() as conn:
        snapshot = create_snapshot(flow,conn,req)
    unassigned = next(d for d in snapshot['drafts'] if d['id']==unknown.candidates[0].id)
    assert unassigned['scope']=='unassigned' and unassigned['unknown_dimensions']==['presentation']


def test_a24_chart_refuses_mixed_method_and_time_units(tmp_path,xlsx,context):
    flow,p = preview(tmp_path,xlsx([['Purity','7D',99,'%'],['Purity','7M',98,'%']]),context)
    confirm_initial(flow,p)
    req = ExportRequest(project='PROJECT-P',base_revision=flow.results('PROJECT-P',actor=TECH).base_revision)
    with flow.store.connect() as conn:
        snapshot = create_snapshot(flow,conn,req)
    with pytest.raises(WorkflowError,match='时间单位'):
        chart_data(snapshot)


def test_a24_specs_methods_days_and_incremental_months_stay_distinct(tmp_path):
    from io import BytesIO
    from openpyxl import Workbook
    from stability_hub.models import ImportContext,ProjectConfig
    from stability_hub.storage import Store
    from stability_hub.workflow import Workflow
    def source(rows):
        book = Workbook()
        book.active.title = 'Results'
        book.active.append(['Project','Sample type','Batch','Presentation','Study','Study id','Condition',
                            'Test item','Method','Time point','Result','Unit'])
        for spec,method,time,value in rows:
            book.active.append(['PROJECT-P','DP','B1',spec,'long_term','S1','5C','Assay',method,time,value,'%'])
        data = BytesIO()
        book.save(data)
        return data.getvalue()
    flow = Workflow(Store(tmp_path/'identity-output'))
    flow.configure(ProjectConfig(project='PROJECT-P'))
    p = flow.upload('PROJECT-P','first.xlsx',source([('pen1','UV','6M',99),('pen2','UV','6M',98),
        ('pen1','SEC','6M',97),('pen1','UV','7D',96),('pen1','UV','7M',95)]),ImportContext(),'first',actor=TECH)
    confirm_initial(flow,p)
    p = flow.upload('PROJECT-P','supplement.xlsx',source([('pen1','UV','3M',99.5)]),ImportContext(),'supplement',actor=TECH)
    confirm_initial(flow,p)
    assert flow.results('PROJECT-P',actor=TECH).total == 6
    day = flow.results('PROJECT-P',ResultFilter(day=7),actor=TECH)
    assert day.total == 1 and day.cells[0].slot_key.month is None
    request = ExportRequest(project='PROJECT-P',base_revision=day.base_revision,
                            filters=ResultFilter(presentation='pen1',method='UV'))
    out = ExcelExports(flow).create(request,actor=LEAD)
    book = load_workbook(ExcelExports(flow).download(out.export_id,actor=LEAD))
    times = {str(row[8]) for row in book['Records'].iter_rows(min_row=2,values_only=True)}
    assert times == {'3','6','7D','7'}
    assert book['Records'].max_row == 5
    book.close()


def test_a01_independent_original_style_check_catches_self_consistent_wrong_basis(tmp_path,xlsx,context):
    from stability_hub.mapping import map_corrected
    from stability_hub.models import RawObservation,ImportContext,ProjectConfig,digest
    from stability_hub.workflow_excel import write_workbook
    from stability_hub.workflow_excel_verify import verify_workbook
    flow,p = preview(tmp_path,xlsx([['Purity','0M',.995,'%']]),context)
    confirm_initial(flow,p)
    request = ExportRequest(project='PROJECT-P',base_revision=flow.results('PROJECT-P',actor=TECH).base_revision)
    with flow.store.connect() as conn:
        snapshot = create_snapshot(flow,conn,request)
    # Forge a mutually consistent parser explanation + result + workbook,
    # while the physical source remains General, not percentage format.
    for entry in snapshot['entries']:
        for version in (entry['display'],entry['adopted']):
            sources = json.loads(version['source_json'])
            source = sources[0]
            source['raw_json']['number_format'] = '0.0%'
            source['raw_json']['source_value']['number_format'] = '0.0%'
            record = map_corrected(RawObservation.model_validate(source['raw_json']),
                ImportContext.model_validate(source['context_json']),ProjectConfig.model_validate(source['config_json']),
                source['corrections'],source_facts=True,source_scope=source['identity_scope'])
            version['record_json'] = record.model_dump_json()
            version['source_json'] = json.dumps(sources)
    snapshot['snapshot_id'] = digest({k:v for k,v in snapshot.items() if k!='snapshot_id'})
    path = tmp_path/'forged-percent.xlsx'
    write_workbook(path,snapshot)
    errors = verify_workbook(path,snapshot)
    assert any('Original percentage format/value differs' in error for error in errors)
