"""Project-independent layout and field-specific identity review, including frozen drafts."""
import json
from decimal import Decimal

import pytest
from openpyxl import Workbook

from stability_hub.matrix_fields import item_text, method_conflict
from stability_hub.merge_layout import MERGE_HAZARD
from stability_hub.review_tasks import checked_sources
from stability_hub.review_batches import undo_batch
from stability_hub.structural_review import resolve_structure
from stability_hub.v5_contracts import TaskBatchScope, TaskBatchUndo, StructuralDecision, IssueDecision
from stability_hub.review_issues import resolve_issue
from stability_hub.workflow_common import WorkflowError
from test_v52_review_tasks import revised, view, apply
from test_v5_regressions import TECH, preview
from test_v51_interpretation import receive


def source(tmp_path, *, title='不同模板', conflict=False, independent=False, horizontal=False, percent=False, comment=False):
    book = Workbook()
    s = book.active
    s.title = title
    for coord, value in {'A1': 'DS长期稳定性试验数据（-80℃）', 'A2': 'B1',
        'A3': '检测项目', 'B3': '方法', 'C3': '质量标准', 'D3': '考察时间（月）',
        'D4': 0, 'E4': 3, 'F4': 6, 'A5': '纯度', 'B5': '还原型CE-SDS法(NR CE-SDS Method)' if conflict else 'CE-SDS',
        'C5': revised('总体小峰%：≤10.0%', '总体小峰：报告报告结果'),
        'D5': .027 if percent else 2.7, 'E5': .024 if percent else 2.4, 'F5': .026 if percent else 2.6}.items():
        s[coord] = value
    s.merge_cells('D3:F3')
    for area in ['A5:A6', 'B5:B6', 'C5:C6']:
        s.merge_cells(area)
    if horizontal:
        s.merge_cells('D5:E5')
    else:
        for col in 'DEF':
            s.merge_cells(f'{col}5:{col}6')
    if independent:
        s.unmerge_cells('C5:C6')
        s['C6'] = '另一个独立子结果：报告结果'
    if percent:
        for col in 'DEF':
            s[f'{col}5'].number_format = '0.0%'
    if comment:
        from openpyxl.comments import Comment
        s['D5'].comment = Comment('核对现有原文结果', 'source')
    path = tmp_path / 'unrelated-template.xlsx'
    book.save(path)
    return path


def setup(tmp_path, context, *, legacy=False, **kw):
    flow, p = preview(tmp_path, source(tmp_path, **kw), context.model_copy(update={'defaults': {}}))
    if legacy:
        # Reproduce an immutable draft from the old parser without changing its frozen Excel.
        with flow.store.connect() as conn:
            for row in conn.execute('SELECT id,raw_json FROM workflow_candidates WHERE upload_id=?', (p.preview_id,)).fetchall():
                raw = json.loads(row['raw_json'])
                raw['hazards'] = [MERGE_HAZARD]
                conn.execute('UPDATE workflow_candidates SET raw_json=? WHERE id=?', (json.dumps(raw, ensure_ascii=False), row['id']))
    return flow, receive(flow, p)


def standards(flow, p):
    task = next(t for t in view(flow, p)[3] if t.code == 'source_revision')
    return apply(flow, p, TaskBatchScope(section=task.section, task_ids=(task.id,), operation='suggested'))


def test_first_nonblank_subresult_is_stable_for_any_label():
    for label in ['总体小峰', '新检测分项', 'Unusual fraction']:
        text = label + '：报告结果'
        assert item_text('检项', text, '方法', '\n\n' + text) == item_text('检项', text, '方法', text)


@pytest.mark.parametrize('title', ['不同模板', 'Another worksheet'])
def test_new_padding_merge_is_one_result_without_structure_task(tmp_path, context, title):
    flow, p = setup(tmp_path, context, title=title)
    assert len(p.candidates) == 3
    assert not any(t.code == 'structure' for t in view(flow, p)[3])
    standards(flow, p)
    after = view(flow, p)[0]
    assert all('总体小峰' in c.interpreted_fields['test_item'] for c in after.candidates)


