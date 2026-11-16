"""Explicit dictionary links preserve historical item/slot identity."""
import json

from .models import ProjectConfig, StabilityRecord
from .normalize import NeedsReview
from .permissions import Capability, require
from .workflow_common import WorkflowError, bump, check_revision, event, project_row, replay, remember
from .workflow_contracts import MutationResult


def frozen_config(conn, upload):
    return ProjectConfig.model_validate_json(upload['interpretation_config_json'] or
                                            project_row(conn, upload['project'])['config_json'])


def linked_config(conn, config):
    links = {}
    for row in conn.execute("SELECT detail_json FROM workflow_events WHERE project=? AND action='link_item_identity' ORDER BY sequence",
                            (config.project,)):
        detail = json.loads(row[0])
        links[detail['item_id']] = detail['historical_item']
    groups = {}
    for original in config.items:
        item = original.model_copy(update={'id':links[original.id],
            'aliases':tuple(sorted(set(original.aliases) | {original.id}))}) if original.id in links else original
        if item.id not in groups:
            groups[item.id] = item
            continue
        prior = groups[item.id]
        if any(getattr(prior,k) != getattr(item,k) for k in ('method','method_version','unit','result_type')):
            raise NeedsReview('identity_rule_conflict','已关联名称的方法/版本/单位不一致，须保留独立身份并处理关联。')
        groups[item.id] = prior.model_copy(update={'aliases':tuple(sorted(
            (set(prior.aliases) | set(item.aliases) | {item.label}) - {prior.id,prior.label}))})
    return config.model_copy(update={'items':tuple(groups.values())})


def check_historical_identity(conn, config, record):
    item = next((i for i in config.items if i.id == record.key.item), None)
    if not item:
        return
    names = {' '.join(n.split()).casefold() for n in (item.label, *item.aliases)}
    for row in conn.execute('SELECT record_json FROM workflow_versions WHERE project=?', (config.project,)):
        old = StabilityRecord.model_validate_json(row[0])
        from .identity_associations import logical_record
        old = logical_record(conn,config.project,old)
        if old.key.item != item.id and ' '.join(old.key.item.split()).casefold() in names:
            raise NeedsReview('identity_link_required', '词典名称对应已有来源身份，请预览并明确关联历史检测项，不能作为另一个首次结果。')


def link_preview(conn, project, item_id, historical_item):
    config = ProjectConfig.model_validate_json(project_row(conn,project)['config_json'])
    item = next((i for i in config.items if i.id == item_id), None)
    if not item:
        raise WorkflowError('unknown_item','检测项不存在。')
    selected, groups, collisions = [], {}, []
    for row in conn.execute('SELECT id,slot_id,record_json FROM workflow_versions WHERE project=?',(project,)):
        record = StabilityRecord.model_validate_json(row['record_json'])
        if record.key.item not in {item_id,historical_item}:
            continue
        selected.append(dict(row))
        sid = record.key.model_copy(update={'item':historical_item}).slot_id
        groups.setdefault(sid,set()).add(row['slot_id'])
        if record.key.item == historical_item and (record.key.method != item.method or
                record.key.method_version != item.method_version or record.result.unit != item.unit):
            collisions.append('历史方法、版本或单位不同：' + row['id'])
    collisions.extend('历史槽位碰撞：' + k for k,v in groups.items() if len(v)>1)
    return {'base_revision':project_row(conn,project)['revision'],'versions':selected,'collisions':collisions,
            'item_id':item_id,'historical_item':historical_item}


def apply_link(flow, project, request, *, actor):
    require(actor,Capability.REVIEW_DUPLICATE)
    with flow.store.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        payload = {'identity_link':request.model_dump(mode='json')}
        prior = replay(conn,project,actor,request.idempotency_key,payload)
        if prior:
            return MutationResult.model_validate(prior)
        check_revision(conn,project,request.base_revision)
        info = link_preview(conn,project,request.item_id,request.historical_item)
        if not info['versions'] or info['collisions']:
            raise WorkflowError('identity_collision','关联存在方法/单位差异或历史槽位碰撞，请保留独立身份并处理冲突。',409)
        event(conn,project,request.item_id,'link_item_identity',actor,request.reason,info)
        result = MutationResult(project=project,base_revision=bump(conn,project))
        remember(conn,project,actor,request.idempotency_key,payload,result.model_dump(mode='json'))
        return result
