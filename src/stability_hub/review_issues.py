"""Source problems have stable evidence identities and individually scoped decisions."""
import json
import re
from contextlib import nullcontext

from .models import digest
from .permissions import Capability, require
from .v5_contracts import ReviewIssue,IssueDecision
from .workflow_common import WorkflowError, bump, check_revision, event, replay, remember,project_row
from .workflow_contracts import MutationResult


def issue_groups(preview):
    """Exact physical origins within one upload, never text-only clustering."""
    groups = {}
    for candidate in preview.candidates:
        if candidate.lifecycle != 'draft':
            continue
        for issue in candidate.issues:
            if issue.code == 'identity_ambiguity':
                evidence = [candidate.evidence[k] for k in issue.fields if k in candidate.evidence]
            elif issue.code in {'source_revision', 'identity_conflict', 'method_conflict', 'structure'}:
                evidence = list(issue.evidence)
            elif issue.code == 'source_comment':
                evidence = []
                # Actual cell comments retain their own physical address.
                import re
                from openpyxl.utils.cell import coordinate_from_string,column_index_from_string
                for coord in re.findall(r'(?:^|\n)([A-Z]+\d+):',issue.detail):
                    col,row = coordinate_from_string(coord)
                    evidence.append(candidate.location.model_copy(update={'row':row,'column':column_index_from_string(col)}))
                if not evidence:
                    evidence = [loc for k,loc in candidate.evidence.items() if k.startswith('block_note')]
            else:
                evidence = []
            evidence = evidence or [candidate.location]
            origin = sorted({(e.section,e.row,e.column) for e in evidence})
            gid = digest([preview.preview_id,issue.code,issue.fields,origin,issue.detail])
            group = groups.setdefault(gid,{'id':gid,'code':issue.code,'fields':issue.fields,'detail':issue.detail,
                'actions':issue.actions,'evidence':origin,'members':[], 'proposal':issue.proposal, 'revision_kind':issue.revision_kind})
            if 'eligible' in issue.proposal and not issue.proposal['eligible']:
                group['proposal']={**group['proposal'],'eligible':False,'reason':issue.proposal.get('reason','该来源存在需分别核对的成员例外')}
            group['members'].append({'candidate_id':candidate.id,'candidate_revision':candidate.candidate_revision,'issue_id':issue.id})
    return list(groups.values())


def comment_requires_review(text):
    from .source_notes import review_text,informational_reference
    text = review_text(text)
    if informational_reference(text):
        return False
    if not text.strip():
        return False
    # Only clearly descriptive method notes are nonblocking. Unknown semantics
    # remain a question; a word match never edits a reported result.
    body = re.sub(r'^[A-Z]+\d+:\s*','',text.strip())
    return not (re.fullmatch(r'(?:方法说明|Method note)[:：].+',body,re.I) and
                not re.search(r'更正|错误|删除|替代|复测|correct|replace|retest|error',text,re.I))


