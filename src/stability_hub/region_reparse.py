"""Explicit column-based extraction from an immutable, selected worksheet region."""
import hashlib
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils.cell import column_index_from_string, range_boundaries

from .models import Location, RawObservation, digest
from .numeric_formats import source_value
from .permissions import Capability, require
from .review_issues import source_issues
from .workflow_common import WorkflowError, bump, check_revision, encoded, event, replay, remember, project_row, uid
from .workflow_contracts import MutationResult
from .xlsx_revisions import annotate


def preview(flow, conn, upload_id, scope):
    from .workflow import EDITABLE
    upload = flow._upload(conn, upload_id)
    if set(scope.columns) - EDITABLE or set(scope.common_fields) - EDITABLE or 'result' in scope.common_fields:
        raise WorkflowError('invalid_region_mapping', '仅可映射允许字段；公共身份不能批量指定结果。')
    if set(scope.columns) & set(scope.common_fields) or 'result' not in scope.columns:
        raise WorkflowError('invalid_region_mapping', '结果列必须明确；列字段和公共字段不能重复。')
    if not {'test_item', 'time_point'} <= (set(scope.columns) | set(scope.common_fields)):
        raise WorkflowError('incomplete_structure', '必须明确检测项和时间点的列或公共身份。')
    try:
        left, top, right, bottom = range_boundaries(scope.range_ref.upper())
        cols = {k: column_index_from_string(v.upper()) for k, v in scope.columns.items()}
        valid = (all(isinstance(v, int) for v in (left, top, right, bottom)) and
                 1 <= left <= right <= 16384 and top <= scope.header_row < bottom <= 1048576 and
                 bottom - top <= 20000 and right - left <= 200 and
                 len(set(cols.values())) == len(cols) and all(left <= c <= right for c in cols.values()))
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise WorkflowError('invalid_region_mapping', '需有效矩形、范围内表头和互不重复的列；最多20001行、201列。')
    if conn.execute("SELECT 1 FROM workflow_events WHERE subject_id=? AND action='confirm_sheet' AND json_extract(detail_json,'$.section')=?",
                    (upload_id, scope.section)).fetchone():
        raise WorkflowError('sheet_committed', '工作表已正式提交。', 409)
    path = Path(conn.execute('SELECT stored_path FROM documents WHERE sha256=?', (upload['source_hash'],)).fetchone()[0])
    if path.suffix.lower() != '.xlsx' or hashlib.sha256(path.read_bytes()).hexdigest() != upload['source_hash']:
        raise WorkflowError('source_changed', 'Excel原件校验失败。', 409)
    occupied = set()
    for row in conn.execute("SELECT raw_json FROM workflow_candidates WHERE upload_id=? AND lifecycle IN ('draft','submitted')", (upload_id,)):
        raw = RawObservation.model_validate_json(row[0])
        if raw.location.section == scope.section:
            occupied.add((raw.location.row, raw.location.column))
    book = load_workbook(path, rich_text=True)
    try:
        if scope.section not in [s.title for s in book.worksheets]:
            raise WorkflowError('unknown_sheet', '工作表不存在或不是单元格工作表。')
        sheet = book[scope.section]
        if any(r.min_row <= bottom and r.max_row >= top and r.min_col <= right and r.max_col >= left
               for r in sheet.merged_cells.ranges):
            raise WorkflowError('merged_region', '所选区域含合并格，请先用指定归属或人工拆分流程处理。')
        def location(cell):
            return Location(format='xlsx', section=scope.section, row=cell.row, column=cell.column)
        headers = {}
        for name, col in cols.items():
            cell = sheet.cell(scope.header_row, col)
            if cell.value is None or cell.data_type == 'f' or cell.comment or cell.font.strike:
                raise WorkflowError('uncertain_header', '映射表头须有明确正文且无公式、批注或删除线。')
            headers[name] = {'coordinate': cell.coordinate, 'text': str(cell.value)}
        rows, consumed = [], {v['coordinate'] for v in headers.values()}
        # Sparse physical cells avoid expanding styled rectangles.
        row_numbers = sorted({r for r, c in sheet._cells if scope.header_row < r <= bottom and c == cols['result']})
        for rr in row_numbers:
            result = sheet.cell(rr, cols['result'])
            if result.value is None:
                continue
            if (rr, cols['result']) in occupied:
                raise WorkflowError('source_already_projected', '所选结果格已有有效候选，请编辑或拆分该候选。', 409)
            refs = {name: sheet.cell(rr, col) for name, col in cols.items() if sheet.cell(rr, col).value is not None}
            raw = RawObservation(adapter='manual_projection', location=location(result),
                evidence={k: location(c) for k, c in refs.items()}, fields={k: str(c.value) for k, c in refs.items()},
                source_value=source_value(result), number_format=result.number_format,
                formula=str(result.value) if result.data_type == 'f' else '',
                comment='\n'.join(f'{c.coordinate}: {c.comment.text}' for c in refs.values() if c.comment))
            raw = annotate(raw, sheet)
            consumed.update(c.coordinate for c in refs.values())
            rows.append({'coordinate': result.coordinate, 'raw': raw.model_dump(mode='json'),
                         'common_fields': scope.common_fields,
                         'issues': [i.model_dump(mode='json') for i in source_issues('preview:' + result.coordinate, raw)]})
        if not rows:
            raise WorkflowError('empty_projection', '所选结果列没有非空结果格。')
        remaining = sorted(c.coordinate for (r, col), c in sheet._cells.items()
                           if top <= r <= bottom and left <= col <= right and c.value is not None and c.coordinate not in consumed)
        info = {'base_revision': project_row(conn, upload['project'])['revision'], 'source_hash': upload['source_hash'],
                'scope': scope.model_dump(mode='json'), 'headers': headers, 'rows': rows, 'unmapped_coordinates': remaining}
        return {**info, 'fingerprint': digest(info)}
    finally:
        book.close()


