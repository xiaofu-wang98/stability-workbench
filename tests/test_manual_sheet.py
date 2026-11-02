"""Working-copy edits must remain source-bound, atomic, explicit and reversible."""
import hashlib

import pytest
from fastapi.testclient import TestClient
from openpyxl.utils import get_column_letter

from stability_hub.api import create_app
from stability_hub.ledger_review import LedgerReview
from stability_hub.manual_sheet import preview as plan, apply, undo, state
from stability_hub.permissions import Role, actor_for_role
from stability_hub.v5_contracts import ManualSheetScope, ManualResult, ManualSheetApply, ManualSheetUndo
from stability_hub.workflow_common import WorkflowError
from stability_hub.workflow_contracts import CandidateEdit
from test_v5_regressions import TECH, preview
from test_v51_interpretation import receive


def prepared(tmp_path, xlsx, context):
    path = xlsx([['Assay', '0M', 99, '%'], ['Assay', '3M', 98, '%']])
    flow, p = preview(tmp_path, path, context)
    p = receive(flow, p)
    c = p.candidates[0]
    ref = get_column_letter(c.location.column)+str(c.location.row)
    fields = {**context.defaults, **c.interpreted_fields, 'result': '97', 'unit': '%'}
    from stability_hub.workflow import EDITABLE
    fields = {k: str(v) for k, v in fields.items() if k in EDITABLE}
    scope = ManualSheetScope(section=c.location.section, cells={ref: '97'}, merges=('A1:B1',),
        candidate_ids=(c.id,), results=(ManualResult(sources=(ref,), fields=fields),))
    return flow, p, scope


def save(flow, p, scope, **kw):
    with flow.store.connect() as conn:
        info = plan(flow, conn, p.preview_id, scope, TECH)
    command = ManualSheetApply(base_revision=info['base_revision'], idempotency_key='manual-save',
        reason='核对原件后明确当前值与归属', scope=scope, fingerprint=info['fingerprint'], reviewed=True, **kw)
    return apply(flow, p.preview_id, command, TECH), info, command


def test_manual_edit_save_no_formal_write_original_preserved_and_undo(tmp_path, xlsx, context):
    flow, p, scope = prepared(tmp_path, xlsx, context)
    with flow.store.connect() as conn:
        source = conn.execute('SELECT stored_path FROM documents WHERE sha256=?', (p.source_hash,)).fetchone()[0]
    from pathlib import Path
    before_hash = hashlib.sha256(Path(source).read_bytes()).hexdigest()
    result, info, command = save(flow, p, scope)
    assert result == apply(flow, p.preview_id, command, TECH)
    after = flow.preview(p.preview_id, actor=TECH)
    child = next(c for c in after.candidates if c.id in result['candidate_ids'])
    assert child.raw_fields['result'] == '99' and child.corrections['result'] == '97'
    from stability_hub.worksheet_regions import coverage
    with flow.store.connect() as conn:
        regions = coverage(conn, flow._upload(conn, p.preview_id))[0]['regions']
    assert any(scope.results[0].sources[0] in region['coordinates'] and region['role']=='result' for region in regions)
    assert next(c for c in after.candidates if c.id == scope.candidate_ids[0]).lifecycle == 'withdrawn'
    assert after.candidates[1].lifecycle == 'draft'
    with flow.store.connect() as conn:
        assert conn.execute('SELECT count(*) FROM workflow_versions').fetchone()[0] == 0
        edited = state(flow, conn, p.preview_id, scope.section, TECH)
    assert edited['cells'][scope.results[0].sources[0]] == '97'
    assert hashlib.sha256(Path(source).read_bytes()).hexdigest() == before_hash
    undo(flow, p.preview_id, ManualSheetUndo(base_revision=result['base_revision'], idempotency_key='undo',
        reason='撤销人工重建', edit_id=result['edit_id']), TECH)
    restored = flow.preview(p.preview_id, actor=TECH)
    assert next(c for c in restored.candidates if c.id == scope.candidate_ids[0]).lifecycle == 'draft'
    assert next(c for c in restored.candidates if c.id == child.id).lifecycle == 'withdrawn'


def test_manual_split_same_source_requires_each_value_and_prevents_duplicate(tmp_path, xlsx, context):
    flow, p, scope = prepared(tmp_path, xlsx, context)
    extra = scope.results[0].model_copy(update={'fields': {**scope.results[0].fields, 'time_point': '6M', 'result': '96'}})
    result, info, _ = save(flow, p, scope.model_copy(update={'results': (*scope.results, extra)}))
    assert len(result['candidate_ids']) == 2 and len(info['parents']) == 1
    assert [r['fields']['result'] for r in info['projections']] == ['97', '96']


