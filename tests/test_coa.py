from io import BytesIO

from docx import Document
from fastapi.testclient import TestClient

from stability_hub.api import create_app
from stability_hub.models import ProjectConfig
from stability_hub.permissions import Role, actor_for_role
from stability_hub.storage import Store


def content(doc):
    output = BytesIO()
    doc.save(output)
    return output.getvalue()


def test_formal_workbench_requires_sample_and_creates_from_confirmed_identity(tmp_path, config, context):
    actor = actor_for_role("local-user", "Local", Role.ADMIN)
    app = create_app(Store(tmp_path), [config], actor, enforce_formal_sample_binding=True)
    with TestClient(app) as client:
        endpoint = "/api/v1/projects/PROJECT-P/worksheets"
        command = {"idempotency_key": "create-sample-required", "name": "Study", "identity": context.defaults,
                   "start_date": "2026-11-17", "items": ["purity"],
                   "points": [{"unit": "M", "amount": 0, "required_items": ["purity"]}]}
        missing = client.post(endpoint, json=command)
        assert missing.status_code == 409 and missing.json()["code"] == "worksheet_sample_required"
        command["material_stage"] = "验收材料"
        made = client.post(endpoint, json=command)
        assert made.status_code == 201, made.text
        sheet = client.get("/api/v1/worksheets/" + made.json()["id"]).json()
        assert sheet["sample_id"]
        assert client.post(endpoint, json=command).json() == made.json()


