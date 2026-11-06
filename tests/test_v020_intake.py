"""Unseen protocols, scoped notes and current-vs-historical reporting language."""
import json
from decimal import Decimal
from io import BytesIO

import pytest
from openpyxl import Workbook

from stability_hub.business_notes import note_scope, time_references
from stability_hub.file_rules import reinterpret_preview
from stability_hub.matrix_fields import IDENTITY_VERSION, LEGACY_IDENTITY_VERSION, interpret_raw
from stability_hub.models import ImportContext, Location, ProjectConfig, RawObservation
from stability_hub.normalize import NeedsReview, SEMANTIC_VERSION, normalize_result
from stability_hub.numeric_formats import interpret_result
from stability_hub.parsing import parse_source
from stability_hub.review_issues import resolve_issue
from stability_hub.review_notes import collect
from stability_hub.storage import Store
from stability_hub.v5_contracts import IssueDecision
from stability_hub.workflow import Workflow
from test_v5_regressions import TECH, confirm_initial, preview


def matrix_bytes(value=98, note=None):
    book = Workbook()
    s = book.active
    for cell, text in {
        'A1': 'DP长期稳定性研究实验数据', 'A2': '稳定性试验方案编号',
        'B2': 'STAB-UNSEEN-2026-01', 'C2': '产品批号', 'D2': 'LOT-UNSEEN',
        'A3': '检测项目', 'B3': '方法', 'C3': '质量标准', 'D3': '考察时段（月）',
        'D4': '0M', 'E4': '3M', 'A5': '纯度', 'B5': 'SEC-HPLC',
        'C5': '单体≥95%', 'D5': 99, 'E5': value,
        'A6': '活性', 'B6': 'ELISA', 'C6': '报告结果', 'D6': 100, 'E6': 101,
    }.items():
        s[cell] = text
    s.merge_cells('D3:E3')
    if note:
        s['A8'] = note
    data = BytesIO()
    book.save(data)
    book.close()
    return data.getvalue()


def test_unseen_protocol_remains_same_slot_after_commit_and_update(tmp_path):
    flow = Workflow(Store(tmp_path / 'store'))
    flow.configure(ProjectConfig(project='UNSEEN-742'))
    context = ImportContext(defaults={'condition': '5C', 'presentation': '10mg'})
    first = flow.upload('UNSEEN-742', 'first.xlsx', matrix_bytes(), context, 'first', actor=TECH)
    assert {c.record.key.study_id for c in first.candidates} == {'STAB-UNSEEN-2026-01'}
    confirm_initial(flow, first)
    with flow.store.read() as conn:
        frozen = [tuple(r) for r in conn.execute('SELECT id,record_json,source_json FROM workflow_versions ORDER BY id')]
    next_upload = flow.upload('UNSEEN-742', 'next.xlsx', matrix_bytes(97), context, 'next', actor=TECH)
    assert [c.status for c in next_upload.candidates] == ['same_value', 'different_value', 'same_value', 'same_value']
    assert [c.record.key.slot_id for c in next_upload.candidates] == [c.record.key.slot_id for c in first.candidates]
    with flow.store.read() as conn:
        assert [tuple(r) for r in conn.execute('SELECT id,record_json,source_json FROM workflow_versions ORDER BY id')] == frozen


def test_old_identity_version_does_not_silently_reinterpret_protocol():
    r = RawObservation(adapter='stability_matrix', fields={'study_id': 'OTHER-2026', 'study_title': '长期',
        'sample_type': 'DP', 'time_point': '3M'}, location=Location(format='xlsx', section='Any', row=1, column=1))
    old = r.model_copy(update={'source_value': {'identity_rule_version': LEGACY_IDENTITY_VERSION}})
    new = r.model_copy(update={'source_value': {'identity_rule_version': IDENTITY_VERSION}})
    assert interpret_raw(old)['study_id'] == ''
    assert interpret_raw(new)['study_id'] == 'OTHER-2026'
    assert interpret_raw(old)['purpose'] == 'unknown'


@pytest.mark.parametrize('value', ['原结果不汇报，现报告为98.5',
    'not reported previously; final result 98.5', '结果应汇报，不是不汇报'])
def test_reporting_prose_requires_explicit_result_and_preserves_old_semantics(value):
    with pytest.raises(NeedsReview, match='原文同时'):
        normalize_result(value, '%', 'qualitative')
    r = RawObservation(fields={'result': value}, location=Location(format='xlsx', section='Any', row=1, column=1))
    assert interpret_result(r, value, '%', 'qualitative').status == 'not_reported'
    with pytest.raises(NeedsReview):
        interpret_result(r.model_copy(update={'source_value': {'semantic_rule_version': SEMANTIC_VERSION}}),
                         value, '%', 'qualitative')


