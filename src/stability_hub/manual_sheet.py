"""Audited worksheet working copies and explicit source-bound manual draft reconstruction."""
import hashlib
import json
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils.cell import coordinate_to_tuple, range_boundaries

from .models import Location, RawObservation, digest
from .permissions import Capability, require
from .review_issues import source_issues, validate_selection
from .workflow_common import WorkflowError, bump, check_revision, encoded, event, project_row, replay, remember
from .workflow_contracts import MutationResult


def original(conn, upload):
    path = Path(conn.execute('SELECT stored_path FROM documents WHERE sha256=?', (upload['source_hash'],)).fetchone()[0])
    if path.suffix.lower() != '.xlsx' or hashlib.sha256(path.read_bytes()).hexdigest() != upload['source_hash']:
        raise WorkflowError('source_changed', 'Excel冻结原件校验失败。', 409)
    return path


def state(flow, conn, upload_id, section, actor):
    require(actor, Capability.VIEW)
    upload = flow._upload(conn, upload_id)
    path = original(conn, upload)
    book = load_workbook(path, rich_text=True)
    try:
        if section not in book.sheetnames:
            raise WorkflowError('unknown_sheet', '工作表不存在。')
        sheet = book[section]
        cells = {c.coordinate: str(c.value) for c in sheet._cells.values() if c.value is not None}
        merges = tuple(str(m) for m in sheet.merged_cells.ranges)
    finally:
        book.close()
    saved = conn.execute("SELECT detail_json FROM workflow_events WHERE subject_id=? AND action='manual_sheet' ORDER BY sequence DESC", (upload_id,)).fetchall()
    undone = {json.loads(r[0])['edit_id'] for r in conn.execute("SELECT detail_json FROM workflow_events WHERE subject_id=? AND action='undo_manual_sheet'", (upload_id,))}
    edit_id = None
    edits = {}
    for row in saved:
        data = json.loads(row[0])
        if data['scope']['section'] == section and data['edit_id'] not in undone:
            edits = data['scope']['cells']
            cells.update(edits)
            merges = tuple(data['scope']['merges'])
            edit_id = data['edit_id']
            break
    return {'base_revision': project_row(conn, upload['project'])['revision'], 'section': section,
            'cells': cells, 'edits': edits, 'merges': merges, 'edit_id': edit_id,
            'defaults': json.loads(upload['context_json']).get('defaults', {})}