@pytest.mark.parametrize('percent', [False, True])
def test_frozen_padding_batch_preserves_ids_values_prior_decisions_and_undo(tmp_path, context, percent):
    flow, p = setup(tmp_path, context, legacy=True, percent=percent)
    standards(flow, p)
    before = view(flow, p)[0]
    tasks = [t for t in view(flow, p)[3] if t.code == 'structure']
    assert len(tasks) == 1 and len(tasks[0].members) == 3
    raw = {c.id: (c.raw_fields, c.corrections) for c in before.candidates}
    result, _, info = apply(flow, p, TaskBatchScope(section=tasks[0].section, task_ids=(tasks[0].id,), operation='suggested'))
    assert len(info['changes']) == 1 and len(info['changes'][0]['retained_results']) == 3
    after, _, _, remaining, _ = view(flow, p)
    assert not any(t.code in {'structure', 'source_revision'} for t in remaining)
    assert {c.id: (c.raw_fields, c.corrections) for c in after.candidates} == raw
    assert [c.record.result.value for c in after.candidates] == [Decimal('2.7'), Decimal('2.4'), Decimal('2.6')]
    with flow.store.connect() as conn:
        assert not conn.execute("SELECT 1 FROM workflow_events WHERE action='split_source'").fetchone()
        checks = checked_sources(conn, flow._upload(conn, p.preview_id), after)
        assert len(checks) == 1 and checks[0]['kind'] == 'checked_layout'
    undo_batch(flow, p.preview_id, TaskBatchUndo(base_revision=after.base_revision,
        idempotency_key='undo-layout', reason='撤销排版解释', batch_id=result['batch_id']), actor=TECH)
    remaining = view(flow, p)[3]
    assert sum(t.code == 'structure' for t in remaining) == 1
    assert not any(t.code == 'source_revision' for t in remaining)


@pytest.mark.parametrize('case', ['independent', 'horizontal'])
def test_genuine_merge_ambiguity_has_no_single_result_shortcut(tmp_path, context, case):
    flow, p = setup(tmp_path, context, **{case: True})
    current, _, _, tasks, _ = view(flow, p)
    structural = [t for t in tasks if t.code == 'structure']
    assert structural and not any(t.proposal.get('operation') == 'keep_single' for t in structural)
    c = next(c for c in current.candidates if any(i.code == 'structure' for i in c.issues))
    issue = next(i for i in c.issues if i.code == 'structure')
    with pytest.raises(WorkflowError, match='跨时间点|完整'):
        resolve_issue(flow, p.preview_id, IssueDecision(base_revision=current.base_revision, idempotency_key='unsafe',
            reason='不允许绕过结构证据', candidate_id=c.id, candidate_revision=c.candidate_revision,
            issue_id=issue.id, action='keep_single'), actor=TECH)


def test_method_conflict_is_one_field_one_source_question_without_guessing(tmp_path, context):
    assert method_conflict('还原型CE-SDS法(NR CE-SDS Method)')
    assert not method_conflict('非还原型\nCE-SDS法(NR CE-SDS Method)')
    flow, p = setup(tmp_path, context, legacy=True, conflict=True)
    tasks = view(flow, p)[3]
    task = next(t for t in tasks if t.code == 'method_conflict')
    assert task.fields == ('method',) and len(task.members) == 3
    assert 'keep_body' not in task.actions
    standard = next(t for t in tasks if t.code == 'source_revision')
    apply(flow, p, TaskBatchScope(section=standard.section, task_ids=(standard.id,), operation='suggested'))
    layout = next(t for t in view(flow, p)[3] if t.code == 'structure')
    apply(flow, p, TaskBatchScope(section=layout.section, task_ids=(layout.id,), operation='suggested'))
    assert any(t.code == 'method_conflict' for t in view(flow, p)[3])
    # A synthetic explicit answer validates the chain; no real business answer is chosen.
    task = next(t for t in view(flow, p)[3] if t.code == 'method_conflict')
    apply(flow, p, TaskBatchScope(section=task.section, task_ids=(task.id,), operation='manual', fields={'method': 'R CE-SDS'}))
    after = view(flow, p)[0]
    assert not any(i.code == 'method_conflict' for c in after.candidates for i in c.issues)
    assert all('NR CE-SDS' not in c.interpreted_fields['test_item'] for c in after.candidates)


