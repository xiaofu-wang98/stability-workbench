"""Project-independent studies. Study-only alignment never rewrites record identities."""
import json
from collections import defaultdict
from typing import Literal

from pydantic import Field, model_validator

from .models import Model, Text, StabilityRecord, digest
from .permissions import Capability, require
from .result_queries import selected_rows, cells_from_rows
from .trend_export import analysis_scope, chart_data
from .workflow_contracts import AxisOptions, ResultFilter
from .workflow_common import WorkflowError, project_row, check_revision, event, bump, uid, replay, remember

SAMPLE_FIELDS = ('sample_type', 'batch', 'presentation', 'purpose')
SCOPE_FIELDS = ('sample_type','presentation','study','study_id','condition','item','method',
                'method_version','unit','time_unit','purpose','reporting_basis')


def sample_identity(record):
    return {k:getattr(record.key,k) for k in SAMPLE_FIELDS}


def sample_id(record):
    return digest([record.key.project, sample_identity(record)])


class Member(Model):
    sample_id: Text
    role: Literal['before','after','selected','pending','exclude']


class StudySave(Model):
    base_revision: int = Field(ge=0)
    idempotency_key: Text
    title: Text
    reason: Text
    members: tuple[Member,...] = Field(min_length=1)
    aligned_fields: tuple[Literal['presentation','study_id'],...] = ()
    alignment_reason: str = ''
    filters: ResultFilter = Field(default_factory=ResultFilter)

    @model_validator(mode='after')
    def explicit_alignment(self):
        if len({m.sample_id for m in self.members}) != len(self.members):
            raise ValueError('同一样品不能重复分组')
        if len(set(self.aligned_fields)) != len(self.aligned_fields):
            raise ValueError('对齐字段不能重复')
        if self.aligned_fields and not self.alignment_reason.strip():
            raise ValueError('研究内对齐须填写确认依据')
        return self


class StudyQuery(AxisOptions):
    base_revision: int = Field(ge=0)
    mode: Literal['release','stability'] = 'stability'
    group_id: str | None = None
    include_cells: bool = True


class QuickCompare(AxisOptions):
    base_revision: int = Field(ge=0)
    sample_ids: tuple[Text,...] = Field(min_length=1)
    filters: ResultFilter = Field(default_factory=ResultFilter)
    members: tuple[Member,...] = ()
    aligned_fields: tuple[Literal['presentation','study_id'],...] = ()
    alignment_reason: str = ''
    group_id: str | None = None
    include_cells: bool = False

    @model_validator(mode='after')
    def validate_selection(self):
        if len(set(self.sample_ids))!=len(self.sample_ids):
            raise ValueError('所选样品不能重复')
        if len({m.sample_id for m in self.members})!=len(self.members) or not {m.sample_id for m in self.members}<=set(self.sample_ids):
            raise ValueError('分组须属于明确选择的样品且不能重复')
        if any(m.role not in {'before','after','selected'} for m in self.members):
            raise ValueError('快速对比仅接收当前参与样品的分组')
        if len(set(self.aligned_fields))!=len(self.aligned_fields) or (self.aligned_fields and not self.alignment_reason.strip()):
            raise ValueError('对齐字段不能重复，且须填写确认依据')
        return self


def inventory(conn, project, pairs=None):
    samples = {}
    for a,b in selected_rows(conn,project,ResultFilter()) if pairs is None else pairs:
        rec = StabilityRecord.model_validate_json((b or a)['record_json'])
        key = sample_id(rec)
        samples.setdefault(key, {'id':key, **sample_identity(rec), 'slot_count':0})['slot_count'] += 1
    return list(samples.values())


def studies(conn, project):
    project_row(conn,project)
    result = {}
    for row in conn.execute("SELECT subject_id,detail_json FROM workflow_events WHERE project=? AND action='comparison_study_saved' ORDER BY rowid",(project,)):
        result[row['subject_id']] = json.loads(row['detail_json'])
    return list(result.values())


def study(conn, project, study_id):
    value = next((s for s in studies(conn,project) if s['id']==study_id),None)
    if value is None:
        raise WorkflowError('study_not_found','该项目没有此可比研究。',404)
    return value


def save(flow,project,study_id,command,actor):
    require(actor,Capability.VIEW)
    if not ({Capability.PREVIEW,Capability.EXPORT,Capability.MANAGE} & actor.capabilities):
        raise PermissionError('当前角色不能保存研究配置')
    payload = {'study_id':study_id,**command.model_dump(mode='json')}
    with flow.store.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        previous = replay(conn,project,actor,command.idempotency_key,payload)
        if previous:
            return previous
        check_revision(conn,project,command.base_revision)
        if study_id != 'new':
            study(conn,project,study_id)
        available = {s['id'] for s in inventory(conn,project)}
        if not {m.sample_id for m in command.members} <= available:
            raise WorkflowError('study_members','研究成员不属于当前项目已提交样品。',409)
        sid = uid('comparison') if study_id=='new' else study_id
        value = {'id':sid,**command.model_dump(mode='json',exclude={'base_revision','idempotency_key'}),
                 'saved_by':actor.id,'saved_revision':command.base_revision+1}
        event(conn,project,sid,'comparison_study_saved',actor,command.reason,value)
        result = {'project':project,'base_revision':bump(conn,project),'study':value}
        remember(conn,project,actor,command.idempotency_key,payload,result)
        return result


