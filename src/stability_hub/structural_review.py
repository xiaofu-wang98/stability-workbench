"""Human structural projections retain common immutable physical evidence."""
import hashlib
import json
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils.cell import coordinate_to_tuple

from .models import Location, RawObservation, digest
from .permissions import Capability, require
from .review_issues import source_issues
from .workflow_common import WorkflowError, bump, check_revision, encoded, event, replay, remember, uid
from .workflow_contracts import MutationResult


def projection_seed(flow, conn, upload_id, candidate_id, actor):
    """Read-only known fields; numeric interpretation uses the same backend rule as intake."""
    from .review_tasks import current_review
    from .review_issues import resolved_raw
    from .numeric_formats import interpret_result
    from .normalize import NeedsReview
    from .models import ImportContext
    upload = flow._upload(conn, upload_id)
    row = flow._candidate(conn, candidate_id)
    if row['upload_id'] != upload_id or row['lifecycle'] != 'draft':
        raise WorkflowError('candidate_changed', '只能核对本上传的未入库来源。', 409)
    preview = current_review(flow, conn, upload, actor, _shared=True)[0]
    c = next(c for c in preview.candidates if c.id == candidate_id)
    context = ImportContext.model_validate_json(upload['context_json'])
    fields = {**context.defaults, **{k: v for k, v in c.interpreted_fields.items() if str(v).strip()}, **c.corrections}
    raw = flow._source_rows(conn, [row], upload)[candidate_id]
    effective, _ = resolved_raw(conn, candidate_id, raw, corrections=c.corrections)
    probe = effective
    if 'result' in c.corrections:
        probe = effective.model_copy(update={'number_format': 'General', 'source_value': {
            **{k: v for k, v in effective.source_value.items() if k.endswith('_rule_version')}, 'raw_type': 's'}})
    warnings = []
    try:
        value = interpret_result(probe, fields.get('result', ''), fields.get('unit', ''), 'qualitative')
        prefix = {'eq': '', 'lt': '<', 'le': '≤', 'gt': '>', 'ge': '≥'}
        fields['result'] = prefix.get(value.comparator, '') + str(value.value) if value.value is not None else value.raw_text
        fields['unit'] = value.unit
    except NeedsReview as exc:
        fields.pop('result', None)
        warnings.append(str(exc))
    from .workflow import EDITABLE
    fields = {k: str(v) for k, v in fields.items() if k in EDITABLE}
    return {'candidate_id': c.id, 'candidate_revision': c.candidate_revision, 'base_revision': preview.base_revision,
            'fields': fields, 'raw_value': raw.fields.get('result', ''), 'number_format': raw.number_format,
            'warnings': warnings, 'missing_required': [k for k in ('test_item', 'time_point', 'result') if not fields.get(k, '').strip()]}


def inherit_decisions(conn, project, parent_id, child_id, raw, child_fields, actor):
    """Carry only already-resolved, unchanged physical issue semantics to a real child."""
    from .review_issues import resolved_raw, effective_source_decisions
    from .matrix_fields import interpret_raw
    row = conn.execute('SELECT corrections_json FROM workflow_candidates WHERE id=?', (parent_id,)).fetchone()
    corrections = json.loads(row[0])
    effective, remaining = resolved_raw(conn, parent_id, raw, corrections=corrections)
    pending = {i.id for i in remaining}
    baseline = {**interpret_raw(effective), **corrections}
    children = {digest([i.code, i.source_field, i.fields, [e.model_dump() for e in i.evidence], i.detail]): i
                for i in source_issues(child_id, raw)}
    parents = effective_source_decisions(conn, parent_id)
    if not parents:
        return
    for old in source_issues(parent_id, raw):
        check_fields = (*old.fields, 'time_point', 'unit', 'reporting_basis') if old.code == 'source_comment' else old.fields
        if old.id in pending or any(
            k in child_fields and child_fields[k] != baseline.get(k, raw.fields.get(k, ''))
            for k in check_fields
        ):
            continue
        key = digest([old.code, old.source_field, old.fields, [e.model_dump() for e in old.evidence], old.detail])
        new = children.get(key)
        if new is None:
            continue
        event(conn, project, child_id, 'inherit_source_decision', actor, '沿用同一物理来源仍有效的父决定，不新增人工改值',
              {'issue_id': new.id, 'action': 'inherited', 'fields': {}, 'inherited_from': parent_id,
               'parent_issue_id': old.id, 'parent_decision_ids': [d['issue_id'] for d in parents]})