def test_real_projection_inherits_only_valid_unchanged_source_decisions(tmp_path, context):
    flow, p = setup(tmp_path, context, legacy=True)
    standards(flow, p)
    current = view(flow, p)[0]
    c = current.candidates[0]
    fields = {**c.corrections, 'test_item': c.interpreted_fields['test_item'], 'time_point': '0M', 'result': '2.7'}
    resolve_structure(flow, p.preview_id, StructuralDecision(base_revision=current.base_revision, idempotency_key='explicit',
        reason='明确逻辑归属的技术测试', candidate_id=c.id, candidate_revision=c.candidate_revision,
        section=c.location.section, coordinate='D5', hazard=MERGE_HAZARD, results=(fields,)), actor=TECH)
    after = view(flow, p)[0]
    child = next(c for c in after.candidates if c.id.startswith('projection_'))
    assert not any(i.code in {'structure', 'source_revision'} for i in child.issues)
    with flow.store.connect() as conn:
        assert conn.execute("SELECT 1 FROM workflow_events WHERE subject_id=? AND action='inherit_source_decision'", (child.id,)).fetchone()


def test_advanced_seed_uses_backend_percent_conversion_without_retyping(tmp_path, context):
    flow, p = setup(tmp_path, context, legacy=True, percent=True)
    from stability_hub.structural_review import projection_seed
    with flow.store.connect() as conn:
        change_count = conn.total_changes
        seed = projection_seed(flow, conn, p.preview_id, p.candidates[0].id, TECH)
        assert conn.total_changes == change_count
    assert Decimal(seed['fields']['result']) == Decimal('2.7') and seed['fields']['unit'] == '%'
    assert seed['fields']['time_point'] == '0M'
    assert not seed['missing_required']


def test_changed_child_result_does_not_inherit_unrelated_comment_approval(tmp_path, context):
    flow, p = setup(tmp_path, context, legacy=True, comment=True)
    standards(flow, p)
    task = next(t for t in view(flow, p)[3] if t.code == 'source_comment')
    apply(flow, p, TaskBatchScope(section=task.section, task_ids=(task.id,), operation='keep_body'))
    current = view(flow, p)[0]
    c = current.candidates[0]
    resolve_structure(flow, p.preview_id, StructuralDecision(base_revision=current.base_revision, idempotency_key='changed-result',
        reason='合成新逻辑结果需要重新核对与数值相关的批注', candidate_id=c.id, candidate_revision=c.candidate_revision,
        section=c.location.section, coordinate='D5', hazard=MERGE_HAZARD,
        results=({**c.corrections, 'test_item': c.interpreted_fields['test_item'], 'time_point': '0M', 'result': '3.1'},)), actor=TECH)
    child = next(c for c in view(flow, p)[0].candidates if c.id.startswith('projection_'))
    assert any(i.code == 'source_comment' for i in child.issues)
    assert not any(i.code == 'source_revision' for i in child.issues)


def test_v56_hyphenated_method_conflict_and_multiline_break():
    from stability_hub.matrix_fields import method_conflict, method_lines, explicit_method
    assert method_conflict('还原型CE-SDS法(NR-CE-SDS Method)')
    assert method_conflict('非还原型CE-SDS(Reduced CE-SDS Method)')
    assert len(method_lines('非还原型\nCE-SDS法(NR CE-SDS Method)')) == 1
    assert 'NR CE-SDS' in explicit_method('非还原型\nCE-SDS法(NR CE-SDS Method)')


def test_v56_subresult_distinguishes_hc_lc_bioactivity_and_icief_result():
    from stability_hub.matrix_fields import item_text
    # 1. Parenthesized (HC+LC)% is never truncated to empty
    assert 'HC+LC' in item_text('纯度', '（重链+轻链）%：≥90.0%', 'NR CE-SDS', '（重链+轻链）%：≥90.0%')
    # 2. Identity vs Potency bioactivity do not collide on the same sheet
    id_bio = item_text('生物学活性', '与阶段性标准品或参考品量效关系曲线相似', '细胞杀伤', '与阶段性标准品或参考品量效关系曲线相似')
    pot_bio = item_text('生物学活性', '应为阶段性标准品或参考品的70% ~ 130%', '细胞杀伤', '应为阶段性标准品或参考品的70% ~ 130%')
    assert id_bio != pot_bio
    # 3. icIEF with '报告数据' criterion separates profile vs pI diff via result text
    icief_1 = item_text('等电点', '报告数据', 'icIEF', '报告数据', '与阶段性标准品图谱一致')
    icief_2 = item_text('等电点', '报告数据', 'icIEF', '报告数据', '样品主峰pI值与阶段性标准品主峰pI值的绝对差值0.0')
    assert icief_1 != icief_2


