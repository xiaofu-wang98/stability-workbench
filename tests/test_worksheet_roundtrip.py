from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from stability_hub.api import create_app
from stability_hub.permissions import Role, actor_for_role
from stability_hub.storage import Store
from test_business_worksheets import create_sheet


def edit_workbook(data, coordinate, value):
    book = load_workbook(BytesIO(data))
    book.active[coordinate] = value
    output = BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


def strip_template_metadata(data):
    with BytesIO() as output:
        book = load_workbook(BytesIO(data))
        del book["_stability_study"]
        book.save(output)
        book.close()
        return output.getvalue()


def test_controlled_export_stacks_batches_without_roundtrip_contract(tmp_path, config, context):
    store = Store(tmp_path / "store")
    actor = actor_for_role("local", "Local", Role.ADMIN)
    with TestClient(create_app(store, [config], actor)) as client:
        first = create_sheet(client, context)
        second = create_sheet(client, context, batch="SECOND-BATCH", key="second")
        view = client.get(first).json()
        token = client.post(first + "/lease").json()["token"]
        point = view["points"][1]
        event = client.post(first + "/events", json={
            "token": token, "revision": view["revision"], "idempotency_key": "controlled-remark",
            "category": "异常调查", "reference": "AE-TEST-001", "description": "复测说明已核对",
            "row_id": view["rows"][0]["id"], "point_id": point["id"],
        })
        assert event.is_success, event.text
        project = "/api/v1/projects/PROJECT-P"
        revision = client.get(project + "/configuration").json()["base_revision"]
        response = client.post(project + "/controlled-workbook", json={
            "base_revision": revision, "sheet_ids": [path.rsplit("/", 1)[-1] for path in (first, second)],
        })
        assert response.is_success, response.text
        file = client.get(response.json()["download_url"])
        assert file.is_success
        book = load_workbook(BytesIO(file.content))
        try:
            assert len(book.sheetnames) == 1
            rows = list(book.active.values)
            headers = [(idx, row[5]) for idx, row in enumerate(rows) if row[0] == "项目编号\nProject No."]
            assert [batch for _, batch in headers] == [context.defaults["batch"], "SECOND-BATCH"]
            assert "☑长期试验 Long-term study" in rows[1][7]
            assert "温度 Temperature" in rows[2][2] and "放置方式 Placement method" in rows[2][2]
            assert {"A3:B3", "C3:G3", "H3:I3"} <= {str(area) for area in book.active.merged_cells.ranges}
            assert "." in rows[4][3].splitlines()[1]
            assert all(value is None for row in rows[headers[1][0] - 2:headers[1][0]] for value in row)
            remarks = [row for row in rows if row[0] == "备注 / Remark"]
            assert "AE-TEST-001 · 复测说明已核对" in remarks[0][3]
            assert all(value is None for value in remarks[1][1:])
        finally:
            book.close()


