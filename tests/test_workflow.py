import json
import sqlite3
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from stability_hub.api import create_app, get_actor
from stability_hub.permissions import Role, actor_for_role
from stability_hub.result_versions import ResultVersions
from stability_hub.storage import Store
from stability_hub.workflow import Workflow
from stability_hub.workflow_common import WorkflowError
from stability_hub.workflow_contracts import (
    CandidateEdit, ConfirmItem, ConfirmRequest, ExportRequest, ReviewRequest, WithdrawRequest,
)
from stability_hub.workflow_excel import ExcelExports
from stability_hub.workflow_excel_verify import verify_workbook

TECH = actor_for_role('tech', '实验员', Role.TECHNICIAN)
SENIOR = actor_for_role('senior', '高级实验员', Role.SENIOR_TECHNICIAN)
LEAD = actor_for_role('lead', '负责人', Role.PROJECT_LEAD)


@pytest.fixture
def flow(tmp_path, config):
    workflow = Workflow(Store(tmp_path / 'store'))
    workflow.configure(config)
    return workflow


def upload(flow, path, context, key=None):
    # These V4 version/adoption regressions exercise historical compatibility.
    # New Excel HTTP boundaries and worksheet atomicity live in test_v5_commit.py.
    return flow.upload('PROJECT-P', path.name, path.read_bytes(), context, key or uuid4().hex, actor=TECH,_legacy_compatibility=True)


def command(preview, intent='initial', indices=None):
    candidates = preview.candidates if indices is None else [preview.candidates[i] for i in indices]
    return ConfirmRequest(base_revision=preview.base_revision, idempotency_key=uuid4().hex, items=tuple(
        ConfirmItem(candidate_id=c.id, candidate_revision=c.candidate_revision,
                    detection_id=c.adopted_detection_id if intent == 'correction' else c.existing_detection_id if intent == 'repeat_source' else c.detection_id,
                    identity_confirmed=True, intent=intent, reason='人工核对') for c in candidates))


def confirm(flow, preview, intent='initial'):
    return ResultVersions(flow).confirm(preview.preview_id, command(preview, intent), actor=TECH)


def review(flow, version, action='adopt', actor=SENIOR):
    return ResultVersions(flow).review(version, ReviewRequest(
        base_revision=flow.results('PROJECT-P', actor=TECH).base_revision,
        idempotency_key=uuid4().hex, action=action, reason='复核通过'), actor=actor)


def test_first_and_duplicate_fact_adoption_are_separate(flow, xlsx, context):
    p = upload(flow, xlsx([['Purity', '12M', '98', '%']]), context)
    assert flow.results('PROJECT-P', actor=TECH).total == 0
    first = confirm(flow, p)
    original = first.result_version_ids[0]
    p2 = upload(flow, xlsx([['Purity', '12M', '99', '%']]), context)
    with pytest.raises(WorkflowError, match='重复槽位'):
        confirm(flow, p2)
    second = confirm(flow, p2, 'retest')
    cell = flow.results('PROJECT-P', actor=TECH).cells[0]
    assert cell.display.pending_confirmation and cell.adopted.result_version_id == original
    with pytest.raises(PermissionError):
        review(flow, second.result_version_ids[0], actor=TECH)
    review(flow, second.result_version_ids[0], 'confirm_fact')
    cell = flow.results('PROJECT-P', actor=TECH).cells[0]
    assert not cell.display.pending_confirmation and cell.adopted.result_version_id == original
    review(flow, second.result_version_ids[0])
    assert flow.results('PROJECT-P', actor=TECH).cells[0].adopted == cell.display


def test_same_value_still_requires_review_and_retry_is_idempotent(flow, xlsx, context):
    path = xlsx([['Purity', '0M', '98', '%']])
    p = upload(flow, path, context, 'upload-1')
    assert upload(flow, path, context, 'upload-1').upload_sequence == p.upload_sequence
    req = command(p)
    service = ResultVersions(flow)
    result = service.confirm(p.preview_id, req, actor=TECH)
    assert service.confirm(p.preview_id, req, actor=TECH) == result
    newer = upload(flow, path, context)
    assert newer.candidates[0].status == 'same_value'
    pending = confirm(flow, newer, 'repeat_source')
    assert pending.pending_confirmation_ids
    with pytest.raises(WorkflowError):
        upload(flow, xlsx([['Purity', '0M', '97', '%']]), context, 'upload-1')