def test_coa_word_round_trip_and_version_guard(tmp_path):
    store = Store(tmp_path)
    actor = actor_for_role('test', 'Test', Role.ADMIN)
    doc = Document()
    doc.add_paragraph('Quality standard cover')
    table = doc.add_table(rows=3, cols=4)
    table.cell(0, 0).merge(table.cell(0, 1))
    for row, texts in enumerate([['Test Item', '', 'Method', 'Specification'],
                                 ['Purity', 'Monomer', 'SEC', '≥90%'],
                                 ['Purity', 'HMW', 'SEC', '≤5%']]):
        for column, value in enumerate(texts):
            if value:
                table.cell(row, column).text = value
    original = content(doc)
    with TestClient(create_app(store, [ProjectConfig(project='P')], actor)) as client:
        blocked = client.post('/api/v1/projects/TEST/previews',
                              headers={'Idempotency-Key': 'sandbox-history'},
                              files={'file': ('history.xlsx', b'not parsed in sandbox')})
        assert blocked.status_code == 409 and blocked.json()['code'] == 'sandbox_history_import'
        standard = client.post('/api/v1/specifications', files={'file': ('standard DS.docx', original)})
        assert standard.status_code == 200, standard.text
        spec = standard.json()['id']
        sample_path = '/api/v1/projects/P/samples'
        sample_input = {'batch': '00017', 'material_stage': 'ADC', 'sample_type': 'DS', 'purpose': '放行', 'presentation': '1mg'}
        sample = client.post(sample_path, json=sample_input).json()
        assert client.post(sample_path, json=sample_input).json()['id'] == sample['id']
        other = client.post(sample_path, json={**sample_input, 'material_stage': '裸抗'}).json()
        assert other['id'] != sample['id']
        assert len(client.get(sample_path, params={'batch': '00017'}).json()) == 2
        path = f"/api/v1/samples/{sample['id']}/coa/{spec}"
        download = client.get(path + '/file')
        assert download.status_code == 200, download.text
        template = Document(BytesIO(download.content))
        assert len(template.tables[0].columns) == 5
        assert template.tables[0].cell(0, 4).text == '00017'
        assert template.tables[0].cell(1, 4).text == ''
        template.tables[0].cell(1, 4).text = 'NA*'
        filled = content(template)

        def preview(data):
            return client.post(path + '/preview', files={'file': ('coa.docx', data)})

        first = preview(filled).json()
        command = {key: first[key] for key in ('upload_id', 'values', 'revision', 'version')}
        command['idempotency_key'] = 'upload'
        saved = client.post(path, json=command)
        assert saved.status_code == 200, saved.text
        assert saved.json()['values'] == {'1:2': 'NA*'}
        assert client.post(path, json=command).json() == saved.json()
        repeat = preview(filled).json()
        same = client.post(path, json={**repeat, 'idempotency_key': 'repeat'})
        assert same.json()['changed'] == 0
        base = '/api/v1/projects/P'
        registered = client.post(base + '/specifications/' + spec + '/components', json={
            'revision': client.get(base + '/configuration').json()['base_revision'], 'idempotency_key': 'register',
            'components': [{'table': 1, 'row': 2, 'assay_zh': 'Purity', 'component_zh': 'Monomer',
                            'method': 'SEC', 'unit': '%', 'result_type': 'numeric'}],
        })
        assert registered.is_success, registered.text
        selected_standard = {'specification_id': spec, 'idempotency_key': 'set-current-standard',
                             'revision': client.get(base + '/configuration').json()['base_revision']}
        activated = client.post(base + '/current-specifications', json=selected_standard)
        assert activated.is_success, activated.text
        assert client.post(base + '/current-specifications', json=selected_standard).json() == activated.json()
        assert client.get(base + '/current-specifications').json()[0]['specification_id'] == spec
        item = registered.json()['links'][0]['item_id']
        study_command = {
            'name': 'COA study', 'idempotency_key': 'study', 'start_date': '2026-01-02',
            'identity': {'batch': sample['batch'], 'presentation': '1mg', 'sample_type': 'DS',
                         'study': 'long_term', 'study_id': 'STUDY', 'condition': '5C'},
            'items': [item], 'points': [{'amount': 0, 'unit': 'M', 'required_items': [item]},
                                        {'amount': 1, 'unit': 'M', 'required_items': [item]}],
            'item_standards': {item: [{'specification_id': spec, 'table': 1, 'row': 2}]},
        }
        created = client.post(base + '/worksheets', json=study_command)
        assert created.is_success, created.text
        sheet_path = '/api/v1/worksheets/' + created.json()['id']
        sheet = client.get(sheet_path).json()
        row = sheet['rows'][0]['id']
        assert client.get(sheet_path + '/coa-release-options', params={'row_id': row}).json() == []
        candidates = client.get(sheet_path + '/sample-candidates').json()
        assert {entry['id'] for entry in candidates if entry['compatible']} == {sample['id'], other['id']}
        token = client.post(sheet_path + '/lease').json()['token']
        link = {'token': token, 'revision': sheet['revision'], 'sample_id': sample['id'],
                'basis': 'Confirmed material stage', 'idempotency_key': 'link-sample'}
        linked = client.post(sheet_path + '/sample', json=link)
        assert linked.is_success, linked.text
        assert client.post(sheet_path + '/sample', json=link).json() == linked.json()
        sheet = client.get(sheet_path).json()
        assert sheet['sample_id'] == sample['id'] and sheet['plan_version'] == 2
        options = client.get(sheet_path + '/coa-release-options', params={'row_id': row}).json()
        assert [option['value'] for option in options] == ['NA*']
        reference = client.post(sheet_path + '/release-reference', json={
            'token': token, 'revision': sheet['revision'], 'idempotency_key': 'release-reference',
            'row_id': row, 'basis': 'Confirmed COA and study', 'release_completed': '2026-01-01',
            'coa_specification_id': spec, 'coa_version': 1,
        })
        assert reference.is_success, reference.text
        view = client.get(sheet_path).json()
        assert view['cells'][0]['raw'] == 'NA*' and view['cells'][0]['adopted'] is None
        assert view['cells'][0]['release_reference']['current']
        source_id = view['release_references'][row]['coa']['source_id']
        assert client.get('/api/v1/coa-sources/' + source_id + '/file').content == filled
        confirmation = client.post(sheet_path + '/confirmation', params={
            'revision': view['revision'], 'selection_id': view['selection_id'],
        })
        assert confirmation.is_success, confirmation.text
        measured = client.post(sheet_path + '/save', json={
            'token': token, 'revision': view['revision'], 'idempotency_key': 'one-month',
            'changes': [{'row_id': row, 'point_id': view['points'][1]['id'], 'intent': 'initial', 'value': '98.90%'}],
        })
        assert measured.is_success, measured.text

        def word_report():
            revision = client.get(base + '/result-cells').json()['base_revision']
            generated = client.post('/api/v1/reports', json={'project': 'P', 'base_revision': revision,
                                                            'stability_document': {}, 'include_charts': False})
            assert generated.is_success, generated.text
            url = generated.json()['word_download_url']
            return url, client.get(url).content

        def report_cells(content):
            doc = Document(BytesIO(content))
            return [cell.text for table in doc.tables for row in table.rows for cell in row.cells]

        original_report, original_content = word_report()
        assert 'NA*（引用放行）' in report_cells(original_content)
        template.tables[0].cell(1, 4).text = '95.00%'
        assert preview(content(template)).status_code == 409
        current = client.get(path).json()
        manual = client.post(path, json={**current, 'idempotency_key': 'web', 'values': {'1:2': '95.00%', '1:3': ''}})
        assert manual.json()['version'] == 2
        stale = client.get(sheet_path).json()['cells'][0]
        assert stale['raw'] == 'NA*' and not stale['release_reference']['current']
        assert '放行引用待复核' in report_cells(word_report()[1])
        assert client.get(original_report).content == original_content
        result = Document(BytesIO(client.get(path + '/file').content))
        assert result.tables[0].cell(1, 4).text == '95.00%'
        assert result.tables[0].cell(2, 4).text == ''
        result.tables[0].cell(1, 3).text = 'Altered criterion'
        assert preview(content(result)).json()['code'] == 'coa_structure'
        assert client.get(f'/api/v1/specifications/{spec}/file').content == original
        # Test Excel CoA export, fill, and preview roundtrip
        excel_download = client.get(path + '/file?format=xlsx')
        assert excel_download.status_code == 200, excel_download.text
        import openpyxl
        wb = openpyxl.load_workbook(BytesIO(excel_download.content))
        assert "质量标准与放行(COA)" in wb.sheetnames
        assert "_stability_coa" in wb.sheetnames
        ws = wb["质量标准与放行(COA)"]
        assert ws.cell(row=1, column=7).value == "00017 (放行结果)"
        # Fill release value in excel
        ws.cell(row=2, column=7).value = "98.50%"
        excel_buf = BytesIO()
        wb.save(excel_buf)
        excel_filled = excel_buf.getvalue()

        excel_preview_resp = client.post(path + '/preview', files={'file': ('coa.xlsx', excel_filled)})
        assert excel_preview_resp.status_code == 200, excel_preview_resp.text
        excel_preview_data = excel_preview_resp.json()
        assert excel_preview_data['values'].get('1:2') == "98.50%"

        # Save excel coa
        cmd_excel = {
            'upload_id': excel_preview_data['upload_id'],
            'values': excel_preview_data['values'],
            'revision': excel_preview_data['revision'],
            'version': excel_preview_data['version'],
            'idempotency_key': 'save-excel-coa'
        }
        excel_save_resp = client.post(path, json=cmd_excel)
        assert excel_save_resp.status_code == 200, excel_save_resp.text
        assert excel_save_resp.json()['values']['1:2'] == "98.50%"

        # Verify sample_release_results table updated
        with store.read() as conn:
            sr = conn.execute("SELECT raw_value FROM sample_release_results WHERE sample_id=? AND item_id=?",
                              (sample['id'], item)).fetchone()
            assert sr is not None
            assert sr['raw_value'] == "98.50%"

        # Verify worksheet 0M auto-discovery of available_coa_references
        sheet_view = client.get(sheet_path).json()
        assert "available_coa_references" in sheet_view
        assert row in sheet_view["available_coa_references"]
        assert any(opt["value"] == "98.50%" for opt in sheet_view["available_coa_references"][row])
        option = client.get(sheet_path + '/coa-release-options', params={'row_id': row}).json()[0]
        command = {'token': token, 'revision': sheet_view['revision'], 'idempotency_key': 'copy-coa',
                   'row_id': row, 'coa_fingerprint': option['fingerprint'], 'intent': 'initial'}
        copied = client.post(sheet_path + '/copy-coa-to-0m', json=command)
        assert copied.is_success, copied.text
        assert client.post(sheet_path + '/copy-coa-to-0m', json=command).json() == copied.json()
        version_id, = copied.json()['result_version_ids']
        copied_view = client.get(sheet_path).json()
        zero = next(cell for cell in copied_view['cells'] if cell['point_id'] == sheet_view['points'][0]['id'])
        assert zero['adopted']['result_version_id'] == version_id
        assert zero['raw'] == '98.50%' and row not in copied_view['release_references']
        cleared = client.post(sheet_path + '/save', json={
            'token': token, 'revision': copied_view['revision'], 'idempotency_key': 'clear-copy',
            'changes': [{'row_id': row, 'point_id': zero['point_id'], 'intent': 'correction',
                         'expected_version_id': version_id, 'value': ''}],
        })
        assert cleared.is_success, cleared.text
        with store.read() as conn:
            assert conn.execute('SELECT 1 FROM workflow_invalidated_versions WHERE version_id=?',
                                (version_id,)).fetchone()
        sample_path = '/api/v1/samples/' + sample['id']
        impact = client.get(sample_path + '/archive-impact').json()
        assert [entry['id'] for entry in impact['worksheets']] == [sheet_view['id']]
        archive_command = {'fingerprint': impact['fingerprint'], 'basis': '误建样品',
                           'idempotency_key': 'archive-sample'}
        assert client.request('DELETE', sample_path, json=archive_command).status_code == 409
        assert client.post(sheet_path + '/lease/release', json={'token': token}).is_success
        archived_sample = client.request('DELETE', sample_path, json=archive_command)
        assert archived_sample.is_success, archived_sample.text
        assert client.request('DELETE', sample_path, json=archive_command).json() == archived_sample.json()
        assert len(archived_sample.json()['invalidated_version_ids']) == 1
        assert client.get(sheet_path).status_code == 404
        assert client.get(path).status_code == 404
        assert client.get(original_report).content == original_content
        reopened = client.post(base + '/worksheets', json={**study_command, 'idempotency_key': 'study-replacement'})
        assert reopened.is_success, reopened.text
        replacement_id = reopened.json()['id']
        assert replacement_id != sheet_view['id']
        replacement = client.get('/api/v1/worksheets/' + replacement_id).json()
        assert not any(cell.get('adopted') for cell in replacement['cells'])

    with store.read() as conn:
        assert conn.execute('SELECT count(*) FROM coa_versions').fetchone()[0] == 3
        assert conn.execute('SELECT count(*) FROM coa_sources').fetchone()[0] == 2
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