def test_study_workbook_preserves_scope_versions_and_provenance(tmp_path, config, context):
    store = Store(tmp_path / 'store')
    actor = actor_for_role('local', 'Local', Role.ADMIN)
    with TestClient(create_app(store, [config], actor)) as client:
        sheet_path = create_sheet(client, context)

        def export(filled=False):
            response = client.post(sheet_path + '/study-workbook', params={'filled': filled})
            assert response.status_code == 200, response.text
            file = client.get(response.json()['download_url'])
            assert file.status_code == 200, file.text
            return file.content

        def preview(data):
            return client.post('/api/v1/study-workbooks/preview', files={'file': ('test.xlsx', data)})

        def commit(view, key, decisions=None, coordinates=None):
            token = client.post(sheet_path + '/lease').json()['token']
            try:
                response = client.post(sheet_path + '/study-workbooks/' + view['source_id'], json={
                    'token': token, 'revision': view['revision'], 'fingerprint': view['fingerprint'],
                    'idempotency_key': key, 'reason': 'Reviewed uploaded result', 'decisions': decisions or {},
                    **({'coordinates': coordinates} if coordinates is not None else {}),
                })
                assert response.status_code == 200, response.text
                return response.json()
            finally:
                assert client.post(sheet_path + '/lease/release', json={'token': token}).status_code == 200

        blank = export()
        with TestClient(create_app(Store(tmp_path / 'other-store'), [config], actor)) as other:
            foreign = other.post('/api/v1/study-workbooks/preview',
                                 files={'file': ('foreign.xlsx', edit_workbook(blank, 'D6', '2.10'))})
            assert foreign.status_code == 404
            assert foreign.json()['code'] == 'worksheet_file_snapshot'
        assert preview(blank).json()['changes'] == []
        assert preview(edit_workbook(blank, 'F1', 'other batch')).json()['code'] == 'worksheet_file_structure'
        assert preview(edit_workbook(blank, 'D6', '=1+1')).json()['code'] == 'worksheet_file_formula'
        assert preview(edit_workbook(blank, 'C6', '2.10')).json()['changes'][0]['status'] == 'blocked'
        filled = edit_workbook(blank, 'D6', '2.10')
        visible_only = strip_template_metadata(filled)
        export()
        assert preview(visible_only).json()['changes'][0]['status'] == 'new'
        first = preview(edit_workbook(filled, 'C6', '2.10')).json()
        assert [cell['status'] for cell in first['changes']] == ['blocked', 'new']
        partial = commit(first, 'initial', coordinates=['D6'])
        assert partial['remaining_coordinates'] == ['C6']
        initial = partial['result_version_ids'][0]
        assert commit(first, 'initial', coordinates=['D6']) == partial
        assert commit(preview(filled).json(), 'repeat')['result_version_ids'] == []
        baseline = export(filled=True)
        ambiguous = preview(visible_only)
        assert ambiguous.status_code == 409
        assert ambiguous.json()['code'] == 'worksheet_file_ambiguous'
        assert [cell['status'] for cell in preview(baseline).json()['changes']] == ['unchanged']
        updated = edit_workbook(baseline, 'D6', '2.20')
        corrected = commit(preview(updated).json(), 'updated')['result_version_ids'][0]
        assert corrected != initial
        competing = preview(edit_workbook(baseline, 'D6', '2.30')).json()
        assert competing['changes'][0]['status'] == 'conflict'
        assert commit(competing, 'keep-current', {'D6': 'keep'})['result_version_ids'] == []
        assert [cell['status'] for cell in preview(baseline).json()['changes']] == ['unchanged']
        sheet = client.get(sheet_path).json()
        assert sheet['cells'][0]['adopted']['result_version_id'] == corrected
        assert sheet['cells'][0]['raw'] == '2.20'
        revision = client.get('/api/v1/projects/PROJECT-P/configuration').json()['base_revision']
        for version in (initial, corrected):
            voided = client.post(f'/api/v1/results/{version}/invalidate', json={
                'base_revision': revision, 'idempotency_key': 'void-' + version, 'reason': 'Invalid test result',
            })
            assert voided.status_code == 200, voided.text
            revision = voided.json()['base_revision']
        stale = preview(filled).json()
        assert stale['changes'][0]['status'] == 'conflict'
        assert stale['changes'][0]['voided_since_export'] is True
        token = client.post(sheet_path + '/lease').json()['token']
        try:
            rejected = client.post(sheet_path + '/study-workbooks/' + stale['source_id'], json={
                'token': token, 'revision': stale['revision'], 'fingerprint': stale['fingerprint'],
                'idempotency_key': 'restore-void', 'reason': 'Attempt to use pre-void workbook',
                'decisions': {'D6': 'correct'},
            })
            assert rejected.status_code == 409
            assert rejected.json()['code'] == 'worksheet_file_voided'
        finally:
            assert client.post(sheet_path + '/lease/release', json={'token': token}).status_code == 200
        refreshed = edit_workbook(export(), 'D6', '2.40')
        assert preview(refreshed).json()['changes'][0]['status'] == 'new'
    with store.read() as conn:
        versions = conn.execute('SELECT id,slot_id,source_json FROM workflow_versions ORDER BY upload_sequence').fetchall()
        assert len(versions) == 2 and versions[0]['slot_id'] == versions[1]['slot_id']
        assert all('D6' in row['source_json'] and 'worksheet_return' in row['source_json'] for row in versions)
        assert not conn.execute('PRAGMA foreign_key_check').fetchall()
