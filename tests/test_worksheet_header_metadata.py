import json
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from stability_hub.api import create_app, get_actor
from stability_hub.business_worksheets import BusinessWorksheets
from stability_hub.permissions import Role, actor_for_role
from stability_hub.result_versions import ResultVersions
from stability_hub.storage import Store
from stability_hub.worksheet_seed import provision_from_results
from test_workflow import TECH, command, confirm, upload

PROJECT = "/api/v1/projects/PROJECT-P"
ADMIN = actor_for_role("admin", "Admin", Role.ADMIN)


def historical_source(path, batch, equipment, location, *, storage="FREEZER-1"):
    with_header = Workbook()
    sheet = with_header.active
    sheet.title = "DP长期"
    sheet.append(["产品批号", batch, "规格", "pen-1mg", "稳定性试验方案编号", "STUDY-P-01"])
    sheet.append(["开始日期", "2024-01-01", "设备编号", equipment, "材料阶段", "制剂"])
    sheet.append(["考察条件", "5℃", "存放位置", storage, "稳定性开展地点", location])
    sheet.append(["检验项目", "可接受标准", "考察时间（月）"])
    sheet.append([None, None, "0M", "3M"])
    sheet.append(["Purity", "≥95%", 98, 97])
    with_header.save(path)
    with_header.close()
    return path


def upload_history(flow, source, context):
    scope = context.model_copy(update={"defaults": {**context.defaults, "condition": "5℃"}})
    return upload(flow, source, scope)


def seed_of(client, batch):
    results = client.get(PROJECT + "/result-cells", params={"batch": batch}).json()["cells"]
    slot = results[0]["slot_id"]
    response = client.get(PROJECT + "/worksheet-seed", params={"batch": batch, "slot_id": slot})
    assert response.is_success, response.text
    return slot, response.json()


def create_historical(client, slot, seed, *, confirm_metadata):
    payload = {
        "name": seed["identity"]["batch"], "identity": seed["identity"], "start_date": seed["start_date"],
        "seed_slot_id": slot, "idempotency_key": seed["identity"]["batch"],
        "basis": "Checked the frozen source header", "items": [item["id"] for item in seed["items"]],
        "points": [{"amount": int(point["label"][:-1]), "unit": point["label"][-1],
                    "required_items": point["items"]} for point in seed["points"]],
    }
    if confirm_metadata:
        payload.update({field: seed[field] for field in ("equipment", "location", "product_name", "specification_text", "protocol_number", "storage_location", "metadata_fingerprint")})
    response = client.post(PROJECT + "/worksheets", json=payload)
    assert response.status_code == 201, response.text
    return "/api/v1/worksheets/" + response.json()["id"]


def controlled_cells(client, sheet):
    revision = client.get(PROJECT + "/configuration").json()["base_revision"]
    export = client.post(PROJECT + "/controlled-workbook", json={"base_revision": revision, "sheet_ids": [sheet]})
    assert export.is_success, export.text
    download = client.get(export.json()["download_url"])
    assert download.is_success
    book = load_workbook(BytesIO(download.content))
    try:
        return tuple(book.active[cell].value for cell in ("D2", "J3"))
    finally:
        book.close()