def preview(flow, conn, upload_id, scope, actor):
    require(actor, Capability.CONFIRM_FACT)
    from .workflow import EDITABLE
    upload = flow._upload(conn, upload_id)
    project = upload['project']
    revision = project_row(conn, project)['revision']
    if upload['uploaded_by'] != actor.id and (scope.cells or scope.results or scope.candidate_ids):
        require(actor, Capability.REVIEW_DUPLICATE)
    if conn.execute("SELECT 1 FROM workflow_events WHERE subject_id=? AND action='confirm_sheet' AND json_extract(detail_json,'$.section')=?", (upload_id, scope.section)).fetchone():
        raise WorkflowError('sheet_committed', '已正式提交的工作表不能进入兜底编辑。', 409)
    path = original(conn, upload)
    book = load_workbook(path, rich_text=True)
    try:
        if scope.section not in book.sheetnames:
            raise WorkflowError('unknown_sheet', '工作表不存在。')
        sheet = book[scope.section]
        def cell(ref):
            try:
                if ref != ref.upper() or ref != ref.strip():
                    raise ValueError()
                rr, cc = coordinate_to_tuple(ref)
                if not 1 <= rr <= 1048576 or not 1 <= cc <= 16384:
                    raise ValueError()
            except (ValueError, TypeError, KeyError):
                raise WorkflowError('invalid_coordinate', '须使用有效Excel单元格坐标。') from None
            return sheet.cell(rr, cc)
        for ref, value in scope.cells.items():
            cell(ref)
            if len(value) > 32767:
                raise WorkflowError('cell_too_long', '单元格文字最多32767字符。')
        merged_cells = set()
        for ref in scope.merges:
            try:
                left, top, right, bottom = range_boundaries(ref)
                if not all(isinstance(n, int) for n in (left, top, right, bottom)) or not (1 <= left <= right <= 16384 and 1 <= top <= bottom <= 1048576) or (right-left+1)*(bottom-top+1) > 4000:
                    raise ValueError()
            except (ValueError, TypeError):
                raise WorkflowError('invalid_merge', '合并须为有效矩形，每个矩形最多4000格。') from None
            area = {(rr, cc) for rr in range(top, bottom+1) for cc in range(left, right+1)}
            if area & merged_cells:
                raise WorkflowError('overlapping_merge', '合并范围不能重叠。')
            merged_cells.update(area)
            if len(merged_cells) > 40000:
                raise WorkflowError('merge_limit', '工作副本合并范围总计最多40000格。')
        parents = []
        if len(set(scope.candidate_ids)) != len(scope.candidate_ids):
            raise WorkflowError('duplicate_scope', '来源候选不能重复。')
        for cid in scope.candidate_ids:
            row = flow._candidate(conn, cid)
            raw = RawObservation.model_validate_json(row['raw_json'])
            if row['upload_id'] != upload_id or row['lifecycle'] != 'draft' or raw.location.section != scope.section:
                raise WorkflowError('candidate_changed', '只允许选择本表尚未入库的草稿。', 409)
            if upload['uploaded_by'] != actor.id:
                require(actor, Capability.REVIEW_DUPLICATE)
            parents.append((row, raw))
        projections = []
        seen = set()
        used = set()
        for index, item in enumerate(scope.results):
            fields = item.fields
            if set(fields)-EDITABLE or not all(fields.get(k, '').strip() for k in ('test_item', 'time_point', 'result')):
                raise WorkflowError('incomplete_manual_result', '每条结果必须明确检项、时间点及最终值，只能填写允许字段。')
            if digest(item.model_dump()) in seen:
                raise WorkflowError('duplicate_projection', '不能创建完全相同的逻辑结果。')
            seen.add(digest(item.model_dump()))
            refs = [cell(ref) for ref in item.sources]
            if len(set(item.sources)) != len(item.sources) or any(c.value is None for c in refs):
                raise WorkflowError('empty_source', '每条结果须绑定冻结原件的非空格；新增拆分结果可共享同一原格。')
            used.update(item.sources)
            parent = next(((r, raw) for r, raw in parents if raw.location.row == refs[0].row and raw.location.column == refs[0].column), None)
            loc = Location(format='xlsx', section=scope.section, row=refs[0].row, column=refs[0].column)
            if parent:
                raw = parent[1].model_copy(update={'adapter': 'manual_projection', 'logical_row': None, 'source_span': None})
            else:
                from .numeric_formats import source_value
                raw = RawObservation(adapter='manual_projection', location=loc, fields={'result': str(refs[0].value)},
                    evidence={'result': loc}, source_value=source_value(refs[0]), number_format=refs[0].number_format,
                    formula=str(refs[0].value) if refs[0].data_type == 'f' else '', comment=refs[0].comment.text if refs[0].comment else '')
            extra_fields, extra_evidence = {}, {}
            for n, c in enumerate(refs):
                key = f'manual_source_{n}'
                extra_fields[key] = str(c.value)
                extra_evidence[key] = loc.model_copy(update={'row': c.row, 'column': c.column})
            raw = raw.model_copy(update={'fields': {**raw.fields, **extra_fields}, 'evidence': {**raw.evidence, **extra_evidence}})
            cid = 'manual_' + digest([upload_id, revision, scope.model_dump(mode='json'), index])
            issues = source_issues(cid, raw)
            resolutions = []
            for issue in issues:
                if issue.code == 'structure':
                    resolutions.append({'issue_id': issue.id, 'action': 'structure', 'hazard': issue.detail})
                    continue
                chosen = {k: fields[k] for k in issue.fields if k in fields}
                action = 'manual' if chosen else 'keep_body'
                validate_selection(issue, action, chosen)
                resolutions.append({'issue_id': issue.id, 'action': action, 'fields': chosen})
            projections.append({'id': cid, 'raw': raw.model_dump(mode='json'), 'fields': fields,
                                'sources': item.sources, 'issues': [i.model_dump(mode='json') for i in issues],
                                'resolutions': resolutions})
        # Replacements at an occupied source must explicitly include ALL active draft projections.
        for row in conn.execute("SELECT id,raw_json,lifecycle FROM workflow_candidates WHERE upload_id=?", (upload_id,)):
            raw = RawObservation.model_validate_json(row['raw_json'])
            ref = sheet.cell(raw.location.row, raw.location.column).coordinate
            if raw.location.section == scope.section and ref in used and conn.execute('SELECT 1 FROM workflow_versions WHERE candidate_id=?', (row['id'],)).fetchone():
                raise WorkflowError('fact_not_draft', '来源格已有正式事实，不能通过兜底重建改写。', 409)
            if raw.location.section == scope.section and ref in used and row['lifecycle'] in {'draft', 'submitted'} and row['id'] not in scope.candidate_ids:
                raise WorkflowError('source_already_projected', '来源格已有有效结果，须明确选择全部相关草稿；正式结果不能重建。', 409)
        current = state(flow, conn, upload_id, scope.section, actor)
        info = {'base_revision': revision, 'source_hash': upload['source_hash'], 'scope': scope.model_dump(mode='json'),
                'parents': [{'id': row['id'], 'revision': row['revision'], 'corrections': json.loads(row['corrections_json'])} for row, raw in parents],
                'projections': projections, 'excluded_count': sum(not any(sheet.cell(raw.location.row, raw.location.column).coordinate in p['sources'] for p in projections) for row, raw in parents),
                'merge_changes': {'added': sorted(set(scope.merges)-set(current['merges'])), 'removed': sorted(set(current['merges'])-set(scope.merges))},
                'cell_changes': [{'coordinate': ref, 'before': current['cells'].get(ref, ''), 'after': value} for ref, value in scope.cells.items() if value != current['cells'].get(ref, '')],
                'notice': '工作副本不覆盖原件；入库以明确结果清单为准。保存仅为草稿，其他区域和正式入库检查继续生效。'}
        return {**info, 'fingerprint': digest(info)}
    finally:
        book.close()


