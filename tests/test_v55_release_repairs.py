"""Explicit evidence, human intent and unresolved bilingual identities."""
import json
from uuid import uuid4

import pytest
from openpyxl import load_workbook

from stability_hub.matrix_fields import item_text
from stability_hub.review_batches import preview_batch, undo_batch
from stability_hub.review_issues import source_issues, resolved_raw, validate_selection
from stability_hub.review_tasks import saved_changes, checked_sources
from stability_hub.v5_contracts import TaskBatchScope, TaskBatchUndo
from stability_hub.workflow_common import WorkflowError
from test_v52_review_tasks import matrix_file, view, apply
from test_v5_regressions import TECH, preview
from test_v51_interpretation import receive


def conflict_file(tmp_path):
    path = matrix_file(tmp_path)
    book = load_workbook(path, rich_text=True)
    s = book.active
    s['A5'] = '纯度Purity'
    s['A6'] = '纯度Purity'
    s['B5'] = '单体（%）：≥95.0%\nHMW（%）'
    s['B6'] = '聚合体（%）：≤3.5%\nMonomer（%）'
    s['C5'], s['D5'] = 99.1, 98.9
    s['C6'], s['D6'] = 0.3, 0.4
    book.save(path)
    return path


def test_preview_contains_frozen_segments_and_distinct_values(tmp_path, context):
    flow, p = preview(tmp_path, matrix_file(tmp_path), context)
    p = receive(flow, p)
    _, _, _, tasks, _ = view(flow, p)
    chosen = [t for t in tasks if t.fields == ('result',)]
    scope = TaskBatchScope(section='DP长期', task_ids=tuple(t.id for t in chosen), operation='suggested')
    with flow.store.connect() as conn:
        before = conn.total_changes
        info = preview_batch(flow, conn, p.preview_id, scope, TECH)
        assert conn.total_changes == before
    changes = sorted(info['changes'], key=lambda c: c['evidence'][0]['column'])
    assert [c['fields']['result']['after'] for c in changes] == ['5.49', '5.54']
    for change, old in zip(changes, ['5.5', '5.4'], strict=True):
        rev = change['source_revisions']['result']
        assert ''.join(s['text'] for s in rev['segments'] if s['struck']) == old
        assert change['operation'] == 'keep_active'
    changed_scope = scope.model_copy(update={'task_ids': (chosen[0].id,)})
    with flow.store.connect() as conn:
        assert preview_batch(flow, conn, p.preview_id, changed_scope, TECH)['fingerprint'] != info['fingerprint']


def test_bilingual_identity_requires_explicit_row_choice_and_keeps_each_value(tmp_path, context):
    context = context.model_copy(update={'defaults': {}})
    flow, p = preview(tmp_path, conflict_file(tmp_path), context)
    p = receive(flow, p)
    current, _, flights, tasks, _ = view(flow, p)
    conflicts = sorted([t for t in tasks if t.code == 'identity_conflict'], key=lambda t: t.evidence[0].row)
    assert len(conflicts) == 2 and all(len(t.members) == 2 for t in conflicts)
    assert all('keep_body' not in t.actions for t in conflicts)
    assert not flights[0].can_commit
    assert '标注待核对' not in item_text('纯度Purity', '单体≥95%\nHMW（%）', subresult='单体≥95%\nHMW（%）')
    original = {c.id: c.raw_fields['result'] for c in current.candidates}
    expected_labels = ['Monomer', 'HMW']
    results = []
    for task, expected in zip(conflicts, expected_labels, strict=True):
        chosen = task.proposal['identity_options']['chinese']
        assert chosen.endswith(expected)
        scope = TaskBatchScope(section=task.section, task_ids=(task.id,), operation='manual', fields={'test_item': chosen})
        result, _, info = apply(flow, p, scope)
        results.append(result)
        assert all(c['fields']['test_item']['after'] == chosen for c in info['changes'])
    after, _, _, tasks, _ = view(flow, p)
    assert not any(t.code == 'identity_conflict' for t in tasks)
    assert {c.id: c.raw_fields['result'] for c in after.candidates} == original
    assert all(c.record for c in after.candidates), [(c.issue_code,c.detail,c.corrections) for c in after.candidates]
    from decimal import Decimal
    assert [c.record.result.value for c in after.candidates] == list(map(Decimal, ['99.1','98.9','.3','.4']))
    undo_batch(flow, p.preview_id, TaskBatchUndo(base_revision=after.base_revision,
        idempotency_key=uuid4().hex, reason='撤销解释核对', batch_id=results[-1]['batch_id']), actor=TECH)
    assert sum(t.code == 'identity_conflict' for t in view(flow, p)[3]) == 1


def test_old_generic_keep_body_does_not_resolve_new_identity(tmp_path, context):
    flow, p = preview(tmp_path, conflict_file(tmp_path), context)
    with flow.store.connect() as conn:
        upload = flow._upload(conn, p.preview_id)
        rows = conn.execute('SELECT * FROM workflow_candidates WHERE upload_id=?', (p.preview_id,)).fetchall()
        raws = flow._source_rows(conn, rows, upload)
        for cid, raw in raws.items():
            issue = next(i for i in source_issues(cid, raw) if i.code == 'identity_conflict')
            with pytest.raises(WorkflowError):
                validate_selection(issue, 'keep_body', {})
            legacy = {'issue_id': 'old-generic-comment-id', 'action': 'keep_body', 'fields': {}}
            _, remaining = resolved_raw(conn, cid, raw, decisions=[legacy])
            assert any(i.code == 'identity_conflict' for i in remaining)


def test_keep_body_history_is_not_a_system_name_edit(tmp_path, context):
    path = matrix_file(tmp_path)
    book = load_workbook(path, rich_text=True)
    from openpyxl.comments import Comment
    book.active['C5'].comment = Comment('核对原表正文', 'source')
    book.save(path)
    flow, p = preview(tmp_path, path, context)
    p = receive(flow, p)
    _, _, _, tasks, _ = view(flow, p)
    task = next(t for t in tasks if t.code == 'source_comment')
    apply(flow, p, TaskBatchScope(section=task.section, task_ids=(task.id,), operation='keep_body'))
    current = view(flow, p)[0]
    with flow.store.connect() as conn:
        changes = saved_changes(conn, flow._upload(conn, p.preview_id), current)
        assert not any(c['field'] == 'test_item' for c in changes)
        checks = checked_sources(conn, flow._upload(conn, p.preview_id), current)
        assert len(checks) == 1 and checks[0]['kind'] == 'checked_original'
        assert checks[0]['label'] == '人工核对保留'
        assert checks[0]['candidate_ids']
        event = conn.execute("SELECT detail_json FROM workflow_events WHERE action='resolve_issue' ORDER BY sequence DESC").fetchone()
        assert json.loads(event[0])['fields'] == {}
