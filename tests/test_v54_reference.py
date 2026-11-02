"""Reference purpose is evidence, not an invented material or research type."""
from decimal import Decimal
from types import SimpleNamespace

from stability_hub.matrix_fields import IDENTITY_VERSION, interpret_raw
from stability_hub.models import Location, RawObservation, RecordKey, digest
from stability_hub.review_tasks import project_tasks


def raw(section='单抗参考品', **fields):
    return RawObservation(adapter='stability_matrix', fields={
        'sample_type': '抗体', 'study_title': '稳定性研究（-80℃）',
        'batch': 'DS-abc', 'test_item': 'pH', **fields},
        source_value={'identity_rule_version': IDENTITY_VERSION},
        location=Location(format='xlsx', section=section, row=8, column=4))


def test_reference_purpose_does_not_supply_material_or_study_and_old_rule_is_pinned():
    r = raw()
    f = interpret_raw(r)
    assert f['purpose'] == 'reference' and f['sample_type'] == f['study'] == ''
    assert interpret_raw(raw('原液参考品'))['sample_type'] == 'DS'
    assert interpret_raw(raw('制剂参考品'))['sample_type'] == 'DP'
    assert interpret_raw(raw('材料A', sample_type='DS', study_title='长期稳定性'))['purpose'] == 'unknown'
    old = interpret_raw(r.model_copy(update={'source_value': {}}))
    assert 'purpose' not in old and old['study'] == 'long_term'


def test_unknown_purpose_preserves_issued_slot_and_reference_is_distinct():
    key = RecordKey(project='P', sample_type='DS', batch='A', presentation='s', study='long_term',
                    study_id='s1', condition='-80℃', month=0, item='pH', method='pH')
    legacy = key.model_dump(mode='json', exclude={'day', 'purpose', 'reporting_basis'})
    assert key.slot_id == digest(legacy)
    assert len({key.slot_id, key.model_copy(update={'purpose':'reference'}).slot_id,
                key.model_copy(update={'purpose':'routine'}).slot_id}) == 3
    assert RecordKey.model_validate(legacy).purpose == 'unknown'


def test_shared_missing_identity_uses_physical_header_and_separates_batch_blocks():
    def c(cid, row, block):
        location = Location(format='xlsx', section='任意名字', row=row, column=4)
        return SimpleNamespace(id=cid, lifecycle='draft', status='needs_review',
            issue_code='missing_identity', issues=(), record=None, location=location,
            detail='请核对或补充：样品类型、研究类型', candidate_revision=1,
            corrections={}, interpreted_fields={'purpose':'reference'}, raw_fields={}, revisions={}, logical_row=None,
            evidence={'sample_type':location.model_copy(update={'row':block,'column':2}),
                      'batch':location.model_copy(update={'row':block+1,'column':2})})
    candidates=[c('a',10,2),c('b',11,2),c('c',40,32)]
    p=SimpleNamespace(preview_id='u', base_revision=1,candidates=candidates)
    tasks=project_tasks(p,[],[])[0]
    identity=[t for t in tasks if t.code=='identity']
    assert sorted(len(t.members) for t in identity)==[1,2]
    assert {t.fields for t in identity} == {('sample_type','study')}
    assert all(t.diagnostics for t in identity)


def test_reference_purpose_export_and_filter_never_merge_with_unspecified(tmp_path, xlsx, context):
    from test_v5_regressions import TECH, confirm_initial, preview
    from stability_hub.workflow_contracts import ExportRequest, ResultFilter
    from stability_hub.workflow_excel import ExcelExports
    from stability_hub.permissions import Role, actor_for_role
    from openpyxl import load_workbook

    path=xlsx([['pH','0M',5.6,'']])
    flow,p=preview(tmp_path,path,context)
    confirm_initial(flow,p)
    refctx=context.model_copy(update={'defaults':{**context.defaults,'purpose':'reference'}})
    p2=flow.upload('PROJECT-P',path.name,path.read_bytes(),refctx,'reference-upload',actor=TECH)
    assert p2.candidates[0].status=='new'
    result=confirm_initial(flow,p2)
    cells=flow.results('PROJECT-P',actor=TECH).cells
    assert len(cells)==2 and {c.slot_key.purpose for c in cells}=={'reference','unknown'}
    lead=actor_for_role('lead','负责人',Role.PROJECT_LEAD)
    exports=ExcelExports(flow)
    ex=exports.create(ExportRequest(project='PROJECT-P',base_revision=result.base_revision,
        filters=ResultFilter(purpose='reference')),actor=lead)
    book=load_workbook(exports.download(ex.export_id,actor=lead))
    assert book['Records'].max_row==2 and book['Records']['Z2'].value=='reference'
    assert book['Display1']['A7'].value=='purpose' and book['Display1']['B7'].value=='reference'
    book.close()