@pytest.mark.parametrize('change', [
    {'candidate_ids': ()}, {'merges': ('A1:B2', 'B2:C3')},
    {'results': (ManualResult(sources=('Z999',), fields={'test_item': 'A', 'time_point': '0M', 'result': '1'}),)},
])
def test_manual_invalid_scope_writes_nothing(tmp_path, xlsx, context, change):
    flow, p, scope = prepared(tmp_path, xlsx, context)
    with flow.store.connect() as conn:
        before = conn.execute('SELECT count(*) FROM workflow_events').fetchone()[0]
        with pytest.raises(WorkflowError):
            plan(flow, conn, p.preview_id, scope.model_copy(update=change), TECH)
        assert conn.execute('SELECT count(*) FROM workflow_events').fetchone()[0] == before


def test_manual_stale_preview_and_unreviewed_rejected(tmp_path, xlsx, context):
    flow, p, scope = prepared(tmp_path, xlsx, context)
    with flow.store.connect() as conn:
        info = plan(flow, conn, p.preview_id, scope, TECH)
    cmd = ManualSheetApply(base_revision=info['base_revision'], idempotency_key='unreviewed', reason='依据',
        scope=scope, fingerprint=info['fingerprint'], reviewed=False)
    with pytest.raises(WorkflowError):
        apply(flow, p.preview_id, cmd, TECH)
    flow.edit(p.candidates[1].id, CandidateEdit(base_revision=p.base_revision, idempotency_key='later',
        reason='其他修改', candidate_revision=p.candidates[1].candidate_revision, fields={'result':'95'}), actor=TECH)
    with pytest.raises(WorkflowError):
        apply(flow, p.preview_id, cmd.model_copy(update={'reviewed':True}), TECH)


def test_manual_read_only_role_and_other_uploader_permissions(tmp_path, xlsx, context):
    flow, p, scope = prepared(tmp_path, xlsx, context)
    lead = actor_for_role('lead', 'Read', Role.PROJECT_LEAD)
    other = actor_for_role('other', 'Other technician', Role.TECHNICIAN)
    with flow.store.connect() as conn:
        with pytest.raises(PermissionError):
            plan(flow, conn, p.preview_id, scope, lead)
        with pytest.raises(PermissionError):
            plan(flow, conn, p.preview_id, scope, other)


def test_manual_http_preview_contract(tmp_path, xlsx, context):
    flow, p, scope = prepared(tmp_path, xlsx, context)
    with TestClient(create_app(flow.store, [], TECH)) as client:
        response = client.post(f'/api/v1/previews/{p.preview_id}/v5/manual-sheet/preview', json=scope.model_dump(mode='json'))
        assert response.status_code == 200, response.text
        assert response.json()['cell_changes'][0]['after'] == '97'


def test_noop_refresh_skips_review_computation_and_detects_tampering(tmp_path, xlsx, context, monkeypatch):
    flow, p, scope = prepared(tmp_path, xlsx, context)
    service = LedgerReview(flow)
    first = service.matrix(p.preview_id, actor=TECH)
    import stability_hub.review_tasks as review
    monkeypatch.setattr(review, 'current_review', lambda *a, **kw: pytest.fail('Unchanged refresh recomputed review'))
    unchanged = service.matrix(p.preview_id, actor=TECH, after_revision=first['preview']['base_revision'])
    assert unchanged['not_modified'] and len(str(unchanged)) < 200
    full = service.matrix(p.preview_id, actor=TECH)
    full['preview']['candidates'].clear()
    assert service.matrix(p.preview_id, actor=TECH)['preview']['candidates']
    with flow.store.connect() as conn:
        path = conn.execute('SELECT stored_path FROM documents WHERE sha256=?', (p.source_hash,)).fetchone()[0]
    from pathlib import Path
    Path(path).write_bytes(b'tampered source')
    with pytest.raises(WorkflowError, match='原件'):
        service.matrix(p.preview_id, actor=TECH, after_revision=first['preview']['base_revision'])


def test_manual_results_can_pass_normal_worksheet_commit(tmp_path, xlsx, context):
    from stability_hub.worksheet_commit import preflight, commit
    from stability_hub.v5_contracts import SheetCommit
    flow, p, scope = prepared(tmp_path, xlsx, context)
    result, _, _ = save(flow, p, scope)
    with flow.store.connect() as conn:
        flight, _ = preflight(flow, conn, flow._upload(conn, p.preview_id), scope.section, TECH)
    assert flight.can_commit, flight.model_dump()
    committed = commit(flow, p.preview_id, SheetCommit(base_revision=result['base_revision'],
        idempotency_key='commit-manual', reason='人工核对后正常入库', section=scope.section, fingerprint=flight.fingerprint), actor=TECH)
    assert len(committed.result_version_ids) == 2
    assert len(flow.results(p.project, actor=TECH).cells) == 2
    from stability_hub.workflow_excel import ExcelExports
    from stability_hub.workflow_contracts import ExportRequest
    lead = actor_for_role('exporter', 'Export', Role.PROJECT_LEAD)
    exported = ExcelExports(flow).create(ExportRequest(project=p.project, base_revision=committed.base_revision, include_png=True), actor=lead)
    assert exported.state == 'verified'


