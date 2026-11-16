"""Display naming never changes an established analytical identity."""
from .models import ProjectConfig
from .permissions import Capability, require
from .workflow_common import WorkflowError, bump, check_revision, event, project_row, replay, remember
from .workflow_contracts import MutationResult


def rename(flow, project, item_id, request, *, actor):
    require(actor, Capability.CORRECT_RESULT)
    with flow.store.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        payload = {'rename_item':item_id, **request.model_dump(mode='json')}
        prior = replay(conn,project,actor,request.idempotency_key,payload)
        if prior:
            return MutationResult.model_validate(prior)
        check_revision(conn,project,request.base_revision)
        config = ProjectConfig.model_validate_json(project_row(conn,project)['config_json'])
        item = next((i for i in config.items if i.id == item_id), None)
        if not item:
            raise WorkflowError('unknown_item','检测项不存在。')
        changed = item.model_copy(update={'label':request.label,
            'aliases':tuple(sorted((set(item.aliases) | {item.label}) - {request.label,item.id}))})
        after = ProjectConfig.model_validate({**config.model_dump(), 'items':[
            changed if i.id == item_id else i for i in config.items]})
        conn.execute('UPDATE workflow_projects SET config_json=? WHERE project=?',(after.model_dump_json(),project))
        event(conn,project,item_id,'rename_item_display',actor,request.reason,
              {'before':item.model_dump(mode='json'),'after':changed.model_dump(mode='json'),'identity_changed':False})
        result = MutationResult(project=project,base_revision=bump(conn,project))
        remember(conn,project,actor,request.idempotency_key,payload,result.model_dump(mode='json'))
        return result
