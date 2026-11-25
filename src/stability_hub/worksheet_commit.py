"""Draft conflict intent, capability-bound review and atomic worksheet commit."""
import json
from contextlib import nullcontext
from collections import Counter

from .models import digest, StabilityRecord
from .permissions import Actor, Capability, require
from .v5_contracts import SheetPreflight, SheetCommitResult
from .workflow_common import WorkflowError, bump, check_revision, event, project_row, replay, remember, uid
from .workflow_contracts import ConfirmItem, ConfirmRequest, MutationResult, ReviewRequest
from .worksheet_regions import coverage


def decisions(conn,upload_id):
    selected = {}
    undone={json.loads(r[0])['batch_id'] for r in conn.execute("SELECT detail_json FROM workflow_events WHERE subject_id=? AND action='undo_review_batch'",(upload_id,))}
    for row in conn.execute("SELECT detail_json FROM workflow_events WHERE subject_id=? AND action='draft_decision' ORDER BY sequence",(upload_id,)):
        d = json.loads(row[0])
        if d.get('batch_id') in undone:
            continue
        selected[d['candidate_id']] = d
    return selected


def previous(conn,project,candidate,action):
    if not candidate.record:
        return None
    from .identity_associations import versions, adoption_choices, current
    if not conn.in_transaction:
        from .identity_associations import adopted, latest
        lookup = adopted if action in {'correction','keep_old'} else latest
        return lookup(conn, project, candidate.record.key.slot_id)
    # All worksheet preflights in this transaction share the same history.
    # Invalidate after ANY write, including an uncommitted adoption/association.
    key = (project, conn.total_changes)
    cached = getattr(conn, 'previous_history', None)
    if cached is None or cached[0] != key:
        rows = versions(conn, project)
        by_slot, by_id = {}, {}
        for row in rows:
            by_slot.setdefault(row['slot_id'], []).append(row)
            by_id[row['id']] = row
        cached = (key, by_slot, by_id, adoption_choices(conn, project))
        conn.previous_history = cached
    _, by_slot, by_id, choices = cached
    slot = candidate.record.key.slot_id
    if action in {'correction','keep_old'}:
        row = by_id.get(choices.get(slot))
        return row if row and row['slot_id'] == slot and row['state'] in {'confirmed','legacy'} else None
    return current(by_slot.get(slot, []))


def decision_basis(conn,upload,candidate,decision):
    old = previous(conn,upload['project'],candidate,decision['action'])
    body = {'candidate_id':candidate.id,'candidate_revision':candidate.candidate_revision,
            'record':candidate.record.model_dump(mode='json') if candidate.record else None,
            'decision':decision,'previous_version_id':old['id'] if old else None}
    return digest(body),old


def save_decision(flow,upload_id,request,*,actor,_conn=None,_batch_id=None,_candidate=None):
    require(actor,Capability.CONFIRM_FACT)
    if request.action == 'restore':
        from .structural_review import restore
        return restore(flow,upload_id,request,actor=actor)
    with (nullcontext(_conn) if _conn is not None else flow.store.connect()) as conn:
        if _conn is None:
            conn.execute('BEGIN IMMEDIATE')
        upload = flow._upload(conn,upload_id)
        project = upload['project']
        detail=request.model_dump(mode='json')
        if _batch_id is not None:
            detail['batch_id']=_batch_id
        payload = {'draft_decision':upload_id,**detail}
        prior = replay(conn,project,actor,request.idempotency_key,payload)
        if prior:
            return MutationResult.model_validate(prior)
        check_revision(conn,project,request.base_revision)
        c = _candidate if _candidate is not None else next(
            (c for c in flow._preview(conn,upload,actor).candidates if c.id == request.candidate_id),None
        )
        if not c or c.lifecycle != 'draft' or c.candidate_revision != request.candidate_revision:
            raise WorkflowError('candidate_changed','候选已变化或已结束草稿。',409)
        if request.action != 'exclude':
            if c.status in {'needs_review','empty'}:
                raise WorkflowError('candidate_unresolved','先明确本次内容和身份。')
            if c.status == 'new' and request.action != 'initial':
                raise WorkflowError('intent_mismatch','新槽位只能按首次结果确认。')
            if c.status != 'new' and request.action == 'initial':
                raise WorkflowError('intent_mismatch','重复结果须明确来源、保留、更正或复测。')
            old = previous(conn,project,c,request.action)
            if old and request.previous_version_id != old['id']:
                raise WorkflowError('previous_changed','必须明确关联当前历史版本，请刷新核对。',409)
            if request.action == 'repeat_source' and c.status != 'same_value':
                raise WorkflowError('not_equivalent','不同值不能关联为重复来源。')
        else:
            if upload['uploaded_by'] != actor.id:
                require(actor,Capability.REVIEW_DUPLICATE)
            conn.execute("UPDATE workflow_candidates SET lifecycle='withdrawn',revision=revision+1 WHERE id=?",(c.id,))
        event(conn,project,upload_id,'draft_decision',actor,request.reason,detail)
        result = MutationResult(project=project,base_revision=bump(conn,project))
        remember(conn,project,actor,request.idempotency_key,payload,result.model_dump(mode='json'))
        return result


