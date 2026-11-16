"""Complete text retention and bounded work for large exclusion histories."""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from openpyxl import load_workbook

from stability_hub.models import Location, RawObservation
from stability_hub.review_issues import source_issues, validate_selection
from stability_hub.review_tasks import saved_changes
from stability_hub.review_batches import undo_batch
from stability_hub.v5_contracts import TaskBatchScope, TaskBatchUndo
from stability_hub.workflow_common import WorkflowError
from stability_hub.xlsx_revisions import revision_proposal
from test_v52_review_tasks import matrix_file, revised, view, apply
from test_v5_regressions import TECH, preview
from test_v51_interpretation import receive


@pytest.mark.parametrize('active', [
    '微黄色，轻微乳光液体\n\nClear yellow, clear to opalescent liquid.',
    '深于超纯水且浅于黄色7号标准溶液\nDeeper than ultrapure water and shallower than yellow Y7 standard solution.',
])
def test_complete_text_preview_save_reload_undo(tmp_path, context, active):
    path = matrix_file(tmp_path)
    book = load_workbook(path, rich_text=True)
    book.active['A6'] = '颜色（目视法）\nColor (Visual Method)'
    book.active['C6'] = revised('old English description', active)
    book.active['D6'] = '符合规定'
    book.save(path)
    flow, p = preview(tmp_path, path, context)
    p = receive(flow, p)
    before, _, _, tasks, _ = view(flow, p)
    task = next(t for t in tasks if t.fields == ('result',))
    assert task.proposal['retain_complete_text']
    assert task.proposal['eligible'] and not task.exception
    result, request, info = apply(flow, p, TaskBatchScope(
        section=task.section, task_ids=(task.id,), operation='suggested'))
    change = info['changes'][0]
    assert change['fields']['result']['after'] == active
    assert '完整保留' in change['explanation']
    assert change['source_revisions']['result']['segments'][0]['struck']
    current = view(flow, p)[0]
    cid = task.members[0]['candidate_id']
    candidate = next(c for c in current.candidates if c.id == cid)
    assert candidate.corrections['result'] == active
    assert candidate.raw_fields == next(c for c in before.candidates if c.id == cid).raw_fields
    from stability_hub.review_batches import apply_batch
    assert apply_batch(flow, p.preview_id, request, actor=TECH) == result
    undo_batch(flow, p.preview_id, TaskBatchUndo(base_revision=current.base_revision,
        idempotency_key=uuid4().hex, reason='撤销全文保留测试', batch_id=result['batch_id']),
        actor=TECH)
    assert any(t.id == task.id for t in view(flow, p)[3])


@pytest.mark.parametrize('active', [
    '99\n98', '结果99\nResult 88', '结果一\n结果二',
    '与参考品图谱一致\nThe profile is NOT consistent with the Reference Standard',
    '单体\nHMW', '含量：99\nPurity: 99', '第一项\nFirst result\nSecond result',
])
def test_multiple_results_and_conflicts_still_require_interpretation(active):
    raw = RawObservation(location=Location(format='xlsx', section='Source', row=1, column=1),
        fields={'result': active}, revisions={'result': {
            'active_text': active, 'all_deleted': False,
            'segments': [{'text': 'old', 'struck': True}, {'text': active, 'struck': False}]}})
    issue = next(i for i in source_issues('candidate', raw) if i.code == 'source_revision')
    assert not revision_proposal(raw, 'result', raw.revisions['result'])['eligible']
    with pytest.raises(WorkflowError):
        validate_selection(issue, 'keep_active', {'result': active})


def test_retaining_text_does_not_approve_independent_identity_conflict():
    from stability_hub.review_issues import resolved_raw
    active = '微黄色液体\nSlightly yellow liquid.'
    raw = RawObservation(location=Location(format='xlsx', section='Source', row=1, column=1),
        fields={'result': 'old\n' + active, 'test_item': '纯度',
                'criterion': '单体\nHMW', 'method': '还原CE-SDS(NR CE-SDS)'},
        revisions={'result': {'active_text': active, 'all_deleted': False,
            'segments': [{'text': 'old\n', 'struck': True}, {'text': active, 'struck': False}]}})
    issues = source_issues('candidate', raw)
    revision = next(i for i in issues if i.code == 'source_revision')
    validate_selection(revision, 'keep_active', {'result': active})
    _, remaining = resolved_raw(None, 'candidate', raw, decisions=[{
        'issue_id': revision.id, 'action': 'keep_active', 'fields': {'result': active}}],
        corrections={'result': active})
    assert not any(i.code == 'source_revision' for i in remaining)
    assert {'identity_conflict', 'method_conflict'} <= {i.code for i in remaining}