def test_ambiguity_requires_manual_resolution(flow, xlsx, context):
    p = upload(flow, xlsx([['Purity', '12M', '98', '%'], ['Purity', '12M', '99', '%']]), context)
    assert all(c.issue_code == 'ambiguous_file_slot' for c in p.candidates)
    with pytest.raises(WorkflowError):
        confirm(flow, p)
    ResultVersions(flow).withdraw_candidate(p.candidates[1].id, WithdrawRequest(
        base_revision=p.base_revision, idempotency_key='withdraw', reason='重复行'), actor=TECH)
    fresh = flow.preview(p.preview_id, actor=TECH)
    ResultVersions(flow).confirm(p.preview_id, command(fresh, indices=[0]), actor=TECH)
    assert flow.results('PROJECT-P', actor=TECH).cells[0].display.text.startswith('98')


def test_correction_reclassifies_identity_and_cannot_edit_confirmed(flow, xlsx, context):
    confirm(flow, upload(flow, xlsx([['Purity', '12M', '98', '%']]), context))
    p = upload(flow, xlsx([['Purity', '12M', '99', '%']]), context)
    edit = CandidateEdit(candidate_revision=1, base_revision=p.base_revision, idempotency_key='edit',
                         reason='批号识别错误', fields={'batch': 'B002'})
    flow.edit(p.candidates[0].id, edit, actor=TECH)
    p = flow.preview(p.preview_id, actor=TECH)
    assert p.candidates[0].status == 'new' and p.candidates[0].record.key.batch == 'B002'
    confirm(flow, p)
    assert flow.results('PROJECT-P', actor=TECH).total == 2
    with pytest.raises(WorkflowError):
        flow.edit(p.candidates[0].id, edit.model_copy(update={
            'base_revision': flow.results('PROJECT-P', actor=TECH).base_revision, 'idempotency_key': 'edit-again'}), actor=TECH)


def test_stale_and_multi_candidate_failure_roll_back(flow, xlsx, context):
    p = upload(flow, xlsx([['Purity', '0M', '98', '%'], ['Purity', '1M', '99', '%']]), context)
    req = command(p)
    bad = req.model_copy(update={'items': (req.items[0], req.items[1].model_copy(update={'detection_id': 'wrong'}))})
    with pytest.raises(WorkflowError):
        ResultVersions(flow).confirm(p.preview_id, bad, actor=TECH)
    assert flow.results('PROJECT-P', actor=TECH).total == 0
    upload(flow, xlsx([['Purity', '2M', '97', '%']]), context)
    with pytest.raises(WorkflowError):
        ResultVersions(flow).confirm(p.preview_id, req, actor=TECH)


def test_upload_order_not_test_date_or_confirmation_time(flow, xlsx, context):
    headers = ['Test item', 'Time point', 'Result', 'Unit', '检测完成日期', '报告编号']
    older = upload(flow, xlsx([['Purity', '0M', '98', '%', '2099-01-01', 'R1']], headers=headers), context)
    newer = upload(flow, xlsx([['Purity', '0M', '99', '%', '2000-01-01', 'R2']], headers=headers), context)
    confirm(flow, newer)
    older = flow.preview(older.preview_id, actor=TECH)
    assert older.candidates[0].source_number == 'R1'
    confirm(flow, older, 'retest')
    assert flow.results('PROJECT-P', actor=TECH).cells[0].display.text.startswith('99')


def test_formal_correction_and_immutable_history(flow, xlsx, context):
    first = confirm(flow, upload(flow, xlsx([['Purity', '0M', '98', '%']]), context))
    p = upload(flow, xlsx([['Purity', '0M', '99', '%']]), context)
    correction = confirm(flow, p, 'correction')
    with pytest.raises(WorkflowError):
        review(flow, correction.result_version_ids[0], 'confirm_fact')
    review(flow, correction.result_version_ids[0])
    with flow.store.connect() as conn:
        assert conn.execute('SELECT count(*) FROM workflow_versions').fetchone()[0] == 2
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute('UPDATE workflow_versions SET reason=? WHERE id=?', ('tamper', first.result_version_ids[0]))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute('DELETE FROM workflow_events')


def test_export_pending_complete_matrix_and_tamper(flow, xlsx, context):
    confirm(flow, upload(flow, xlsx([['Purity', f'{m}M', '98', '%'] for m in [0, 1, 3, 6, 9, 12, 18]]), context))
    confirm(flow, upload(flow, xlsx([['Purity', '12M', '99', '%']]), context), 'retest')
    request = ExportRequest(project='PROJECT-P', base_revision=flow.results('PROJECT-P', actor=TECH).base_revision)
    exporter = ExcelExports(flow)
    with pytest.raises(PermissionError):
        exporter.create(request, actor=TECH)
    out = exporter.create(request, actor=LEAD)
    path = exporter.download(out.export_id, actor=LEAD)
    snapshot = json.loads((path.parent / 'snapshot.json').read_text(encoding='utf-8'))
    assert not verify_workbook(path, snapshot)
    book = load_workbook(path)
    assert book['Display1'].max_column == 12
    pending = [c for row in book['Display1'] for c in row if '（待确认）' in str(c.value)]
    assert len(pending) == 1 and pending[0].font.color.rgb == 'FFDC2626'
    pending[0].value = '999%'
    book.save(path)
    book.close()
    assert verify_workbook(path, snapshot)
    with pytest.raises(WorkflowError):
        exporter.download(out.export_id, actor=LEAD)