def test_repeated_standard_registration_reports_existing_and_conflicts(tmp_path):
    store = Store(tmp_path)
    actor = actor_for_role("reviewer", "Reviewer", Role.ADMIN)
    word = Document()
    table = word.add_table(rows=3, cols=4)
    for row, values in enumerate((("Test Item", "Subresult", "Method", "Specification"),
                                  ("Purity", "Monomer", "SEC", "≥90%"),
                                  ("Purity", "HMW", "SEC", "≤5%"))):
        for column, value in enumerate(values):
            table.cell(row, column).text = value
    source = content(word)
    with TestClient(create_app(store, [ProjectConfig(project="P"), ProjectConfig(project="Q")], actor)) as client:
        def upload():
            response = client.post("/api/v1/specifications", files={"file": ("P DS.docx", source)})
            assert response.is_success, response.text
            return response.json()["id"]

        spec = upload()
        assert upload() == spec
        base = "/api/v1/projects/P"
        endpoint = base + f"/specifications/{spec}/components"
        monomer = {"table": 1, "row": 2, "assay_zh": "Purity", "component_zh": "Monomer",
                   "method": "SEC", "unit": "%", "result_type": "numeric"}
        hmw = {**monomer, "row": 3, "component_zh": "HMW"}

        def register(key, *components):
            revision = client.get(base + "/configuration").json()["base_revision"]
            return client.post(endpoint, json={"revision": revision, "idempotency_key": key, "components": components})

        added = register("add", monomer)
        assert added.is_success, added.text
        repeated = register("repeat", monomer).json()
        assert repeated == {**added.json(), "added": 0, "existing": 1}
        for change in ({"method": "NR CE-SDS"}, {"unit": "ppm"}, {"component_zh": "Another component"}):
            conflict = register("conflict-" + next(iter(change)), hmw, {**monomer, **change})
            assert conflict.status_code == 409, conflict.text
            assert "表 1 行 2" in conflict.json()["message"]
            assert len(client.get(endpoint).json()) == 1
        expanded = register("expand", monomer, hmw)
        assert expanded.is_success, expanded.text
        assert (expanded.json()["added"], expanded.json()["existing"]) == (1, 1)
        rows = client.get(endpoint).json()
        assert [row["component_zh"] for row in rows] == ["Monomer", "HMW"]
        assert len({row["item_id"] for row in rows}) == 2
        assert client.get(endpoint.replace("/P/", "/Q/")).json() == []