def source_issues(candidate_id,raw):
    issues = []
    def add(code,detail,fields,actions,*,source_field=None,proposal=None,origin=None,origin_kind=None):
        evidence = tuple(sorted(set([raw.location,*raw.evidence.values()]),key=lambda e:(e.format,e.section,e.row,e.column)))
        if source_field:
            evidence=(raw.evidence.get(source_field,raw.location),)
        if origin:
            evidence=(origin,)
        iid = digest([candidate_id,code,detail,[e.model_dump() for e in evidence],source_field]) if source_field else digest([candidate_id,code,detail,[e.model_dump() for e in evidence]])
        origin_kind=origin_kind or ('source_revision' if code=='source_revision' else 'original_comment' if code=='source_comment' else 'extraction')
        issues.append(ReviewIssue(source_kind=origin_kind,id=iid,code=code,candidate_ids=(candidate_id,),fields=fields,
                                  evidence=evidence,detail=detail,actions=actions,source_field=source_field,
                                  revision_kind=(proposal or {}).get('kind'),proposal=proposal or {}))
    from .standards import ambiguous
    if ambiguous(raw):
        add('source_comment','本格含多套标准的报告前缀，但未能明确各段边界；请在对应原行明确当前标准及报告值，分别报告时使用原格拆分入口。',('result','criterion'),('manual',),origin=raw.location,origin_kind='system_hint')
    for hazard in raw.hazards:
        from .merge_layout import MERGE_HAZARD
        add('structure',hazard,(),('structure','exclude','keep_single') if hazard == MERGE_HAZARD else ('structure','exclude'),
            origin=raw.location)
    for field,revision in raw.revisions.items():
        from .xlsx_revisions import revision_proposal
        proposal=revision_proposal(raw,field,revision)
        actions=('manual',)
        if proposal['eligible']:
            actions=(('exclude','manual') if proposal['operation']=='exclude' else
                     ('keep_active','keep_body','manual') if not revision['all_deleted'] else ('keep_active','manual'))
        add('source_revision',json.dumps(revision,ensure_ascii=False,sort_keys=True),(field,),actions,
            source_field=field,proposal=proposal)
    if raw.formula:
        add('formula_result',raw.formula,('result',),('manual','exclude'))
    from .source_notes import comment_parts
    for detail,origin in comment_parts(raw):
        if comment_requires_review(detail):
            add('source_comment',detail,('result','test_item','method'),('keep_body','manual','exclude'),
                origin=origin,proposal={'eligible':True,'operation':'keep_body','kind':'comment','role':'comment',
                                        'reason':'核对原表批注或表后说明，保留当前正文及数值'})
    from .models import Location
    from .matrix_fields import purity_conflict
    conflict = purity_conflict(raw.fields)
    if conflict:
        origin = raw.evidence.get('criterion', raw.location)
        from openpyxl.utils import get_column_letter
        coord = f'{get_column_letter(origin.column)}{origin.row}'
        detail = (f'{coord} 中英文子结果标签不一致：中文含义为 {conflict["chinese"]}，'
                  f'英文为 {conflict["english"]}。原文：{raw.fields.get("criterion", "")}。'
                  '请按中文含义、英文含义或分别指定解释；系统不根据数值选择。保留原文不能解除身份未决。')
        add('identity_conflict', detail, ('test_item',), ('manual',), origin=origin,
            origin_kind='system_hint', proposal={'identity_options': conflict['options'], 'source_kind': 'system_hint',
                                               'unresolved_identity': raw.fields.get('test_item', '')})
    for hint in raw.system_hints:
        if conflict and any(text in hint['text'] for text in ('子结果标注包含多个含义', '多行子结果未提供可区分的名称')):
            continue  # Replaced by the explicit unresolved identity, including old frozen drafts.
        from .review_guidance import hint_guidance
        guide = hint_guidance(hint)
        add('source_comment',hint['text'],tuple(guide['required_fields']) if guide else ('result','test_item','method'),
            ('manual','exclude') if guide else ('keep_body','manual','exclude'),
            origin=Location.model_validate(hint['source']),origin_kind='system_hint',
            proposal={'guidance': guide} if guide else None)
    from .matrix_fields import method_conflict
    conflict_method = method_conflict(raw.fields.get('method', ''))
    if conflict_method:
        add('method_conflict', f'方法中英文含义不一致：中文“{conflict_method["chinese_label"]}”，英文“{conflict_method["english_label"]}”。'
            f'原文：{raw.fields["method"]}。请只明确方法解释，各结果与时间点不变。', ('method',), ('manual',),
            origin=raw.evidence.get('method', raw.location), origin_kind='system_hint',
            proposal={'identity_options': conflict_method['options'], 'source_kind': 'system_hint',
                      'unresolved_identity': raw.fields['method']})
    for name in ('method','presentation'):
        if name == 'method' and conflict_method:
            continue
        from .matrix_fields import explicit_method, presentation_lines, method_lines
        text = explicit_method(raw.fields.get(name,'')) if name == 'method' else raw.fields.get(name,'')
        lines = method_lines(text) if name == 'method' else presentation_lines(text)
        if len(set(lines)) > 1:
            add('identity_ambiguity',raw.fields[name],(name,),('manual','exclude'))
    from .numeric_formats import interpret_result
    from .normalize import NeedsReview
    try:
        interpret_result(raw,raw.fields.get('result',''),raw.fields.get('unit',''),'qualitative')
    except NeedsReview as exc:
        add(exc.code,str(exc),('result','unit'),('manual','exclude'))
    return tuple(issues)