def apply(flow, upload_id, command, actor):
    require(actor, Capability.CONFIRM_FACT)
    with flow.store.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        upload = flow._upload(conn, upload_id)
        payload = {'manual_sheet': upload_id, **command.model_dump(mode='json')}
        prior = replay(conn, upload['project'], actor, command.idempotency_key, payload)
        if prior:
            return prior
        check_revision(conn, upload['project'], command.base_revision)
        info = preview(flow, conn, upload_id, command.scope, actor)
        if not command.reviewed or not command.reason.strip() or info['fingerprint'] != command.fingerprint:
            raise WorkflowError('manual_confirmation', '须核对最新精确预览、确认来源问题并填写人工处理依据。', 409)
        children = [p['id'] for p in info['projections']]
        for parent in info['parents']:
            conn.execute("UPDATE workflow_candidates SET lifecycle='withdrawn',revision=revision+1 WHERE id=?", (parent['id'],))
            event(conn, upload['project'], parent['id'], 'split_source', actor, command.reason, {'children': children, 'manual': True})
        for p in info['projections']:
            detection = 'detection_' + p['id']
            conn.execute('INSERT INTO workflow_detections(id,project) VALUES (?,?)', (detection, upload['project']))
            conn.execute('INSERT INTO workflow_candidates(id,project,upload_id,raw_json,detection_id,corrections_json) VALUES (?,?,?,?,?,?)',
                (p['id'], upload['project'], upload_id, encoded(p['raw']), detection, encoded(p['fields'])))
            for resolution in p['resolutions']:
                action = 'resolve_structure' if resolution['action'] == 'structure' else 'resolve_issue'
                event(conn, upload['project'], p['id'], action, actor, command.reason, resolution)
            event(conn, upload['project'], p['id'], 'manual_projection', actor, command.reason,
                  {'source': p['raw']['location'], 'sources': p['sources'], 'fields': p['fields']})
        if children:
            event(conn, upload['project'], upload_id, 'receive_source', actor, command.reason,
                  {'candidate_ids': children, 'defer_confirmation': True})
        edit_id = 'sheetedit_' + digest([upload_id, actor.id, command.idempotency_key])
        event(conn, upload['project'], upload_id, 'manual_sheet', actor, command.reason,
              {'edit_id': edit_id, 'scope': command.scope.model_dump(mode='json'), 'parents': info['parents'],
               'children': children, 'saved_revision': info['base_revision']+1, 'source_hash': info['source_hash'], 'cell_changes': info['cell_changes']})
        result = {'project': upload['project'], 'base_revision': bump(conn, upload['project']), 'edit_id': edit_id, 'candidate_ids': children}
        remember(conn, upload['project'], actor, command.idempotency_key, payload, result)
        return result


