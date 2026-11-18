"""Regression gates for interactive plan changes and identity isolation."""
import json
from datetime import date
from io import BytesIO

import openpyxl
import pytest
from docx import Document
from fastapi.testclient import TestClient

from stability_hub.api import create_app
from stability_hub.assay_catalog import compute_norm_signature
from stability_hub.coa import read_coa_excel, render_coa_excel
from stability_hub.permissions import Role, actor_for_role
from stability_hub.storage import Store
from stability_hub.workflow_common import WorkflowError
from test_business_worksheets import create_sheet


@pytest.mark.parametrize("remove_with_results", [False, True])
def test_plan_edits_preserve_history_and_require_impact_confirmation(tmp_path, config, context, remove_with_results):
    store = Store(tmp_path / 'store')
    app = create_app(store, [config], actor_for_role('local', '验收', Role.ADMIN))
    with TestClient(app) as client:
        protocol = Document()
        table = protocol.add_table(rows=0, cols=3)
        for values in (("Study items", "0M", "12M"), ("Purity", "n/a", "✓"), ("Appearance", "✓", "✓")):
            for cell, text in zip(table.add_row().cells, values, strict=True):
                cell.text = text
        raw = BytesIO()
        protocol.save(raw)
        draft = client.post("/api/v1/intake/protocol", files={"file": ("plan.docx", raw.getvalue())})
        assert draft.is_success, draft.text
        created = client.post("/api/v1/projects/PROJECT-P/worksheets", json={
            "name": "Plan maintenance", "identity": context.defaults, "start_date": "2024-01-31",
            "idempotency_key": "create-plan", "protocol_draft_id": draft.json()["draft_id"], "protocol_table": 1,
            "protocol_row_items": {"2": ["purity"], "3": ["appearance"]}, "items": ["purity", "appearance"],
            "points": [{"amount": 0, "unit": "M", "required_items": ["appearance"]},
                       {"amount": 12, "unit": "M", "required_items": ["purity", "appearance"]}],
        })
        assert created.is_success, created.text
        path = "/api/v1/worksheets/" + created.json()["id"]
        sheet = client.get(path).json()
        row = sheet['rows'][0]['id']
        other = sheet['rows'][1]['id']
        token = client.post(path + '/lease').json()['token']
        command = dict(token=token, revision=1, idempotency_key='criteria', row_id=row,
                       basis='人工核对', criteria=[], criteria_text='≥95%，补充标准')
        saved = client.post(path + '/standards', json=command)
        assert saved.status_code == 200, saved.text
        assert client.post(path + '/standards', json=command).json() == saved.json()
        updated = client.get(path).json()
        assert updated['rows'][0]['criteria_text'] == '≥95%，补充标准'
        stale = {**command, 'idempotency_key': 'stale', 'criteria_text': 'bad'}
        assert client.post(path + '/standards', json=stale).status_code == 409
        change = dict(token=token, revision=2, idempotency_key='result', changes=[{
            'row_id': row, 'point_id': sheet['points'][1]['id'], 'value': '98%', 'intent': 'initial'}])
        assert client.post(path + '/save', json=change).status_code == 200
        remove = dict(token=token, revision=3, idempotency_key='remove', row_id=row, basis='删错行')
        assert client.post(path + '/remove-row', json=remove).status_code == 409
        if remove_with_results:
            preview = client.get(path + '/remove-row', params={'row_id': row}).json()
            assert [result['value'] for result in preview['results']] == ['98%']
            remove['fingerprint'] = preview['fingerprint']
        else:
            remove['row_id'] = other
        response = client.post(path + '/remove-row', json=remove)
        assert response.status_code == 200, response.text
        assert client.post(path + '/remove-row', json=remove).json() == response.json()
        view = client.get(path).json()
        assert len(view['rows']) == 1
        if remove_with_results:
            assert view['cells'] == []
            assert view['protocol_row_items'] == {"2": [], "3": ["appearance"]}
            assert response.json()['invalidated_version_ids'] == [preview['results'][0]['id']]
        else:
            assert len(view['cells']) == 1 and view['cells'][0]['adopted']['text'] == '98%'
            assert view['protocol_row_items'] == {"2": ["purity"], "3": []}
        with store.read() as conn:
            old = json.loads(conn.execute('SELECT plan_json FROM business_worksheet_plans WHERE sheet_id=? AND version=1',
                                         (sheet['id'],)).fetchone()[0])
            assert len(old['rows']) == 2 and not old['rows'][0].get('criteria_text')
            assert old['protocol_row_items']['3'] == ['appearance']


