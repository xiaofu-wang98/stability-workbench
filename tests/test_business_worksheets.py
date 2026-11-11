import json
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from stability_hub.api import create_app, get_actor
from stability_hub.permissions import Role, actor_for_role
from stability_hub.result_versions import ResultVersions
from stability_hub.storage import Store
from stability_hub.workflow_common import WorkflowError
from stability_hub.workflow_contracts import ExportRequest, ReviewRequest
from stability_hub.workflow_excel import ExcelExports
from test_workflow import LEAD, SENIOR, TECH, command, confirm, upload

PROJECT = "/api/v1/projects/PROJECT-P/worksheets"
OTHER = actor_for_role("other", "第二位实验员", Role.TECHNICIAN)


def create_sheet(client, context, *, batch=None, key="create-sheet", orientation=""):
    identity = {**context.defaults}
    if batch:
        identity["batch"] = batch
    response = client.post(PROJECT, json={
        "idempotency_key": key, "name": "长期研究表", "identity": {**identity, "orientation": orientation},
        "start_date": "2024-01-31",
        "items": ["purity", "appearance"],
        "points": [{"unit": "M", "amount": 0, "required_items": ["appearance"]},
                   {"unit": "M", "amount": 12, "required_items": ["purity", "appearance"]}],
    })
    assert response.status_code == 201, response.text
    return "/api/v1/worksheets/" + response.json()["id"]


def test_shared_sheet_lease_version_and_atomic_save(tmp_path, config, context, monkeypatch):
    app = create_app(Store(tmp_path / "store"), [config], TECH)
    now = [100]
    monkeypatch.setattr("stability_hub.business_worksheets.time.time", lambda: now[0])
    with TestClient(app) as client:
        first = create_sheet(client, context)
        assert first == create_sheet(client, context)
        second = create_sheet(client, context, batch="B002", key="other-sheet")
        token = client.post(first + "/lease").json()["token"]
        view = client.get(first).json()
        app.dependency_overrides[get_actor] = lambda: OTHER
        assert client.post(first + "/lease").json()["code"] == "worksheet_occupied"
        assert client.post(second + "/lease").status_code == 200
        assert client.post(first + "/lease/renew", json={"token": token}).status_code == 409
        app.dependency_overrides[get_actor] = lambda: LEAD
        assert client.get(first).json()["editable"] is False
        assert client.post(first + "/lease").status_code == 403
        app.dependency_overrides.clear()

        purity, appearance = view["rows"]
        initial, year = view["points"]
        assert purity["id"] != purity["item_id"]
        valid = {"row_id": appearance["id"], "point_id": initial["id"], "value": "澄清\n无异物", "intent": "initial"}
        invalid = {"row_id": purity["id"], "point_id": initial["id"], "value": "99", "intent": "initial"}
        save = {"token": token, "revision": 1, "idempotency_key": "save", "changes": [valid, invalid]}
        assert client.post(first + "/save", json=save).status_code == 422
        assert client.get(first).json()["cells"] == []
        save["changes"] = [valid, {**invalid, "point_id": year["id"], "value": "NA*"}]
        save["layout"] = {"row_heights": {"2": 66}, "column_widths": {"1": 151}, "title_merge_to": 2}
        response = client.post(first + "/save", json=save)
        assert response.status_code == 200, response.text
        assert client.post(first + "/save", json=save).json() == response.json()
        assert client.post(first + "/save", json={**save, "idempotency_key": "stale"}).json()["code"] == "worksheet_revision_changed"
        saved = client.get(first).json()
        assert {cell["raw"] for cell in saved["cells"]} == {"澄清\n无异物", "NA*"}
        assert saved["layout"] == save["layout"]
        now[0] += 91
        app.dependency_overrides[get_actor] = lambda: OTHER
        assert client.post(first + "/lease").status_code == 200
        app.dependency_overrides.clear()
        assert client.post(first + "/save", json={**save, "idempotency_key": "old-token", "revision": 2}).json()[
            "code"] == "worksheet_lease_lost"


