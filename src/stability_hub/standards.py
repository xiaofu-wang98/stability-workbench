"""Explicit reporting segments and standards; no conversion, edition or adoption inference."""
import re

from .models import StandardApplicability

VERSION = 'source-standards-2'
LEGACY_VERSION = 'source-standards-1'
LABELS = {
    'CN': r'中国药典|[（(]\s*中国\s*[）)]|\bChP\b',
    'EP': r'欧洲药典|[（(]\s*欧洲\s*[）)]|\bEP\b|European\s+Pharmacopoeia',
    'US': r'美国药典|[（(]\s*美国\s*[）)]|\bUSP\b|United\s+States\s+Pharmacopeia',
}
MERGED_HAZARD = '结果格跨行或跨时间点合并，须先明确每个检测结果，不能复制计入。'


def labels(text):
    return {name for name, pattern in LABELS.items() if re.search(pattern, text, re.I)}


def legacy_segments(text):
    """Only complete explicitly labelled lines; adjacent bilingual lines stay one segment.

    Unlabelled content, nonadjacent repeated labels and ambiguous combined labels
    are deliberately not split. This distinguishes separately reported values
    from one physical value shared by several acceptance standards.
    """
    groups = []
    for line_index, line in enumerate(str(text).splitlines()):
        if not line.strip():
            continue
        found = labels(line)
        if len(found) != 1:
            return []
        basis = next(iter(found))
        if groups and groups[-1]['basis'] == basis:
            groups[-1]['text'] += '\n' + line
            groups[-1]['end_line'] = line_index+1
        elif any(g['basis'] == basis for g in groups):
            return []
        else:
            groups.append({'basis': basis, 'text': line, 'start_line': line_index, 'end_line': line_index+1})
    return groups


def segments(text, *, version=VERSION):
    source=str(text)
    if version==LEGACY_VERSION or not re.search(r'[;；]\s*(?:ChP|EP|USP)\s*[:：]',source,re.I):
        return legacy_segments(source)
    # A semicolon followed by a new explicit reporting prefix starts a segment.
    # Absolute character anchors retain the exact original same-cell evidence.
    starts=[0]+[m.end() for m in re.finditer(r'[;；]\s*(?=(?:ChP|EP|USP)\s*[:：])',source,re.I)]
    groups=[]
    for start,end in zip(starts,starts[1:]+[len(source)],strict=True):
        value=source[start:end]
        found=labels(value)
        if len(found)!=1:
            return []
        basis=next(iter(found))
        if any(g['basis']==basis for g in groups):
            return []
        groups.append({'basis':basis,'text':value,'start_char':start,'end_char':end})
    return groups


def ambiguous(raw):
    if raw.source_value.get('standards_rule_version')!=VERSION or raw.reporting_basis:
        return False
    text=effective_text(raw,'result')
    return len(labels(text))>1 and not segments(text) and len(re.findall(r'(?:ChP|EP|USP)\s*[:：]',text,re.I))>1


def enabled(raw):
    return raw.source_value.get('standards_rule_version') in {VERSION,LEGACY_VERSION}


def effective_text(raw, name):
    revision = raw.revisions.get(name)
    return revision['active_text'] if revision and not revision['all_deleted'] else raw.fields.get(name, '')


def expand(raw):
    """Source projections keep the full raw fields; a segment is an explicit anchor."""
    if raw.reporting_basis or raw.adapter != 'stability_matrix':
        return [raw]
    groups = segments(effective_text(raw, 'result'),version=raw.source_value.get('standards_rule_version',VERSION))
    if len(groups) < 2:
        return [raw]
    area = raw.result_anchor.get('range') or raw.source_span
    return [raw.model_copy(update={
        'reporting_basis': g['basis'],
        'result_anchor': {**raw.result_anchor, **g, 'source': raw.location.model_dump(mode='json'),
                          'text_basis':'active_revision' if raw.revisions.get('result') else 'original'},
        # Only vertical source merges can be explained by labelled subresults.
        'hazards': tuple(h for h in raw.hazards if not (
            h == MERGED_HAZARD and area and area[1] == area[3])),
    }) for g in groups]


def select(raw, fields):
    """Select each result's own labelled content after revisions/manual corrections."""
    fields = dict(fields)
    if not enabled(raw):
        return fields
    basis = raw.reporting_basis or fields.get("reporting_basis", "")
    if basis:
        for name in ('result','criterion','subresult'):
            found = segments(fields.get(name,''),version=raw.source_value.get('standards_rule_version',VERSION))
            match = next((g for g in found if g['basis'] == basis), None)
            if match:
                fields[name] = match['text']
        fields['reporting_basis'] = basis
    else:
        result_labels = labels(fields.get('result',''))
        criterion_labels = labels(fields.get('criterion',''))
        # A single explicitly reported basis can distinguish physical source rows.
        one = result_labels if len(result_labels) == 1 else criterion_labels if len(criterion_labels) == 1 else set()
        if len(one) == 1:
            fields['reporting_basis'] = next(iter(one))
    return fields


def associations(raw, fields):
    if not enabled(raw):
        return ()
    text = fields.get('criterion','').strip()
    if not text:
        return ()
    origin = raw.evidence.get('criterion', raw.location)
    parts = segments(text,version=raw.source_value.get('standards_rule_version',VERSION))
    if not parts:
        return (StandardApplicability(basis=fields.get('reporting_basis',''), criterion_text=text, source=origin),)
    return tuple(StandardApplicability(basis=g['basis'],criterion_text=g['text'],source=origin) for g in parts)
