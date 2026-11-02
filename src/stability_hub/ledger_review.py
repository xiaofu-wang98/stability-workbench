"""Receive immutable source facts; unresolved rows never enter adoption or statistics."""
import json
import hashlib
from pathlib import Path

from openpyxl import load_workbook

from .mapping import map_observation
from .models import ImportContext, ProjectConfig, RawObservation
from .normalize import NeedsReview
from .permissions import Capability, require
from .result_versions import ResultVersions
from .workflow_common import WorkflowError, bump, check_revision, event, project_row, remember, replay
from .workflow_contracts import ConfirmItem, ConfirmRequest, MutationResult


class LedgerReview:
    def __init__(self, workflow):
        self.flow = workflow

    @staticmethod
    def row_signature(raw, *, interpreted=None):
        """Exact source-row structure for explicit same-file selection, never fuzzy aliasing."""
        from .models import digest
        from .matrix_fields import interpret_raw
        fields = raw.fields
        identity = [fields.get(k, '') for k in ('test_item', 'category', 'criterion', 'method', 'method_version')]
        interpreted = interpreted if interpreted is not None else interpret_raw(raw, fields) if raw.adapter == 'stability_matrix' else fields
        identity.append(interpreted.get('sample_type', ''))
        # Repeated unnamed subrows must remain separate even when their labels match.
        role = raw.subresult_hint
        if raw.logical_row and raw.evidence.get('test_item'):
            role += ':' + str(raw.logical_row - raw.evidence['test_item'].row)
        return digest([' '.join(v.split()).casefold() for v in identity] + [role])

    def decide(self, upload_id, request, *, actor):
        """One atomic draft-only decision; adoption remains the worksheet command."""
        require(actor, Capability.CONFIRM_FACT)
        with self.flow.store.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            upload = self.flow._upload(conn, upload_id)
            project = upload['project']
            payload = {'ledger_decision': upload_id, **request.model_dump(mode='json')}
            prior = replay(conn, project, actor, request.idempotency_key, payload)
            if prior:
                return MutationResult.model_validate(prior)
            check_revision(conn, project, request.base_revision)
            preview = self.flow._preview(conn, upload, actor)
            views = {c.id: c for c in preview.candidates}
            rows = [self.flow._candidate(conn, cid) for cid in request.candidate_ids]
            for row in rows:
                view = views.get(row['id'])
                if not view or view.lifecycle != 'draft' or not view.received:
                    raise WorkflowError('candidate_changed', '只能处理本次接收中尚未入库的内容，请刷新。', 409)
                if request.action == 'exclude' and upload['uploaded_by'] != actor.id:
                    require(actor, Capability.REVIEW_DUPLICATE)
                if request.action == 'review' and (not view.can_receive or view.issue_code in
                        {'ambiguous_file_slot', 'unsupported_structure', 'formula_result', 'deleted_source'}
                        or view.status == 'different_value'):
                    raise WorkflowError('unresolved_content', '所选内容仍需明确：' + view.detail)
            reason = request.reason.strip() or ('核对原表后排除所选内容' if request.action == 'exclude' else '核对原表，确认所选内容')
            for row in rows:
                if request.action == 'exclude':
                    conn.execute("UPDATE workflow_candidates SET lifecycle='withdrawn',revision=revision+1 WHERE id=?", (row['id'],))
                    event(conn, project, row['id'], 'withdraw_candidate', actor, reason, {'batch_decision': True})
                else:
                    view = views[row['id']]
                    changes = {**json.loads(row['corrections_json'])}
                    if upload['workflow_version'] < 5:
                        changes['result'] = changes.get('result', view.suggested_fields.get('result', view.raw_fields.get('result', '')))
                    conn.execute('UPDATE workflow_candidates SET corrections_json=?,acknowledge_warning=1,revision=revision+1 WHERE id=?',
                                 (json.dumps(changes, ensure_ascii=False), row['id']))
                    event(conn, project, row['id'], 'correct_extraction', actor, reason,
                          {'after': changes, 'acknowledge_source_warning': True, 'revision_confirmed': True})
            result = MutationResult(project=project, base_revision=bump(conn, project))
            remember(conn, project, actor, request.idempotency_key, payload, result.model_dump(mode='json'))
            return result

    def attribution_problem(self, conn, row, upload, *, config=None, context=None,raw=None,interpreted=None):
        raw = raw if raw is not None else RawObservation.model_validate_json(row['raw_json'])
        changes = json.loads(row['corrections_json'])
        from .matrix_fields import interpret_raw
        fields = interpreted if interpreted is not None else interpret_raw(raw, raw.fields) if raw.adapter == 'stability_matrix' else raw.fields
        # Only check attribution here, not the trustworthiness of the result itself.
        probe = raw.model_copy(update={'adapter': 'long_table', 'fields': {**fields, **changes, 'result': '0'},
                                      'hazards': (), 'comment': '', 'formula': '',
                                      'number_format':'General','source_value':{'raw_type':'s'}})
        if config is None:
            config = ProjectConfig.model_validate_json(project_row(conn, upload['project'])['config_json'])
            config = config.model_copy(update={'items': tuple(i.model_copy(update={'unit': ''}) for i in config.items)})
        try:
            context = context or ImportContext.model_validate_json(upload['context_json'])
            context = context.model_copy(update={'defaults': {k:v for k,v in context.defaults.items() if k not in changes}})
            map_observation(probe, context, config, source_facts=True)
            return None
        except NeedsReview as exc:
            return exc

    def receivable(self, conn, row, upload):
        return self.attribution_problem(conn, row, upload) is None

    def receive(self, upload_id, request, *, actor):
        require(actor, Capability.CONFIRM_FACT)
        with self.flow.store.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            upload = self.flow._upload(conn, upload_id)
            project = upload['project']
            payload = {'receive': upload_id, **request.model_dump(mode='json')}
            prior = replay(conn, project, actor, request.idempotency_key, payload)
            if prior:
                return MutationResult.model_validate(prior)
            check_revision(conn, project, request.base_revision)
            normal = []
            worksheet_flow = upload['workflow_version'] >= 4 and Path(upload['filename']).suffix.lower() == '.xlsx'
            # Receipt exposes immutable sources and incomplete drafts. Completeness
            # belongs to the shared worksheet preflight, after corrections.
            preview = None if worksheet_flow else self.flow._preview(conn, upload, actor)
            views = {c.id: c for c in preview.candidates} if preview else {}
            for cid in request.candidate_ids:
                view = views.get(cid)
                row = self.flow._candidate(conn, cid)
                if row['upload_id'] != upload_id or row['lifecycle'] != 'draft':
                    raise WorkflowError('candidate_changed', '只能接收本次上传的当前草稿。', 409)
                if not worksheet_flow and (not view or not self.receivable(conn, row, upload)):
                    raise WorkflowError('unknown_attribution', '请先补充无法识别的基础归属。')
                if not worksheet_flow and view.status == 'new' and not request.defer_confirmation:
                    normal.append(ConfirmItem(candidate_id=cid, candidate_revision=view.candidate_revision,
                        detection_id=view.detection_id, identity_confirmed=True, intent='initial', reason='文件接收确认正常首次结果'))
            if worksheet_flow:
                receipts = conn.execute("SELECT detail_json FROM workflow_events WHERE subject_id=? AND action='receive_source'",(upload_id,)).fetchall()
                received = {cid for r in receipts for cid in json.loads(r[0])['candidate_ids']}
                if receipts and set(request.candidate_ids) <= received:
                    return MutationResult(project=project,base_revision=project_row(conn,project)['revision'])
            # Existing storage already freezes the source/candidates. An audited receipt exposes
            # selected rows in the ledger, without pretending they are adopted result versions.
            event(conn, project, upload_id, 'receive_source', actor, '接收来源，争议在台账内核对',
                  {'candidate_ids': list(request.candidate_ids), 'defer_confirmation': request.defer_confirmation or worksheet_flow})
            if normal:
                result = ResultVersions(self.flow).confirm(upload_id, ConfirmRequest(base_revision=request.base_revision,
                    idempotency_key=request.idempotency_key + ':normal', items=tuple(normal)), actor=actor, _conn=conn)
            else:
                result = MutationResult(project=project, base_revision=bump(conn, project))
            remember(conn, project, actor, request.idempotency_key, payload, result.model_dump(mode='json'))
            return result

    def uploads(self, project, *, actor):
        require(actor, Capability.VIEW)
        with self.flow.store.connect() as conn:
            project_row(conn, project)
            return [dict(r) for r in conn.execute('''SELECT u.id,u.filename,u.uploaded_at FROM workflow_uploads u
                WHERE u.project=? AND EXISTS(SELECT 1 FROM workflow_events e WHERE e.subject_id=u.id
                AND e.action='receive_source') OR u.project=? AND EXISTS(SELECT 1 FROM workflow_versions v JOIN workflow_candidates c ON c.id=v.candidate_id WHERE c.upload_id=u.id) ORDER BY u.sequence DESC''', (project, project))]

    def confirm_sheet(self, upload_id, request, *, actor):
        require(actor, Capability.CONFIRM_FACT)
        with self.flow.store.connect() as check:
            checked_upload = self.flow._upload(check,upload_id)
            is_v5 = checked_upload['workflow_version'] >= 4 and Path(checked_upload['filename']).suffix.lower() == '.xlsx'
        if is_v5:
            from .worksheet_commit import preflight,commit
            from .v5_contracts import SheetCommit
            with self.flow.store.connect() as check:
                view,_ = preflight(self.flow,check,self.flow._upload(check,upload_id),request.section,actor)
            # Compatibility endpoint still rechecks inside the atomic commit.
            result = commit(self.flow,upload_id,SheetCommit(**request.model_dump(),fingerprint=view.fingerprint),actor=actor,_compat=True)
            return MutationResult(project=result.project,base_revision=result.base_revision,result_version_ids=result.result_version_ids)
        with self.flow.store.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            upload = self.flow._upload(conn, upload_id)
            project = upload['project']
            payload = {'confirm_sheet': upload_id, **request.model_dump(mode='json')}
            prior = replay(conn, project, actor, request.idempotency_key, payload)
            if prior:
                return MutationResult.model_validate(prior)
            check_revision(conn, project, request.base_revision)
            preview = self.flow._preview(conn, upload, actor)
            if request.section not in {c.location.section for c in preview.candidates}:
                raise WorkflowError('unknown_sheet', '该来源中不存在此工作表。')
            rows = [c for c in preview.candidates if c.location.section == request.section and c.lifecycle == 'draft']
            if any(not c.received for c in rows):
                raise WorkflowError('not_received', '请先接收本工作表来源。')
            unresolved = [c for c in rows if c.status in {'needs_review', 'different_value'}]
            if unresolved:
                raise WorkflowError('sheet_unresolved', f'本表仍有{len(unresolved)}个争议格，请用上一个/下一个争议定位处理。')
            ready = [c for c in rows if c.status == 'new']
            if ready:
                result = ResultVersions(self.flow).confirm(upload_id, ConfirmRequest(base_revision=request.base_revision,
                    idempotency_key=request.idempotency_key + ':facts', items=tuple(ConfirmItem(candidate_id=c.id,
                    candidate_revision=c.candidate_revision, detection_id=c.detection_id, identity_confirmed=True,
                    intent='initial', reason=request.reason) for c in ready)), actor=actor, _conn=conn, _sheet_section=request.section)
            else:
                result = MutationResult(project=project, base_revision=bump(conn, project))
            event(conn, project, upload_id, 'confirm_sheet', actor, request.reason, {'section': request.section, 'result_ids': list(result.result_version_ids)})
            remember(conn, project, actor, request.idempotency_key, payload, result.model_dump(mode='json'))
            return result

    def resolve_revisions(self, upload_id, request, *, actor):
        from .matrix_fields import interpret_raw
        from .models import ItemDefinition, digest
        from .xlsx_revisions import is_deleted, retained_fields
        require(actor, Capability.CONFIRM_FACT)
        with self.flow.store.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            upload = self.flow._upload(conn, upload_id)
            project = upload['project']
            payload = {'resolve_revisions': upload_id, **request.model_dump(mode='json')}
            if upload['workflow_version'] >= 4 and Path(upload['filename']).suffix.lower()=='.xlsx' and request.remember_alias:
                raise WorkflowError('separate_project_rule','请先保存本文件修订决定，再通过独立项目规则命令保存可复用映射。')
            prior = replay(conn, project, actor, request.idempotency_key, payload)
            if prior:
                return MutationResult.model_validate(prior)
            check_revision(conn, project, request.base_revision)
            rows = []
            selected_rows = [self.flow._candidate(conn, cid) for cid in dict.fromkeys(request.candidate_ids)]
            raws = self.flow._source_rows(conn, selected_rows, upload)
            for row in selected_rows:
                if row['upload_id'] != upload_id or row['lifecycle'] != 'draft':
                    raise WorkflowError('candidate_changed', '只能处理本次上传未采用的来源修订。', 409)
                raw = raws[row['id']]
                if not raw.revisions:
                    raise WorkflowError('no_source_revision', '所选来源没有删除线证据，请使用普通表内修改。')
                reviewed = conn.execute("SELECT detail_json FROM workflow_events WHERE subject_id=? AND action='correct_extraction'", (row['id'],)).fetchall()
                if any(json.loads(e[0]).get('revision_confirmed') for e in reviewed):
                    raise WorkflowError('revision_already_resolved', '修订已核对，请刷新后处理尚未解决的内容。', 409)
                effective = retained_fields(raw)
                fields = interpret_raw(raw, effective) if raw.adapter == 'stability_matrix' else effective
                rows.append((row, raw, fields))
            live = [(r, raw, f) for r, raw, f in rows if not is_deleted(raw)]
            if request.label and len({f.get('test_item') for r, raw, f in live}) > 1:
                raise WorkflowError('different_subresults', '选择内容包含不同子结果，请按同一子结果分组映射。')
            target = None
            config = ProjectConfig.model_validate_json(project_row(conn, project)['config_json'])
            if request.remember_alias and live:
                if not request.label.strip() or not request.method.strip():
                    raise WorkflowError('missing_mapping', '记住映射需要确认保留的项目名和方法。')
                aliases = set()
                for _row, raw, f in live:
                    original = interpret_raw(raw, raw.fields) if raw.adapter == 'stability_matrix' else raw.fields
                    aliases.update([original['test_item'], f['test_item']])
                existing = next((i for i in config.items if i.label == request.label.strip() or i.id == request.label.strip()), None)
                if existing and existing.method != request.method.strip():
                    raise WorkflowError('method_change', '已有项目方法不同，不能用新增映射替换原方法配置。')
                target = existing.id if existing else 'source_' + digest([project, request.label.strip(), request.method.strip()])[:20]
                definition = existing.model_copy(update={'aliases': tuple(sorted(set(existing.aliases) | aliases - {existing.id, existing.label}))}) if existing else ItemDefinition(id=target, label=request.label, method=request.method, aliases=tuple(sorted(aliases - {request.label.strip()})), result_type='qualitative')
                updated = config.model_copy(update={'items': tuple(i for i in config.items if i.id != target) + (definition,)})
                updated = ProjectConfig.model_validate(updated.model_dump())
                conn.execute('UPDATE workflow_projects SET config_json=? WHERE project=?', (updated.model_dump_json(), project))
                event(conn, project, target, 'remember_source_alias', actor, request.reason, {'label': request.label, 'aliases': sorted(aliases)})
            allowed = {'sample_type','batch','presentation','study','study_id','condition','time_point','method','method_version','test_item','result','unit'}
            for row, raw, fields in rows:
                # This compatibility command resolves only revision evidence;
                # comments, formulas and structural hazards remain independent.
                from .review_issues import source_issues
                for issue in source_issues(row['id'],raw):
                    if issue.code == 'source_revision':
                        event(conn,project,row['id'],'resolve_issue',actor,request.reason,
                              {'issue_id':issue.id,'action':'keep_body','fields':{}})
                if is_deleted(raw):
                    conn.execute("UPDATE workflow_candidates SET lifecycle='withdrawn',revision=revision+1 WHERE id=?", (row['id'],))
                    event(conn, project, row['id'], 'withdraw_candidate', actor, request.reason, {'source_deleted': True})
                else:
                    changes = {**{k:v for k,v in fields.items() if k in allowed}, **json.loads(row['corrections_json'])}
                    if upload['workflow_version'] >= 5 and 'result' not in raw.revisions and 'result' not in json.loads(row['corrections_json']):
                        changes.pop('result',None)
                    if request.label:
                        changes['test_item'] = target or request.label.strip()
                    if request.method:
                        changes['method'] = request.method.strip()
                    conn.execute('UPDATE workflow_candidates SET corrections_json=?,acknowledge_warning=1,revision=revision+1 WHERE id=?', (json.dumps(changes,ensure_ascii=False), row['id']))
                    event(conn, project, row['id'], 'correct_extraction', actor, request.reason, {'after': changes, 'revision_confirmed': True})
            result = MutationResult(project=project, base_revision=bump(conn, project))
            remember(conn, project, actor, request.idempotency_key, payload, result.model_dump(mode='json'))
            return result

    def matrix(self, upload_id, *, actor, after_revision=None):
        require(actor, Capability.VIEW)
        cache_key = (upload_id,actor.id,actor.role,actor.capabilities)
        with self.flow._source_cache_lock:
            prior_snapshot = self.flow._review_snapshots.get(cache_key)
        prior_preview = prior_snapshot[0] if prior_snapshot is not None and prior_snapshot[0].base_revision == after_revision else None
        with self.flow.store.connect() as conn:
            conn.execute('BEGIN')
            upload = self.flow._upload(conn, upload_id)
            # A no-op refresh must not rebuild thousands of candidates or touch the DOM.
            # The permission-scoped server snapshot and project revision must both match;
            # the frozen source is still checked on every request.
            revision = project_row(conn, upload['project'])['revision']
            cache_current = prior_snapshot is not None and prior_snapshot[0].base_revision == revision
            if cache_current and (prior_preview is not None or len(prior_snapshot) > 2):
                path = Path(conn.execute('SELECT stored_path FROM documents WHERE sha256=?',
                                         (upload['source_hash'],)).fetchone()[0])
                if hashlib.sha256(path.read_bytes()).hexdigest() != upload['source_hash']:
                    raise WorkflowError('source_changed', '原件校验失败。', 409)
                if prior_preview is not None:
                    return {'not_modified': True, 'base_revision': revision, 'preview_id': upload_id}
                return json.loads(prior_snapshot[2])
            receipts = conn.execute("SELECT detail_json FROM workflow_events WHERE subject_id=? AND action='receive_source'", (upload_id,)).fetchall()
            legacy = conn.execute('SELECT 1 FROM workflow_versions v JOIN workflow_candidates c ON c.id=v.candidate_id WHERE c.upload_id=? LIMIT 1', (upload_id,)).fetchone()
            if not receipts and not legacy:
                raise WorkflowError('not_received', '此上传尚未接收至台账。')
            ids = {cid for r in receipts for cid in json.loads(r[0])['candidate_ids']}
            is_v5 = upload['workflow_version'] >= 4 and Path(upload['filename']).suffix.lower() == '.xlsx'
            if is_v5:
                from .review_tasks import current_review
                preview, physical_sheets, flight_views, tasks, links = current_review(self.flow, conn, upload, actor, _shared=True)
            else:
                preview = self.flow._preview(conn, upload, actor)
            if not receipts and legacy:
                ids = {c.id for c in preview.candidates if c.can_receive or c.lifecycle == 'submitted'}
            selected = [c for c in preview.candidates if c.id in ids]
            v5_progress = None
            worksheet_review = None
            if upload['workflow_version'] >= 4 and Path(upload['filename']).suffix.lower() == '.xlsx':
                from .review_tasks import (saved_changes, checked_sources, worksheet_states, candidate_states, count_resolved_decisions, enrich_sheet_preflight, build_action_groups, build_review_blocks)
                changes = saved_changes(conn, upload, preview)
                checks = checked_sources(conn, upload, preview)
                resolved_counts = count_resolved_decisions(conn, upload, preview, physical_sheets, changes, checks)
                sheet_states = worksheet_states(preview, flight_views, physical_sheets, tasks, changes, resolved_counts)
                flights = []
                v5_progress = []
                updated_flight_views = []
                for view in flight_views:
                    updated_view = enrich_sheet_preflight(conn, upload, preview, view, physical_sheets=physical_sheets, tasks=tasks, resolved_counts=resolved_counts)
                    updated_flight_views.append(updated_view)
                    dumped = updated_view.model_dump(mode='json')
                    flights.append(dumped)
                    scope = [c for c in preview.candidates if c.location.section == view.section]
                    v5_progress.append({
                        **dumped,
                        'name': view.section,
                        'total': len(scope),
                        'remaining': updated_view.pending_decision_count,
                        'issues': updated_view.pending_decision_count,
                        'state': 'confirmed' if view.state == 'committed' else 'ready' if view.can_commit else 'needs_review',
                    })
                flight_views = updated_flight_views
                from .identity_links import frozen_config
                from .worksheet_commit import decisions
                from .review_issues import issue_groups
                worksheet_review = {
                    'sheets': physical_sheets,
                    'preflights': flights,
                    'decisions': decisions(conn, upload_id),
                    'issue_groups': issue_groups(preview),
                    'source_items': [i.model_dump(mode='json') for i in frozen_config(conn, upload).items],
                    'configuration': json.loads(project_row(conn, upload['project'])['config_json']),
                    'candidate_states': candidate_states(preview, tasks, changes),
                    'tasks': [t.model_dump(mode='json') for t in tasks],
                    'blocker_tasks': links,
                    'changes': changes,
                    'checks': checks,
                    'sheet_states': sheet_states,
                    'action_groups': {s['section']: [ag.model_dump(mode='json') for ag in build_action_groups(tasks, physical_sheets, s['section'], preview)] for s in physical_sheets},
                    'review_blocks': {s['section']: [rb.model_dump(mode='json') for rb in build_review_blocks(physical_sheets, s['section'], preview, tasks)] for s in physical_sheets},
                }
                from .review_notes import annotate
                worksheet_review['notes']=annotate(prior_snapshot[1].get('notes',[]) if prior_snapshot else [],preview,tasks)
                worksheet_review['diagnostic_counts']={f.section:{'unresolved':len(f.unresolved),'approval':len(f.review_required),
                    'candidate_diagnostics':sum(c.status=='needs_review' for c in preview.candidates if c.location.section==f.section)} for f in flight_views}
                undone={json.loads(r[0])['batch_id'] for r in conn.execute("SELECT detail_json FROM workflow_events WHERE subject_id=? AND action='undo_review_batch'",(upload_id,))}
                worksheet_review['batches']=[{'batch_id':d['batch_id'],'summary':d['summary'],'undone':d['batch_id'] in undone}
                    for r in conn.execute("SELECT detail_json FROM workflow_events WHERE subject_id=? AND action='review_batch' ORDER BY sequence",(upload_id,)) for d in [json.loads(r[0])]]
            confirmed_sheets = [json.loads(r[0])['section'] for r in conn.execute(
                "SELECT detail_json FROM workflow_events WHERE subject_id=? AND action='confirm_sheet'", (upload_id,))]
            path = Path(conn.execute('SELECT stored_path FROM documents WHERE sha256=?', (upload['source_hash'],)).fetchone()[0])
            states = {r[0]: r[1] for r in conn.execute("SELECT v.candidate_id,s.state FROM workflow_versions v JOIN workflow_version_states s ON s.version_id=v.id WHERE v.candidate_id IN (SELECT id FROM workflow_candidates WHERE upload_id=?)", (upload_id,))}
            adopted = {}
            from .identity_associations import adopted as adopted_result, adoption_choices
            choices = adoption_choices(conn, upload['project'])
            for c in selected if choices else ():
                if c.record and c.record.key.slot_id in choices:
                    r = adopted_result(conn,upload['project'],c.record.key.slot_id)
                    if r:
                        adopted[c.id] = json.loads(r['record_json'])['result']['raw_text']
        if hashlib.sha256(path.read_bytes()).hexdigest() != upload["source_hash"]:
            raise WorkflowError("source_changed", "原件校验失败，不能展示为可信来源。", 409)
        with self.flow._source_cache_lock:
            self.flow._review_snapshots[cache_key] = (preview,worksheet_review)
            self.flow._review_snapshots.move_to_end(cache_key)
            while len(self.flow._review_snapshots)>4:
                self.flow._review_snapshots.popitem(last=False)
        if prior_preview is not None and worksheet_review is not None:
            before = {c.id:c for c in prior_preview.candidates}
            current_ids = {c.id for c in preview.candidates}
            return {'partial_base_revision':after_revision,
                'preview':preview.model_dump(mode='json',exclude={'candidates','parsing_summary'}),
                'candidate_updates':[c.model_dump(mode='json') for c in preview.candidates if before.get(c.id) != c],
                'removed_candidate_ids':sorted(set(before)-current_ids),'candidate_ids':sorted(ids),
                'worksheet_review':{k:v for k,v in worksheet_review.items() if prior_snapshot[1].get(k) != v},
                'adopted':adopted,'states':states,
                'confirmed_sheets':sorted(set(confirmed_sheets)), 'sheet_progress':v5_progress}
        sheets = []
        progress = []
        for title in dict.fromkeys(c.location.section for c in preview.candidates):
            scope = [c for c in preview.candidates if c.location.section == title]
            drafts = [c for c in scope if c.lifecycle == 'draft' and c.status not in {'empty', 'same_value'}]
            issues = sum(c.status in {'needs_review', 'different_value'} for c in drafts)
            progress.append({'name': title, 'total': len(scope), 'remaining': len(drafts), 'issues': issues,
                             'state': 'confirmed' if title in confirmed_sheets and not drafts else
                             'not_received' if any(not c.received for c in drafts) else 'needs_review' if issues else 'ready'})
        legacy_revision_ids = []
        if path.suffix.lower() == '.xlsx':
            book = load_workbook(path, read_only=False, data_only=False, rich_text=True)
            try:
                for title in book.sheetnames:
                    sheet = book[title]
                    if not hasattr(sheet,'_cells'):
                        sheets.append({'name':title,'rows':0,'columns':0,'cells':[],'merges':[],
                                       'notes':[{'text':'图表工作表保留在原件，请查看物理来源区域清单。'}]})
                        continue
                    scope = [c for c in selected if c.location.section == title]
                    from .xlsx_revisions import cell_revision
                    legacy_revision_ids.extend(c.id for c in scope if c.lifecycle == 'submitted' and not c.revisions
                        and any(cell_revision(sheet.cell(loc.row, loc.column)) for loc in c.evidence.values()))
                    from .xlsx_matrix import source_cells
                    physical = source_cells(sheet)
                    summary = next((s for s in preview.parsing_summary if s.get("section") == title), {})
                    blocks = [b for b in summary.get("blocks", []) if "end_row" in b]
                    max_row = max((r for r,c in physical),default=0)
                    max_col = max((c for r,c in physical),default=0)
                    cells = [{'row':r,'column':col,'text':str(cell.value),'revision':cell_revision(cell),'comment':cell.comment.text if cell.comment else ''}
                             for (r,col),cell in sorted(physical.items())]
                    merges = [[m.min_row,m.min_col,m.max_row,m.max_col] for m in sheet.merged_cells.ranges
                              if m.min_row <= max_row and m.min_col <= max_col]
                    notes = []
                    # Footer regions only: no arbitrary worksheet text becomes an assay result.
                    for block in blocks:
                        following = min((c.evidence['batch'].row for c in scope if c.evidence.get('batch')
                                         and c.evidence['batch'].row > block['end_row']), default=sheet.max_row+1)
                        for (r, col), cell in sorted(sheet._cells.items()):
                            if block['end_row'] < r < following and isinstance(cell.value, str) and cell.value.strip() and cell.data_type != 'f':
                                notes.append({'row': r, 'column': col, 'text': cell.value, 'header_row': block['header_row']})
                    sheets.append({'name': title, 'rows': max_row, 'columns': max_col, 'cells': cells, 'merges': merges, 'notes': notes})
            finally:
                book.close()
        if worksheet_review is not None:
            from .review_notes import collect,annotate
            worksheet_review['notes']=annotate(collect(sheets),preview,tasks)
        response = {'preview': preview.model_dump(mode='json'), 'candidate_ids': sorted(ids),
                'worksheet_review':worksheet_review,
                'sheets': sheets, 'adopted': adopted, 'states': states, 'legacy_revision_ids': legacy_revision_ids,
                'confirmed_sheets': sorted(set(confirmed_sheets)), 'sheet_progress': v5_progress if v5_progress is not None else progress}

        # JSON snapshots are immutable and much cheaper to detach than millions of deepcopy calls.
        serialized = json.dumps(response, ensure_ascii=False, separators=(',', ':'))
        with self.flow._source_cache_lock:
            current = self.flow._review_snapshots.get(cache_key)
            if current is not None and current[0].base_revision == preview.base_revision:
                self.flow._review_snapshots[cache_key] = (preview, worksheet_review, serialized)
        return response