def undone_batches(conn, candidate_id):
    """Read the project's undo set once per transaction state, including inherited decisions."""
    project = conn.execute('SELECT project FROM workflow_candidates WHERE id=?', (candidate_id,)).fetchone()
    if project is None:
        return set()
    cache = getattr(conn, 'undo_sets', None)
    key = (project[0], conn.total_changes)
    if cache is not None and key in cache:
        return cache[key]
    result = frozenset(json.loads(r[0])['batch_id'] for r in conn.execute(
        "SELECT detail_json FROM workflow_events WHERE project=? AND action='undo_review_batch'", (project[0],)))
    if cache is not None:
        if len(cache) > 8:
            cache.clear()
        cache[key] = result
    return result


def effective_source_decisions(conn,candidate_id):
    """Replay and immutable export snapshots use the same active audit decisions."""
    restored = conn.execute("SELECT COALESCE(MAX(sequence),0) FROM workflow_events WHERE subject_id=? AND action='restore_draft'",(candidate_id,)).fetchone()[0]
    undone = undone_batches(conn, candidate_id)
    return [d for row in conn.execute(
        "SELECT detail_json FROM workflow_events WHERE subject_id=? AND action IN ('resolve_issue','resolve_structure','inherit_source_decision') AND sequence>? ORDER BY sequence",(candidate_id,restored))
        if (d:=json.loads(row[0])).get('batch_id') not in undone]


