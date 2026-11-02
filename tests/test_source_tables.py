import io
import json
import sqlite3
from uuid import uuid4

import pytest
from docx import Document
from fastapi.testclient import TestClient

from stability_hub.api import create_app
from stability_hub.models import ImportContext
from stability_hub.permissions import Role, actor_for_role
from stability_hub.source_contracts import SourcePackage, SourcePreviewRequest
from stability_hub.source_docx import make_package
from stability_hub.source_tables import SourceTables
from stability_hub.storage import Store
from stability_hub.workflow import Workflow
from stability_hub.workflow_common import WorkflowError
from stability_hub.workflow_contracts import ConfirmItem, ConfirmRequest, ExportRequest
from stability_hub.result_versions import ResultVersions
from stability_hub.workflow_excel import ExcelExports

TECH = actor_for_role('tech', '实验员', Role.TECHNICIAN)
LEAD = actor_for_role('lead', '负责人', Role.PROJECT_LEAD)


def document():
    doc = Document()
    doc.add_paragraph('表13：合成夹具')
    table = doc.add_table(rows=3, cols=4)
    for cell, value in zip(table.rows[0].cells, ['Purity', '12M', '98', '%'], strict=True):
        cell.text = value
    table.cell(1, 0).merge(table.cell(2, 0)).text = '合并\n换行'
    table.cell(1, 1).add_table(rows=1, cols=1).cell(0, 0).text = '嵌套'
    doc.add_paragraph('注：保留原文及空白')
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def setup(tmp_path, config):
    flow = Workflow(Store(tmp_path / 'store'))
    flow.configure(config)
    return flow, SourceTables(flow)


def projection_package(data):
    package = make_package(data)
    package['projections'] = [{'reason': '人工指定检测值映射', 'evidence': {
        name: {'format': 'docx', 'section': 'ooxml:word/document.xml#1', 'row': 1, 'column': ci}
        for ci, name in enumerate(['test_item', 'time_point', 'result', 'unit'], 1)}}]
    return SourcePackage.model_validate(package)


def test_full_archive_no_facts_retry_paging_restart(tmp_path, config):
    flow, service = setup(tmp_path, config)
    data = document()
    package = projection_package(data)
    view = service.ingest(config.project, 'data.docx', data, package, 'retry', actor=TECH)
    assert view['summary']['total_tables'] == 2
    assert service.ingest(config.project, 'data.docx', data, package, 'retry', actor=TECH) == view
    assert flow.results(config.project, actor=TECH).total == 0
    assert view['tables'][0]['rows'][1][0]['rowspan'] == 2
    assert view['tables'][0]['rows'][2][0]['continuation']
    assert view['tables'][0]['footnotes'] == ['注：保留原文及空白']
    assert view['tables'][1]['parent_table_id'] == 'table-1'
    page = service.table(view['import_id'], 'table-1', 2, 1, actor=TECH)
    assert page['total'] == 3 and page['rows'][0][0]['row'] == 3
    restarted = SourceTables(Workflow(Store(flow.store.home)))
    assert restarted.get(view['import_id'], actor=LEAD) == view
    with flow.store.connect() as conn:
        assert conn.execute('SELECT count(*) FROM workflow_candidates').fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute('DELETE FROM source_imports')


def test_reject_bad_package_and_permissions(tmp_path, config):
    flow, service = setup(tmp_path, config)
    data = document()
    package = projection_package(data)
    with pytest.raises(PermissionError):
        service.ingest(config.project, 'x.docx', data, package, 'k', actor=LEAD)
    for bad in [package.model_copy(update={'source_hash': 'a'*64}),
                package.model_copy(update={'tables': package.tables[:-1]})]:
        with pytest.raises(ValueError):
            service.ingest(config.project, 'x.docx', data, bad, 'k', actor=TECH)
    bad = package.model_dump()
    bad['projections'][0]['evidence']['result']['row'] = 99
    with pytest.raises(ValueError):
        service.ingest(config.project, 'x.docx', data, SourcePackage.model_validate(bad), 'k', actor=TECH)
    with flow.store.connect() as conn:
        assert conn.execute('SELECT count(*) FROM source_imports').fetchone()[0] == 0
    service.ingest(config.project, 'x.docx', data, package, 'k', actor=TECH)
    with pytest.raises(WorkflowError):
        service.ingest(config.project, 'different.docx', data, package, 'k', actor=TECH)