def compare(conn,project,study_id,query):
    check_revision(conn,project,query.base_revision)
    config = study(conn,project,study_id)
    return comparison_results(conn,project,config,query)


def comparison_results(conn,project,config,query,all_pairs=None):
    roles = {m['sample_id']:m['role'] for m in config['members']}
    aligned = set(config['aligned_fields'])
    pairs = []
    groups = defaultdict(list)
    filters = ResultFilter.model_validate(config.get('filters',{}))
    all_pairs = selected_rows(conn,project,filters) if all_pairs is None else all_pairs
    for a,b in all_pairs:
        display_key = StabilityRecord.model_validate_json(a['record_json']).key.model_dump()
        if any(display_key[k]!=v for k,v in filters.model_dump(exclude_none=True).items()):
            continue
        rec = StabilityRecord.model_validate_json((b or a)['record_json'])
        if roles.get(sample_id(rec)) not in {'before','after','selected'}:
            continue
        if isinstance(query,StudyQuery) and (rec.key.study=='release') != (query.mode=='release'):
            continue
        scope = tuple('研究内显式对齐' if field in aligned else value
                      for field,value in zip(SCOPE_FIELDS,analysis_scope(rec),strict=True))
        groups[digest(scope)].append((a,b))
        pairs.append((a,b))
    labels = {i['id']:i['label'] for i in json.loads(project_row(conn,project)['config_json']).get('items',[])}
    if query.group_id:
        members = groups.get(query.group_id)
        if members is None:
            raise WorkflowError('comparison_scope','当前研究/模式中没有此检测组。',409)
        snap = comparison_snapshot(project,query.base_revision,config,members,labels,query)
        return {'project':project,'base_revision':query.base_revision,
                'cells':comparison_cells(members,roles) if query.include_cells else [],
                'trend':chart_data(snap,aligned_fields=aligned)}
    result_groups = []
    for gid,members in groups.items():
        record = StabilityRecord.model_validate_json((members[0][1] or members[0][0])['record_json'])
        exact = sum(b is not None and b['state'] in {'confirmed','legacy'} and
                    (r:=StabilityRecord.model_validate_json(b['record_json'])).result.status=='numeric' and r.result.comparator=='eq'
                    for a,b in members)
        result_groups.append({'id':gid,'label':labels.get(record.key.item,record.key.item),'exact_numeric_count':exact,
            'key':record.key.model_dump(mode='json'),'unit':record.result.unit,
            'slot_ids':[a['slot_id'] for a,b in members],
            'scope':dict(zip(SCOPE_FIELDS,('研究内显式对齐' if f in aligned else v for f,v in zip(SCOPE_FIELDS,analysis_scope(record),strict=True)),strict=True))})
    cells = comparison_cells(pairs,roles) if query.include_cells else []
    roles_count = {role:sum(m['role']==role for m in config['members']) for role in ('before','after','selected')}
    result = {'project':project,'base_revision':query.base_revision,'study':config,'mode':getattr(query,'mode','quick'),
              'groups':result_groups,'cells':cells,'item_labels':labels,
              'total_slots':len(pairs),'roles_count':roles_count,
              'pending_samples':[s for s in inventory(conn,project) if roles.get(s['id'],'pending')=='pending'] if isinstance(query,StudyQuery) else []}
    return result


def comparison_cells(pairs,roles):
    cells = []
    for cell,(a,b) in zip(cells_from_rows(pairs),pairs,strict=True):
        record = StabilityRecord.model_validate_json((b or a)['record_json'])
        cells.append({**cell.model_dump(mode='json'),'study_role':roles[sample_id(record)],
                      'sample_id':sample_id(record),'unit':record.result.unit,
                      'standards':[s.model_dump(mode='json') for s in record.standards]})
    return cells


def comparison_snapshot(project,revision,config,members,labels,query):
    roles = {m['sample_id']:m['role'] for m in config['members']}
    series_labels = {}
    records = [StabilityRecord.model_validate_json((b or a)['record_json']) for a,b in members]
    origins = sorted({(r.key.batch,sample_id(r),r.key.study_id) for r in records})
    for (a,_b),r in zip(members,records,strict=True):
        sid = sample_id(r)
        role = {'before':'变更前','after':'变更后','selected':'按批次'}[roles[sid]]
        batch_origins = [o for o in origins if o[0]==r.key.batch]
        index = batch_origins.index((r.key.batch,sid,r.key.study_id))+1
        suffix = f' · {role} · 来源{index}' if role!='按批次' or len(batch_origins)>1 else ''
        series_labels[a['slot_id']] = r.key.batch+suffix
    snap = {'project':project,'revision':revision,'filters':config.get('filters',{}),'item_labels':labels,'study':config,
            'series_labels':series_labels,'axis':query.model_dump(include={'axis_mode','axis_min','axis_max'}),
            'entries':[{'display':dict(a),'adopted':dict(b) if b else None} for a,b in members]}
    snap['snapshot_id'] = digest(snap)
    return snap
