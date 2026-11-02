"""Revision evidence, worksheet atomicity and content-based HTTP duplicate protection."""
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont
from openpyxl.styles import Font

from stability_hub.api import create_app
from stability_hub.ledger_review import LedgerReview
from stability_hub.models import ProjectConfig
from stability_hub.permissions import Role, actor_for_role
from stability_hub.result_versions import ResultVersions
from stability_hub.storage import Store
from stability_hub.workflow import Workflow
from stability_hub.workflow_common import WorkflowError
from stability_hub.workflow_contracts import ConfirmItem, ConfirmRequest, ReceiveRequest, RevisionResolution, SheetConfirmRequest


def test_rich_run_font_overrides_struck_cell_font():
    from stability_hub.xlsx_revisions import cell_revision
    cell = Workbook().active['A1']
    cell.font = Font(strike=True)
    cell.value = CellRichText(TextBlock(InlineFont(strike=True), 'old name\n'),
                             TextBlock(InlineFont(rFont='Arial'), 'new name'))
    revision = cell_revision(cell)
    assert not revision['all_deleted']
    assert revision['active_text'].strip() == 'new name'


def source():
    book = Workbook()
    sheet = book.active
    sheet.title = 'First'
    sheet.append(['Test item', 'Time point', 'Result', 'Unit'])
    sheet.append(['CZE', '0M', 'N/A\nconsistent', ''])
    sheet['C2'] = CellRichText(TextBlock(InlineFont(strike=True), 'N/A\n'), 'consistent')
    sheet.append(['Old CZE', '0M', '12', ''])
    sheet['A3'].font = Font(strike=True)
    sheet.append(['Other', '0M', '删', ''])
    sheet.append(['CZE old name', '1M', 'consistent', ''])
    sheet['A5'] = CellRichText(TextBlock(InlineFont(strike=True), 'CZE old name\n'), 'CZE new name')
    second = book.create_sheet('Second')
    second.append(['Test item', 'Time point', 'Result', 'Unit'])
    second.append(['pH', '0M', '7', ''])
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def setup(tmp_path, context):
    flow = Workflow(Store(tmp_path / 'sheet-review'))
    flow.configure(ProjectConfig(project='PROJECT-P'))
    tech = actor_for_role('tech', '实验员', Role.TECHNICIAN)
    preview = flow.upload('PROJECT-P', 'source.xlsx', source(), context, 'upload', actor=tech)
    return flow, LedgerReview(flow), tech, preview


def test_sheet_confirm_is_atomic_and_technician_adopts_first_facts(tmp_path, context):
    flow, service, actor, preview = setup(tmp_path, context)
    before = preview.candidates[0].raw_fields['result']
    assert before == 'N/A\nconsistent'
    assert preview.candidates[0].suggested_fields['result'] == '\nconsistent'
    assert [c.issue_code for c in preview.candidates[:4]] == ['source_revision', 'deleted_source', 'deleted_source', 'source_revision']
    result = service.receive(preview.preview_id, ReceiveRequest(base_revision=preview.base_revision,
        idempotency_key='receive', candidate_ids=tuple(c.id for c in preview.candidates), defer_confirmation=True), actor=actor)
    assert not result.result_version_ids
    assert flow.results('PROJECT-P', actor=actor).total == 0
    preview = flow.preview(preview.preview_id, actor=actor)
    assert preview.sheet_confirmation_required
    command = SheetConfirmRequest(base_revision=preview.base_revision, idempotency_key='blocked', section='First')
    with pytest.raises(WorkflowError, match='争议格'):
        service.confirm_sheet(preview.preview_id, command, actor=actor)
    ready = preview.candidates[-1]
    with pytest.raises(WorkflowError, match='确认整表'):
        ResultVersions(flow).confirm(preview.preview_id, ConfirmRequest(base_revision=preview.base_revision,
            idempotency_key='no-bypass',items=(ConfirmItem(candidate_id=ready.id,candidate_revision=ready.candidate_revision,
            detection_id=ready.detection_id,identity_confirmed=True,intent='initial',reason='bypass'),)), actor=actor)
    second = service.confirm_sheet(preview.preview_id, SheetConfirmRequest(base_revision=preview.base_revision,
        idempotency_key='second', section='Second'), actor=actor)
    assert len(second.result_version_ids) == 1 and not second.pending_confirmation_ids
    resolved = service.resolve_revisions(preview.preview_id, RevisionResolution(base_revision=second.base_revision,
        idempotency_key='resolve', candidate_ids=tuple(c.id for c in preview.candidates[:4])), actor=actor)
    command = SheetConfirmRequest(base_revision=resolved.base_revision,idempotency_key='first',section='First')
    first = service.confirm_sheet(preview.preview_id, command, actor=actor)
    assert service.confirm_sheet(preview.preview_id, command, actor=actor) == first
    assert len(first.result_version_ids) == 2 and not first.pending_confirmation_ids
    assert flow.results('PROJECT-P', actor=actor).total == 3
    updated = flow.preview(preview.preview_id, actor=actor)
    assert updated.candidates[0].raw_fields['result'] == before
    assert updated.candidates[0].record.result.raw_text.strip() == 'consistent'
    assert [c.lifecycle for c in updated.candidates[1:3]] == ['withdrawn', 'withdrawn']