def test_saved_changes_indexes_origins_and_preserves_partial_rows(tmp_path, context, monkeypatch):
    flow, p = preview(tmp_path, matrix_file(tmp_path), context)
    template = p.candidates[0]
    candidates = []
    for index in range(1000):
        group, point = divmod(index, 20)
        section = 'First' if group < 25 else 'Second'
        row = group % 25 + 1
        candidates.append(template.model_copy(update={
            'id': str(index), 'corrections': {}, 'raw_fields': {'test_item': 'item', 'result': '99'},
            'lifecycle': 'draft' if index == 0 else 'withdrawn',
            'location': Location(format='xlsx', section=section, row=row, column=point+3),
            'evidence': {'test_item': Location(format='xlsx', section=section, row=row, column=1)}}))
    comparisons = 0
    original = Location.__eq__
    def counted(self, other):
        nonlocal comparisons
        comparisons += 1
        return original(self, other)
    monkeypatch.setattr(Location, '__eq__', counted)
    with flow.store.connect() as conn:
        changes = saved_changes(conn, flow._upload(conn, p.preview_id), SimpleNamespace(candidates=candidates))
    assert len(changes) == 49 + 19
    whole = [c for c in changes if c['column'] == 1]
    partial = [c for c in changes if c['column'] != 1]
    assert len(whole) == 49 and all(len(c['candidate_ids']) == 20 for c in whole)
    assert len(partial) == 19 and all(len(c['candidate_ids']) == 1 for c in partial)
    assert comparisons < 10000  # Old implementation performs nearly one million comparisons.


def test_cached_mapping_still_observes_new_formal_history(tmp_path, xlsx, context, monkeypatch):
    from stability_hub.workflow import Workflow
    from stability_hub.storage import Store
    from test_v5_regressions import confirm_initial
    flow, first = preview(tmp_path, xlsx([['Appearance', '0M', 'Clear', '']]), context)
    # Different bytes, same physical slot: prime mapping before formal history exists.
    second = flow.upload('PROJECT-P', 'second.xlsx',
        xlsx([['Appearance', '0M', 'Yellow', '']]).read_bytes(), context, 'second', actor=TECH)
    assert second.candidates[0].status == 'new'
    confirm_initial(flow, flow.preview(first.preview_id, actor=TECH))
    calls = []
    original = flow._mapped
    def counted(*args, **kwargs):
        calls.append(args[1]['id'])
        return original(*args, **kwargs)
    monkeypatch.setattr(flow, '_mapped', counted)
    updated = flow.preview(second.preview_id, actor=TECH)
    assert updated.candidates[0].status == 'different_value'
    assert updated.candidates[0].existing_record.result.raw_text == 'Clear'
    assert not calls  # Reuse source mapping, recompute comparison against current history.
    fresh_flow = Workflow(Store(flow.store.home))
    assert fresh_flow.preview(second.preview_id, actor=TECH).candidates == updated.candidates


def test_previous_history_cache_invalidates_after_uncommitted_write(tmp_path, xlsx, context):
    from test_v5_regressions import confirm_initial
    from stability_hub.worksheet_commit import previous
    flow, p = preview(tmp_path, xlsx([['Appearance', '0M', 'Clear', '']]), context)
    vid = confirm_initial(flow, p).result_version_ids[0]
    candidate = flow.preview(p.preview_id, actor=TECH).candidates[0]
    with flow.store.connect() as conn:
        conn.execute('BEGIN')
        assert previous(conn, p.project, candidate, 'correction')['id'] == vid
        conn.execute('DELETE FROM workflow_adoptions WHERE project=?', (p.project,))
        assert previous(conn, p.project, candidate, 'correction') is None
        assert previous(conn, p.project, candidate, 'repeat_source')['id'] == vid
        conn.rollback()
    with flow.store.connect() as conn:
        conn.execute('BEGIN')
        assert previous(conn, p.project, candidate, 'correction')['id'] == vid
