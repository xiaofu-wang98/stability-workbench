"""V4.2 configuration changes preserve facts, authorization and export provenance."""
import json
import sqlite3
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from stability_hub.api import create_app, get_actor
from stability_hub.models import ImportContext, ItemDefinition, ProjectConfig
from stability_hub.permissions import Role, actor_for_role
from stability_hub.storage import Store
from stability_hub.workflow import Workflow
from stability_hub.workflow_common import WorkflowError
from stability_hub.workflow_contracts import ConfigurationChange, ExportRequest
from stability_hub.workflow_excel import ExcelExports
from test_workflow import TECH, SENIOR, LEAD, command
from stability_hub.result_versions import ResultVersions


def change(flow, *, actor=TECH, **fields):
    request = ConfigurationChange(base_revision=flow.configuration('PROJECT-P', actor=TECH)['base_revision'],
                                  idempotency_key=uuid4().hex, reason='人工确认项目配置', **fields)
    return flow.change_configuration('PROJECT-P', request, actor=actor)


@pytest.fixture
def flow(tmp_path, config):
    workflow = Workflow(Store(tmp_path / 'store'))
    workflow.configure(config)
    return workflow


def test_add_unknown_item_without_standard_then_confirm_export_and_restart(flow, xlsx, context, config):
    path = xlsx([['HMW', '12M', '1.2', '%']])
    preview = flow.upload('PROJECT-P', path.name, path.read_bytes(), context, 'first', actor=TECH)
    assert preview.candidates[0].status == 'new'
    request = ConfigurationChange(base_revision=preview.base_revision, idempotency_key='add-hmw', reason='首次确认',
                                  item=ItemDefinition(id='hmw', label='HMW', method='SEC', unit='%'))
    result = flow.change_configuration('PROJECT-P', request, actor=TECH)
    assert flow.change_configuration('PROJECT-P', request, actor=TECH) == result
    preview = flow.preview(preview.preview_id, actor=TECH)
    assert preview.candidates[0].status == 'new'
    # V5 freezes draft interpretation. Choosing the new target is an explicit
    # file mapping, rather than a side effect of changing the project dictionary.
    from stability_hub.workflow_contracts import CandidateMapping
    flow.map_items(preview.preview_id, CandidateMapping(base_revision=preview.base_revision,
        idempotency_key='explicit-hmw',reason='explicit file association',
        candidate_ids=(preview.candidates[0].id,),item_id='hmw'),actor=TECH)
    preview = flow.preview(preview.preview_id,actor=TECH)
    from test_v5_regressions import confirm_initial
    confirm_initial(flow,preview)
    before = flow.results('PROJECT-P', actor=TECH).cells
    # No quality-standard definition exists or is required.
    change(flow, actor=SENIOR, item=ItemDefinition(id='hmw', label='HMW', method='SEC-v2', unit='%'))
    assert flow.results('PROJECT-P', actor=TECH).cells == before
    assert flow.preview(preview.preview_id, actor=TECH).candidates[0].record.key.method == 'SEC'
    exported = ExcelExports(flow).create(ExportRequest(project='PROJECT-P',
        base_revision=flow.results('PROJECT-P', actor=TECH).base_revision), actor=LEAD)
    assert exported.state == 'verified'
    restarted = Workflow(Store(flow.store.home))
    restarted.configure(config)  # Original startup file remains usable after UI changes.
    assert restarted.configuration('PROJECT-P', actor=TECH)['config']['items'][-1]['method'] == 'SEC-v2'
    with flow.store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM workflow_events WHERE action='configure_project'").fetchone()[0] == 2


def test_modify_requires_senior_and_duplicate_definition_is_atomic(flow):
    original = flow.configuration('PROJECT-P', actor=TECH)
    with pytest.raises(PermissionError):
        change(flow, item=ItemDefinition(id='purity', label='Purity', method='New method', unit='%'))
    with pytest.raises(ValidationError):
        change(flow, item=ItemDefinition(id='other', label='Other', aliases=('purity',), method='SEC'))
    assert flow.configuration('PROJECT-P', actor=TECH) == original
    with pytest.raises(PermissionError):
        change(flow, actor=LEAD, item=ItemDefinition(id='hmw', label='HMW', method='SEC'))
    change(flow, actor=SENIOR, item=ItemDefinition(id='purity', label='Purity', method='New method', unit='%'))