def test_human_alias_reused_but_other_subresults_not_merged(tmp_path, context):
    flow, service, actor, preview = setup(tmp_path, context)
    cid = preview.candidates[3].id
    with pytest.raises(WorkflowError,match='独立项目规则'):
        service.resolve_revisions(preview.preview_id,RevisionResolution(base_revision=preview.base_revision,
            idempotency_key='combined-rule',candidate_ids=(cid,),remember_alias=True),actor=actor)
    result = service.resolve_revisions(preview.preview_id, RevisionResolution(base_revision=preview.base_revision,
        idempotency_key='name',candidate_ids=(cid,),label='CZE confirmed',method='CZE cpB'), actor=actor)
    next_upload = flow.upload('PROJECT-P','changed.xlsx',source(),context,'next',actor=actor)
    # B03/A10: file-local name decisions never approve the next file's revisions.
    assert next_upload.candidates[3].issue_code == 'source_revision'
    assert flow.configuration('PROJECT-P',actor=actor)['config']['items'] == []
    assert next_upload.candidates[0].issue_code == 'source_revision', 'Remembering names must not approve changed result text'
    with pytest.raises(WorkflowError, match='已核对'):
        service.resolve_revisions(preview.preview_id, RevisionResolution(base_revision=next_upload.base_revision,
            idempotency_key='do-not-overwrite',candidate_ids=(cid,)), actor=actor)
    assert result.base_revision < next_upload.base_revision
    with pytest.raises(WorkflowError, match='不同子结果'):
        service.resolve_revisions(next_upload.preview_id, RevisionResolution(base_revision=next_upload.base_revision,
            idempotency_key='do-not-merge',candidate_ids=(next_upload.candidates[0].id,next_upload.candidates[3].id),
            label='Wrong merge',method='CZE'),actor=actor)


def test_http_same_hash_is_read_only_and_changed_bytes_create_preview(tmp_path, context):
    store = Store(tmp_path / 'http')
    actor = actor_for_role('tech','实验员',Role.TECHNICIAN)
    client = TestClient(create_app(store,[ProjectConfig(project='PROJECT-P')],actor))
    def upload(data, key):
        response = client.post('/api/v1/projects/PROJECT-P/previews',headers={'Idempotency-Key':key},
            files={'file':('source.xlsx',data)},data={'context':context.model_dump_json()})
        assert response.status_code == 200, response.text
        return response.json()
    data = source()
    first = upload(data, 'one')
    with store.connect() as conn:
        before = tuple(conn.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in ['workflow_uploads','workflow_candidates','workflow_events','workflow_versions'])
    again = upload(data, 'two')
    assert again['preview_id'] == first['preview_id'] and again['duplicate_source']
    with store.connect() as conn:
        assert before == tuple(conn.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in ['workflow_uploads','workflow_candidates','workflow_events','workflow_versions'])
    from openpyxl import load_workbook
    book = load_workbook(BytesIO(source()),rich_text=True)
    book['Second']['C2'] = '8'
    output = BytesIO()
    book.save(output)
    changed = upload(output.getvalue(), 'three')
    assert changed['preview_id'] != first['preview_id'] and not changed['duplicate_source']


