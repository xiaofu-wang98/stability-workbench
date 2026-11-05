"""Read-only, unsaved comparisons using the same study grouping and adoption rules."""
import json
from collections import defaultdict

from .models import StabilityRecord, digest
from .comparability import (SCOPE_FIELDS, inventory, sample_id, comparison_results, studies)
from .result_queries import selected_rows
from .trend_export import analysis_scope
from .workflow_contracts import ResultFilter
from .workflow_common import check_revision, project_row, WorkflowError


def catalog(conn,project):
    row = project_row(conn,project)
    pairs = selected_rows(conn,project,ResultFilter())
    labels = {i['id']:i['label'] for i in json.loads(row['config_json']).get('items',[])}
    options = {}
    for a,b in pairs:
        r = StabilityRecord.model_validate_json((b or a)['record_json'])
        key = (sample_id(r),r.key.study,r.key.condition,r.key.item)
        option = options.setdefault(key,{'sample_id':key[0],'study':key[1],'condition':key[2],
            'item':key[3],'label':labels.get(key[3],key[3]),'slot_count':0,'exact_numeric_count':0})
        option['slot_count'] += 1
        option['exact_numeric_count'] += int(b is not None and b['state'] in {'confirmed','legacy'} and
            r.result.status=='numeric' and r.result.comparator=='eq')
    return {'project':project,'base_revision':row['revision'],'samples':inventory(conn,project,pairs),
            'options':list(options.values()),'studies':studies(conn,project)}


def quick_compare(conn,project,command):
    check_revision(conn,project,command.base_revision)
    pairs = selected_rows(conn,project,ResultFilter())
    available = {s['id'] for s in inventory(conn,project,pairs)}
    if not set(command.sample_ids)<=available:
        raise WorkflowError('comparison_members','所选样品不属于当前项目已提交数据，请刷新后选择。',409)
    roles = {sid:'selected' for sid in command.sample_ids}
    roles.update({m.sample_id:m.role for m in command.members})
    config = {'id':'unsaved','title':'临时批次对比','reason':'用户明确选择的批次与检测范围',
              'kind':'quick','members':[{'sample_id':sid,'role':role} for sid,role in roles.items()],
              'aligned_fields':list(command.aligned_fields),'alignment_reason':command.alignment_reason,
              'filters':command.filters.model_dump(mode='json')}
    result = comparison_results(conn,project,config,command,pairs)
    if command.group_id:
        return result
    # Only offer alignment where these two fields actually split otherwise-compatible results.
    groups = defaultdict(list)
    filters = command.filters.model_dump(exclude_none=True)
    for a,b in pairs:
        display = StabilityRecord.model_validate_json(a['record_json'])
        r = StabilityRecord.model_validate_json((b or a)['record_json'])
        if sample_id(r) not in roles or any(getattr(display.key,k)!=v for k,v in filters.items()):
            continue
        scope = dict(zip(SCOPE_FIELDS,analysis_scope(r),strict=True))
        groups[digest({k:v for k,v in scope.items() if k not in {'presentation','study_id'}})].append(scope)
    candidates = {}
    for members in groups.values():
        for field in ('presentation','study_id'):
            values = {m[field] for m in members}
            if len(values)>1 and field not in command.aligned_fields:
                candidates.setdefault(field,set()).update(values)
    result['alignment_candidates'] = [{'field':field,'values':sorted(values)} for field,values in candidates.items()]
    result['selection_id'] = digest([project,command.base_revision,config])
    return result