def test_stop_future_check_preserves_result_and_plan(tmp_path, config, context):
    store = Store(tmp_path / "store")
    with TestClient(create_app(store, [config], actor_for_role("local", "Local", Role.ADMIN))) as client:
        made = client.post("/api/v1/projects/PROJECT-P/worksheets", json={
            "name": "Future checks", "identity": context.defaults, "start_date": date.today().isoformat(),
            "idempotency_key": "create", "items": ["purity"],
            "points": [{"amount": n, "unit": "M", "required_items": ["purity"]} for n in (0, 6, 12)],
        })
        assert made.is_success, made.text
        path = "/api/v1/worksheets/" + made.json()["id"]
        sheet = client.get(path).json()
        row = sheet["rows"][0]["id"]
        first, middle, last = (point["id"] for point in sheet["points"])
        token = client.post(path + "/lease").json()["token"]
        recorded = client.post(path + "/save", json={
            "revision": 1, "token": token, "idempotency_key": "result",
            "changes": [{"row_id": row, "point_id": first, "intent": "initial", "value": "98%"}],
        })
        assert recorded.is_success, recorded.text
        payload = {"revision": 2, "token": token, "row_id": row, "basis": "Plan change", "idempotency_key": "stop"}
        mixed = client.post(path + "/stop-checks", json={**payload, "point_ids": [middle, first]})
        assert mixed.json()["code"] == "plan_point_has_history"
        stopped = client.post(path + "/stop-checks", json={**payload, "point_ids": [middle]})
        assert stopped.is_success, stopped.text
        assert client.post(path + "/stop-checks", json={**payload, "point_ids": [middle]}).json() == stopped.json()
        view = client.get(path).json()
        assert [point["required_row_ids"] for point in view["points"]] == [[row], [], [row]]
        assert view["stopped_checks"] == [{"row_id": row, "point_ids": [middle], "basis": "Plan change"}]
        assert view["cells"][0]["adopted"]["result_version_id"] == recorded.json()["result_version_ids"][0]
        assert view["points"][2]["id"] == last
        exported = client.post(path + "/confirmation", params={
            "revision": view["revision"], "selection_id": view["selection_id"],
        })
        assert exported.is_success, exported.text
        book = openpyxl.load_workbook(BytesIO(client.get(exported.json()["download_url"]).content))
        assert ["98%", "已停检", None] == [book.active[f"{column}9"].value for column in ("E", "F", "G")]
        book.close()
        with store.read() as conn:
            old = json.loads(conn.execute(
                "SELECT plan_json FROM business_worksheet_plans WHERE sheet_id=? AND version=1", (sheet["id"],),
            ).fetchone()[0])
            assert old["points"][1]["required_row_ids"] == [row]


def test_bad_bridge_cannot_show_another_batch_result(tmp_path, config, context):
    store = Store(tmp_path / 'store')
    app = create_app(store, [config], actor_for_role('local', '验收', Role.ADMIN))
    with TestClient(app) as client:
        first = create_sheet(client, context)
        other = create_sheet(client, context, batch='OTHER', key='other')
        sheet = client.get(first).json()
        token = client.post(first + '/lease').json()['token']
        assert client.post(first + '/save', json=dict(token=token, revision=1, idempotency_key='result', changes=[{
            'row_id': sheet['rows'][0]['id'], 'point_id': sheet['points'][1]['id'], 'value': '98%', 'intent': 'initial'}])).status_code == 200
        source = client.get(first).json()['cells'][0]['slot_id']
        with store.connect() as conn:
            target = conn.execute('SELECT slot_id FROM worksheet_result_positions WHERE sheet_id=? LIMIT 1',
                                  (other.rsplit('/', 1)[-1],)).fetchone()[0]
            conn.execute('INSERT INTO slot_identity_bridges VALUES (?,?,?,CURRENT_TIMESTAMP)', (source, target, config.project))
        assert client.get(other).json()['cells'] == []


def test_method_normalization_does_not_treat_any_r_as_reduced():
    sig = lambda method: compute_norm_signature('纯度', method, '主峰')
    assert sig('RP-HPLC') != sig('R CE-SDS')
    assert sig('RP-HPLC') != sig('HPLC')
    assert sig('SEC-HPLC Method') == sig('SEC-HPLC法')
    assert sig('NR CE-SDS') != sig('R CE-SDS')


def test_coa_excel_rejects_edited_identity_columns():
    spec = {'id': 's'}
    sample = {'id': 'sample', 'batch': 'B1'}
    preview = {'rows': [{'table': 1, 'row': 2, 'item': 'Purity', 'method': 'SEC', 'criterion': '>=95%'}]}
    content = render_coa_excel(spec, preview, sample, 0, {})
    wb = openpyxl.load_workbook(BytesIO(content))
    ws = wb['质量标准与放行(COA)']
    ws.cell(2, 7, '98%')
    def data():
        output = BytesIO(); wb.save(output); return output.getvalue()
    assert read_coa_excel(spec, preview, sample, 0, data())[0] == {'1:2': '98%'}
    ws.cell(2, 3, 'Different item')
    with pytest.raises(WorkflowError, match='顺序已变化'):
        read_coa_excel(spec, preview, sample, 0, data())
    wb.close()
