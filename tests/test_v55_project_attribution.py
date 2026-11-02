from io import BytesIO
from openpyxl import Workbook
from starlette.testclient import TestClient

from stability_hub.api import create_app
from stability_hub.permissions import Role, actor_for_role
from stability_hub.storage import Store
from stability_hub.workflow import Workflow

TECH = actor_for_role('tech', 'technician', Role.TECHNICIAN)


def test_hash_filename_does_not_extract_false_project(tmp_path):
    book = Workbook()
    sheet = book.active
    # Sheet has no project header
    sheet.append(['test_item', 'result'])
    sheet.append(['pH', '6.5'])
    file_bytes = BytesIO()
    book.save(file_bytes)

    store = Store(tmp_path / 'store')
    client = TestClient(create_app(store, [], TECH))

    # Filename is a 64-char sha256 hash containing 'bc355e'
    hash_filename = '962a0b6bc355e1eeb42179a368fd2b75aa319ae4769b7a106ed5b477bdeef4a5.xlsx'
    resp = client.post('/api/v1/intake/inspect', files={'file': (hash_filename, file_bytes.getvalue())})
    assert resp.status_code == 200
    data = resp.json()
    # It must NOT guess BC355E from the hash string
    assert 'BC355E' not in data['projects']
    assert data['projects'] == []


def test_content_project_overrides_duplicate_conflict(tmp_path):
    book = Workbook()
    sheet = book.active
    sheet.append(['project', 'test_item', 'result'])
    sheet.append(['IP275', 'pH', '6.5'])
    file_bytes = BytesIO()
    book.save(file_bytes)
    data = file_bytes.getvalue()

    store = Store(tmp_path / 'store')
    app = create_app(store, [], TECH)
    client = TestClient(app)

    # First, simulate an old erroneous upload under BC355E
    flow = Workflow(store)
    from stability_hub.workflow_contracts import ProjectCreation
    flow.create_project(ProjectCreation(project='BC355E', name='BC355E'), actor=TECH)
    with store.connect() as conn:
        import_sha = __import__('hashlib').sha256(data).hexdigest()
        conn.execute("INSERT INTO documents(sha256, filename, stored_path) VALUES (?, 'test.xlsx', 'test.xlsx')", (import_sha,))
        conn.execute(
            "INSERT INTO workflow_uploads(id, project, source_hash, filename, context_json, uploaded_by, client_key, request_hash) "
            "VALUES ('up_old', 'BC355E', ?, 'test.xlsx', '{}', 'tech', 'k1', 'r1')",
            (import_sha,)
        )

    # Now inspect the same file
    resp = client.post('/api/v1/intake/inspect', files={'file': ('upload.xlsx', data)})
    assert resp.status_code == 200
    res = resp.json()
    assert res['duplicate_source'] is True
    assert res['existing_project'] == 'BC355E'
    # The detected project from content is IP275, and conflict is explicitly reported
    assert res['projects'] == ['IP275']
    assert 'project_conflict' in res
    assert res['project_conflict']['existing_project'] == 'BC355E'
    assert res['project_conflict']['detected_projects'] == ['IP275']