def test_manual_delete_is_explicit_exclusion_and_can_undo(tmp_path, xlsx, context):
    flow, p, scope = prepared(tmp_path, xlsx, context)
    scope = scope.model_copy(update={'results': (), 'cells': {scope.results[0].sources[0]: ''}})
    result, info, _ = save(flow, p, scope)
    assert info['excluded_count'] == 1 and not result['candidate_ids']
    assert next(c for c in flow.preview(p.preview_id, actor=TECH).candidates if c.id == scope.candidate_ids[0]).lifecycle == 'withdrawn'
    undo(flow, p.preview_id, ManualSheetUndo(base_revision=result['base_revision'], idempotency_key='undo-delete',
        reason='恢复来源', edit_id=result['edit_id']), TECH)
    assert next(c for c in flow.preview(p.preview_id, actor=TECH).candidates if c.id == scope.candidate_ids[0]).lifecycle == 'draft'


def test_manual_transaction_failure_rolls_back_everything(tmp_path, xlsx, context, monkeypatch):
    flow, p, scope = prepared(tmp_path, xlsx, context)
    import stability_hub.manual_sheet as manual
    original_event = manual.event
    def injected(conn, project, subject, action, *args):
        if action == 'manual_projection':
            raise RuntimeError('injected failure')
        return original_event(conn, project, subject, action, *args)
    monkeypatch.setattr(manual, 'event', injected)
    with pytest.raises(RuntimeError, match='injected'):
        save(flow, p, scope)
    after = flow.preview(p.preview_id, actor=TECH)
    assert after.base_revision == p.base_revision and len(after.candidates) == 2
    assert all(c.lifecycle == 'draft' and not c.corrections for c in after.candidates)


def test_manual_unresolved_method_is_not_implicitly_approved(tmp_path, context):
    from test_v55_intake_flow import setup
    flow, p = setup(tmp_path, context, conflict=True)
    c = p.candidates[0]
    ref = get_column_letter(c.location.column)+str(c.location.row)
    scope = ManualSheetScope(section=c.location.section, candidate_ids=(c.id,), results=(ManualResult(sources=(ref,),
        fields={**c.interpreted_fields, 'test_item': 'Explicit minor species', 'time_point': '0M', 'result': '2.7', 'unit': '%'}),))
    from stability_hub.workflow import EDITABLE
    scope = scope.model_copy(update={'results': tuple(r.model_copy(update={'fields': {k:v for k,v in r.fields.items() if k in EDITABLE}}) for r in scope.results)})
    with flow.store.connect() as conn:
        with pytest.raises(WorkflowError, match='方法'):
            plan(flow, conn, p.preview_id, scope, TECH)


def test_manual_undo_refuses_later_writes(tmp_path, xlsx, context):
    flow, p, scope = prepared(tmp_path, xlsx, context)
    result, _, _ = save(flow, p, scope)
    later = flow.edit(p.candidates[1].id, CandidateEdit(base_revision=result['base_revision'], idempotency_key='later-edit',
        reason='后续独立改值', candidate_revision=p.candidates[1].candidate_revision, fields={'result':'95'}), actor=TECH)
    with pytest.raises(WorkflowError, match='后续'):
        undo(flow, p.preview_id, ManualSheetUndo(base_revision=later.base_revision, idempotency_key='reject-undo',
            reason='不能覆盖后续写入', edit_id=result['edit_id']), TECH)


def test_manual_merge_retains_both_original_sources(tmp_path, xlsx, context):
    flow, p, scope = prepared(tmp_path, xlsx, context)
    other = p.candidates[1]
    source2 = get_column_letter(other.location.column)+str(other.location.row)
    result_row = scope.results[0].model_copy(update={'sources': (*scope.results[0].sources, source2)})
    merged = scope.model_copy(update={'candidate_ids': tuple(c.id for c in p.candidates), 'results': (result_row,)})
    result, info, _ = save(flow, p, merged)
    assert len(info['parents']) == 2 and len(result['candidate_ids']) == 1
    child = next(c for c in flow.preview(p.preview_id, actor=TECH).candidates if c.id == result['candidate_ids'][0])
    assert child.raw_fields['manual_source_0'] == '99' and child.raw_fields['manual_source_1'] == '98'
    assert child.corrections['result'] == '97'
