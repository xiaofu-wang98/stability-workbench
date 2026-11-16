"""Field-specific questions and source-bound name suggestions, never automatic decisions."""
import re
from collections import defaultdict


def hint_guidance(hint):
    # Old frozen imports contain only text/source. Recognize the parser's own
    # diagnostic here as well; business prose and actual comments are untouched.
    kind = hint.get('kind')
    text = hint['text']
    if kind == 'missing_subresult' or '多行子结果未提供可区分的名称' in text:
        return {'kind': 'missing_subresult', 'title': '明确本行的检测分项',
                'question': '本行具体报告哪个分项？请确认名称，各时间点保留自己的结果。',
                'required_fields': ['test_item'],
                'help': '“报告数据 / 报告结果”是报告要求，不能作为结果或分项名称。没有可靠候选时，请对照原表填写。'}
    if kind == 'multiple_subresults' or '子结果标注包含多个含义' in text:
        return {'kind': 'multiple_subresults', 'title': '明确本行的检测分项',
                'question': '原标签含多个分项，请明确当前行的完整检测项及分项名称。',
                'required_fields': ['test_item'],
                'help': '如确实需要拆成多个独立结果，请使用“核对原表结构”；不要复制一个结果给多个分项。'}
    return None


def valid_item_name(value):
    """Reject a reporting instruction or unresolved placeholder, not unfamiliar vocabulary."""
    value = value.strip()
    instruction = r'(?:[\s.:：/（）()]*(?:报告(?:数据|结果)|report\s*(?:results?|data)))+[\s.:：/（）()]*'
    return bool(value) and not re.fullmatch(instruction, value, re.I) and not re.search(r'原表分项\d+（未命名）', value)


def source_row_key(candidate):
    origin = candidate.evidence.get('test_item', candidate.location)
    return (candidate.location.section, candidate.logical_row or candidate.location.row,
            origin.row, origin.column, *(candidate.raw_fields.get(k, '') for k in
            ('test_item', 'batch', 'study_id', 'condition')))


def name_suggestions(candidates, keys=None):
    """Only the same physical logical row in this upload can supply a name.

    Recompute from frozen source fields, never from operator corrections. Revised
    labels/results and conflicting labels cannot silently become suggested answers.
    """
    from .matrix_fields import interpret_raw, item_text, purity_conflict, method_conflict
    from .models import RawObservation
    rows = defaultdict(dict)
    for c in candidates:
        if keys is not None and source_row_key(c) not in keys:
            continue
        if set(c.revisions) & {'test_item', 'criterion', 'subresult', 'method', 'result'}:
            continue
        if purity_conflict(c.raw_fields) or method_conflict(c.raw_fields.get('method', '')):
            continue
        raw = RawObservation(fields=c.raw_fields, location=c.location, adapter='stability_matrix')
        fields = interpret_raw(raw, raw.fields)
        name = fields.get('test_item', '')
        parent = item_text(raw.fields.get('test_item', ''), '', fields.get('method', ''))
        if not valid_item_name(name) or name == parent:
            continue
        entries = rows[source_row_key(c)].setdefault(name, [])
        entries.append({'candidate_id': c.id, 'location': c.location.model_dump(mode='json'),
                        'time_point': fields.get('time_point', ''), 'result': raw.fields.get('result', '')})
    return rows


def guide_tasks(tasks, candidates):
    keys = {source_row_key(candidates[m['candidate_id']]) for t in tasks.values()
            if t.proposal.get('guidance', {}).get('kind') == 'missing_subresult' for m in t.members}
    names = name_suggestions(candidates.values(), keys) if keys else {}
    for tid, task in tasks.items():
        guide = task.proposal.get('guidance')
        if not guide or guide['kind'] != 'missing_subresult':
            continue
        members = [candidates[m['candidate_id']] for m in task.members]
        keys = {source_row_key(c) for c in members}
        suggestions = names.get(next(iter(keys)), {}) if len(keys) == 1 else {}
        # Contradictory historical names are evidence to inspect, not quick choices.
        options = [{'value': name, 'evidence': evidence} for name, evidence in suggestions.items()] if len(suggestions) == 1 else []
        tasks[tid] = task.model_copy(update={'proposal': {**task.proposal, 'guidance': {
            **guide, 'name_options': options,
            'suggestion_notice': '候选名称来自同一原表行的其他时间点，需点选确认。' if options else
                '同一原表行没有唯一、明确的分项名称，请人工填写；可展开各时间点原文核对。'}}})