def approvals(conn,upload_id):
    return {d['fingerprint']:d for r in conn.execute(
        "SELECT detail_json FROM workflow_events WHERE subject_id=? AND action='review_draft_decision' ORDER BY sequence",(upload_id,))
        for d in [json.loads(r[0])]}


def preflight(flow,conn,upload,section,actor,*,preview=None,sheets=None):
    sheets = coverage(conn,upload) if sheets is None else sheets
    physical = next((s for s in sheets if s['section'] == section),None)
    p = preview if preview is not None else flow._preview(conn,upload,actor,fresh=True)
    scope = [c for c in p.candidates if c.location.section == section]
    if physical is None and not scope:
        raise WorkflowError('unknown_sheet','该来源中不存在此工作表。')
    ds,approved = decisions(conn,upload['id']),approvals(conn,upload['id'])
    counts = Counter({a:0 for a in ['initial','repeat_source','keep_old','correction','retest','exclude','blank']})
    unresolved,review_required,bases,chosen = [],[],[],[]
    fingerprints,previous_versions,adopted_versions = {},{},{}
    history = sum(c.lifecycle == 'submitted' for c in scope)
    superseded = {r[0] for r in conn.execute("SELECT subject_id FROM workflow_events WHERE project=? AND action IN ('split_source','supersede_projection')",(upload['project'],))}
    for region in (physical or {}).get('regions',[]):
        if region['role'] == 'unclassified':
            unresolved.append('region:' + region['id'])
        if region['role'] == 'result':
            from openpyxl.utils import get_column_letter
            represented = {get_column_letter(c.location.column)+str(c.location.row) for c in scope}
            if any(coord not in represented for coord in region['coordinates']):
                unresolved.append('unprojected_region:'+region['id'])
    receipt = conn.execute("SELECT 1 FROM workflow_events WHERE subject_id=? AND action='receive_source'",(upload['id'],)).fetchone()
    if not receipt:
        unresolved.append('not_received')
    for c in scope:
        bases.append([c.id,c.candidate_revision,c.lifecycle,c.status])
        if c.lifecycle == 'withdrawn':
            if c.id not in superseded:
                counts['exclude'] += 1
            continue
        if c.lifecycle == 'submitted':
            continue
        if not c.received:
            unresolved.append('not_received:' + c.id)
        if c.status == 'empty':
            counts['blank'] += 1
            continue
        if c.status == 'needs_review' or c.issues:
            unresolved.append('candidate:' + c.id)
            continue
        decision = ds.get(c.id)
        latest = previous(conn,upload['project'],c,'repeat_source')
        if latest:
            previous_versions[c.id] = latest['id']
        adopted = previous(conn,upload['project'],c,'correction')
        if adopted:
            adopted_versions[c.id] = adopted['id']
        if decision and decision['candidate_revision'] != c.candidate_revision:
            unresolved.append('stale_decision:' + c.id)
            continue
        if decision is None:
            if c.status == 'new':
                decision = {'candidate_id':c.id,'candidate_revision':c.candidate_revision,'action':'initial',
                            'reason':'整表核对首次结果','previous_version_id':None}
            elif c.status == 'same_value' and latest:
                decision = {'candidate_id':c.id,'candidate_revision':c.candidate_revision,'action':'repeat_source',
                            'reason':'整表核对同值来源，沿用已有结果，不创建复测事实','previous_version_id':latest['id']}
            else:
                unresolved.append('missing_intent:' + c.id)
                continue
        action = decision['action']
        fingerprint,old = decision_basis(conn,upload,c,decision)
        fingerprints[c.id] = fingerprint
        if action == 'initial' and old or action != 'initial' and (not old or decision.get('previous_version_id') != old['id']):
            unresolved.append('previous_changed:' + c.id)
            continue
        review = approved.get(fingerprint)
        if action not in {'initial','repeat_source'} and review is None:
            review_required.append(c.id)
        counts[action] += 1
        chosen.append((c,decision,old,review))
        bases.append(fingerprint)
    prior = conn.execute("SELECT detail_json FROM workflow_events WHERE subject_id=? AND action='confirm_sheet' AND json_extract(detail_json,'$.section')=? ORDER BY sequence DESC LIMIT 1",(upload['id'],section)).fetchone()
    state = 'needs_review' if unresolved else 'needs_approval' if review_required else 'ready'
    if prior and not any(c.lifecycle == 'draft' for c in scope):
        state = 'committed'
        detail = json.loads(prior[0])
        if 'counts' in detail:
            counts = Counter(detail['counts'])
            history = detail.get('historical_count',history)
    elif history:
        state = 'historical_partial'
    fingerprint = digest([section,bases,physical,ds,approved,p.base_revision])
    view = SheetPreflight(section=section,fingerprint=fingerprint,base_revision=p.base_revision,counts=dict(counts),
        decision_fingerprints=fingerprints,previous_versions=previous_versions,adopted_versions=adopted_versions,
        unresolved=tuple(unresolved),review_required=tuple(review_required),can_commit=not unresolved and not review_required,
        state=state,historical_count=history)
    return view,chosen