def resolved_raw(conn,candidate_id,raw,decisions=None,corrections=None):
    """Apply decisions to an interpretation copy; frozen raw_json is never edited."""
    original_issues = {i.id:i for i in source_issues(candidate_id,raw)}
    resolved = {}
    if decisions is None:
        decisions = effective_source_decisions(conn,candidate_id)
    # V5.1 whole-bundle decisions replay only their original frozen bundle.
    legacy_evidence=tuple(sorted(set([raw.location,*raw.evidence.values()]),key=lambda e:(e.format,e.section,e.row,e.column)))
    legacy_id=digest([candidate_id,'source_revision',json.dumps(raw.revisions,ensure_ascii=False,sort_keys=True),[e.model_dump() for e in legacy_evidence]])
    undone=undone_batches(conn, candidate_id) if conn is not None else set()
    decisions=[d for d in decisions if d.get('batch_id') not in undone]
    legacy_comment=digest([candidate_id,'source_comment',raw.comment,[e.model_dump() for e in legacy_evidence]])
    # Replay exact historical structure decisions without rewriting their events.
    legacy_structures = {
        digest([candidate_id, 'structure', i.detail, [e.model_dump() for e in legacy_evidence]]): i.id
        for i in original_issues.values() if i.code == 'structure'
    }
    for d in decisions:
        if d['issue_id'] in legacy_structures:
            resolved[legacy_structures[d['issue_id']]] = d
        if d['issue_id']==legacy_comment:
            resolved.update({i.id:d for i in original_issues.values() if i.code=='source_comment' and not i.proposal.get('guidance')})
        if d['issue_id']==legacy_id:
            resolved.update({i.id:d for i in original_issues.values() if i.code=='source_revision'})
        if d['issue_id'] in original_issues:
            if original_issues[d['issue_id']].proposal.get('guidance'):
                try:
                    validate_selection(original_issues[d['issue_id']], d['action'], d.get('fields', {}))
                except WorkflowError:
                    continue  # Old result-only decisions do not answer a name question.
            resolved[d['issue_id']] = d
    updates = {}
    for iid in resolved:
        issue = original_issues[iid]
        if issue.code == 'source_comment':
            updates['comment'] = '\n'.join(i.detail for i in original_issues.values() if i.code=='source_comment' and i.source_kind!='system_hint' and i.id not in resolved)
            pending_hints={i.detail for i in original_issues.values() if i.source_kind=='system_hint' and i.id not in resolved}
            updates['system_hints'] = tuple(h for h in raw.system_hints if h['text'] in pending_hints)
        elif issue.code == 'formula_result':
            updates['formula'] = ''
        elif issue.code == 'source_revision':
            fields=dict(updates.get('fields',raw.fields))
            revisions=dict(updates.get('revisions',raw.revisions))
            for field in issue.fields:
                rev=revisions.pop(field,None)
                if rev and not rev['all_deleted']:
                    fields[field]=rev['active_text']
                elif rev and field=='criterion':
                    fields[field]=''
            updates.update(fields=fields,revisions=revisions)
        elif issue.code == 'numeric_format':
            updates['number_format'] = 'General'
            updates['source_value'] = {**{k:v for k,v in raw.source_value.items() if k.endswith('_rule_version')}, 'raw_type':'s'}
        elif issue.code == 'structure':
            updates['hazards'] = tuple(h for h in updates.get('hazards',raw.hazards) if h != issue.detail)
    effective = raw.model_copy(update=updates)
    if corrections is None:
        row = conn.execute('SELECT corrections_json FROM workflow_candidates WHERE id=?',(candidate_id,)).fetchone() if conn is not None else None
        corrections = json.loads(row[0]) if row else {}
    if not (set(corrections) & {'method','presentation','result','unit'}) and effective.fields == raw.fields:
        return effective,tuple(i for i in original_issues.values() if i.id not in resolved)
    # Re-derive field problems from the effective interpretation. A correction to
    # one identity field cannot approve comments, formulas, or other identities,
    # while already-resolved issues with unchanged IDs remain resolved.
    field_codes = {'identity_ambiguity','numeric_format','method_conflict'}
    remaining = [i for i in original_issues.values() if i.id not in resolved and i.code not in field_codes]
    probe = effective.model_copy(update={'fields':{**effective.fields,**corrections}})
    if 'result' in corrections:
        probe = probe.model_copy(update={'number_format':'General','source_value':{**{k:v for k,v in effective.source_value.items() if k.endswith('_rule_version')}, 'raw_type':'s'}})
    for i in source_issues(candidate_id, probe):
        if i.code in field_codes and i.id not in resolved:
            remaining.append(i)
    return effective, tuple(remaining)

