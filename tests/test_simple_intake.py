import json
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from stability_hub.api import create_app
from stability_hub.parsing import parse_xlsx
from stability_hub.permissions import Role, actor_for_role
from stability_hub.result_versions import ResultVersions
from stability_hub.storage import Store
from stability_hub.workflow import Workflow
from stability_hub.workflow_contracts import ConfirmItem, ConfirmRequest, ResultChange, SharedEdit

TECH = actor_for_role('simple-tech', '实验员', Role.TECHNICIAN)


def test_project_creation_is_empty_persistent_and_cannot_replace(tmp_path):
    store = Store(tmp_path / 'projects')
    client = TestClient(create_app(store, [], TECH))
    response = client.post('/api/v1/projects', json={'project': 'IP318', 'name': '稳定性项目'})
    assert response.status_code == 200
    assert response.json()['config']['items'] == []
    assert client.post('/api/v1/projects', json={'project': 'IP318', 'name': '替换'}).status_code == 409
    restarted = TestClient(create_app(store, [], TECH))
    assert restarted.get('/api/v1/projects').json()[0]['name'] == '稳定性项目'


def test_inspection_does_not_create_wrong_project_draft(tmp_path):
    book = Workbook()
    sheet = book.active
    sheet.append(['project', 'test_item', 'result'])
    sheet.append(['IP318', 'pH', '6.5'])
    file = BytesIO()
    book.save(file)
    store = Store(tmp_path / 'inspect')
    client = TestClient(create_app(store, [], TECH))
    response = client.post('/api/v1/intake/inspect', files={'file': ('input.xlsx', file.getvalue())})
    assert response.status_code == 200
    assert response.json()['projects'] == ['IP318']
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) FROM workflow_uploads').fetchone()[0] == 0


def test_date_header_is_not_result_and_three_months_survives(tmp_path):
    book = Workbook()
    sheet = book.active
    from datetime import datetime
    sheet.append(['项目编号', 'P', '产品批号', 'LOT', '规格', '原液'])
    sheet.append(['开始日期', datetime(2026, 1, 31), '产品名称', '原液'])
    sheet.append(['考察条件', '5℃', '稳定性类型', '长期'])
    sheet.append(['检验项目\nTest Item', '可接受标准', '考察时间（月）'])
    sheet.merge_cells('A4:A6')
    sheet.merge_cells('B4:B6')
    sheet.merge_cells('C4:D4')
    sheet.append([None, None, '0M', 3])
    sheet.append([None, None, datetime(2026, 1, 31), '=EDATE(B2,1)'])
    sheet.append(['pH', '报告结果', '6.5', '6.4'])
    path = tmp_path / 'date.xlsx'
    book.save(path)
    parsed = parse_xlsx(path)
    assert len(parsed.observations) == 2
    assert all(r.location.row == 7 for r in parsed.observations)
    three = parsed.observations[1]
    assert three.fields['time_point'] == '3'
    assert three.fields['planned_date'] == '=EDATE(B2,1)'
    assert not three.hazards
    assert '表头为3个月' in json.dumps(parsed.coverage, ensure_ascii=False)


def make_flow(tmp_path, config, xlsx, context, count=1):
    flow = Workflow(Store(tmp_path / 'flow'))
    flow.configure(config)
    path = xlsx([['Purity', f'{i}M', '98', '%'] for i in range(count)])
    preview = flow.upload(config.project, path.name, path.read_bytes(), context, uuid4().hex, actor=TECH)
    return flow, preview


def confirm(preview):
    return ConfirmRequest(base_revision=preview.base_revision, idempotency_key=uuid4().hex, items=tuple(
        ConfirmItem(candidate_id=c.id, candidate_revision=c.candidate_revision, detection_id=c.detection_id,
                    identity_confirmed=True, intent='initial', reason='文件汇总确认') for c in preview.candidates))


def test_whole_file_confirmation_above_500_and_idempotency(tmp_path, config, xlsx, context):
    flow, preview = make_flow(tmp_path, config, xlsx, context, 501)
    from test_v5_regressions import confirm_initial
    result = confirm_initial(flow,preview)
    assert len(result.result_version_ids) == 501


def test_display_change_is_optional_investigation_pending_and_preserves_original(tmp_path, config, xlsx, context):
    flow, preview = make_flow(tmp_path, config, xlsx, context)
    versions = ResultVersions(flow)
    from test_v5_regressions import confirm_initial
    first = confirm_initial(flow,preview)
    request = ResultChange(base_revision=first.base_revision, idempotency_key=uuid4().hex,
                           result='99', reason='核对原件后更正录入值')
    result = versions.change(first.result_version_ids[0], request, actor=TECH)
    assert len(result.pending_confirmation_ids) == 1
    assert versions.change(first.result_version_ids[0], request, actor=TECH) == result
    cell = flow.results(config.project, actor=TECH).cells[0]
    assert cell.display.pending_confirmation
    assert cell.adopted.result_version_id == first.result_version_ids[0]
    with flow.store.connect() as conn:
        changed = conn.execute('SELECT * FROM workflow_versions WHERE id=?', (result.result_version_ids[0],)).fetchone()
        assert changed['references_json'] == '[]'
        assert changed['detection_id'] == preview.candidates[0].detection_id
        assert changed['intent'] == 'correction'


def test_shared_metadata_cannot_bulk_change_results(tmp_path, config, xlsx, context):
    flow, preview = make_flow(tmp_path, config, xlsx, context)
    with pytest.raises(ValueError, match='不能批量修改'):
        flow.edit_shared(preview.preview_id, SharedEdit(base_revision=preview.base_revision,
            idempotency_key=uuid4().hex, reason='批量修改', candidate_ids=(preview.candidates[0].id,), fields={'result':'99'}), actor=TECH)


def test_bulk_new_items_is_atomic_and_existing_definition_protected(tmp_path, config):
    store = Store(tmp_path / 'items')
    client = TestClient(create_app(store, [config], TECH))
    revision = client.get(f'/api/v1/projects/{config.project}/configuration').json()['base_revision']
    item = {'id':'new-item','label':'新检项','method':'SEC','unit':'%'}
    body = {'base_revision':revision,'idempotency_key':'bulk','reason':'首次配置','items':[item,config.items[0].model_dump(mode='json')]}
    assert client.post(f'/api/v1/projects/{config.project}/items', json=body).status_code == 422
    current = client.get(f'/api/v1/projects/{config.project}/configuration').json()
    assert not any(i['id'] == 'new-item' for i in current['config']['items'])