def review_decision(flow,upload_id,request,*,actor):
    require(actor,Capability.REVIEW_DUPLICATE)
    with flow.store.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        upload = flow._upload(conn,upload_id)
        project = upload['project']
        payload = {'review_draft':upload_id,**request.model_dump(mode='json')}
        prior = replay(conn,project,actor,request.idempotency_key,payload)
        if prior:
            return MutationResult.model_validate(prior)
        check_revision(conn,project,request.base_revision)
        c = next((c for c in flow._preview(conn,upload,actor).candidates if c.id == request.candidate_id),None)
        ds = decisions(conn,upload_id)
        if not c or c.lifecycle != 'draft' or c.id not in ds or c.issues:
            raise WorkflowError('decision_changed','须先保存已解决问题的草稿决定。',409)
        d = ds[c.id]
        fingerprint,old = decision_basis(conn,upload,c,d)
        if fingerprint != request.decision_fingerprint or c.candidate_revision != d['candidate_revision'] or not old or old['id'] != d['previous_version_id']:
            raise WorkflowError('decision_changed','决定或历史依据已变化，请重新复核。',409)
        if d['action'] == 'correction':
            require(actor,Capability.CORRECT_RESULT)
            if request.action != 'adopt':
                raise WorkflowError('correction_requires_adoption','更正须明确正式采用。')
        if request.action == 'adopt':
            require(actor,Capability.ADOPT_RESULT)
        event(conn,project,upload_id,'review_draft_decision',actor,request.reason,
            {'fingerprint':fingerprint,'candidate_id':c.id,'action':request.action,'reviewer':actor.model_dump(mode='json')})
        result = MutationResult(project=project,base_revision=bump(conn,project))
        remember(conn,project,actor,request.idempotency_key,payload,result.model_dump(mode='json'))
        return result


def archive_auxiliary(flow,upload_id,request,*,actor):
    """Explicit human source-only conclusion; never excludes analytical candidates."""
    require(actor,Capability.CONFIRM_FACT)
    with flow.store.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        upload=flow._upload(conn,upload_id)
        project=upload['project']
        payload={'archive_auxiliary':upload_id,**request.model_dump(mode='json')}
        prior=replay(conn,project,actor,request.idempotency_key,payload)
        if prior:
            return SheetCommitResult.model_validate(prior)
        check_revision(conn,project,request.base_revision)
        if any(json.loads(r[0])['location']['section']==request.section for r in conn.execute(
                'SELECT raw_json FROM workflow_candidates WHERE upload_id=?',(upload_id,))):
            raise WorkflowError('contains_candidates','本表有检测候选，须逐项核对后走整表入库，不能归为无结果来源。')
        view,_=preflight(flow,conn,upload,request.section,actor)
        if view.fingerprint!=request.fingerprint:
            raise WorkflowError('stale_scope','辅助来源范围已变化，请刷新原表。',409)
        if view.state=='committed':
            raise WorkflowError('sheet_committed','本表已完成来源核对。',409)
        if not conn.execute("SELECT 1 FROM workflow_events WHERE subject_id=? AND action='receive_source'",(upload_id,)).fetchone():
            raise WorkflowError('not_received','请先接收来源。')
        sheet=next(s for s in coverage(conn,upload) if s['section']==request.section)
        if any(r['role']=='result' for r in sheet['regions']):
            raise WorkflowError('contains_results','覆盖中仍有未投影结果，不能归为无结果辅助来源。')
        event(conn,project,upload_id,'classify_source_sheet',actor,request.reason,
            {'region_ids':[r['id'] for r in sheet['regions'] if r['role']=='unclassified'],
             'section':request.section,'role':'auxiliary','reason':request.reason,'base_revision':request.base_revision,
             'fingerprint':request.fingerprint})
        result=SheetCommitResult(commit_id=uid('source_archive'),project=project,base_revision=bump(conn,project),
            section=request.section,counts=view.counts,historical_count=0,result_version_ids=())
        event(conn,project,upload_id,'confirm_sheet',actor,request.reason,
            {**result.model_dump(mode='json'),'source_only':True})
        remember(conn,project,actor,request.idempotency_key,payload,result.model_dump(mode='json'))
        return result


