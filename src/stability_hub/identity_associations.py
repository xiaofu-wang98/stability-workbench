"""Explicit logical identity overlays; immutable historical slots and facts remain intact."""
import json

from .models import RecordKey, StabilityRecord, digest
from .permissions import Capability, require
from .workflow_common import WorkflowError, bump, check_revision, event, project_row, replay, remember
from .workflow_contracts import MutationResult

FIELDS = {'item', 'method', 'method_version', 'presentation', 'study_id'}


def overlays(conn, project):
    result = {}
    for row in conn.execute("SELECT sequence,actor_id,reason,detail_json FROM workflow_events WHERE project=? AND action='associate_identity' ORDER BY sequence", (project,)):
        data = json.loads(row['detail_json'])
        for sid, key in data['physical_slots'].items():
            target = RecordKey.model_validate(key).slot_id
            group = next(g for g in data['groups'] if g['slot_id'] == target)
            physical_version = next(v for v in group['versions'] if v.get('physical_slot_id',v['slot_id']) == sid)
            original = json.loads(physical_version.get('original_record_json',physical_version['record_json']))['key']
            result[sid] = {'key': key, 'event_sequence': row['sequence'], 'actor_id':row['actor_id'], 'reason':row['reason'],
                           'original_key':original,
                           'fingerprint':data['fingerprint'], 'display_version_id':data['adoptions'][target],
                           'through_sequence':max(v['upload_sequence'] for v in group['versions'])}
    return result


def logical_record(conn, project, record, *, links=None):
    link = record_link(conn,project,record,links=links)
    return record.model_copy(update={'key': RecordKey.model_validate(link['key'])}) if link else record


def record_link(conn, project, record, *, links=None):
    links = overlays(conn,project) if links is None else links
    if record.key.slot_id in links:
        return links[record.key.slot_id]
    # Existing simple links may still use a historical item ID in dictionaries.
    # Accept that alias only when EVERY other dimension already equals the
    # explicitly associated target. Missing identities in a new file do not fit.
    matches = {}
    for link in links.values():
        key = RecordKey.model_validate(link['key']).model_dump(mode='json')
        if (record.key.item == link['original_key']['item'] and
                all(v == key[k] for k,v in record.key.model_dump(mode='json').items() if k != 'item')):
            matches[RecordKey.model_validate(key).slot_id] = link
    if len(matches) > 1:
        from .normalize import NeedsReview
        raise NeedsReview('identity_association_ambiguous','旧名称对应多个明确关联目标，请人工选择本文件身份。')
    return next(iter(matches.values()),None)


def logical_row(row, links):
    data = dict(row)
    if data.get('identity_previous_version_id'):
        data['previous_version_id'] = data['identity_previous_version_id']
    if row['slot_id'] in links:
        link = links[row['slot_id']]
        original = StabilityRecord.model_validate_json(row['record_json'])
        key = RecordKey.model_validate(link['key'])
        data.update(physical_slot_id=row['slot_id'], original_record_json=row['record_json'],
                    identity_association=link, slot_id=key.slot_id,
                    record_json=original.model_copy(update={'key': key}).model_dump_json())
    return data


def versions(conn, project, slot_id=None):
    links = overlays(conn, project)
    args = [project]
    where = ''
    if slot_id is not None:
        physical = {slot_id, *(sid for sid,link in links.items() if RecordKey.model_validate(link['key']).slot_id == slot_id)}
        where = ' AND v.slot_id IN (' + ','.join('?' for _ in physical) + ')'
        args.extend(sorted(physical))
    rows = conn.execute("""SELECT v.*,s.state,i.previous_version_id AS identity_previous_version_id
        FROM workflow_versions v JOIN workflow_version_states s ON s.version_id=v.id
        LEFT JOIN workflow_identity_lineage i ON i.version_id=v.id
        WHERE v.project=? AND s.state IN ('pending','confirmed','legacy')""" + where +
        ' ORDER BY v.upload_sequence DESC,v.id', args).fetchall()
    result = [logical_row(row, links) for row in rows]
    return [r for r in result if slot_id is None or r['slot_id'] == slot_id]


def latest(conn, project, slot_id):
    rows = versions(conn, project, slot_id)
    return current(rows)


def current(rows):
    if not rows:
        return None
    newest = max(r['upload_sequence'] for r in rows)
    links = [r['identity_association'] for r in rows if r.get('identity_association')]
    if links:
        link = max(links, key=lambda v:v['event_sequence'])
        if newest <= link['through_sequence']:
            chosen = next((r for r in rows if r['id'] == link['display_version_id']), None)
            if chosen:
                return chosen
    matches = [r for r in rows if r['upload_sequence'] == newest]
    if len(matches) > 1:
        raise WorkflowError('ambiguous_display', '同一上传存在多个有效候选，须先人工消除歧义。', 409)
    return matches[0]


def adopted(conn, project, slot_id):
    choice = conn.execute('SELECT version_id FROM workflow_identity_adoptions WHERE project=? AND slot_id=?',(project,slot_id)).fetchone()
    if not choice:
        choice = conn.execute('SELECT version_id FROM workflow_adoptions WHERE project=? AND slot_id=?',(project,slot_id)).fetchone()
    if not choice:
        return None
    row = conn.execute('''SELECT v.*,s.state FROM workflow_versions v JOIN workflow_version_states s ON s.version_id=v.id
        WHERE v.id=? AND v.project=? AND s.state IN ('confirmed','legacy')''',(choice[0],project)).fetchone()
    result = logical_row(row,overlays(conn,project)) if row else None
    return result if result and result['slot_id'] == slot_id else None


