import json
from openpyxl import Workbook
import pytest

from stability_hub.models import ImportContext, ProjectConfig
from stability_hub.permissions import Role, actor_for_role
from stability_hub.review_batches import apply_batch, preview_batch, undo_batch
from stability_hub.review_tasks import current_review
from stability_hub.storage import Store
from stability_hub.v5_contracts import TaskBatchApply, TaskBatchScope, TaskBatchUndo
from stability_hub.workflow import Workflow
from stability_hub.workflow_common import WorkflowError
from stability_hub.worksheet_regions import classify, coverage
from stability_hub.v5_contracts import RegionDecision

TECH = actor_for_role('tech', 'technician', Role.TECHNICIAN)


def create_tail_workbook(tmp_path):
    book = Workbook()
    s = book.active
    s.title = "DP长期"
    for coord, value in {
        "A1": "DP长期稳定性实验数据（5℃）",
        "A2": "B1",
        "A3": "检测项目",
        "B3": "质量标准",
        "C3": "考察时间（月）",
        "C4": 0,
        "D4": 3,
        "A5": "纯度",
        "B5": "≥95%",
        "C5": 99,
        "D5": 98,
        "A6": "pH",
        "B6": "报告结果",
        "C6": 5.5,
        "D6": 5.4,
    }.items():
        s[coord] = value
    s.merge_cells("C3:D3")

    for r in range(8, 26):
        s[f"B{r}"] = f"Chart-Label-{r}"

    path = tmp_path / "tail_test.xlsx"
    book.save(path)
    return path


def test_region_task_batch_preview_apply_and_undo(tmp_path):
    path = create_tail_workbook(tmp_path)
    store = Store(tmp_path / 'store')
    flow = Workflow(store)
    flow.configure(ProjectConfig(project='P1'))
    ctx = ImportContext(role='stability_source', defaults={'sample_type': 'DP', 'batch': 'B1', 'presentation': 'pen', 'study': 'long_term', 'condition': '5C'})
    p = flow.upload('P1', 'tail_test.xlsx', path.read_bytes(), ctx, 'k1', actor=TECH)

    with store.connect() as conn:
        _, _, _, tasks, _ = current_review(flow, conn, flow._upload(conn, p.preview_id), TECH)

    region_tasks = [t for t in tasks if t.code == 'region']
    assert len(region_tasks) == 1
    rt = region_tasks[0]
    assert rt.proposal.get('eligible') is True
    assert rt.proposal.get('operation') == 'classify'

    # Preview batch with region task
    scope = TaskBatchScope(
        section='DP长期',
        task_ids=(rt.id,),
        operation='classify',
        role='auxiliary',
    )
    with store.connect() as conn:
        info = preview_batch(flow, conn, p.preview_id, scope, TECH)

    assert info['summary']['tasks'] == 1
    assert info['summary']['results'] == 0
    assert len(info['changes']) == 1
    assert info['changes'][0]['fields']['role'] == {'before': 'unclassified', 'after': 'auxiliary'}
    assert len(info['members']) == 1
    assert info['members'][0]['kind'] == 'region'

    # Apply batch
    apply_cmd = TaskBatchApply(
        base_revision=p.base_revision,
        idempotency_key='apply-1',
        reason='批量确认表尾辅助区域',
        scope=scope,
        fingerprint=info['fingerprint'],
    )
    result = apply_batch(flow, p.preview_id, apply_cmd, actor=TECH)
    assert result['remaining_tasks'] == len(tasks) - 1
    batch_id = result['batch_id']

    # Verify region role is now auxiliary
    with store.connect() as conn:
        sheets = coverage(conn, flow._upload(conn, p.preview_id))
    unclassified = [r for r in sheets[0]['regions'] if r['role'] == 'unclassified']
    assert len(unclassified) == 0
    aux = [r for r in sheets[0]['regions'] if r['role'] == 'auxiliary']
    assert any(len(r['coordinates']) == 18 for r in aux)

    # Undo batch
    undo_cmd = TaskBatchUndo(
        base_revision=result['base_revision'],
        idempotency_key='undo-1',
        reason='撤销辅助区域批量处理',
        batch_id=batch_id,
    )
    undo_res = undo_batch(flow, p.preview_id, undo_cmd, actor=TECH)

    # Verify region role is reverted to unclassified
    with store.connect() as conn:
        sheets_reverted = coverage(conn, flow._upload(conn, p.preview_id))
    unclass_reverted = [r for r in sheets_reverted[0]['regions'] if r['role'] == 'unclassified']
    assert any(len(r['coordinates']) == 18 for r in unclass_reverted)