def test_v56_changed_child_time_point_does_not_inherit_comment(tmp_path, context):
    flow, p = setup(tmp_path, context, legacy=True, comment=True)
    standards(flow, p)
    task = next(t for t in view(flow, p)[3] if t.code == 'source_comment')
    apply(flow, p, TaskBatchScope(section=task.section, task_ids=(task.id,), operation='keep_body'))
    current = view(flow, p)[0]
    c = current.candidates[0]
    resolve_structure(flow, p.preview_id, StructuralDecision(
        base_revision=current.base_revision, idempotency_key='changed-timepoint',
        reason='改变时间点的拆分条目必须重新核对原格批注', candidate_id=c.id, candidate_revision=c.candidate_revision,
        section=c.location.section, coordinate='D5', hazard=MERGE_HAZARD,
        results=({**c.corrections, 'test_item': c.interpreted_fields['test_item'], 'time_point': '3M', 'result': '2.7'},)), actor=TECH)
    child = next(c for c in view(flow, p)[0].candidates if c.id.startswith('projection_'))
    assert any(i.code == 'source_comment' for i in child.issues)


def test_v56_undo_batch_requires_review_duplicate_for_other_actor_exclusion(tmp_path, context):
    from stability_hub.permissions import Role, actor_for_role
    from stability_hub.review_batches import preview_batch, apply_batch, undo_batch
    from stability_hub.v5_contracts import TaskBatchApply
    flow, p = setup(tmp_path, context)
    senior = actor_for_role('senior_1', 'Senior', Role.SENIOR_TECHNICIAN)
    other_tech = actor_for_role('tech_other', 'Other Tech', Role.TECHNICIAN)
    scope = TaskBatchScope(section=p.candidates[0].location.section, candidate_ids=(p.candidates[0].id,), operation='exclude_selected')
    with flow.store.connect() as conn:
        info = preview_batch(flow, conn, p.preview_id, scope, senior)
    res = apply_batch(flow, p.preview_id, TaskBatchApply(
        base_revision=info['base_revision'], idempotency_key='exclude-by-senior',
        reason='高级技术员排除', scope=scope, fingerprint=info['fingerprint']), actor=senior)
    with pytest.raises(PermissionError):
        undo_batch(flow, p.preview_id, TaskBatchUndo(
            base_revision=res['base_revision'], idempotency_key='undo-by-other-tech',
            reason='非上传者普通技术员尝试越权撤销排除', batch_id=res['batch_id']), actor=other_tech)


@pytest.mark.parametrize('changed', [{'unit': '%'}, {'reporting_basis': 'EP'}])
def test_v56_changed_child_context_keeps_comment_pending(tmp_path, context, changed):
    flow, p = setup(tmp_path, context, legacy=True, comment=True)
    standards(flow, p)
    task = next(t for t in view(flow, p)[3] if t.code == 'source_comment')
    apply(flow, p, TaskBatchScope(section=task.section, task_ids=(task.id,), operation='keep_body'))
    current = view(flow, p)[0]
    c = current.candidates[0]
    resolve_structure(flow, p.preview_id, StructuralDecision(
        base_revision=current.base_revision, idempotency_key='context-change', reason='新上下文重新核对批注',
        candidate_id=c.id, candidate_revision=c.candidate_revision, section=c.location.section,
        coordinate='D5', hazard=MERGE_HAZARD, results=({**c.corrections,
            'test_item': c.interpreted_fields['test_item'], 'time_point': '0M', 'result': '2.7', **changed},)), actor=TECH)
    child = next(c for c in view(flow, p)[0].candidates if c.id.startswith('projection_'))
    assert any(i.code == 'source_comment' for i in child.issues)