def undo(flow, upload_id, command, actor):
    require(actor, Capability.CONFIRM_FACT)
    with flow.store.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        upload = flow._upload(conn, upload_id)
        payload = {'undo_manual_sheet': upload_id, **command.model_dump(mode='json')}
        prior = replay(conn, upload['project'], actor, command.idempotency_key, payload)
        if prior:
            return MutationResult.model_validate(prior)
        check_revision(conn, upload['project'], command.base_revision)
        if not command.reason.strip():
            raise WorkflowError('missing_reason', '撤销须填写依据。')
        row = conn.execute("SELECT detail_json,actor_id FROM workflow_events WHERE subject_id=? AND action='manual_sheet' AND json_extract(detail_json,'$.edit_id')=?", (upload_id, command.edit_id)).fetchone()
        if not row:
            raise WorkflowError('edit_missing', '本来源不存在该编辑批次。')
        if row['actor_id'] != actor.id or upload['uploaded_by'] != actor.id:
            require(actor, Capability.REVIEW_DUPLICATE)
        original(conn, upload)
        data = json.loads(row['detail_json'])
        if command.base_revision != data['saved_revision']:
            raise WorkflowError('later_change', '已有后续保存，请先处理较新的操作；未覆盖任何修改。', 409)
        if conn.execute("SELECT 1 FROM workflow_events WHERE subject_id=? AND action='undo_manual_sheet' AND json_extract(detail_json,'$.edit_id')=?", (upload_id, command.edit_id)).fetchone():
            raise WorkflowError('already_undone', '该编辑已撤销。', 409)
        if any(flow._candidate(conn, cid)['lifecycle'] != 'draft' or flow._candidate(conn, cid)['revision'] != 1 for cid in data['children']):
            raise WorkflowError('later_change', '重建结果已更改或正式入库，不能覆盖后续处理。', 409)
        for parent in data['parents']:
            current = flow._candidate(conn, parent['id'])
            if current['lifecycle'] != 'withdrawn' or current['revision'] != parent['revision']+1:
                raise WorkflowError('later_change', '原草稿已有后续修改。', 409)
        for cid in data['children']:
            conn.execute("UPDATE workflow_candidates SET lifecycle='withdrawn',revision=revision+1 WHERE id=?", (cid,))
            event(conn, upload['project'], cid, 'supersede_projection', actor, command.reason, {'edit_id': command.edit_id, 'parent_id': data['parents'][0]['id'] if data['parents'] else command.edit_id})
        for parent in data['parents']:
            conn.execute("UPDATE workflow_candidates SET lifecycle='draft',revision=revision+1 WHERE id=?", (parent['id'],))
            event(conn, upload['project'], parent['id'], 'undo_manual_parent', actor, command.reason, {'edit_id': command.edit_id})
        event(conn, upload['project'], upload_id, 'undo_manual_sheet', actor, command.reason, {'edit_id': command.edit_id})
        result = MutationResult(project=upload['project'], base_revision=bump(conn, upload['project']))
        remember(conn, upload['project'], actor, command.idempotency_key, payload, result.model_dump(mode='json'))
        return result
