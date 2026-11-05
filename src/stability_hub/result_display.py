"""Read-only presentation of existing result decisions; no source reparse or repair."""
import json
from collections import defaultdict

from .models import StabilityRecord, digest
from .result_queries import selected_rows, cells_from_rows
from .trend_export import analysis_scope, chart_data
from .workflow_common import project_row, check_revision, WorkflowError


def grouped_results(conn, project, filters):
    pairs = selected_rows(conn, project, filters)
    groups = defaultdict(list)
    for display, adopted in pairs:
        record = StabilityRecord.model_validate_json((adopted or display)['record_json'])
        groups[digest(analysis_scope(record))].append((display, adopted))
    return pairs, groups


def display_results(conn, project, filters):
    revision = project_row(conn, project)['revision']
    pairs, groups = grouped_results(conn, project, filters)
    config = json.loads(project_row(conn, project)['config_json'])
    labels = {i['id']: i['label'] for i in config.get('items', [])}
    analyses = []
    for gid, members in groups.items():
        sample = StabilityRecord.model_validate_json((members[0][1] or members[0][0])['record_json'])
        records = [StabilityRecord.model_validate_json((b or a)['record_json']) for a,b in members]
        numeric = sum(record.result.status == 'numeric' for record in records)
        exact = sum(b is not None and b['state'] in {'confirmed','legacy'} and record.result.status == 'numeric'
                    and record.result.comparator == 'eq' for record,(_a,b) in zip(records,members,strict=True))
        analyses.append({'id':gid, 'label':labels.get(sample.key.item,sample.key.item),
                         'key':sample.key.model_dump(mode='json'), 'unit':sample.result.unit,
                         'time_unit':analysis_scope(sample)[9], 'numeric_count':numeric, 'exact_numeric_count':exact,
                         'slot_ids':[a['slot_id'] for a,b in members],
                         'batches':sorted({StabilityRecord.model_validate_json(a['record_json']).key.batch for a,b in members})})
    cells = [c.model_dump(mode='json') for c in cells_from_rows(pairs)]
    for cell,(a,_b) in zip(cells,pairs,strict=True):
        record = StabilityRecord.model_validate_json(a['record_json'])
        cell.update(unit=record.result.unit, standards=[s.model_dump(mode='json') for s in record.standards])
    return {'project':project, 'base_revision':revision, 'total':len(pairs),
            'cells':cells,
            'analysis_groups':analyses, 'item_labels':labels}


def trend_results(conn, project, filters, *, group_id, revision, batches=(), axis=None):
    check_revision(conn, project, revision)
    _, groups = grouped_results(conn, project, filters)
    members = groups.get(group_id)
    if members is None:
        raise WorkflowError('chart_group_changed', '所选检测身份已不在当前范围，请重新选择。', 409)
    available = {StabilityRecord.model_validate_json(a['record_json']).key.batch for a,b in members}
    if not batches or len(set(batches)) != len(batches) or not set(batches) <= available:
        raise WorkflowError('chart_batch_scope', '请明确选择该检测身份下的有效批次。', 422)
    members = [(a,b) for a,b in members if StabilityRecord.model_validate_json(a['record_json']).key.batch in batches]
    snapshot = {'project':project,'revision':revision,'filters':filters.model_dump(),
                'entries':[{'display':dict(a),'adopted':dict(b) if b else None} for a,b in members]}
    snapshot['snapshot_id'] = digest(snapshot)
    return chart_data(snapshot, axis=axis)