def test_old_draft_recovers_revision_evidence_without_rewriting_source(tmp_path, context):
    import json
    flow, service, actor, preview = setup(tmp_path, context)
    with flow.store.connect() as conn:
        originals = {}
        for row in conn.execute('SELECT id,raw_json FROM workflow_candidates').fetchall():
            raw = json.loads(row['raw_json'])
            raw.pop('revisions')
            raw.pop('evidence')  # old long-table uploads had no per-field coordinates
            originals[row['id']] = json.dumps(raw)
            conn.execute('UPDATE workflow_candidates SET raw_json=?,acknowledge_warning=1 WHERE id=?', (originals[row['id']],row['id']))
    updated = flow.preview(preview.preview_id, actor=actor)
    assert [c.issue_code for c in updated.candidates[:4]] == ['source_revision','deleted_source','deleted_source','source_revision']
    with flow.store.connect() as conn:
        assert dict(conn.execute('SELECT id,raw_json FROM workflow_candidates')) == originals
        assert conn.execute('SELECT count(*) FROM workflow_versions').fetchone()[0] == 0
    service.resolve_revisions(preview.preview_id, RevisionResolution(base_revision=updated.base_revision,
        idempotency_key='legacy-style-review',candidate_ids=(updated.candidates[0].id,)), actor=actor)
    reviewed = flow.preview(preview.preview_id, actor=actor)
    assert reviewed.candidates[0].status == 'new'
    assert reviewed.candidates[0].record.result.raw_text.strip() == 'consistent'
    assert reviewed.candidates[3].issue_code == 'source_revision'


def test_http_withdraw_removes_conflict_and_sheet_adopts_only_retained_facts(tmp_path, xlsx, context):
    store = Store(tmp_path / 'withdraw-http')
    actor = actor_for_role('tech','实验员',Role.TECHNICIAN)
    client = TestClient(create_app(store,[ProjectConfig(project='PROJECT-P')],actor))
    source = xlsx([['Assay','0M','1','%'],['Assay','0M','2','%'],['Other','0M','3','%']])
    preview = client.post('/api/v1/projects/PROJECT-P/previews',headers={'Idempotency-Key':'upload'},
        files={'file':(source.name,source.read_bytes())},data={'context':context.model_dump_json()}).json()
    uid = preview['preview_id']
    received = client.post(f'/api/v1/previews/{uid}/receive',json={'base_revision':preview['base_revision'],
        'idempotency_key':'receive','defer_confirmation':True,'candidate_ids':[c['id'] for c in preview['candidates']]}).json()
    candidate = preview['candidates'][0]
    # The former frontend payload was rejected, not an invisible successful withdrawal.
    old = client.post(f"/api/v1/candidates/{candidate['id']}/withdraw",json={'base_revision':received['base_revision'],
        'candidate_revision':1,'idempotency_key':'old','reason':'exclude'})
    assert old.status_code == 422
    response = client.post(f"/api/v1/candidates/{candidate['id']}/withdraw",json={'base_revision':received['base_revision'],
        'idempotency_key':'exclude','reason':'not a result'})
    assert response.status_code == 200, response.text
    updated = client.get(f'/api/v1/previews/{uid}').json()
    assert updated['candidates'][0]['lifecycle'] == 'withdrawn'
    assert updated['candidates'][1]['status'] == 'new'
    assert updated['counts']['needs_review'] == 0
    confirmed = client.post(f'/api/v1/previews/{uid}/confirm-sheet',json={'base_revision':updated['base_revision'],
        'idempotency_key':'sheet','section':'Results'})
    assert confirmed.status_code == 200, confirmed.text
    assert len(confirmed.json()['result_version_ids']) == 2
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) FROM workflow_adoptions').fetchone()[0] == 2
        assert conn.execute('SELECT count(*) FROM workflow_versions WHERE candidate_id=?',(candidate['id'],)).fetchone()[0] == 0
