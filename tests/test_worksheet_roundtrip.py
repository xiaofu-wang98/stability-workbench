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

        def commit(view, key, decisions=None):
            token = client.post(sheet_path + '/lease').json()['token']
            try:
                response = client.post(sheet_path + '/study-workbooks/' + view['source_id'], json={
                    'token': token, 'revision': view['revision'], 'fingerprint': view['fingerprint'],
                    'idempotency_key': key, 'reason': 'Reviewed uploaded result', 'decisions': decisions or {},
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
        first = preview(filled).json()
        assert [cell['status'] for cell in first['changes']] == ['new']
        initial = commit(first, 'initial')['result_version_ids'][0]
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
        assert preview(filled).json()['changes'][0]['status'] == 'conflict'
        refreshed = edit_workbook(export(), 'D6', '2.40')
        assert preview(refreshed).json()['changes'][0]['status'] == 'new'
    with store.read() as conn:
        versions = conn.execute('SELECT id,slot_id,source_json FROM workflow_versions ORDER BY upload_sequence').fetchall()
        assert len(versions) == 2 and versions[0]['slot_id'] == versions[1]['slot_id']
        assert all('D6' in row['source_json'] and 'worksheet_return' in row['source_json'] for row in versions)
        assert not conn.execute('PRAGMA foreign_key_check').fetchall()