def test_default_specification_is_scoped_visible_and_does_not_override_file(flow, xlsx, context):
    change(flow, sample_type='DS', presentation='原液')
    ds = ImportContext(defaults={**context.defaults, 'sample_type': 'DS'})
    ds.defaults.pop('presentation')
    path = xlsx([['Purity', '12M', '98', '%']])
    preview = flow.upload('PROJECT-P', path.name, path.read_bytes(), ds, 'ds', actor=TECH)
    assert preview.candidates[0].record.key.presentation == '原液'
    assert preview.candidates[0].applied_defaults == {'presentation': '原液'}
    dp = ImportContext(defaults={**ds.defaults, 'sample_type': 'DP'})
    unresolved = flow.upload('PROJECT-P', path.name, path.read_bytes(), dp, 'dp', actor=TECH)
    assert unresolved.candidates[0].record.key.presentation.startswith('原文未注明@' + unresolved.preview_id + ':')
    explicit = ImportContext(defaults={**ds.defaults, 'presentation': '20 mg/mL'})
    p2 = flow.upload('PROJECT-P', path.name, path.read_bytes(), explicit, 'explicit', actor=TECH)
    assert p2.candidates[0].record.key.presentation == '20 mg/mL'
    assert not p2.candidates[0].applied_defaults
    with pytest.raises(PermissionError):
        change(flow, sample_type='DS', presentation='20 mg/mL')
    from test_v5_regressions import confirm_initial
    confirm_initial(flow,flow.preview(preview.preview_id,actor=TECH))
    change(flow, actor=SENIOR, sample_type='DS', presentation='20 mg/mL')
    historical = flow.preview(preview.preview_id, actor=TECH).candidates[0]
    assert historical.record.key.presentation == '原液'
    exported = ExcelExports(flow).create(ExportRequest(project='PROJECT-P',
        base_revision=flow.results('PROJECT-P', actor=TECH).base_revision), actor=LEAD)
    assert exported.state == 'verified'


def test_configuration_api_conflict_permissions_and_validation(tmp_path, config):
    app = create_app(Store(tmp_path / 'api'), [config], TECH)
    with TestClient(app) as client:
        url = '/api/v1/projects/PROJECT-P/configuration'
        assert client.get(url).json()['base_revision'] == 0
        payload = {'base_revision':0,'idempotency_key':'new','reason':'核对','item':
                   {'id':'hmw','label':'HMW','unit':'%','method':'SEC'}}
        assert client.post(url, json=payload).status_code == 200
        assert client.post(url, json=payload).status_code == 200
        assert client.post(url, json={**payload, 'reason':'changed'}).status_code == 409
        assert client.post(url, json={**payload, 'idempotency_key':'stale'}).status_code == 409
        assert client.post(url, json={**payload, 'base_revision':1, 'idempotency_key':'edit'}).status_code == 403
        assert client.post(url, json={'base_revision':1,'idempotency_key':'bad','reason':'x',
                                    'sample_type':'DS'}).status_code == 422
        app.dependency_overrides[get_actor] = lambda: actor_for_role('lead', 'lead', Role.PROJECT_LEAD)
        assert client.post(url, json={**payload,'base_revision':1,'idempotency_key':'denied'}).status_code == 403


def test_configuration_change_invalidates_previous_confirmation(flow, xlsx, context):
    path = xlsx([['Purity','0M','98','%']])
    preview = flow.upload('PROJECT-P', path.name, path.read_bytes(), context, 'upload', actor=TECH)
    change(flow, item=ItemDefinition(id='hmw', label='HMW', method='SEC'))
    with pytest.raises(WorkflowError, match='变化'):
        ResultVersions(flow).confirm(preview.preview_id, command(preview), actor=TECH)
    assert flow.results('PROJECT-P', actor=TECH).total == 0


def test_v4_database_upgrade_backs_up_and_preserves_configuration(tmp_path, config):
    from stability_hub import migrations
    home = tmp_path / 'v4'
    home.mkdir()
    db = home / 'history.sqlite'
    with sqlite3.connect(db) as conn:
        for version in range(1, 5):
            for sql in migrations.MIGRATIONS[version]:
                conn.execute(sql)
        conn.execute('INSERT INTO workflow_projects(project,config_json) VALUES (?,?)',
                     (config.project, config.model_dump_json()))
        conn.execute('PRAGMA user_version=4')
    store = Store(home)
    with store.connect() as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == migrations.CURRENT_VERSION
        assert ProjectConfig.model_validate_json(conn.execute('SELECT startup_config_json FROM workflow_projects').fetchone()[0]) == config
    backups = list((home / 'migration-backups').glob('*.sqlite'))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 4
        assert json.loads(conn.execute('SELECT config_json FROM workflow_projects').fetchone()[0])['project'] == config.project