def test_v56_structure_origin_is_stable_and_replays_legacy_decision():
    from stability_hub.models import Location, RawObservation, digest
    from stability_hub.review_issues import source_issues, resolved_raw
    loc = Location(format='xlsx', section='Synthetic', row=8, column=4)
    raw = RawObservation(location=loc, fields={'result': '4.2'}, hazards=(MERGE_HAZARD,),
                         evidence={'result': loc, 'criterion': loc.model_copy(update={'column': 3})})
    issue = next(i for i in source_issues('c', raw) if i.code == 'structure')
    assert issue.evidence == (loc,)
    moved = raw.model_copy(update={'evidence': {**raw.evidence, 'criterion': loc.model_copy(update={'row': 2})}})
    assert next(i for i in source_issues('c', moved) if i.code == 'structure').id == issue.id
    old_evidence = sorted(set([raw.location, *raw.evidence.values()]), key=lambda e: (e.format, e.section, e.row, e.column))
    legacy_id = digest(['c', 'structure', MERGE_HAZARD, [e.model_dump() for e in old_evidence]])
    effective, remaining = resolved_raw(None, 'c', raw,
        decisions=[{'issue_id': legacy_id, 'action': 'keep_single'}], corrections={})
    assert not effective.hazards and not remaining


def test_v56_resolved_identity_stays_resolved_when_other_field_changes():
    from stability_hub.models import Location, RawObservation
    from stability_hub.review_issues import source_issues, resolved_raw
    raw = RawObservation(location=Location(format='xlsx', section='Synthetic', row=3, column=7),
        fields={'result': '99', 'method': 'UV\nSEC', 'unit': '%'})
    issue = next(i for i in source_issues('c', raw) if i.code == 'identity_ambiguity')
    _, remaining = resolved_raw(None, 'c', raw,
        decisions=[{'issue_id': issue.id, 'action': 'manual'}], corrections={'result': '98'})
    assert not any(i.code == 'identity_ambiguity' for i in remaining)


@pytest.mark.parametrize(('active', 'eligible'), [
    ('与参考品图谱一致\nThe profile is consistent with the Reference Standard', True),
    ('与参考品图谱一致\nThe profile is NOT consistent with the Reference Standard', False),
    ('结果99\nResult 88', False),
    ('未知结论\nUnknown conclusion', False),
])
def test_v56_bilingual_revision_requires_known_equivalence(active, eligible):
    from stability_hub.models import Location, RawObservation
    from stability_hub.xlsx_revisions import revision_proposal
    raw = RawObservation(location=Location(format='xlsx', section='Synthetic', row=3, column=7),
                         fields={'result': active})
    revision = {'active_text': active, 'all_deleted': False,
                'segments': [{'text': 'old', 'struck': True}, {'text': active, 'struck': False}]}
    assert revision_proposal(raw, 'result', revision)['eligible'] is eligible


def test_v56_comment_action_group_preserves_values_and_undo(tmp_path, context):
    from stability_hub.review_tasks import build_action_groups
    flow, p = setup(tmp_path, context, comment=True)
    standards(flow, p)
    before, sheets, _, tasks, _ = view(flow, p)
    values = {c.id: c.raw_fields['result'] for c in before.candidates}
    groups = build_action_groups(tasks, sheets, before.candidates[0].location.section, before)
    group = next(g for g in groups if g.scope.operation == 'keep_body')
    result, _, _ = apply(flow, p, group.scope)
    after = view(flow, p)[0]
    assert all(c.raw_fields['result'] == values[c.id] and 'result' not in c.corrections for c in after.candidates)
    assert not any(i.code == 'source_comment' for c in after.candidates for i in c.issues)
    undo_batch(flow, p.preview_id, TaskBatchUndo(base_revision=after.base_revision, idempotency_key='undo-comment',
        reason='撤销批注核对', batch_id=result['batch_id']), actor=TECH)
    assert any(i.code == 'source_comment' for c in view(flow, p)[0].candidates for i in c.issues)


def test_v56_multiline_footer_matches_each_timepoint_separately(tmp_path):
    from openpyxl import load_workbook
    from stability_hub.parsing import parse_source
    path = source(tmp_path)
    book = load_workbook(path, rich_text=True)
    book.active['A9'] = '0M CE-SDS 修改记录 FIRST\n3M CE-SDS 调查记录 SECOND'
    book.save(path)
    rows = parse_source(path).observations
    assert len(rows) == 3
    comments = {r.fields['time_point']: r.comment for r in rows}
    assert 'FIRST' in comments['0'] and 'SECOND' not in comments['0']
    assert 'SECOND' in comments['3'] and 'FIRST' not in comments['3']
    assert 'FIRST' not in comments['6'] and 'SECOND' not in comments['6']
