"""Explicit draft projections through the existing rule-upgrade transaction."""
import json
import re
from pathlib import Path
import hashlib

from .models import digest
from .standards import VERSION, expand, labels
from .workflow_common import WorkflowError, event

SPLIT_HAZARD = '原格分别报告多套标准的结果，请预览各标准及对应结果后分别保留。'


def source_evidence(flow, conn, upload):
    path = Path(conn.execute('SELECT stored_path FROM documents WHERE sha256=?', (upload['source_hash'],)).fetchone()[0])
    if hashlib.sha256(path.read_bytes()).hexdigest() != upload['source_hash']:
        raise WorkflowError('source_changed','原件校验失败，不能升级标准来源解释。',409)
    cache = getattr(flow, '_standard_sources', {})
    key = (str(path), upload['source_hash'])
    if key not in cache:
        from .parsing import parse_source
        index = {}
        for raw in parse_source(path,allow_partial=True).observations:
            index.setdefault(raw.location, raw)
        cache[key] = index
        if len(cache) > 4:
            cache.pop(next(iter(cache)))
        flow._standard_sources = cache
    return cache[key]


def upgrade_raw(raw, current):
    """Reconcile only old parser hints, with frozen original comments as counterevidence."""
    if current is None:
        return raw
    from .source_notes import comment_parts
    hints = list(raw.system_hints)
    original = []
    for text, origin in comment_parts(raw):
        generated = re.fullmatch(r'[A-Z]+\d+: (?:多行子结果未提供可区分的名称|子结果标注包含多个含义[^\n]*)，?请核对[^\n]*',text)
        if generated and text not in current.comment:
            unresolved = next((h for h in current.system_hints if h['text'] == text),None)
            if unresolved:
                hints.append(unresolved)
            elif not labels(raw.fields.get('criterion','')):
                hints.append({'text':text,'source':origin.model_dump(mode='json')})
        else:
            original.append(text)
    # Physical range is evidence, not a new interpretation of its value.
    range_hint = current.result_anchor.get('range')
    anchor = raw.result_anchor or ({'range':range_hint} if range_hint else {})
    upgraded = raw.model_copy(update={'comment':'\n'.join(original), 'system_hints':tuple(hints),
        'result_anchor':anchor})
    if len(expand(upgraded)) > 1 and SPLIT_HAZARD not in upgraded.hazards:
        upgraded = upgraded.model_copy(update={'hazards':(*upgraded.hazards,SPLIT_HAZARD)})
    return upgraded


def project(flow, conn, upload, actor, *, reason):
    rows = conn.execute("SELECT * FROM workflow_candidates WHERE upload_id=? AND lifecycle='draft' ORDER BY rowid",(upload['id'],)).fetchall()
    raws = flow._source_rows(conn, rows, upload)
    changes, exceptions = [], []
    prior_decisions={json.loads(e[0]).get('candidate_id') for e in conn.execute("SELECT detail_json FROM workflow_events WHERE subject_id=? AND action='draft_decision'",(upload['id'],))}
    for row in rows:
        raw = raws[row['id']]
        children = expand(raw)
        if len(children) < 2:
            continue
        manual = row['id'] in prior_decisions or json.loads(row['corrections_json']) or conn.execute(
            "SELECT 1 FROM workflow_events WHERE subject_id=? AND action IN ('resolve_issue','resolve_structure','correct_extraction','restore_draft','bulk_draft_edit','map_item') LIMIT 1",(row['id'],)).fetchone()
        if manual:
            exceptions.append({'candidate_id':row['id'],'location':raw.location.model_dump(mode='json'),
                'reason':'已有人工草稿决定，保留现状；请在本格核对分别报告的标准范围，不覆盖人工内容'})
            continue
        ids = ['standard_' + digest([row['id'],VERSION,c.reporting_basis,c.result_anchor]) for c in children]
        if any(conn.execute('SELECT 1 FROM workflow_candidates WHERE id=?',(cid,)).fetchone() for cid in ids):
            exceptions.append({'candidate_id':row['id'],'reason':'本格已有标准投影，保留现状请核对撤销/恢复记录'})
            continue
        for cid,child in zip(ids,children,strict=True):
            child = child.model_copy(update={'hazards':tuple(h for h in child.hazards if h != SPLIT_HAZARD)})
            conn.execute('INSERT INTO workflow_candidates(id,project,upload_id,raw_json,detection_id,corrections_json) VALUES (?,?,?,?,?,?)',
                (cid,upload['project'],upload['id'],child.model_dump_json(),row['detection_id'],'{}'))
            event(conn,upload['project'],cid,'standard_projection',actor,reason,
                  {'parent_id':row['id'],'basis':child.reporting_basis,'anchor':child.result_anchor})
        else:
            conn.execute("UPDATE workflow_candidates SET lifecycle='withdrawn',revision=revision+1 WHERE id=?",(row['id'],))
            event(conn,upload['project'],row['id'],'split_source',actor,reason,{'children':ids,'rule':VERSION})
            received = {cid for e in conn.execute("SELECT detail_json FROM workflow_events WHERE subject_id=? AND action='receive_source'",(upload['id'],)) for cid in json.loads(e[0]).get('candidate_ids',[])}
            if row['id'] in received:
                event(conn,upload['project'],upload['id'],'receive_source',actor,reason,{'candidate_ids':ids,'defer_confirmation':True})
            changes.append({'parent_id':row['id'],'location':raw.location.model_dump(mode='json'),
                'children':[{'id':cid,'basis':child.reporting_basis,'anchor':child.result_anchor} for cid,child in zip(ids,children,strict=True)]})
    return changes, exceptions
