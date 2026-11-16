import json
from fastapi.testclient import TestClient

from stability_hub.api import create_app
from stability_hub.file_rules import rule_impact, save_rule, reinterpret_preview, reinterpret,retire_rule
from stability_hub.identity_links import link_preview
from stability_hub.models import ItemDefinition
from stability_hub.v5_contracts import IdentityLink, ProjectRule, Reinterpret,RuleRetirement
from stability_hub.workflow_contracts import CandidateMapping, NewItems
from test_v5_regressions import TECH, preview, confirm_initial
from stability_hub.permissions import Role, actor_for_role


def test_a08_file_mapping_and_explicit_project_rule(tmp_path,xlsx,context):
    flow,p = preview(tmp_path,xlsx([['raw name','0M',99,'%']]),context)
    added = flow.add_items('PROJECT-P',NewItems(base_revision=p.base_revision,idempotency_key='item',reason='new independent item',
        items=(ItemDefinition(id='x',label='X',method='UV',unit='%'),)),actor=TECH)
    mapped = flow.map_items(p.preview_id,CandidateMapping(base_revision=added.base_revision,idempotency_key='map',
        reason='file scope',candidate_ids=(p.candidates[0].id,),item_id='x'),actor=TECH)
    config_before = flow.configuration('PROJECT-P',actor=TECH)['config']
    other = flow.upload('PROJECT-P','b.xlsx',xlsx([['raw name','0M',98,'%']]).read_bytes(),context,'b',actor=TECH)
    assert other.candidates[0].record.key.item == 'raw name'
    assert flow.preview(p.preview_id,actor=TECH).candidates[0].record.key.item == 'x'
    rule = ProjectRule(base_revision=other.base_revision,idempotency_key='rule',reason='explicit reuse rule',
        upload_id=p.preview_id,candidate_id=p.candidates[0].id,source_name='raw name',method='',unit='%',item_id='x')
    with flow.store.connect() as conn:
        impact = rule_impact(conn,'PROJECT-P',rule)
        assert all(not d['automatic_change'] for d in impact['drafts'])
    result = save_rule(flow,'PROJECT-P',rule,actor=TECH)
    assert save_rule(flow,'PROJECT-P',rule,actor=TECH) == result
    rules_client = TestClient(create_app(flow.store,[],TECH))
    listed = rules_client.get('/api/v1/projects/PROJECT-P/v5/rules').json()
    assert len(listed)==1 and not listed[0]['retired'] and listed[0]['target']['id']=='x'
    after = flow.upload('PROJECT-P','c.xlsx',xlsx([['raw name','0M',97,'%']]).read_bytes(),context,'c',actor=TECH)
    assert after.candidates[0].record.key.item == 'x'
    assert flow.preview(other.preview_id,actor=TECH).candidates[0].record.key.item == 'raw name'
    assert flow.configuration('PROJECT-P',actor=TECH)['config'] == config_before
    assert mapped.base_revision < result.base_revision
    with flow.store.connect() as conn:
        upgrade = reinterpret_preview(flow,conn,flow._upload(conn,other.preview_id),TECH)
    assert upgrade['rule_updates'] and not upgrade['rule_conflicts']
    changed = reinterpret(flow,other.preview_id,Reinterpret(base_revision=upgrade['base_revision'],
        idempotency_key='upgrade-rule',reason='explicit project rule upgrade',fingerprint=upgrade['fingerprint']),actor=TECH)
    updated = flow.preview(other.preview_id,actor=TECH)
    assert updated.candidates[0].record.key.item == 'x'
    assert updated.candidates[0].rule_history[-1]['rule_version'] == impact['rule_version']
    retire_rule(flow,'PROJECT-P',RuleRetirement(base_revision=changed.base_revision,idempotency_key='retire-rule',
        reason='explicitly retire old project rule',rule_version=impact['rule_version']),actor=TECH)
    assert rules_client.get('/api/v1/projects/PROJECT-P/v5/rules').json()[0]['retired']
    future = flow.upload('PROJECT-P','d.xlsx',xlsx([['raw name','0M',96,'%']]).read_bytes(),context,'d',actor=TECH)
    assert future.candidates[0].record.key.item == 'raw name'
    assert flow.preview(other.preview_id,actor=TECH).candidates[0].record.key.item == 'x'


def test_a09_draft_upgrade_is_explicit(tmp_path,xlsx,context):
    flow,p = preview(tmp_path,xlsx([['Purity','0M',99,'%']]),context)
    c = p.candidates[0]
    flow.add_items('PROJECT-P',NewItems(base_revision=p.base_revision,idempotency_key='item',reason='dictionary',
        items=(ItemDefinition(id='purity',label='Purity',method='SEC',unit='%'),)),actor=TECH)
    old = flow.preview(p.preview_id,actor=TECH)
    assert old.candidates[0].record == c.record
    with flow.store.connect() as conn:
        impact = reinterpret_preview(flow,conn,flow._upload(conn,p.preview_id),TECH)
        assert impact['changes']
    reinterpret(flow,p.preview_id,Reinterpret(base_revision=old.base_revision,idempotency_key='upgrade',
        reason='explicit reviewed configuration upgrade',fingerprint=impact['fingerprint']),actor=TECH)
    assert flow.preview(p.preview_id,actor=TECH).candidates[0].record.key.item == 'purity'


