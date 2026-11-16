"""One filtered evidence snapshot for table, workbook and image exports."""
import json

from .identity_links import frozen_config,linked_config
from .matrix_fields import interpret_raw
from .models import ImportContext,digest
from .mapping import map_corrected
from .normalize import NeedsReview
from .result_queries import selected_rows
from .worksheet_regions import coverage
from .workflow_common import project_row
from .models import ProjectConfig, StabilityRecord


def create_snapshot(flow,conn,request):
    pairs = selected_rows(conn,request.project,request.filters)
    if request.slot_ids:
        selected = set(request.slot_ids)
        if len(selected) != len(request.slot_ids) or not selected <= {a['slot_id'] for a,b in pairs}:
            from .workflow_common import WorkflowError
            raise WorkflowError('result_scope_changed', '所选结果不属于当前范围，请重新读取后导出。', 409)
        pairs = [(a,b) for a,b in pairs if a['slot_id'] in selected]
    drafts = []
    filters = request.filters.model_dump(exclude_none=True)
    selected_scopes = [dict(s) for s in {
        tuple(sorted((k,v) for k,v in StabilityRecord.model_validate_json(a['record_json']).key.model_dump().items()
                     if k not in {'project','month','day'})) for a,b in pairs}] if request.slot_ids else []
    selected_dimensions = {k for s in selected_scopes for k in s}
    scope_matches = {}
    for row in conn.execute("""SELECT c.*,u.filename FROM workflow_candidates c JOIN workflow_uploads u ON u.id=c.upload_id
                            WHERE c.project=? AND c.lifecycle='draft' ORDER BY c.id""",(request.project,)):
        upload = flow._upload(conn,row['upload_id'])
        raw = flow._source_rows(conn, [row], upload)[row['id']]
        try:
            record = flow._mapped(conn,row,upload,config=frozen_config(conn,upload))
            fields = record.key.model_dump()
        except NeedsReview:
            context = ImportContext.model_validate_json(upload['context_json'])
            changes = json.loads(row['corrections_json'])
            probe = raw.model_copy(update={'hazards':(),'revisions':{},'comment':'','formula':'',
                                          'number_format':'General','source_value':{**{k:v for k,v in raw.source_value.items() if k.endswith('_rule_version')}, 'raw_type':'s'}})
            try:
                record = map_corrected(probe,context,frozen_config(conn,upload),{**changes,'result':'0'},source_facts=True)
                fields = record.key.model_dump()
            except NeedsReview:
                extracted = interpret_raw(raw, raw.fields) if raw.adapter == 'stability_matrix' else raw.fields
                fields = {**context.defaults,**{k:v for k,v in extracted.items() if v.strip()},**changes}
                fields.pop('item',None)  # unresolved names cannot be excluded by guessing a canonical ID
        fields = {k:v for k,v in fields.items() if v != '' and not (
            isinstance(v,str) and ('\n' in v or v.startswith('原文未注明') or v in {'未注明','not_reported'})
            and filters.get(k)!=v)}
        if fields.get('sample_type') not in {'DS','DP','placebo'}:
            fields.pop('sample_type',None)
        if fields.get('study') not in {'release','long_term','accelerated','stress'}:
            fields.pop('study',None)
        configured_ids = {i.id for i in linked_config(conn,frozen_config(conn,upload)).items}
        if 'item' in filters and fields.get('item')!=filters['item'] and fields.get('item') not in configured_ids:
            fields.pop('item',None)
        if any(k in fields and fields[k] != v for k,v in filters.items()):
            continue
        unknown = [k for k in filters if k not in fields]
        if request.slot_ids:
            signature = tuple(sorted((k,v) for k,v in fields.items() if k in selected_dimensions))
            if signature not in scope_matches:
                matching = [s for s in selected_scopes if all(k not in fields or fields[k] == v for k,v in s.items())]
                scope_matches[signature] = (bool(matching), {k for s in matching for k in s if k not in fields})
            matched, missing = scope_matches[signature]
            if not matched:
                continue
            unknown = sorted(set(unknown) | missing)
        drafts.append({'id':row['id'],'raw_json':row['raw_json'],'corrections_json':row['corrections_json'],
            'filename':row['filename'],'scope':'unassigned' if unknown else 'in_scope','unknown_dimensions':unknown})
    pending_regions = []
    for upload in conn.execute('SELECT * FROM workflow_uploads WHERE project=?',(request.project,)):
        for sheet in coverage(conn,upload):
            unresolved = [r for r in sheet.get('regions',[]) if r['role'] == 'unclassified']
            if unresolved:
                pending_regions.append({'upload_id':upload['id'],'filename':upload['filename'],'section':sheet['section'],
                    'scope':'unassigned','regions':unresolved})
    snapshot = {'project':request.project,'revision':request.base_revision,'orientation':request.orientation,
                'item_labels':{i.id:i.label for i in linked_config(conn,ProjectConfig.model_validate_json(
                    project_row(conn,request.project)['config_json'])).items},
                'filters':request.filters.model_dump(),'selected_slot_ids':list(request.slot_ids),
                'entries':[{'display':dict(a),'adopted':dict(b) if b else None} for a,b in pairs],
                'drafts':drafts,'pending_source_regions':pending_regions}
    from .trend_export import freeze_plot_limits
    if limits := freeze_plot_limits(conn, snapshot):
        snapshot['plot_limits'] = limits
    snapshot['snapshot_id'] = digest(snapshot)
    return snapshot