def test_projection_existing_confirmation_duplicate_and_excel(tmp_path, config, context):
    flow, service = setup(tmp_path, config)
    data = document()
    source = service.ingest(config.project, 'x.docx', data, projection_package(data), 'archive', actor=TECH)
    revision = flow.results(config.project, actor=TECH).base_revision
    request = SourcePreviewRequest(base_revision=revision, idempotency_key='p', context=context)
    preview = service.preview(source['import_id'], request, actor=TECH)
    assert service.preview(source['import_id'], request, actor=TECH) == preview
    assert preview.candidates[0].status == 'new'
    candidate = preview.candidates[0]
    command = ConfirmRequest(base_revision=preview.base_revision, idempotency_key=uuid4().hex, items=(
        ConfirmItem(candidate_id=candidate.id, candidate_revision=candidate.candidate_revision,
                    detection_id=candidate.detection_id, identity_confirmed=True, intent='initial', reason='核对原表'),))
    ResultVersions(flow).confirm(preview.preview_id, command, actor=TECH)
    page = flow.results(config.project, actor=TECH)
    assert page.total == 1 and page.cells[0].display.text.startswith('98')
    linked = service.get(source['import_id'], actor=TECH)
    assert candidate.id in linked['tables'][0]['rows'][0][2]['candidate_ids']
    exported = ExcelExports(flow).create(ExportRequest(project=config.project, base_revision=page.base_revision), actor=LEAD)
    assert exported.download_url
    again = service.preview(source['import_id'], SourcePreviewRequest(base_revision=page.base_revision,
                              idempotency_key='new-upload', context=context), actor=TECH)
    assert again.candidates[0].status == 'same_value'


def test_original_tamper_and_independent_field_check(tmp_path, config):
    from stability_hub.models import RawObservation
    from stability_hub.verification import check_original_fields, reread_source_rows
    flow, service = setup(tmp_path, config)
    data = document()
    pack = projection_package(data)
    archived = service.ingest(config.project, 'x.docx', data, pack, 'one', actor=TECH)
    path, _ = service.original(archived['import_id'], actor=TECH)
    tables = reread_source_rows(path, archive_only=True)
    evidence = pack.projections[0].evidence
    raw = RawObservation(fields={'test_item': 'Purity', 'time_point': '12M', 'result': '98', 'unit': '%'},
                         location=evidence['result'], evidence=evidence)
    assert check_original_fields(tables, raw)
    assert not check_original_fields(tables, raw.model_copy(update={'fields': {**raw.fields, 'result': '99'}}))
    path.write_bytes(b'tampered isolated test source')
    with pytest.raises(WorkflowError, match='哈希'):
        service.original(archived['import_id'], actor=TECH)


def test_assets_math_and_merge_evidence(tmp_path, config):
    from docx.oxml import parse_xml
    import base64
    doc = Document()
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).merge(table.cell(1, 0)).text = 'merge'
    png = io.BytesIO(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aU1sAAAAASUVORK5CYII='))
    table.cell(0, 1).paragraphs[0].add_run().add_picture(png)
    table.cell(1, 1)._tc.append(parse_xml('<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><m:r><m:t>x+1</m:t></m:r></m:oMath>'))
    out = io.BytesIO()
    doc.save(out)
    data = out.getvalue()
    pack = SourcePackage.model_validate(make_package(data))
    flow, service = setup(tmp_path, config)
    view = service.ingest(config.project, 'assets.docx', data, pack, 'assets', actor=TECH)
    rows = view['tables'][0]['rows']
    assert rows[1][1]['formula'] == 'x+1' and rows[0][1]['has_image']
    part = rows[0][1]['references'][0]['part']
    assert service.asset(view['import_id'], part, actor=TECH) == png.getvalue()
    with pytest.raises(WorkflowError):
        service.asset(view['import_id'], '../../secret', actor=TECH)
    bad = pack.model_dump()
    bad['projections'] = [{'reason':'wrong merged location','evidence': {'result': {
        'format':'docx','section':'ooxml:word/document.xml#1','row':2,'column':1}}}]
    with pytest.raises(ValueError, match='合并'):
        service.ingest(config.project, 'assets.docx', data, SourcePackage.model_validate(bad), 'bad', actor=TECH)


def test_real_http_archive(tmp_path, config):
    app = create_app(Store(tmp_path / 'http'), [config], TECH)
    data = document()
    with TestClient(app) as client:
        response = client.post('/api/v1/projects/PROJECT-P/source-imports',
            files={'file': ('x.docx', data), 'package': ('x.json', json.dumps(make_package(data)))},
            headers={'Idempotency-Key': 'one'})
        assert response.status_code == 200, response.text
        archive_id = response.json()['import_id']
        assert len(client.get('/api/v1/projects/PROJECT-P/source-imports').json()) == 1
        assert client.get(f'/api/v1/source-imports/{archive_id}/tables/table-1?offset=2&limit=1').json()['total'] == 3
        response = client.post(f'/api/v1/source-imports/{archive_id}/previews', json={
            'base_revision': 1, 'idempotency_key': 'none', 'context': ImportContext().model_dump()})
        assert response.status_code == 422