def test_a09_rule_collision_requires_explicit_file_mapping(tmp_path,xlsx,context):
    flow,p = preview(tmp_path,xlsx([['raw','0M',99,'%']]),context)
    result = flow.add_items('PROJECT-P',NewItems(base_revision=p.base_revision,idempotency_key='items',reason='independent targets',items=(
        ItemDefinition(id='x',label='X',method='UV',unit='%'),ItemDefinition(id='y',label='Y',method='UV',unit='%'))),actor=TECH)
    for item_id in ('x','y'):
        result = flow.map_items(p.preview_id,CandidateMapping(base_revision=result.base_revision,idempotency_key='map-'+item_id,
            reason='explicit mapping before rule save',candidate_ids=(p.candidates[0].id,),item_id=item_id),actor=TECH)
        result = save_rule(flow,'PROJECT-P',ProjectRule(base_revision=result.base_revision,idempotency_key='rule-'+item_id,
            reason='synthetic overlapping rules',upload_id=p.preview_id,candidate_id=p.candidates[0].id,
            source_name='raw',method='',unit='%',item_id=item_id),actor=TECH)
    other = flow.upload('PROJECT-P','collision.xlsx',xlsx([['raw','0M',98,'%']]).read_bytes(),context,'collision',actor=TECH)
    assert other.candidates[0].status=='needs_review'
    assert '项目规则存在' in other.candidates[0].detail
    flow.map_items(other.preview_id,CandidateMapping(base_revision=other.base_revision,idempotency_key='explicit-target',
        reason='human selects target X for this file',candidate_ids=(other.candidates[0].id,),item_id='x'),actor=TECH)
    after = flow.preview(other.preview_id,actor=TECH)
    assert after.candidates[0].status=='new' and after.candidates[0].record.key.item=='x'


def test_a03_explicit_link_preserves_versions(tmp_path,xlsx,context):
    flow,p = preview(tmp_path,xlsx([['Purity','0M',99,'%']]),context)
    confirm_initial(flow,p)
    with flow.store.connect() as conn:
        original = [tuple(r) for r in conn.execute('SELECT id,slot_id,record_json FROM workflow_versions')]
    result = flow.add_items('PROJECT-P',NewItems(base_revision=flow.results('PROJECT-P',actor=TECH).base_revision,
        idempotency_key='dictionary',reason='dictionary',items=(ItemDefinition(id='purity',label='Purity',method='未注明',unit='%'),)),actor=TECH)
    senior = actor_for_role('senior','Senior',Role.SENIOR_TECHNICIAN)
    with flow.store.connect() as conn:
        assert not link_preview(conn,'PROJECT-P','purity','Purity')['collisions']
    client = TestClient(create_app(flow.store,[],senior))
    impact = client.get('/api/v1/projects/PROJECT-P/v5/identity-link',params={'item_id':'purity','historical_item':'Purity'})
    assert impact.status_code==200 and not impact.json()['collisions']
    command = IdentityLink(base_revision=result.base_revision,idempotency_key='link',reason='explicit history association',
        item_id='purity',historical_item='Purity')
    response = client.post('/api/v1/projects/PROJECT-P/v5/identity-link',json=command.model_dump(mode='json'))
    assert response.status_code==200,response.text
    p2 = flow.upload('PROJECT-P','next.xlsx',xlsx([['Purity','0M',99,'%']]).read_bytes(),context,'next',actor=TECH)
    assert p2.candidates[0].status == 'same_value'
    with flow.store.connect() as conn:
        assert [tuple(r) for r in conn.execute('SELECT id,slot_id,record_json FROM workflow_versions')] == original
        assert json.loads(conn.execute('SELECT record_json FROM workflow_versions').fetchone()[0])['key']['item'] == 'Purity'


def test_a09_unknown_study_is_not_shared_across_workbook_blocks(tmp_path):
    from io import BytesIO
    from openpyxl import Workbook
    from test_xlsx_matrix import add_block
    from stability_hub.models import ImportContext,ProjectConfig
    from stability_hub.storage import Store
    from stability_hub.workflow import Workflow
    book = Workbook()
    add_block(book.active)
    add_block(book.active,offset=20)
    book.active['B2'] = book.active['B22'] = None
    data = BytesIO()
    book.save(data)
    flow = Workflow(Store(tmp_path/'local-unknown-objects'))
    flow.configure(ProjectConfig(project='P'))
    p = flow.upload('P','unknown-studies.xlsx',data.getvalue(),ImportContext(),'upload',actor=TECH)
    assert len({c.record.key.study_id for c in p.candidates}) == 2
    assert all(c.status in {'new','empty'} for c in p.candidates)