def test_http_upload_filter_confirm_download_and_roles(tmp_path, config, context, xlsx):
    app = create_app(Store(tmp_path / 'http'), [config], TECH, api_token='test-token')
    with TestClient(app) as client:
        assert client.get('/api/v1/me').status_code == 401
        client.headers['Authorization'] = 'Bearer test-token'
        assert client.get('/api/v1/me', headers={'X-Role': 'admin'}).json()['role'] == 'technician'
        assert client.get('/api/v1/projects/PROJECT-P/result-cells').status_code == 200
        file = xlsx([['Purity', '0M', '98', '%']])
        response = client.post('/api/v1/projects/PROJECT-P/previews', headers={'Idempotency-Key': 'u1'},
                               files={'file': (file.name, file.read_bytes())}, data={'context': context.model_dump_json()})
        assert response.status_code == 200, response.text
        from stability_hub.workflow_contracts import UploadPreview
        p = UploadPreview.model_validate(response.json())
        response = client.post(f'/api/v1/previews/{p.preview_id}/confirm', json=command(p).model_dump(mode='json'))
        assert response.status_code == 422 and response.json()['code'] == 'sheet_confirmation_required'
        received = client.post(f'/api/v1/previews/{p.preview_id}/receive',json={'base_revision':p.base_revision,
            'idempotency_key':'http-receive','candidate_ids':[c.id for c in p.candidates]})
        response = client.post(f'/api/v1/previews/{p.preview_id}/confirm-sheet',json={'base_revision':received.json()['base_revision'],
            'idempotency_key':'http-sheet','section':'Results'})
        assert response.status_code == 200, response.text
        assert client.get('/api/v1/projects/PROJECT-P/result-cells?batch=NOPE').json()['total'] == 0
        assert client.get('/api/v1/projects/PROJECT-P/result-cells?month=-1').status_code == 422
        app.dependency_overrides[get_actor] = lambda: LEAD
        export = client.post('/api/v1/exports', json={'project': 'PROJECT-P', 'base_revision': response.json()['base_revision']})
        assert export.status_code == 200, export.text
        assert client.get(export.json()['download_url']).content.startswith(b'PK')
        assert client.post(f'/api/v1/previews/{p.preview_id}/confirm', json=command(p).model_dump(mode='json')).status_code == 403
        assert client.post('/api/v1/exports', headers={'Origin': 'https://untrusted.invalid'}, json={}).status_code == 403


@pytest.mark.parametrize('orientation', ['items', 'batches'])
def test_release_docx_export_both_orientations(flow, coa, context, orientation):
    from stability_hub.models import ImportContext
    release = ImportContext(role='release_coa', defaults={**context.defaults, 'study': 'release', 'condition': 'release'})
    confirm(flow, upload(flow, coa(), release))
    exporter = ExcelExports(flow)
    out = exporter.create(ExportRequest(project='PROJECT-P', base_revision=flow.results('PROJECT-P', actor=TECH).base_revision,
                                       orientation=orientation), actor=LEAD)
    book = load_workbook(exporter.download(out.export_id, actor=LEAD))
    assert book['Display1'].cell(7, 2).value == 'release_' + orientation
    book.close()


def test_legacy_preserved_and_automatic_import_disabled(tmp_path, config, context, xlsx):
    from stability_hub.service import Hub
    hub = Hub(tmp_path / 'legacy')
    path = xlsx([['Purity', '0M', '98', '%']])
    hub.import_file(path, config, context, legacy_compatibility=True)
    before = hub.store.snapshot(config.project)
    flow = Workflow(hub.store)
    flow.configure(config)
    assert hub.store.snapshot(config.project) == before
    assert flow.results(config.project, actor=TECH).cells[0].display_status == 'legacy'
    with pytest.raises(ValueError, match='V4'):
        hub.import_file(path, config, context, legacy_compatibility=True)
    with pytest.raises(ValueError):
        hub.export(config.project, tmp_path / 'export')
    ExcelExports(flow).create(ExportRequest(project=config.project, base_revision=0), actor=LEAD)