def validate_selection(issue,action,fields):
    if action not in issue.actions or set(fields)-set(issue.fields):
        raise WorkflowError('issue_scope','此决定不能更改无关字段或使用不支持的动作。')
    if action=='keep_active':
        if not issue.proposal.get('eligible') or issue.proposal.get('operation')!='keep_active' or fields!={issue.source_field:issue.proposal['after']}:
            raise WorkflowError('proposal_changed','须按本处建议及明确范围确认；例外需另行解释。')
    if issue.code=='source_revision' and action=='exclude' and (not issue.proposal.get('eligible') or issue.proposal.get('operation')!='exclude'):
        raise WorkflowError('active_result','不能因标准或有活跃结果的检项删改排除检测结果。')
    if action in {'keep_body','exclude'} and fields:
        raise WorkflowError('issue_scope','保留正文/排除不能同时隐含人工改值，请明确选择人工指定。')
    if action == 'keep_single' and (issue.code != 'structure' or fields):
        raise WorkflowError('issue_scope', '排版核对仅解除本处结构问题，不接受字段改值。')
    if action=='manual' and not fields:
        raise WorkflowError('missing_fields','人工解释须填写明确字段。')
    guide = issue.proposal.get('guidance', {})
    if action == 'manual' and guide:
        if any(not fields.get(k, '').strip() for k in guide['required_fields']):
            raise WorkflowError('missing_fields', '请明确当前问题所需的分项名称，结果和方法无需重填。')
        from .review_guidance import valid_item_name
        if 'test_item' in guide['required_fields'] and not valid_item_name(fields['test_item']):
            raise WorkflowError('identity_unresolved', '请填写具体检测分项；“报告数据”或“未命名”不能作为分项名称。')
    if issue.code == 'identity_conflict' and (action != 'manual' or not fields.get('test_item', '').strip()):
        raise WorkflowError('identity_unresolved', '必须明确子结果身份；保留原文不能解除中英文冲突。')
    if issue.code == 'identity_conflict' and fields.get('test_item', '').strip() == issue.proposal.get('unresolved_identity', '').strip():
        raise WorkflowError('identity_unresolved', '仅重复原检测项名称尚未区分子结果，请明确本行的具体身份。')
    if issue.code == 'method_conflict':
        from .matrix_fields import method_conflict
        chosen = fields.get('method', '').strip()
        if action != 'manual' or not chosen or method_conflict(chosen) or chosen == issue.proposal.get('unresolved_identity', '').strip():
            raise WorkflowError('identity_unresolved', '请明确一个无中英文矛盾的方法解释，数值和其他字段不需要重填。')
    if issue.code in {'formula_result','identity_ambiguity','numeric_format'} and action=='manual' and not set(issue.fields)<=set(fields):
        raise WorkflowError('missing_fields','须明确问题涉及的全部字段。')


def validate_single_layout(flow, conn, upload, raw):
    """Same frozen physical proof for preview, direct commands and atomic batch save."""
    from pathlib import Path
    import hashlib
    from .merge_layout import inventory, can_keep_single
    from .worksheet_regions import coverage
    cache = getattr(conn, 'layout_proofs', None)
    if cache is None:
        cache = {}
        conn.layout_proofs = cache
    key = (upload['id'], upload['source_hash'])
    if key not in cache:
        path = Path(conn.execute('SELECT stored_path FROM documents WHERE sha256=?', (upload['source_hash'],)).fetchone()[0])
        if hashlib.sha256(path.read_bytes()).hexdigest() != upload['source_hash']:
            raise WorkflowError('source_changed', '冻结原件校验失败，不能沿用结构解释。', 409)
        cache[key] = inventory(coverage(conn, upload))
    if not can_keep_single(raw, cache[key]):
        raise WorkflowError('structure_ambiguous', '没有完整的单一结果排版证据；跨时间点或独立逻辑行须明确拆分归属。')


def resolve_issue(flow,upload_id,request,*,actor,_conn=None,_raws=None):
    require(actor,Capability.CONFIRM_FACT)
    with (nullcontext(_conn) if _conn is not None else flow.store.connect()) as conn:
        if _conn is None:
            conn.execute('BEGIN IMMEDIATE')
        upload = flow._upload(conn,upload_id)
        project = upload['project']
        payload = {'resolve_issue':upload_id,**request.model_dump(mode='json')}
        prior = replay(conn,project,actor,request.idempotency_key,payload)
        if prior:
            return MutationResult.model_validate(prior)
        check_revision(conn,project,request.base_revision)
        row = flow._candidate(conn,request.candidate_id)
        if row['upload_id'] != upload_id or row['lifecycle'] != 'draft' or row['revision'] != request.candidate_revision:
            raise WorkflowError('candidate_changed','只能处理本上传尚未入库的当前候选。',409)
        raw = _raws[row['id']] if _raws is not None else flow._source_rows(conn,[row],upload)[row['id']]
        _,issues = resolved_raw(conn,row['id'],raw)
        issue = next((i for i in issues if i.id == request.issue_id),None)
        if not issue or request.action not in issue.actions:
            raise WorkflowError('issue_changed','问题已变化或不支持此动作，请刷新。',409)
        validate_selection(issue,request.action,request.fields)
        if request.action == 'keep_single':
            validate_single_layout(flow, conn, upload, raw)
        changes = {**json.loads(row['corrections_json']),**request.fields}
        conn.execute('UPDATE workflow_candidates SET corrections_json=?,revision=revision+1 WHERE id=?',
                     (json.dumps(changes,ensure_ascii=False),row['id']))
        if request.action == 'exclude':
            if upload['uploaded_by'] != actor.id:
                require(actor,Capability.REVIEW_DUPLICATE)
            conn.execute("UPDATE workflow_candidates SET lifecycle='withdrawn' WHERE id=?",(row['id'],))
        event(conn,project,row['id'],'resolve_issue',actor,request.reason,request.model_dump(mode='json'))
        result = MutationResult(project=project,base_revision=bump(conn,project))
        remember(conn,project,actor,request.idempotency_key,payload,result.model_dump(mode='json'))
        return result