def test_existing_intake_and_direct_results_share_query_and_export(tmp_path, config, context, xlsx):
    app = create_app(Store(tmp_path / "store"), [config], TECH)
    flow = app.state.workflow
    first = confirm(flow, upload(flow, xlsx([["Purity", "12M", 98, "%"]]), context))
    with TestClient(app) as client:
        url = create_sheet(client, context)
        token = client.post(url + "/lease").json()["token"]
        view = client.get(url).json()
        existing = view["cells"][0]
        assert existing["adopted"]["result_version_id"] == first.result_version_ids[0]
        correction = {
            "row_id": existing["row_id"], "point_id": existing["point_id"], "value": "98.20%",
            "expected_version_id": existing["adopted"]["result_version_id"], "intent": "correction",
        }
        command_body = {"token": token, "revision": 1, "idempotency_key": "correction", "changes": [correction]}
        assert client.post(url + "/save", json=command_body).status_code == 422
        assert client.post(url + "/save", json={**command_body, "reason": "复核原记录后更正"}).status_code == 200
        actual = client.get(url).json()["cells"][0]
        assert actual["adopted"]["text"] == "98.20%"
        assert actual["adopted"]["detection_id"] == existing["adopted"]["detection_id"]
        source = client.get("/api/v1/sources/" + actual["adopted"]["source_ids"][0]).json()
        assert source["kind"] == "worksheet_entry" and source["previous_version_id"] == first.result_version_ids[0]
        candidate = upload(flow, xlsx([["Purity", "12M", "99", "%"]], name="duplicate.xlsx"), context)
        with pytest.raises(WorkflowError) as locked:
            ResultVersions(flow).confirm(candidate.preview_id, command(candidate, "retest"), actor=TECH)
        assert locked.value.code == "worksheet_occupied"
        extend = {"token": token, "revision": 2, "idempotency_key": "extend", "basis": "STUDY-P-01 v2",
                  "points": [{"unit": "M", "amount": 36, "required_items": ["purity"]}]}
        result = client.post(url + "/plan", json=extend)
        assert result.status_code == 200, result.text
        view = client.get(url).json()
        assert view["plan_version"] == 2 and [p["label"] for p in view["points"]] == ["0M", "12M", "36M"]
        assert view["points"][-1]["required_row_ids"] == [view["rows"][0]["id"]]
        assert len(view["cells"]) == 1
        with flow.store.read() as conn:
            old_plan = conn.execute("SELECT plan_json FROM business_worksheet_plans WHERE version=1").fetchone()
            assert len(json.loads(old_plan[0])["points"]) == 2
        results = flow.results("PROJECT-P", actor=TECH)
        assert results.total == 1
        display = client.get("/api/v1/projects/PROJECT-P/result-display").json()
        trend = client.get("/api/v1/projects/PROJECT-P/result-trend", params={
            "group_id": display["analysis_groups"][0]["id"], "base_revision": display["base_revision"],
            "batches": [context.defaults["batch"]],
        })
        assert trend.status_code == 200, trend.text
        assert trend.json()["points"][0]["version_id"] == actual["adopted"]["result_version_id"]
        exporter = ExcelExports(flow)
        exported = exporter.create(ExportRequest(project="PROJECT-P", base_revision=results.base_revision), actor=LEAD)
        book = load_workbook(exporter.download(exported.export_id, actor=LEAD), read_only=True)
        try:
            assert book["WorksheetEntries"]["F2"].value == "98.20%"
        finally:
            book.close()