def test_partial_update_blank_keeps_history_and_filter_exact(flow, xlsx, context):
    from stability_hub.workflow_contracts import ResultFilter
    confirm(flow, upload(flow, xlsx([['Purity', '12M', '98', '%']]), context))
    p = upload(flow, xlsx([['Purity', '12M', '', '%'], ['Protein content', '12M', '2.1', 'mg/ml']]), context)
    assert p.candidates[0].status == 'empty'
    ResultVersions(flow).confirm(p.preview_id, command(p, indices=[1]), actor=TECH)
    cells = flow.results('PROJECT-P', actor=TECH)
    assert cells.total == 2 and not any(c.display.pending_confirmation for c in cells.cells)
    req = ExportRequest(project='PROJECT-P', base_revision=cells.base_revision, filters=ResultFilter(item='protein'))
    exporter = ExcelExports(flow)
    out = exporter.create(req, actor=LEAD)
    book = load_workbook(exporter.download(out.export_id, actor=LEAD))
    assert book['Records'].max_row == 2 and book['Records'].cell(2, 10).value == 'protein'
    book.close()


def test_correction_targets_adopted_not_pending_and_old_base_rejected(flow, xlsx, context):
    confirm(flow, upload(flow, xlsx([['Purity', '0M', '98', '%']]), context))
    retest = confirm(flow, upload(flow, xlsx([['Purity', '0M', '99', '%']]), context), 'retest')
    p = upload(flow, xlsx([['Purity', '0M', '97', '%']]), context)
    assert p.candidates[0].adopted_detection_id != p.candidates[0].existing_detection_id
    correction = confirm(flow, p, 'correction')
    review(flow, retest.result_version_ids[0])
    with pytest.raises(WorkflowError, match='更正依据'):
        review(flow, correction.result_version_ids[0])


def test_rejected_pending_falls_back_and_constraints_block_wrong_adoption(flow, xlsx, context):
    first = confirm(flow, upload(flow, xlsx([['Purity', '0M', '98', '%'], ['Purity', '1M', '97', '%']]), context))
    p = upload(flow, xlsx([['Purity', '0M', '99', '%']]), context)
    pending = confirm(flow, p, 'retest')
    with flow.store.connect() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute('UPDATE workflow_adoptions SET version_id=?', (pending.result_version_ids[0],))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute('UPDATE workflow_adoptions SET version_id=?', (first.result_version_ids[0],))
    review(flow, pending.result_version_ids[0], 'reject')
    assert not any(c.display.pending_confirmation for c in flow.results('PROJECT-P', actor=TECH).cells)


def test_source_mutation_blocks_verified_export(flow, xlsx, context):
    confirm(flow, upload(flow, xlsx([['Purity', '0M', '98', '%']]), context))
    source = next((flow.store.home / 'sources').glob('*.xlsx'))
    book = load_workbook(source)
    book.active.cell(2, 3).value = '99'
    book.save(source)
    book.close()
    with pytest.raises(WorkflowError, match='反读失败'):
        ExcelExports(flow).create(ExportRequest(project='PROJECT-P', base_revision=flow.results('PROJECT-P', actor=TECH).base_revision), actor=LEAD)
    with flow.store.connect() as conn:
        assert conn.execute('SELECT count(*) FROM workflow_exports').fetchone()[0] == 0


def test_workflow_schema_is_current():
    from pathlib import Path
    from stability_hub.workflow_contracts import WorkflowSchema
    schema = json.loads((Path(__file__).parents[1] / 'contracts/v1/workflow-schema.json').read_text(encoding='utf-8'))
    assert schema == WorkflowSchema.model_json_schema()


def test_workflow_examples_and_openapi_match(tmp_path):
    from pathlib import Path
    from stability_hub.models import ImportContext
    from stability_hub.permissions import Actor
    from stability_hub.workflow_contracts import ExportResult, MutationResult, ResultPage, UploadPreview
    root = Path(__file__).parents[1] / 'contracts/v1'
    models = {'actor': Actor, 'context': ImportContext, 'preview-0': UploadPreview, 'preview-1': UploadPreview,
              'confirm-0': ConfirmRequest, 'confirm-1': ConfirmRequest, 'confirmation-0': MutationResult,
              'confirmation-1': MutationResult, 'review': ReviewRequest, 'result-page': ResultPage,
              'export-request': ExportRequest, 'export-result': ExportResult}
    for name, model in models.items():
        model.model_validate_json((root / 'examples/workflow' / (name + '.json')).read_text(encoding='utf-8'))
    app = create_app(Store(tmp_path / 'schema'), [], TECH)
    assert json.loads((root / 'openapi.json').read_text(encoding='utf-8')) == app.openapi()
