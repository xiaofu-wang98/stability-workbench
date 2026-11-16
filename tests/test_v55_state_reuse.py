"""Committed projection reuse must never weaken revision/permission/rollback checks."""
from uuid import uuid4

import pytest

from stability_hub.permissions import actor_for_role, Role
from stability_hub.review_tasks import current_review
from stability_hub.ledger_review import LedgerReview
from stability_hub.v5_contracts import TaskBatchScope
from test_v52_review_tasks import matrix_file, view, apply
from test_v5_regressions import TECH, preview
from test_v51_interpretation import receive


def test_saved_revision_reused_by_matrix_and_undo_invalidates(tmp_path, context):
    flow, p = preview(tmp_path, matrix_file(tmp_path), context)
    p = receive(flow, p)
    tasks = view(flow, p)[3]
    task = next(t for t in tasks if t.fields == ('result',))
    result, _, _ = apply(flow, p, TaskBatchScope(section=task.section, task_ids=(task.id,), operation='suggested'))
    builds = flow.review_build_counts['preview']
    matrix = LedgerReview(flow).matrix(p.preview_id, actor=TECH)
    assert matrix['preview']['base_revision'] == result['base_revision']
    assert flow.review_build_counts['preview'] == builds
    assert flow.review_build_counts['revision_hit'] > 0
    from stability_hub.review_batches import undo_batch
    from stability_hub.v5_contracts import TaskBatchUndo
    undo_batch(flow, p.preview_id, TaskBatchUndo(base_revision=result['base_revision'],
        idempotency_key=uuid4().hex, reason='撤销', batch_id=result['batch_id']), actor=TECH)
    current = view(flow, p)
    assert any(t.id == task.id for t in current[3])
    assert current[0].base_revision != result['base_revision']


def test_actor_partition_and_returned_values_cannot_mutate_cache(tmp_path, context):
    flow, p = preview(tmp_path, matrix_file(tmp_path), context)
    p = receive(flow, p)
    current = view(flow, p)
    current[1][0]['section'] = 'tampered'
    current[3][0].proposal['changed'] = True
    assert view(flow, p)[1][0]['section'] != 'tampered'
    assert 'changed' not in view(flow, p)[3][0].proposal
    builds = flow.review_build_counts['preview']
    with flow.store.connect() as conn:
        current_review(flow, conn, flow._upload(conn, p.preview_id), actor_for_role(TECH.id, 'lead', Role.PROJECT_LEAD))
    assert flow.review_build_counts['preview'] == builds + 1


def test_rollback_state_is_never_published(tmp_path, context):
    flow, p = preview(tmp_path, matrix_file(tmp_path), context)
    p = receive(flow, p)
    original = view(flow, p)[0]
    from stability_hub.workflow_common import bump
    with pytest.raises(RuntimeError):
        with flow.store.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            bump(conn, original.project)
            changed = current_review(flow, conn, flow._upload(conn, p.preview_id), TECH)
            assert changed[0].base_revision > original.base_revision
            raise RuntimeError('rollback')
    assert view(flow, p)[0].base_revision == original.base_revision
    assert not any(k[2] > original.base_revision for k in flow._review_states)