def commit(flow,upload_id,request,*,actor,_compat=False):
    require(actor,Capability.CONFIRM_FACT)
    from .result_versions import ResultVersions
    with flow.store.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        upload = flow._upload(conn,upload_id)
        project = upload['project']
        payload = {'v5_commit':upload_id,**request.model_dump(mode='json',exclude={'fingerprint'} if _compat else set())}
        prior = replay(conn,project,actor,request.idempotency_key,payload)
        if prior:
            return SheetCommitResult.model_validate(prior)
        check_revision(conn,project,request.base_revision)
        view,chosen = preflight(flow,conn,upload,request.section,actor)
        if request.fingerprint != view.fingerprint:
            raise WorkflowError('stale_scope','工作表预检已变化，请刷新。',409)
        if not view.can_commit:
            raise WorkflowError('sheet_unresolved',f'本表仍有{len(view.unresolved)}个争议格/区域、{len(view.review_required)}项待必要复核。')
        if view.state == 'committed':
            raise WorkflowError('sheet_committed','工作表已完成，请读取原提交结果。',409)
        ids = []
        service = ResultVersions(flow)
        items,submitted = [],[]
        for c,d,old,review in chosen:
            if d['action'] == 'repeat_source':
                # A receipt reference is not a new analytical fact or adoption.
                # Keep the old immutable version and explicitly record the source
                # that was checked, instead of manufacturing a duplicate version.
                if not c.record.equivalent(StabilityRecord.model_validate_json(old['record_json'])):
                    raise WorkflowError('not_equivalent','来源值已变化，不能沿用同值结果。',409)
                conn.execute("UPDATE workflow_candidates SET lifecycle='submitted',revision=revision+1 WHERE id=?",(c.id,))
                event(conn,project,c.id,'repeat_source_reference',actor,d['reason'],
                    {'previous_version_id':old['id'],'upload_id':upload_id,'section':request.section,
                     'candidate_revision':c.candidate_revision,'record':c.record.model_dump(mode='json')})
                continue
            if d['action'] == 'keep_old':
                conn.execute("UPDATE workflow_candidates SET lifecycle='withdrawn',revision=revision+1 WHERE id=?",(c.id,))
                event(conn,project,c.id,'keep_old_result',actor,d['reason'],{'previous_version_id':old['id'],'draft_decision':d})
                continue
            detection = old['detection_id'] if d['action'] in {'correction','repeat_source'} else c.detection_id
            items.append(ConfirmItem(candidate_id=c.id,candidate_revision=c.candidate_revision,
                detection_id=detection,identity_confirmed=True,intent=d['action'],reason=d['reason']))
            submitted.append((c,d,review))
        if items:
            result = service.confirm(upload_id,ConfirmRequest(base_revision=project_row(conn,project)['revision'],
                idempotency_key=request.idempotency_key + ':facts',items=tuple(items)),actor=actor,_conn=conn,_sheet_section=request.section)
            ids.extend(result.result_version_ids)
            for vid,(c,_d,review) in zip(ids,submitted,strict=True):
                if vid in result.pending_confirmation_ids:
                    reviewer = Actor.model_validate(review['reviewer'])
                    service.review(vid,ReviewRequest(base_revision=project_row(conn,project)['revision'],
                        idempotency_key=request.idempotency_key + ':review:' + c.id,action=review['action'],reason='执行已记录的工作表草稿复核'),
                        actor=reviewer,_conn=conn)
        # Blank cells end the receipt without creating analytical facts.
        for c in flow._preview(conn,upload,actor).candidates:
            if c.location.section == request.section and c.lifecycle == 'draft' and c.status == 'empty':
                conn.execute("UPDATE workflow_candidates SET lifecycle='withdrawn',revision=revision+1 WHERE id=?",(c.id,))
        from .business_worksheets import BusinessWorksheets
        from .worksheet_seed import provision_from_results
        sync = provision_from_results(conn, BusinessWorksheets(flow, service), project, ids, actor)
        result = SheetCommitResult(commit_id=uid('sheet_commit'),project=project,base_revision=bump(conn,project),
            section=request.section,counts=view.counts,historical_count=view.historical_count,
            result_version_ids=tuple(ids), worksheet_sync=tuple(sync))
        event(conn,project,upload_id,'confirm_sheet',actor,request.reason,result.model_dump(mode='json'))
        remember(conn,project,actor,request.idempotency_key,payload,result.model_dump(mode='json'))
        return result