def resolve_structure(flow,upload_id,request,*,actor):
    require(actor,Capability.CONFIRM_FACT)
    from .workflow import EDITABLE
    with flow.store.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        upload = flow._upload(conn,upload_id)
        project = upload['project']
        payload = {'structure':upload_id,**request.model_dump(mode='json')}
        prior = replay(conn,project,actor,request.idempotency_key,payload)
        if prior:
            return MutationResult.model_validate(prior)
        check_revision(conn,project,request.base_revision)
        if any(set(f)-EDITABLE or not {'result','test_item','time_point'} <= set(f) for f in request.results):
            raise WorkflowError('incomplete_structure','每条人工结果须明确检测项、时间点和结果，仅可填写允许字段。')
        if len({encoded(f) for f in request.results}) != len(request.results):
            raise WorkflowError('duplicate_projection','逻辑结果不能完全重复。')
        path = Path(conn.execute('SELECT stored_path FROM documents WHERE sha256=?',(upload['source_hash'],)).fetchone()[0])
        if hashlib.sha256(path.read_bytes()).hexdigest() != upload['source_hash']:
            raise WorkflowError('source_changed','原件校验失败。',409)
        if path.suffix.lower() != '.xlsx':
            raise WorkflowError('worksheet_only','此命令仅用于Excel工作表。')
        book = load_workbook(path,rich_text=True)
        try:
            if request.section not in book.sheetnames:
                raise WorkflowError('unknown_sheet','工作表不存在。')
            rr,cc = coordinate_to_tuple(request.coordinate)
            cell = book[request.section]._cells.get((rr,cc))
            if cell is None or cell.value is None:
                raise WorkflowError('unknown_source','人工结果必须绑定原件非空格。')
            row = flow._candidate(conn,request.candidate_id) if request.candidate_id else None
            if row:
                if row['upload_id'] != upload_id or row['lifecycle'] != 'draft' or row['revision'] != request.candidate_revision:
                    raise WorkflowError('candidate_changed','父候选已变化。',409)
                raw = flow._source_rows(conn,[row],upload)[row['id']]
                if raw.location != Location(format='xlsx',section=request.section,row=rr,column=cc) or request.hazard not in raw.hazards:
                    raise WorkflowError('structure_scope','必须指定父候选的来源格及一个确切结构问题。')
                detection = row['detection_id']
                base_fields = json.loads(row['corrections_json'])
            else:
                from .numeric_formats import source_value
                location = Location(format='xlsx',section=request.section,row=rr,column=cc)
                raw = RawObservation(location=location,adapter='manual_projection',evidence={'result':location},
                    fields={'result':str(cell.value)},source_value=source_value(cell),number_format=cell.number_format,
                    formula=str(cell.value) if cell.data_type == 'f' else '',comment=cell.comment.text if cell.comment else '')
                detection = uid('detection')
                conn.execute('INSERT INTO workflow_detections(id,project) VALUES (?,?)',(detection,project))
                base_fields = {}
            child_ids = []
            for index,fields in enumerate(request.results):
                cid = 'projection_' + digest([upload_id,request.idempotency_key,index])
                conn.execute('INSERT INTO workflow_candidates(id,project,upload_id,raw_json,detection_id,corrections_json) VALUES (?,?,?,?,?,?)',
                             (cid,project,upload_id,raw.model_dump_json(),detection,encoded({**base_fields,**fields})))
                if row:
                    inherit_decisions(conn, project, row['id'], cid, raw, {**base_fields, **fields}, actor)
                if request.hazard:
                    issue = next(i for i in source_issues(cid,raw) if i.code == 'structure' and i.detail == request.hazard)
                    event(conn,project,cid,'resolve_structure',actor,request.reason,{'issue_id':issue.id,'hazard':request.hazard})
                event(conn,project,cid,'manual_projection',actor,request.reason,{'parent_id':request.candidate_id,
                    'source':raw.location.model_dump(),'fields':fields,'logical_index':index})
                child_ids.append(cid)
            if row:
                conn.execute("UPDATE workflow_candidates SET lifecycle='withdrawn',revision=revision+1 WHERE id=?",(row['id'],))
                event(conn,project,row['id'],'split_source',actor,request.reason,{'children':child_ids})
            event(conn,project,upload_id,'receive_source',actor,request.reason,{'candidate_ids':child_ids,'defer_confirmation':True})
            result = MutationResult(project=project,base_revision=bump(conn,project))
            remember(conn,project,actor,request.idempotency_key,payload,result.model_dump(mode='json'))
            return result
        finally:
            book.close()


def restore(flow,upload_id,request,*,actor):
    require(actor,Capability.CONFIRM_FACT)
    with flow.store.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        upload = flow._upload(conn,upload_id)
        project = upload['project']
        payload = {'restore':upload_id,**request.model_dump(mode='json')}
        prior = replay(conn,project,actor,request.idempotency_key,payload)
        if prior:
            return MutationResult.model_validate(prior)
        check_revision(conn,project,request.base_revision)
        row = flow._candidate(conn,request.candidate_id)
        if row['upload_id'] != upload_id or row['lifecycle'] != 'withdrawn' or row['revision'] != request.candidate_revision:
            raise WorkflowError('candidate_changed','只能恢复当前未入库的排除候选。',409)
        if conn.execute('SELECT 1 FROM workflow_versions WHERE candidate_id=?',(row['id'],)).fetchone():
            raise WorkflowError('fact_not_draft','已创建过结果版本的候选不能通过草稿恢复。',409)
        if upload['uploaded_by'] != actor.id:
            require(actor,Capability.REVIEW_DUPLICATE)
        raw = RawObservation.model_validate_json(row['raw_json'])
        done = conn.execute("SELECT 1 FROM workflow_events WHERE subject_id=? AND action='confirm_sheet' AND json_extract(detail_json,'$.section')=?",(upload_id,raw.location.section)).fetchone()
        if done:
            raise WorkflowError('sheet_committed','工作表已正式提交，不能通过草稿恢复改写。',409)
        for e in conn.execute("SELECT detail_json FROM workflow_events WHERE subject_id=? AND action='split_source'",(row['id'],)):
            for cid in json.loads(e[0])['children']:
                child = flow._candidate(conn,cid)
                if child['lifecycle'] == 'submitted':
                    raise WorkflowError('projection_final','拆分结果已正式提交，不能撤销。',409)
                conn.execute("UPDATE workflow_candidates SET lifecycle='withdrawn',revision=revision+1 WHERE id=?",(cid,))
                event(conn,project,cid,'supersede_projection',actor,request.reason,{'parent_id':row['id']})
        conn.execute("UPDATE workflow_candidates SET lifecycle='draft',revision=revision+1,acknowledge_warning=0 WHERE id=?",(row['id'],))
        event(conn,project,row['id'],'restore_draft',actor,request.reason,{})
        result = MutationResult(project=project,base_revision=bump(conn,project))
        remember(conn,project,actor,request.idempotency_key,payload,result.model_dump(mode='json'))
        return result
