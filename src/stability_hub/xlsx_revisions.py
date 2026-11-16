"""Excel revision evidence. Original strings are never replaced or discarded."""
import re

from openpyxl.cell.rich_text import CellRichText


def cell_revision(cell):
    value = cell.value
    if value is None:
        return None
    if isinstance(value, CellRichText):
        # A run with its own rPr supplies the font; missing strike there means off,
        # even when the cell's base font is struck. Plain runs use the cell font.
        segments = [{'text': part if isinstance(part, str) else part.text,
                     'struck': bool(cell.font.strike) if isinstance(part, str) else
                     bool(part.font.strike)} for part in value]
    else:
        segments = [{'text': str(value), 'struck': bool(cell.font.strike)}]
    deleted_word = bool(re.fullmatch(r'\s*(?:删|删除|已删除|delete[d]?)\s*', str(value), re.I))
    if not deleted_word and not any(p['struck'] and p['text'].strip() for p in segments):
        return None
    # Keep line separators even when they are struck, to avoid gluing a name to its method.
    active = ''.join(p['text'] if not p['struck'] else '\n' if '\n' in p['text'] else '' for p in segments)
    all_deleted = deleted_word or not active.strip()
    return {'segments': segments, 'active_text': '' if all_deleted else active,
            'all_deleted': all_deleted, 'deleted_word': deleted_word}


def annotate(raw, sheet):
    refs = raw.evidence or {'result': raw.location}
    revisions = {name: revision for name, loc in refs.items()
                 if (revision := cell_revision(sheet.cell(loc.row, loc.column))) is not None}
    return raw.model_copy(update={'revisions': revisions})


def retained_fields(raw):
    return {**raw.fields, **{k: r['active_text'] for k, r in raw.revisions.items() if not r['all_deleted']}}


def is_deleted(raw):
    return any(r['all_deleted'] for k, r in raw.revisions.items()
               if k in {'test_item', 'result'})


def revision_proposal(raw, field, revision):
    """Suggest only; the user confirms the source range before any draft changes."""
    active=revision['active_text'].strip()
    kind='whole' if revision['all_deleted'] else 'partial'
    role='standard' if field=='criterion' else 'result' if field=='result' else 'identity'
    proposal={'kind':kind,'role':role,'field':field,'before':raw.fields.get(field,''),
              'after':active,'removed':''.join(s['text'] for s in revision['segments'] if s['struck']),
              'operation':'keep_active','eligible':True,'reason':'原表含划线修订痕迹，建议采纳未划线的最终有效值'}
    if kind=='whole':
        if field=='result':
            proposal.update(operation='exclude',reason='原表该结果已全划线作废，建议排除不录入，保留历史正式值')
        elif field=='test_item':
            result_deleted=raw.revisions.get('result',{}).get('all_deleted',False)
            proposal.update(operation='exclude',eligible=result_deleted,
                reason='原表该检验项目已全划线作废，建议排除整行' if result_deleted else '检验项目虽划线但仍有检测数值，请人工核对是否保留结果')
        elif field=='criterion':
            proposal.update(reason='质量标准已全划线，建议确认为无质量标准要求并保留检测结果')
        else:
            proposal.update(eligible=False,reason='项目/规格等基础信息已划线，需人工核对填写真实信息')
    elif field=='result':
        from .source_notes import result_lines, can_retain_bilingual_text
        lines=result_lines(active)
        from .standards import enabled, segments
        if len(lines)!=1 and not (enabled(raw) and len(segments(active,version=raw.source_value.get('standards_rule_version')))>=2):
            if not raw.hazards and can_retain_bilingual_text(active):
                proposal.update(retain_complete_text=True,
                    reason='完整保留未划线的中英文内容作为一条文字结果；请核对全部保留文字，不代表系统判定翻译等价。其他含义、身份或批注争议仍须单独处理。')
            else:
                proposal.update(eligible=False,reason='原格包含多行结果、含义冲突或结构疑点，需人工核对确认具体结果')
    return proposal