@pytest.mark.parametrize("confirm_metadata", [True, False])
def test_historical_header_requires_explicit_save(tmp_path, config, context, confirm_metadata):
    store = Store(tmp_path / "store")
    app = create_app(store, [config], ADMIN)
    with TestClient(app) as client:
        batch = context.defaults["batch"]
        source = historical_source(tmp_path / "history.xlsx", batch, "UNIT-1", "□相城工厂X\nR常熟工厂C")
        confirm(app.state.workflow, upload_history(app.state.workflow, source, context))
        slot, seed = seed_of(client, batch)
        assert (seed["equipment"], seed["location"]) == ("UNIT-1", "常熟工厂")
        path = create_historical(client, slot, seed, confirm_metadata=confirm_metadata)
        original = client.get(path).json()
        target = ("UNIT-1", "☑ 常熟工厂")
        assert controlled_cells(client, original["id"]) == (target if confirm_metadata else (None, "□ 未填写 / Unspecified"))
        preview = client.get(path + "/header-metadata").json()
        assert preview["metadata_fingerprint"] == seed["metadata_fingerprint"]
        assert [preview["metadata_sources"][field][0]["cell"] for field in ("equipment", "location")] == ["D2", "F3"]
        if confirm_metadata:
            assert (original["equipment"], original["location"]) == ("UNIT-1", "常熟工厂")
            return
        assert not original.get("equipment") and not original.get("location")
        lease = client.post(path + "/lease").json()["token"]
        command = dict(token=lease, revision=original["revision"], idempotency_key="header", equipment="UNIT-1",
                       location="常熟工厂", basis="Compared D2 and F3", metadata_fingerprint=preview["metadata_fingerprint"])
        app.dependency_overrides[get_actor] = lambda: actor_for_role("reader", "Reader", Role.PROJECT_LEAD)
        assert client.post(path + "/header-metadata", json=command).status_code == 403
        app.dependency_overrides.clear()
        assert client.post(path + "/header-metadata", json={**command, "basis": " "}).status_code == 422
        assert client.post(path + "/header-metadata", json={**command, "metadata_fingerprint": "0" * 64}).status_code == 409
        saved = client.post(path + "/header-metadata", json=command)
        assert saved.is_success, saved.text
        assert client.post(path + "/header-metadata", json=command).json() == saved.json()
        assert client.post(path + "/header-metadata", json={**command, "idempotency_key": "stale", "location": "other"}).status_code == 409
        after = client.get(path).json()
        no_change = {**command, "revision": after["revision"], "idempotency_key": "no-change"}
        assert client.post(path + "/header-metadata", json=no_change).json() == saved.json()
        assert after["plan_version"] == 2
        assert after["cells"] == original["cells"] and after["rows"] == original["rows"]
        assert controlled_cells(client, original["id"]) == target
        with store.read() as conn:
            audit, = conn.execute("SELECT reason, detail_json FROM workflow_events WHERE action='worksheet_header_metadata'").fetchall()
            assert audit["reason"] == "Compared D2 and F3"
            assert json.loads(audit["detail_json"])["metadata_sources"]["location"][0]["cell"] == "F3"


@pytest.mark.parametrize("location", [None, "□相城工厂X\n□常熟工厂C", "☑相城工厂\n☑常熟工厂", "☑相城工厂 ☑常熟工厂"])
def test_historical_seed_does_not_infer_site_from_storage(tmp_path, config, context, location):
    app = create_app(Store(tmp_path / "store"), [config], ADMIN)
    with TestClient(app) as client:
        batch = context.defaults["batch"]
        source = historical_source(tmp_path / "history.xlsx", batch, "UNIT-1", location)
        confirmed = confirm(app.state.workflow, upload_history(app.state.workflow, source, context))
        _, seed = seed_of(client, batch)
        assert seed["equipment"] == "UNIT-1"
        assert seed["location"] == ""
        if not location:
            assert seed["metadata_sources"]["location"] == []
        flow = app.state.workflow
        with flow.store.connect() as conn:
            outcomes = provision_from_results(conn, BusinessWorksheets(flow, ResultVersions(flow)),
                                              config.project, confirmed.result_version_ids, TECH)
        created, = outcomes
        assert created["status"] == "created", created
        sheet = client.get("/api/v1/worksheets/" + created["id"]).json()
        assert not sheet.get("equipment") and not sheet.get("location")


def test_conflicting_sources_leave_fields_pending_for_every_slot(tmp_path, config, context):
    app = create_app(Store(tmp_path / "store"), [config], ADMIN)
    with TestClient(app) as client:
        flow = app.state.workflow
        batch = context.defaults["batch"]
        for index, (equipment, location) in enumerate((("UNIT-1", "常熟工厂"), ("UNIT-2", "相城工厂"))):
            source = historical_source(tmp_path / f"history-{index}.xlsx", batch, equipment, location)
            preview = upload_history(flow, source, context)
            ResultVersions(flow).confirm(preview.preview_id, command(preview, indices=[index]), actor=TECH)
        results = client.get(PROJECT + "/result-cells", params={"batch": batch}).json()["cells"]
        assert len(results) == 2
        fingerprints = set()
        for result in results:
            seed = client.get(PROJECT + "/worksheet-seed", params={"batch": batch, "slot_id": result["slot_id"]}).json()
            assert (seed["equipment"], seed["location"]) == ("", "")
            assert {ref["raw"] for ref in seed["metadata_sources"]["equipment"]} == {"UNIT-1", "UNIT-2"}
            assert {ref["raw"] for ref in seed["metadata_sources"]["location"]} == {"常熟工厂", "相城工厂"}
            fingerprints.add(seed["metadata_fingerprint"])
        assert len(fingerprints) == 1