def test_plan_export_freezes_empty_and_adopted_results(tmp_path, config, context):
    app = create_app(Store(tmp_path / "store"), [config], TECH)
    with TestClient(app) as client:
        url = create_sheet(client, context)
        token = client.post(url + "/lease").json()["token"]
        view = client.get(url).json()
        old_request = {"revision": 1, "selection_id": view["selection_id"]}
        assert client.post(url + "/confirmation", params=old_request).status_code == 403
        app.dependency_overrides[get_actor] = lambda: LEAD
        empty = client.post(url + "/confirmation", params=old_request)
        assert empty.status_code == 200, empty.text
        old_path = empty.json()["download_url"]
        old_bytes = client.get(old_path).content
        app.dependency_overrides.clear()

        row = view["rows"][0]
        point = view["points"][1]
        entry = {"row_id": row["id"], "point_id": point["id"], "value": "97.60%", "intent": "initial"}
        save = {"token": token, "revision": 1, "idempotency_key": "first", "changes": [entry]}
        first = client.post(url + "/save", json=save)
        assert first.status_code == 200, first.text
        initial_version = first.json()["result_version_ids"][0]
        retest = {**entry, "intent": "retest", "value": "99.10%", "expected_version_id": initial_version}
        updated = client.post(url + "/save", json={**save, "revision": 2, "idempotency_key": "retest",
                                                  "reason": "独立复测", "changes": [retest]})
        assert updated.status_code == 200, updated.text
        retest_version = updated.json()["result_version_ids"][0]
        projection = client.get(url).json()
        cell = projection["cells"][0]
        assert cell["display"]["result_version_id"] == retest_version
        assert cell["adopted"]["result_version_id"] == initial_version
        assert cell["display"]["detection_id"] != cell["adopted"]["detection_id"]
        client.post(url + "/lease/release", json={"token": token}).raise_for_status()

        app.dependency_overrides[get_actor] = lambda: LEAD
        assert client.post(url + "/confirmation", params=old_request).status_code == 409
        current_request = {"revision": 3, "selection_id": projection["selection_id"]}
        latest = client.post(url + "/confirmation", params=current_request)
        assert latest.status_code == 200, latest.text
        current_bytes = client.get(latest.json()["download_url"]).content
        assert client.get(old_path).content == old_bytes
        for content, value in [(old_bytes, None), (current_bytes, "97.60%")]:
            book = load_workbook(BytesIO(content), read_only=True)
            try:
                sheet = book["稳定性确认表"]
                assert sheet["E9"].value == "不要求" and sheet["F9"].value == value
            finally:
                book.close()
        ResultVersions(app.state.workflow).review(retest_version, ReviewRequest(
            base_revision=updated.json()["project_revision"], idempotency_key="adopt-retest", action="adopt", reason="选用复测"
        ), actor=SENIOR)
        assert client.get(url).json()["cells"][0]["adopted"]["result_version_id"] == retest_version
        assert client.post(url + "/confirmation", params=current_request).status_code == 409


def test_explicit_orientation_matches_existing_condition_identity(tmp_path, config, context, xlsx):
    app = create_app(Store(tmp_path / "store"), [config], TECH)
    flow = app.state.workflow
    legacy = context.model_copy(update={"defaults": {**context.defaults, "condition": "25℃ · 正置"}})
    original = confirm(flow, upload(flow, xlsx([["Purity", "12M", 98, "%"]]), legacy))
    with TestClient(app) as client:
        base = context.model_copy(update={"defaults": {**context.defaults, "condition": "25°C"}})
        upright = create_sheet(client, base, orientation="正置")
        inverted = create_sheet(client, base, key="inverted", orientation="倒置")
        assert client.get(upright).json()["cells"][0]["adopted"]["result_version_id"] == original.result_version_ids[0]
        view = client.get(inverted).json()
        assert view["cells"] == []
        token = client.post(inverted + "/lease").json()["token"]
        result = client.post(inverted + "/save", json={
            "token": token, "revision": 1, "idempotency_key": "inverted-result",
            "changes": [{"row_id": view["rows"][0]["id"], "point_id": view["points"][1]["id"],
                         "value": "97%", "intent": "initial"}],
        })
        assert result.status_code == 200, result.text
        data = flow.results("PROJECT-P", actor=TECH)
        assert {cell.slot_key.condition for cell in data.cells} == {"25℃ · 正置", "25℃ · 倒置"}