def test_stale_batch_preview_rejected_atomically(tmp_path):
    path = create_tail_workbook(tmp_path)
    store = Store(tmp_path / 'store_stale')
    flow = Workflow(store)
    flow.configure(ProjectConfig(project='P1'))
    ctx = ImportContext(role='stability_source', defaults={'sample_type': 'DP', 'batch': 'B1', 'presentation': 'pen', 'study': 'long_term', 'condition': '5C'})
    p = flow.upload('P1', 'tail_test.xlsx', path.read_bytes(), ctx, 'k2', actor=TECH)

    with store.connect() as conn:
        _, _, _, tasks, _ = current_review(flow, conn, flow._upload(conn, p.preview_id), TECH)

    region_tasks = [t for t in tasks if t.code == 'region']
    rt = region_tasks[0]

    scope = TaskBatchScope(
        section='DP长期',
        task_ids=(rt.id,),
        operation='classify',
        role='auxiliary',
    )
    with store.connect() as conn:
        info = preview_batch(flow, conn, p.preview_id, scope, TECH)

    # Apply with wrong/stale fingerprint
    stale_cmd = TaskBatchApply(
        base_revision=p.base_revision,
        idempotency_key='stale-1',
        reason='尝试过期提交',
        scope=scope,
        fingerprint='stale-fake-fingerprint',
    )
    with pytest.raises(WorkflowError) as exc:
        apply_batch(flow, p.preview_id, stale_cmd, actor=TECH)
    assert exc.value.status == 409


def test_subsequent_region_modification_blocks_undo(tmp_path):
    path = create_tail_workbook(tmp_path)
    store = Store(tmp_path / 'store_sub')
    flow = Workflow(store)
    flow.configure(ProjectConfig(project='P1'))
    ctx = ImportContext(role='stability_source', defaults={'sample_type': 'DP', 'batch': 'B1', 'presentation': 'pen', 'study': 'long_term', 'condition': '5C'})
    p = flow.upload('P1', 'tail_test.xlsx', path.read_bytes(), ctx, 'k3', actor=TECH)

    with store.connect() as conn:
        _, _, _, tasks, _ = current_review(flow, conn, flow._upload(conn, p.preview_id), TECH)

    rt = [t for t in tasks if t.code == 'region'][0]
    scope = TaskBatchScope(section='DP长期', task_ids=(rt.id,), operation='classify', role='auxiliary')
    with store.connect() as conn:
        info = preview_batch(flow, conn, p.preview_id, scope, TECH)

    apply_cmd = TaskBatchApply(
        base_revision=p.base_revision,
        idempotency_key='apply-sub',
        reason='批次1',
        scope=scope,
        fingerprint=info['fingerprint'],
    )
    res = apply_batch(flow, p.preview_id, apply_cmd, actor=TECH)
    batch_id = res['batch_id']

    # Subsequent manual classification on the region
    classify(flow, p.preview_id, RegionDecision(
        base_revision=res['base_revision'],
        idempotency_key='sub-cls',
        reason='后续改动为排除',
        region_id=rt.members[0]['region_id'],
        role='excluded',
    ), actor=TECH)

    # Now attempt to undo batch 1
    with pytest.raises(WorkflowError) as exc:
        undo_batch(flow, p.preview_id, TaskBatchUndo(
            base_revision=res['base_revision'] + 1,
            idempotency_key='undo-sub',
            reason='撤销已被覆盖的批次',
            batch_id=batch_id,
        ), actor=TECH)
    assert exc.value.status == 409
    assert exc.value.code == 'later_change'