def apply(flow, upload_id, request, *, actor):
    require(actor, Capability.CONFIRM_FACT)
    with flow.store.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        upload = flow._upload(conn, upload_id)
        project = upload['project']
        payload = {'parse_region': upload_id, **request.model_dump(mode='json')}
        prior = replay(conn, project, actor, request.idempotency_key, payload)
        if prior:
            return MutationResult.model_validate(prior)
        check_revision(conn, project, request.base_revision)
        info = preview(flow, conn, upload_id, request.scope)
        if info['fingerprint'] != request.fingerprint:
            raise WorkflowError('stale_scope', '区域预览已变化，整批未执行。', 409)
        ids = []
        for index, row in enumerate(info['rows']):
            cid = 'projection_' + digest([upload_id, request.idempotency_key, index])
            detection = uid('detection')
            conn.execute('INSERT INTO workflow_detections(id,project) VALUES (?,?)', (detection, project))
            conn.execute('INSERT INTO workflow_candidates(id,project,upload_id,raw_json,detection_id,corrections_json) VALUES (?,?,?,?,?,?)',
                         (cid, project, upload_id, encoded(row['raw']), detection, encoded(request.scope.common_fields)))
            event(conn, project, cid, 'manual_projection', actor, request.reason,
                  {'parent_id': None, 'region_fingerprint': info['fingerprint'], 'source': row['raw']['location']})
            ids.append(cid)
        event(conn, project, upload_id, 'parse_region', actor, request.reason,
              {'candidate_ids': ids, 'headers': info['headers'], 'scope': info['scope'], 'fingerprint': info['fingerprint']})
        event(conn, project, upload_id, 'receive_source', actor, request.reason, {'candidate_ids': ids, 'defer_confirmation': True})
        result = MutationResult(project=project, base_revision=bump(conn, project))
        remember(conn, project, actor, request.idempotency_key, payload, result.model_dump(mode='json'))
        return result