def test_reporting_manual_resolution_reaches_official_value_without_losing_original(tmp_path, xlsx, context):
    text = '原结果不汇报，现报告为98.5'
    path = xlsx([['Purity', '0M', text, '%']])
    original = path.read_bytes()
    flow, p = preview(tmp_path, path, context)
    c = p.candidates[0]
    assert c.status == 'needs_review'
    issue = next(i for i in c.issues if i.code == 'reporting_semantics')
    from stability_hub.worksheet_commit import preflight
    with flow.store.connect() as conn:
        check, _ = preflight(flow, conn, flow._upload(conn, p.preview_id), 'Results', TECH)
        assert not check.can_commit
    current = flow.preview(p.preview_id, actor=TECH)
    resolve_issue(flow, p.preview_id, IssueDecision(base_revision=current.base_revision,
        idempotency_key='explain', reason='核对原件，本次结果98.5，保留历史说明', candidate_id=c.id,
        candidate_revision=c.candidate_revision, issue_id=issue.id, action='manual', fields={'result': '98.5'}), actor=TECH)
    p = flow.preview(p.preview_id, actor=TECH)
    assert p.candidates[0].record.result.value == Decimal('98.5')
    confirm_initial(flow, p)
    assert flow.results('PROJECT-P', actor=TECH).cells[0].adopted.text == '98.5%'
    assert path.read_bytes() == original
    with flow.store.read() as conn:
        source = json.loads(conn.execute('SELECT source_json FROM workflow_versions').fetchone()[0])[0]
        assert source['raw_json']['fields']['result'] == text
        assert source['corrections']['result'] == '98.5'


def test_old_draft_stays_frozen_until_explicit_reinterpret_preview(tmp_path, xlsx, context):
    path = xlsx([['Purity', '0M', '原结果不汇报，现报告为98.5', '%']])
    flow, p = preview(tmp_path, path, context)
    with flow.store.connect() as conn:
        row = conn.execute('SELECT id,raw_json FROM workflow_candidates').fetchone()
        raw = json.loads(row['raw_json'])
        raw['source_value'].pop('semantic_rule_version')
        conn.execute('UPDATE workflow_candidates SET raw_json=? WHERE id=?', (json.dumps(raw), row['id']))
    flow = Workflow(flow.store)
    assert flow.preview(p.preview_id, actor=TECH).candidates[0].record.result.status == 'not_reported'
    with flow.store.connect() as conn:
        info = reinterpret_preview(flow, conn, flow._upload(conn, p.preview_id), TECH)
        assert info['semantic_changes']
        assert info['semantic_rule_version'] == SEMANTIC_VERSION
    assert flow.preview(p.preview_id, actor=TECH).candidates[0].record.result.status == 'not_reported'


@pytest.mark.parametrize('note,expected', [
    ('3个月 SEC结果更正为98.5', [('纯度', '3M')]),
    ('第三个月 SEC结果更正为98.5', [('纯度', '3M')]),
    ('3M ELISA结果更正为98.5', [('活性', '3M')]),
    ('3M ELISA retest result replaces previous result', [('活性', '3M')]),
    ('3M ELISA桥接至新方法结果', [('活性', '3M')]),
    ('3M XYZ结果更正为98.5', []),
    ('3M 电导率结果更正为98.5', []),
    ('后续月份 ELISA结果更正', []),
    ('批号 OTHER 的3M结果更正', []),
    ('AR-C-P-2026003', []),
    ('6~3M SEC结果更正', []),
    ('0.5M SEC结果更正', []),
    ('3M至90D SEC结果更正', []),
    ('3M ELISA结果更正，详见AR-SEC-M0-2026', [('活性', '3M')]),
    ('SEC结果更正，详见AR-M3-2026', [('纯度', '0M'), ('纯度', '3M')]),
])
def test_note_scope_is_shared_and_never_expands_unknown_restrictions(tmp_path, note, expected):
    path = tmp_path / 'source.xlsx'
    path.write_bytes(matrix_bytes(note=note))
    parsed = parse_source(path)
    assert [(r.fields['test_item'], r.fields['time_point']) for r in parsed.observations if r.comment] == expected
    notes = collect([{'name': 'Any', 'cells': [{'text': note, 'row': 8, 'column': 1}]}])
    assert notes, 'Unknown scope remains discoverable with the original text'
    assert notes[0]['original'] == note


@pytest.mark.parametrize('text,points', [
    ('3M至6M', {(3, 'M'), (4, 'M'), (5, 'M'), (6, 'M')}),
    ('3~6个月', {(3, 'M'), (4, 'M'), (5, 'M'), (6, 'M')}),
    ('3、6M', {(3, 'M'), (6, 'M')}),
    ('D90', {(90, 'D')}), ('90 days', {(90, 'D')}), ('第三个月', {(3, 'M')}),
])
def test_note_time_units_sets_and_ranges(text, points):
    assert time_references(text) == (points, False)


def test_day_note_never_targets_month_result():
    def raw(token):
        return RawObservation(fields={'time_point': token, 'test_item': '活性', 'method': 'ELISA'},
            location=Location(format='xlsx', section='Any', row=1, column=1))
    assert note_scope('D90 ELISA结果更正', [raw('90D'), raw('90M')]) == {0}


def test_purity_note_requires_subresult_even_when_method_matches():
    def raw(criterion):
        return RawObservation(fields={'time_point': '3M', 'test_item': '纯度', 'method': 'SEC-HPLC', 'criterion': criterion},
            location=Location(format='xlsx', section='Any', row=1, column=1))
    assert note_scope('3M SEC HMW结果更正', [raw('单体Monomer'), raw('聚合体HMW')]) == {1}