def preview_issue_batch(flow,conn,upload_id,scope):
    upload = flow._upload(conn,upload_id)
    members,seen,fields_by_candidate = [],set(),{}
    rows={s.candidate_id:flow._candidate(conn,s.candidate_id) for s in scope.issues}
    raws=flow._source_rows(conn,list(rows.values()),upload)
    for selected in scope.issues:
        pair = (selected.candidate_id,selected.issue_id)
        if pair in seen:
            raise WorkflowError('duplicate_issue','批量范围包含重复问题。')
        seen.add(pair)
        row = rows[selected.candidate_id]
        if row['upload_id']!=upload_id or row['lifecycle']!='draft' or row['revision']!=selected.candidate_revision:
            raise WorkflowError('candidate_changed','批量成员已变化，整批未执行。',409)
        raw = raws[row['id']]
        _,issues = resolved_raw(conn,row['id'],raw)
        issue = next((i for i in issues if i.id==selected.issue_id),None)
        if not issue or selected.action not in issue.actions or set(selected.fields)-set(issue.fields):
            raise WorkflowError('issue_changed','问题或处置范围已变化，整批未执行。',409)
        validate_selection(issue,selected.action,selected.fields)
        if selected.action == 'keep_single':
            validate_single_layout(flow, conn, upload, raw)
        previous_fields = fields_by_candidate.setdefault(row['id'],{})
        if any(k in previous_fields and previous_fields[k]!=v for k,v in selected.fields.items()):
            raise WorkflowError('conflicting_changes','同一候选的批量决定对同字段给出不同值。')
        previous_fields.update(selected.fields)
        members.append({'selection':selected.model_dump(mode='json'),'issue':issue.model_dump(mode='json'),
                        'section':raw.location.section,'location':raw.location.model_dump(),
                        'before':json.loads(row['corrections_json']),'after_fields':selected.fields,
                        'original_result':raw.fields.get('result','')})
    return {'members':members,'count':len(members),'fingerprint':digest(members)}


def apply_issue_batch(flow,upload_id,request,*,actor):
    require(actor,Capability.CONFIRM_FACT)
    with flow.store.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        project = flow._upload(conn,upload_id)['project']
        payload = {'issue_batch':upload_id,**request.model_dump(mode='json')}
        prior = replay(conn,project,actor,request.idempotency_key,payload)
        if prior:
            return MutationResult.model_validate(prior)
        check_revision(conn,project,request.base_revision)
        view = preview_issue_batch(flow,conn,upload_id,request.scope)
        if view['fingerprint']!=request.fingerprint:
            raise WorkflowError('stale_scope','问题组预览已变化，整批未执行。',409)
        for index,selected in enumerate(request.scope.issues):
            row = flow._candidate(conn,selected.candidate_id)
            resolve_issue(flow,upload_id,IssueDecision(**{**selected.model_dump(),
                'candidate_revision':row['revision'],'base_revision':project_row(conn,project)['revision'],
                'idempotency_key':request.idempotency_key+':'+str(index),'reason':request.reason}),actor=actor,_conn=conn)
        result = MutationResult(project=project,base_revision=project_row(conn,project)['revision'])
        remember(conn,project,actor,request.idempotency_key,payload,result.model_dump(mode='json'))
        return result
