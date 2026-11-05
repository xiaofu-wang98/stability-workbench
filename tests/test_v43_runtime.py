from fastapi.testclient import TestClient
import importlib.util
from pathlib import Path

import pytest

from stability_hub.api import create_app
from stability_hub.models import ProjectConfig
from stability_hub.permissions import Actor, Role, actor_for_role
from stability_hub.storage import Store


def test_empty_dictionary_can_be_configured_without_fake_items_and_survives_restart(tmp_path):
    config = ProjectConfig(project='IP166E')
    store = Store(tmp_path)
    actor = actor_for_role('tech', '实验员', Role.TECHNICIAN)
    app = create_app(store, [config], actor)
    with TestClient(app) as client:
        assert client.get('/health').json()['version'] == '0.1.4'
        assert client.get('/health').json()['delivery_state'] == 'release'
        assert client.get('/api/v1/runtime').json()['data_home'] == str(tmp_path.resolve())
        assert client.get('/api/v1/projects/IP166E/configuration').json()['config']['items'] == []
        revision = client.get('/api/v1/projects/IP166E/configuration').json()['base_revision']
        command = {'base_revision': revision, 'idempotency_key': 'first-item', 'reason': '人工确认新检项',
                   'item': {'id': 'monomer', 'label': '单体纯度', 'method': 'SEC', 'unit': '%'}}
        assert client.post('/api/v1/projects/IP166E/configuration', json=command).status_code == 200
    with TestClient(create_app(Store(tmp_path), [config], actor)) as client:
        assert client.get('/api/v1/projects/IP166E/configuration').json()['config']['items'][0]['id'] == 'monomer'


def test_previews_resume_after_reconnect_with_counts_and_project_scope(tmp_path, config, xlsx, context):
    store = Store(tmp_path)
    actor = actor_for_role('tech', '实验员', Role.TECHNICIAN)
    app = create_app(store, [config, ProjectConfig(project='OTHER')], actor)
    file = xlsx([['Purity', '12M', '98', '%']])
    with TestClient(app) as client:
        response = client.post('/api/v1/projects/PROJECT-P/previews',
                               files={'file': (file.name, file.read_bytes())},
                               data={'context': context.model_dump_json()}, headers={'Idempotency-Key': 'upload'})
        assert response.status_code == 200
        preview_id = response.json()['preview_id']
    # A fresh app, without any in-memory preview or browser state.
    with TestClient(create_app(Store(tmp_path), [config, ProjectConfig(project='OTHER')], actor)) as client:
        rows = client.get('/api/v1/projects/PROJECT-P/previews').json()
        assert rows[0]['preview_id'] == preview_id
        assert rows[0]['total'] == rows[0]['draft'] == 1
        assert rows[0]['submitted'] == rows[0]['withdrawn'] == 0
        assert client.get('/api/v1/projects/OTHER/previews').json() == []
        assert client.get('/api/v1/projects/PROJECT-P/previews?offset=1').json() == []
        assert client.get('/api/v1/projects/PROJECT-P/previews?limit=101').status_code == 422
        assert client.get(f'/api/v1/previews/{preview_id}').status_code == 200
    with TestClient(create_app(Store(tmp_path), [config], Actor(id='none', display_name='无权限', capabilities=frozenset()))) as client:
        assert client.get('/api/v1/projects/PROJECT-P/previews').status_code == 403
        assert client.get('/api/v1/runtime').status_code == 403
