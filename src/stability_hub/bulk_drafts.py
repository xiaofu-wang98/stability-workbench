"""Exact range preview and one transaction for identity-only draft edits."""
import json
from contextlib import nullcontext

from .models import digest
from .permissions import Capability, require
from .workflow_common import WorkflowError, bump, check_revision, encoded, event, replay, remember
from .workflow_contracts import MutationResult

FIELDS = {'test_item','method','method_version','sample_type','purpose','batch','presentation','study','study_id','condition'}


def preview(flow,conn,upload_id,scope):
    if len(set(scope.candidate_ids)) != len(scope.candidate_ids) or set(scope.fields)-FIELDS:
        raise WorkflowError('invalid_bulk_scope','批量仅允许明确成员的身份字段；不能批量复制结果。')
    members = []
    for cid in scope.candidate_ids:
        row = flow._candidate(conn,cid)
        if row['upload_id'] != upload_id or row['lifecycle'] != 'draft':
            raise WorkflowError('candidate_changed','所选候选不属于当前上传或已退出草稿。',409)
        before = json.loads(row['corrections_json'])
        raw = json.loads(row['raw_json'])
        members.append({'id':cid,'revision':row['revision'],'section':raw['location']['section'],
                        'location':raw['location'],'before':before,'after':{**before,**scope.fields},
                        'original_result':raw['fields'].get('result','')})
    return {'members':members,'fingerprint':digest(members),'count':len(members)}


def apply(flow,upload_id,request,*,actor,_conn=None,batch_id=None):
    require(actor,Capability.CONFIRM_FACT)
    with (nullcontext(_conn) if _conn is not None else flow.store.connect()) as conn:
        if _conn is None:
            conn.execute('BEGIN IMMEDIATE')
        project = flow._upload(conn,upload_id)['project']
        payload = {'bulk_apply':upload_id,**request.model_dump(mode='json')}
        prior = replay(conn,project,actor,request.idempotency_key,payload)
        if prior:
            return MutationResult.model_validate(prior)
        check_revision(conn,project,request.base_revision)
        info = preview(flow,conn,upload_id,request.scope)
        if info['fingerprint'] != request.fingerprint:
            raise WorkflowError('stale_scope','范围成员或字段已变化，整批未执行，请重新预览。',409)
        for member in info['members']:
            conn.execute('UPDATE workflow_candidates SET corrections_json=?,revision=revision+1 WHERE id=?',(encoded(member['after']),member['id']))
            event(conn,project,member['id'],'bulk_draft_edit',actor,request.reason,{**member,'batch_id':batch_id})
        result = MutationResult(project=project,base_revision=bump(conn,project))
        remember(conn,project,actor,request.idempotency_key,payload,result.model_dump(mode='json'))
        return result