def adoption_choices(conn, project):
    result = dict(conn.execute('SELECT slot_id,version_id FROM workflow_adoptions WHERE project=?',(project,)))
    result.update(dict(conn.execute('SELECT slot_id,version_id FROM workflow_identity_adoptions WHERE project=?',(project,))))
    return result


def set_adoption(conn, project, slot_id, version_id):
    linked = any(RecordKey.model_validate(v['key']).slot_id == slot_id for v in overlays(conn,project).values())
    table = 'workflow_identity_adoptions' if linked else 'workflow_adoptions'
    conn.execute(f'INSERT INTO {table} VALUES (?,?,?) ON CONFLICT(project,slot_id) DO UPDATE SET version_id=excluded.version_id',
                 (project,slot_id,version_id))


def preview(conn, project, scope):
    if set(scope.fields) - FIELDS or len(set(scope.slot_ids)) != len(scope.slot_ids) or any(not v.strip() for v in scope.fields.values()):
        raise WorkflowError('invalid_identity_scope', '仅能明确关联检测项、方法/版本、规格或研究编号；成员不能重复。')
    all_rows = versions(conn, project)
    selected = [r for r in all_rows if r['slot_id'] in scope.slot_ids]
    if set(scope.slot_ids) != {r['slot_id'] for r in selected}:
        raise WorkflowError('identity_scope_changed', '关联槽位不存在或已变化，请按当前逻辑槽位重新预览。', 409)
    groups, physical = {}, {}
    for row in selected:
        record = StabilityRecord.model_validate_json(row['record_json'])
        # Validate instead of model_copy, which intentionally does not revalidate.
        key = RecordKey.model_validate({**record.key.model_dump(), **scope.fields})
        if key.project != project:
            raise WorkflowError('identity_scope_changed', '不允许跨项目关联。')
        physical[row.get('physical_slot_id', row['slot_id'])] = key.model_dump(mode='json')
        group = groups.setdefault(key.slot_id, {'slot_id':key.slot_id, 'key':key.model_dump(mode='json'), 'versions':[], 'units':set()})
        group['versions'].append(row)
        group['units'].add(record.result.unit)
    # A target already present outside the selection must be reviewed together.
    omitted = sorted({r['slot_id'] for r in all_rows if r['slot_id'] in groups and r['slot_id'] not in scope.slot_ids})
    if omitted:
        raise WorkflowError('incomplete_identity_scope', '目标已有结果，须把目标槽位一起纳入预览：' + ','.join(omitted), 409)
    for group in groups.values():
        if len(group.pop('units')) != 1:
            raise WorkflowError('identity_unit_conflict', '关联值的单位不同，不能作为同一分析槽位。', 409)
        group['adoption_options'] = [r['id'] for r in group['versions'] if r['state'] in {'confirmed','legacy'}]
        group['existing_adoptions'] = sorted(set(adoption_choices(conn,project).values()) & {v['id'] for v in group['versions']})
        group['collision'] = len({r['slot_id'] for r in group['versions']}) > 1
    info = {'base_revision':project_row(conn, project)['revision'], 'scope':scope.model_dump(mode='json'),
            'groups':list(groups.values()), 'physical_slots':physical,
            'automatic_choice':False, 'history_rewritten':False}
    return {**info, 'fingerprint':digest(info)}


def apply(flow, project, request, *, actor):
    require(actor, Capability.REVIEW_DUPLICATE)
    require(actor, Capability.ADOPT_RESULT)
    with flow.store.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        payload = {'identity_association':request.model_dump(mode='json')}
        prior = replay(conn, project, actor, request.idempotency_key, payload)
        if prior:
            return MutationResult.model_validate(prior)
        check_revision(conn, project, request.base_revision)
        info = preview(conn, project, request.scope)
        if info['fingerprint'] != request.fingerprint:
            raise WorkflowError('stale_scope', '身份预览已变化，未执行关联。', 409)
        if set(request.adoptions) != {g['slot_id'] for g in info['groups']}:
            raise WorkflowError('adoption_choice_required', '每个目标槽位都须明确选择一个已确认的采用版本。')
        for group in info['groups']:
            chosen = request.adoptions[group['slot_id']]
            if chosen not in group['adoption_options']:
                raise WorkflowError('invalid_adoption', '采用版本必须属于该关联组且已确认。')
            if next(v for v in group['versions'] if v['id'] == chosen)['intent'] == 'correction':
                require(actor, Capability.CORRECT_RESULT)
        for sid, version in request.adoptions.items():
            conn.execute('INSERT INTO workflow_identity_adoptions VALUES (?,?,?) ON CONFLICT(project,slot_id) DO UPDATE SET version_id=excluded.version_id',
                         (project, sid, version))
        event(conn, project, project, 'associate_identity', actor, request.reason,
              {**info, 'adoptions':request.adoptions})
        result = MutationResult(project=project, base_revision=bump(conn, project))
        remember(conn, project, actor, request.idempotency_key, payload, result.model_dump(mode='json'))
        return result
