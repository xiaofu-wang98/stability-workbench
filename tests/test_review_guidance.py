"""Focused identity questions apply to arbitrary source rows, not project IDs."""
import json

import pytest
from openpyxl import Workbook

from stability_hub.review_guidance import valid_item_name
from stability_hub.review_issues import resolved_raw, source_issues, validate_selection
from stability_hub.review_batches import preview_batch, undo_batch
from stability_hub.models import RawObservation
from stability_hub.v5_contracts import TaskBatchScope, TaskBatchUndo
from stability_hub.workflow_common import WorkflowError
from test_v52_review_tasks import view, apply
from test_v5_regressions import TECH, preview
from test_v51_interpretation import receive


def source(tmp_path, *, title='陌生模板', conflict=False):
    book = Workbook()
    s = book.active
    s.title = title
    for cell, value in {'A1': 'DS长期稳定性实验数据（5℃）', 'A2': 'LOT-UNFAMILIAR',
        'A3': '检测项目', 'B3': '质量标准', 'C3': '考察时间（月）',
        'C4': 0, 'D4': 1, 'E4': 3,
        'A5': '等电点（icIEF法）', 'B5': '报告数据\nReport Result', 'B6': '报告数据\nReport Result',
        'C5': '与标准品图谱一致', 'D5': '与标准品图谱一致',
        'C6': '样品主峰pI值与标准品主峰pI值的绝对差值0.0',
        'D6': '与标准品图谱一致' if conflict else '样品主峰pI值与标准品主峰pI值的绝对差值0.1',
        'E5': '因峰型变化，不进行汇报结果，详见：AR-C-NEW-2026999'}.items():
        s[cell] = value
    for area in ('C3:E3', 'A5:A6', 'E5:E6'):
        s.merge_cells(area)
    path = tmp_path / 'unfamiliar.xlsx'
    book.save(path)
    return path


def setup(tmp_path, context, **kwargs):
    path = source(tmp_path, **kwargs)
    flow, p = preview(tmp_path, path, context.model_copy(update={'defaults': {}}))
    return flow, receive(flow, p)


def name_tasks(flow, p):
    return [t for t in view(flow, p)[3] if t.proposal.get('guidance')]


@pytest.mark.parametrize('title', ['新稳定性', 'Unseen stability worksheet'])
def test_same_logical_row_suggests_names_without_copying_results(tmp_path, context, title):
    flow, p = setup(tmp_path, context, title=title)
    tasks = name_tasks(flow, p)
    assert len(tasks) == 2
    assert all(t.fields == ('test_item',) and 'keep_body' not in t.actions and t.exception is None for t in tasks)
    assert [t.proposal['guidance']['name_options'][0]['value'].split(' · ')[-1] for t in tasks] == ['图谱一致性', '主峰pI差值']
    assert all(len(t.proposal['guidance']['name_options'][0]['evidence']) == 2 for t in tasks)
    originals = {c.id: c.raw_fields for c in view(flow, p)[0].candidates}
    for t in tasks:
        chosen = t.proposal['guidance']['name_options'][0]['value']
        result, _, info = apply(flow, p, TaskBatchScope(section=t.section, task_ids=(t.id,), operation='manual', fields={'test_item': chosen}))
        retained = info['changes'][0]['review_results']
        assert len(retained) == 1 and not retained[0]['result_changed']
        assert '不进行汇报' in retained[0]['original_result']
    after = view(flow, p)[0]
    assert {c.id: c.raw_fields for c in after.candidates} == originals
    assert not name_tasks(flow, p)
    changed = [c for c in after.candidates if c.corrections]
    assert len(changed) == 2 and all(set(c.corrections) == {'test_item'} for c in changed)
    assert all(c.record.result.status == 'not_reported' and c.record.result.value is None and
               c.record.result.report_references == ('AR-C-NEW-2026999',) for c in changed)
    assert all(c.record.key.item == c.corrections['test_item'] for c in changed)
    assert flow.results('PROJECT-P', actor=TECH).total == 0
    undo_batch(flow, p.preview_id, TaskBatchUndo(base_revision=result['base_revision'], idempotency_key='undo-name',
        reason='重新核对本行名称', batch_id=result['batch_id']), actor=TECH)
    assert len(name_tasks(flow, p)) == 1


def test_conflicting_names_do_not_offer_a_guess(tmp_path, context):
    flow, p = setup(tmp_path, context, conflict=True)
    tasks = name_tasks(flow, p)
    assert tasks[0].proposal['guidance']['name_options']
    assert not tasks[1].proposal['guidance']['name_options']


def test_name_question_cannot_be_dismissed_by_result_or_legacy_keep_body(tmp_path, context):
    flow, p = setup(tmp_path, context)
    with flow.store.connect() as conn:
        row = next(r for r in conn.execute('SELECT id,raw_json FROM workflow_candidates') if json.loads(r['raw_json'])['system_hints'])
        raw = RawObservation.model_validate_json(row['raw_json'])
        # Old frozen parser had no machine-readable kind.
        raw = raw.model_copy(update={'system_hints': tuple({k:v for k,v in h.items() if k != 'kind'} for h in raw.system_hints)})
        issue = next(i for i in source_issues(row['id'], raw) if i.proposal.get('guidance'))
        for action, fields in [('manual', {'result': '报告数据'}), ('keep_body', {}),
                               ('manual', {'test_item': '报告数据\nReport Result'}),
                               ('manual', {'test_item': '原表分项1（未命名）'})]:
            with pytest.raises(WorkflowError):
                validate_selection(issue, action, fields)
        _, remaining = resolved_raw(conn, row['id'], raw, decisions=[
            {'issue_id': issue.id, 'action': 'manual', 'fields': {'result': '报告数据'}}], corrections={'result': '报告数据'})
        assert any(i.id == issue.id for i in remaining)
    task = name_tasks(flow, p)[0]
    with flow.store.connect() as conn, pytest.raises(WorkflowError):
        preview_batch(flow, conn, p.preview_id, TaskBatchScope(section=task.section, task_ids=(task.id,),
            operation='manual', fields={'result': '报告数据'}), TECH)


@pytest.mark.parametrize('value', ['Test', 'Δ pI', '外观 · 复溶前', 'New fraction 72'])
def test_unfamiliar_names_are_valid(value):
    assert valid_item_name(value)
