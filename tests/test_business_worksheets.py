import json
from io import BytesIO

import pytest
from docx import Document
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from PIL import Image

from stability_hub.api import create_app, get_actor
from stability_hub.models import ImportContext, RecordKey, StabilityRecord
from stability_hub.normalize import normalize_worksheet_result
from stability_hub.permissions import Role, actor_for_role
from stability_hub.result_versions import ResultVersions
from stability_hub.storage import Store
from stability_hub.workflow_common import WorkflowError
from stability_hub.workflow_contracts import ExportRequest, ReviewRequest
from stability_hub.workflow_excel import ExcelExports
from test_workflow import LEAD, SENIOR, TECH, command, confirm, upload

PROJECT = "/api/v1/projects/PROJECT-P/worksheets"
OTHER = actor_for_role("other", "第二位实验员", Role.TECHNICIAN)


def test_same_batch_sample_stages_have_separate_results(tmp_path, config, context, xlsx):
    store = Store(tmp_path / "store")
    app = create_app(store, [config], actor_for_role("local", "本地", Role.ADMIN))
    identity = {**context.defaults, "sample_type": "DS"}
    with TestClient(app) as client:
        sheets = []
        for stage, value in (("裸抗", 90), ("ADC", 95)):
            response = client.post("/api/v1/projects/PROJECT-P/samples", json={
                "batch": identity["batch"], "sample_type": "DS", "presentation": identity["presentation"],
                "material_stage": stage, "purpose": "研究",
            })
            assert response.is_success, response.text
            sample_id = response.json()["id"]
            source = ImportContext(sample_id=sample_id, defaults=identity)
            draft = upload(app.state.workflow, xlsx([["Purity", "12M", value, "%"]], name=f"{value}.xlsx"), source)
            confirm(app.state.workflow, draft)
            created = client.post(PROJECT, json={
                "name": stage, "idempotency_key": stage, "identity": identity, "sample_id": sample_id,
                "start_date": "2024-01-01", "items": ["purity"],
                "points": [{"unit": "M", "amount": 12, "required_items": ["purity"]}],
            })
            assert created.is_success, created.text
            sheet = client.get("/api/v1/worksheets/" + created.json()["id"]).json()
            assert len(sheet["cells"]) == 1 and sheet["cells"][0]["raw"] == str(value)
            sheets.append(sheet)
        assert sheets[0]["cells"][0]["slot_id"] != sheets[1]["cells"][0]["slot_id"]
        result = client.get("/api/v1/projects/PROJECT-P/result-cells").json()
        assert result["total"] == 2
        exported = ExcelExports(app.state.workflow).create(
            ExportRequest(project=config.project, base_revision=result["base_revision"]), actor=LEAD)
        assert exported.export_id


def test_protocol_standard_can_be_added_after_results(tmp_path, config, context):
    store = Store(tmp_path / "store")
    app = create_app(store, [config], actor_for_role("local", "本地", Role.ADMIN))
    with TestClient(app) as client:
        project = "/api/v1/projects/PROJECT-P"
        protocol = Document()
        protocol.add_paragraph("STP(C)-QC-IP501-2-001")
        table = protocol.add_table(rows=0, cols=3)
        for texts in (("Study items", "0M", "6M"), ("Purity", "✓", "✓")):
            for cell, value in zip(table.add_row().cells, texts, strict=True):
                cell.text = value
        data = BytesIO()
        protocol.save(data)
        draft = client.post("/api/v1/intake/protocol", files={"file": ("protocol.docx", data.getvalue())})
        assert draft.status_code == 200, draft.text
        draft = draft.json()
        definition = {"assay_zh": "Purity", "component_zh": "Monomer", "method": "SEC-HPLC",
                      "unit": "%", "result_type": "numeric", "table": 1, "row": 2}

        def upload_specification(number, limit):
            doc = Document()
            table = doc.add_table(rows=0, cols=3)
            for texts in (("Test item", "Method", "Specification"), ("Purity", "SEC-HPLC", limit)):
                for cell, value in zip(table.add_row().cells, texts, strict=True):
                    cell.text = value
            data = BytesIO()
            doc.save(data)
            response = client.post("/api/v1/specifications", files={"file": (number + ".docx", data.getvalue())})
            assert response.status_code == 200, response.text
            return response.json()["id"]

        def register(spec_id, key):
            revision = client.get(project + "/configuration").json()["base_revision"]
            response = client.post(f"{project}/specifications/{spec_id}/components", json={
                "revision": revision, "idempotency_key": key, "components": [definition],
            })
            assert response.status_code == 200, response.text
            return response.json()["links"][0]["item_id"]

        other = upload_specification("STP(C)-QC-IP501-2-004", "≥95%")
        item = register(other, "other")
        seed_url = project + "/worksheet-seed"
        blank = client.get(seed_url, params={"source": "blank", "batch": "TEST"}).json()
        assert blank["identity"]["batch"] == "TEST" and blank["points"][-1]["label"] == "36M"
        standard = client.get(seed_url, params={"source": "standard", "source_id": other}).json()
        assert standard["item_standards"][item][0]["specification_id"] == other
        assert standard["points"][0]["items"] == [item]
        seeded_protocol = client.get(seed_url, params={"source": "protocol", "source_id": draft["draft_id"], "table": 1}).json()
        assert seeded_protocol["points"] == [{"label": "0M", "items": ["purity"]}, {"label": "6M", "items": ["purity"]}]
        assert seeded_protocol["protocol_row_items"] == {"2": ["purity"]}
        assert seeded_protocol["item_standards"] == {}
        assert seeded_protocol["protocol_standards"]["missing"] == ["STP(C)-QC-IP501-2-001"]
        assert seeded_protocol["protocol_standards"]["references"] == [
            {"number": "STP(C)-QC-IP501-2-001", "state": "missing", "registered_links": 0},
        ]
        created = client.post(PROJECT, json={
            "idempotency_key": "study", "name": "Study", "identity": context.defaults, "start_date": "2024-01-01",
            "protocol_draft_id": draft["draft_id"], "protocol_table": 1,
            "items": [item], "points": [{"unit": "M", "amount": 0, "required_items": [item]}],
            "row_criteria": {item: {"criteria_text": "Report the measured value"}},
        })
        assert created.status_code == 201, created.text
        path = "/api/v1/worksheets/" + created.json()["id"]
        sheet = client.get(path).json()
        assert sheet["rows"][0]["item_id"] == item
        assert sheet["rows"][0]["criteria_text"] == "Report the measured value"
        token = client.post(path + "/lease").json()["token"]
        wrong_material = upload_specification("STP(C)-QC-IP501-2-007 原液质量标准", "≥93%")
        assert client.get(f"/api/v1/specifications/{wrong_material}").json()["material_scope"] == "DS"
        blocked = client.post(path + "/standards", json={
            "token": token, "revision": sheet["revision"], "row_id": sheet["rows"][0]["id"],
            "idempotency_key": "wrong-material", "basis": "Scope check",
            "criteria": [{"specification_id": wrong_material, "table": 1, "row": 2}],
        })
        assert blocked.json()["code"] == "specification_scope"
        recorded = client.post(path + "/save", json={
            "token": token, "revision": sheet["revision"], "idempotency_key": "value",
            "changes": [{"row_id": sheet["rows"][0]["id"], "point_id": sheet["points"][0]["id"], "value": "97", "intent": "initial"}],
        })
        assert recorded.status_code == 200, recorded.text
        missing = client.get(path + "/protocol-standards").json()
        assert missing["additions"] == [] and missing["issues"][0]["number"] == "STP(C)-QC-IP501-2-001"
        exact = upload_specification("STP(C)-QC-IP501-2-001-1.0", "≥90%")
        assert register(exact, "exact") == item
        preview = client.get(path + "/protocol-standards").json()
        assert [row["standard"]["specification_id"] for row in preview["additions"]] == [exact]
        command = {"token": token, "revision": preview["revision"], "fingerprint": preview["fingerprint"],
                   "idempotency_key": "attach-exact", "basis": "Reviewed specification"}
        applied = client.post(path + "/protocol-standards", json=command)
        assert applied.status_code == 200, applied.text
        assert client.post(path + "/protocol-standards", json=command).json() == applied.json()
        after = client.get(path).json()
        assert after["plan_version"] == 2 and after["rows"][0]["standards"][0]["criterion"] == "≥90%"
        assert after["rows"][0]["criteria_text"] == "Report the measured value"
        assert after["rows"][0]["id"] == sheet["rows"][0]["id"]
        assert after["cells"][0]["adopted"]["result_version_id"] == recorded.json()["result_version_ids"][0]
        assert client.get(path + "/protocol-standards").json()["additions"] == []
        alternative = upload_specification("Review_STP(C)-QC-IP501-2-001-2.0", "≥92%")
        conflicting = client.post(path + "/standards", json={
            "token": token, "revision": after["revision"], "row_id": after["rows"][0]["id"],
            "idempotency_key": "two-versions", "basis": "Verify conflict",
            "criteria": [{"specification_id": identifier, "table": 1, "row": 2}
                         for identifier in (exact, alternative)],
        })
        assert conflicting.status_code == 409
        assert conflicting.json()["code"] == "specification_version_conflict"
        assert client.get(path).json()["rows"][0]["standards"] == after["rows"][0]["standards"]
        extension = client.post(path + "/protocol", json={
            "token": token, "revision": after["revision"], "idempotency_key": "extend-from-protocol",
            "basis": "Reviewed required cells", "protocol_draft_id": draft["draft_id"], "protocol_table": 1,
            "add_months": True, "row_mapping": {"2": [item]},
        })
        assert extension.status_code == 200, extension.text
        extended = client.get(path).json()
        assert extended["plan_version"] == 3
        assert [point["label"] for point in extended["points"]] == ["0M", "6M"]
        assert extended["points"][1]["required_row_ids"] == [after["rows"][0]["id"]]
        assert extended["cells"] == after["cells"]
        copied = client.get(seed_url, params={"source": "copy", "source_id": created.json()["id"]}).json()
        assert copied["copy"]["plan_version"] == 3 and copied["copy"]["has_standards"]
        assert copied["identity"]["batch"] == ""
        assert copied["points"] == [{"label": "0M", "items": [item]}, {"label": "6M", "items": [item]}]


def test_result_seed_keeps_report_systems_in_one_worksheet(tmp_path, config, context):
    store = Store(tmp_path / "store")
    app = create_app(store, [config], TECH)
    flow = app.state.workflow
    systems = ("", "EP", "US", "CN")
    with store.connect() as conn:
        for index, system in enumerate(systems):
            key = RecordKey(project=config.project, **context.defaults, month=0, item="purity",
                            method="SEC-HPLC", reporting_basis=system)
            record = StabilityRecord(key=key, result=normalize_worksheet_result(str(95 + index), "%", "numeric"))
            ResultVersions(flow).record_worksheet_result(conn, record, actor=TECH, previous=None, current=None,
                                                         intent="initial", reason="fixture",
                                                         source={"kind": "worksheet_entry", "worksheet_id": "fixture"})
    with TestClient(app) as client:
        endpoint = "/api/v1/projects/PROJECT-P"
        results = client.get(endpoint + "/result-cells").json()["cells"]
        original_slots = {entry["slot_id"] for entry in results}
        seed_slot = results[0]["slot_id"]
        seed = client.get(endpoint + "/worksheet-seed", params={"batch": context.defaults["batch"], "slot_id": seed_slot})
        assert seed.status_code == 200, seed.text
        seed = seed.json()
        assert {row["reporting_basis"] for row in seed["items"]} == set(systems)
        items = [row["id"] for row in seed["items"]]
        created = client.post(PROJECT, json={
            "name": "Report systems", "idempotency_key": "report-systems", "identity": seed["identity"],
            "seed_slot_id": seed_slot, "start_date": "2024-01-01", "items": items,
            "points": [{"unit": "M", "amount": 0, "required_items": items}],
        })
        assert created.status_code == 201, created.text
        sheet_id = created.json()["id"]
        sheet = client.get("/api/v1/worksheets/" + sheet_id).json()
        assert {cell["slot_id"] for cell in sheet["cells"]} == original_slots
        assert {row["reporting_basis"] for row in sheet["rows"]} == set(systems)
        assert all(cell["adopted"] for cell in sheet["cells"])
        copied = client.post(PROJECT, json={
            "name": "Copied report systems", "idempotency_key": "copy-report-systems",
            "identity": {**seed["identity"], "batch": "B002"}, "start_date": "2024-01-01",
            "copy_source_id": sheet_id, "copy_source_plan_version": 1, "items": items,
            "points": [{"unit": "M", "amount": 0, "required_items": items}],
        })
        assert copied.status_code == 201, copied.text
        copied_sheet = client.get("/api/v1/worksheets/" + copied.json()["id"]).json()
        assert not copied_sheet["cells"]
        assert {row["reporting_basis"] for row in copied_sheet["rows"]} == set(systems)
    with store.read() as conn:
        assert {row["slot_id"] for row in conn.execute(
            "SELECT slot_id FROM worksheet_result_positions WHERE sheet_id=?", (sheet_id,)
        )} == original_slots


def test_release_reference_freezes_source_without_creating_stability_result(tmp_path, config, context, coa):
    actor = actor_for_role("local", "本地操作员", Role.ADMIN)
    app = create_app(Store(tmp_path / "store"), [config], actor)
    flow = app.state.workflow
    release = ImportContext(role="release_coa", defaults={**context.defaults, "study": "release", "condition": "release"})
    original = confirm(flow, upload(flow, coa(), release)).result_version_ids[0]
    with TestClient(app) as client:
        protocol = Document()
        matrix = protocol.add_table(rows=0, cols=3)
        for values in (("Study items", "0M", "12M"), ("Protein content", "引用放行检测数据", "✓")):
            for cell, text in zip(matrix.add_row().cells, values, strict=True):
                cell.text = text
        source = BytesIO()
        protocol.save(source)
        archived_protocol = client.post("/api/v1/intake/protocol", files={"file": ("study.docx", source.getvalue())})
        assert archived_protocol.is_success, archived_protocol.text
        created = client.post(PROJECT, json={
            "idempotency_key": "release-plan", "name": "0M 引用验收", "identity": context.defaults,
            "start_date": "2024-02-29", "items": ["protein"],
            "protocol_draft_id": archived_protocol.json()["draft_id"], "protocol_table": 1,
            "protocol_row_items": {"2": ["protein"]},
            "points": [{"unit": "M", "amount": point, "required_items": ["protein"]} for point in (0, 12)],
        })
        assert created.status_code == 201, created.text
        path = "/api/v1/worksheets/" + created.json()["id"]
        view = client.get(path).json()
        token = client.post(path + "/lease").json()["token"]
        assert view["pending_release_references"][view["rows"][0]["id"]]["column"] == 2
        blocked = client.post(path + "/save", json={
            "token": token, "revision": view["revision"], "idempotency_key": "blocked-0m",
            "changes": [{"row_id": view["rows"][0]["id"], "point_id": view["points"][0]["id"],
                         "intent": "initial", "value": "2.20"}],
        })
        assert blocked.json()["code"] == "release_reference_locked"
        body = {"token": token, "revision": view["revision"], "row_id": view["rows"][0]["id"],
                "idempotency_key": "link", "result_version_id": original, "basis": "核对方案与放行日期"}
        too_early = client.post(path + "/release-reference", json={**body, "release_completed": "2024-01-28"})
        assert too_early.json()["code"] == "release_reference_date"
        body["release_completed"] = "2024-01-31"
        saved = client.post(path + "/release-reference", json=body)
        assert saved.status_code == 200, saved.text
        assert client.post(path + "/release-reference", json=body).json() == saved.json()
        view = client.get(path).json()
        assert view["cells"][0]["release_reference"]["current"]
        assert view["pending_release_references"] == {}
        assert view["cells"][0]["adopted"] is None
        assert client.get("/api/v1/projects/PROJECT-P/result-cells?study=long_term").json()["total"] == 0
        export = client.post(path + "/confirmation", params={"revision": view["revision"], "selection_id": view["selection_id"]})
        assert export.status_code == 200, export.text
        book = load_workbook(BytesIO(client.get(export.json()["download_url"]).content))
        assert "引用放行" in book.active["E9"].value
        assert any(original in row for row in book.active.values)
        book.close()
        spec = Document()
        table = spec.add_table(rows=0, cols=3)
        for values in (("Test Item", "Method", "Specification"), ("Protein content", "UV", "1–3 mg/ml")):
            for cell, text in zip(table.add_row().cells, values, strict=True):
                cell.text = text
        buffer = BytesIO()
        spec.save(buffer)
        archived = client.post("/api/v1/specifications", files={"file": ("standard.docx", buffer.getvalue())}).json()
        linked = client.post(path + "/standards", json={
            "token": token, "revision": view["revision"], "row_id": view["rows"][0]["id"],
            "idempotency_key": "standard", "basis": "核对适用范围",
            "criteria": [{"specification_id": archived["id"], "table": 1, "row": 2, "lower": 1, "upper": 3}],
        })
        assert linked.status_code == 200, linked.text
        recorded = client.post(path + "/save", json={
            "token": token, "revision": linked.json()["revision"], "idempotency_key": "12m",
            "changes": [{"row_id": view["rows"][0]["id"], "point_id": view["points"][1]["id"],
                         "intent": "initial", "value": "2.20"}],
        })
        assert recorded.status_code == 200, recorded.text
        current = client.get(path).json()
        event = client.post(path + "/events", json={
            "token": token, "revision": current["revision"], "idempotency_key": "event",
            "category": "异常调查", "reference": "AE-TEST-001", "description": "复测说明已核对",
            "row_id": current["rows"][0]["id"], "point_id": current["points"][1]["id"],
        })
        assert event.status_code == 200, event.text
        current = client.get(path).json()
        confirmation = client.post(path + "/confirmation", params={
            "revision": current["revision"], "selection_id": current["selection_id"],
        })
        assert confirmation.status_code == 200, confirmation.text
        book = load_workbook(BytesIO(client.get(confirmation.json()["download_url"]).content))
        assert any("AE-TEST-001" in row and "复测说明已核对" in row for row in book.active.values)
        book.close()
        revision = client.get("/api/v1/projects/PROJECT-P/result-cells").json()["base_revision"]
        generated = client.post("/api/v1/reports", json={"project": "PROJECT-P", "base_revision": revision,
                                "stability_document": {}, "include_charts": False})
        assert generated.status_code == 200, generated.text
        document = Document(BytesIO(client.get(generated.json()["word_download_url"]).content))
        texts = [cell.text for table in document.tables for row in table.rows for cell in row.cells]
        assert any("引用放行" in value for value in texts)
        assert any("研究表已关联：1–3 mg/ml" in value for value in texts)
        assert original in texts and "2.20" in texts
        assert "AE-TEST-001" in texts and "复测说明已核对" in texts
        assert generated.json()["result_count"] == 1
        base = "/api/v1/projects/PROJECT-P"
        display = client.get(base + "/result-display?study=long_term").json()
        group = display["analysis_groups"][0]
        trend = client.get(base + "/result-trend", params={"group_id": group["id"], "base_revision": revision,
                           "study": "long_term", "batches": group["batches"]})
        assert trend.status_code == 200, trend.text
        standard = trend.json()["standard_limits"]
        assert standard["lower"] == 1 and standard["upper"] == 3 and standard["unit"] == "mg/ml"
        png = client.post("/api/v1/exports", json={"project": "PROJECT-P", "base_revision": revision,
                          "slot_ids": group["slot_ids"], "include_png": True})
        assert png.status_code == 200, png.text
        with Image.open(BytesIO(client.get(png.json()["image_download_url"]).content)) as image:
            assert json.loads(image.info["Description"])["standard_limits"] == standard
        assert client.post(path + "/lease/release", json={"token": token}).status_code == 200
        old_export = client.get(confirmation.json()["download_url"]).content
        impact_path = f"/api/v1/results/{original}/invalidate"
        impact = client.get(impact_path).json()
        assert impact["studies"] == [{"id": current["id"], "name": "0M 引用验收", "release_reference": 1}]
        voided = client.post(impact_path, json={
            "base_revision": impact["base_revision"], "idempotency_key": "void-release", "reason": "来源复核后作废",
        })
        assert voided.status_code == 200, voided.text
        after = client.get(path).json()
        assert after["revision"] == current["revision"] + 1
        assert after["invalidated_release_rows"] == [current["rows"][0]["id"]]
        assert all(cell["point_id"] != current["points"][0]["id"] for cell in after["cells"])
        new_export = client.post(path + "/confirmation", params={
            "revision": after["revision"], "selection_id": after["selection_id"],
        })
        assert new_export.status_code == 200, new_export.text
        with BytesIO(client.get(new_export.json()["download_url"]).content) as content:
            updated_book = load_workbook(content)
            assert updated_book.active["E9"].value == "放行结果已作废，待重新关联"
            updated_book.close()
        assert client.get(confirmation.json()["download_url"]).content == old_export


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


def test_confirmation_keeps_selected_result_report_reference(tmp_path, config, context, xlsx):
    app = create_app(Store(tmp_path / "store"), [config], TECH)
    result = "本次结果不汇报，详见 AR-C-123"
    confirm(app.state.workflow, upload(app.state.workflow, xlsx([["Purity", "12M", result, "%"]]), context))
    with TestClient(app) as client:
        url = create_sheet(client, context)
        view = client.get(url).json()
        app.dependency_overrides[get_actor] = lambda: LEAD
        item = next(row for row in view["rows"] if row["item_id"] == "purity")
        params = {"revision": view["revision"], "selection_id": view["selection_id"]}
        for point, included in zip(view["points"], (False, True), strict=True):
            export = client.post(url + "/confirmation", params={**params, "point_ids": point["id"]})
            assert export.is_success, export.text
            book = load_workbook(BytesIO(client.get(export.json()["download_url"]).content), read_only=True)
            try:
                rows = list(book.active.values)
                assert any(row[:5] == (item["label"], item["method"], "12M", "结果原文中的报告编号", "AR-C-123")
                           for row in rows) is included
                assert any(result in row for row in rows) is included
            finally:
                book.close()


def test_excel_commit_waits_for_target_worksheet_lease(tmp_path, config, context, xlsx):
    app = create_app(Store(tmp_path / "store"), [config], TECH)
    source = xlsx([["Purity", "12M", "98.1", "%"]])
    with TestClient(app) as client:
        target = create_sheet(client, context)
        other = create_sheet(client, context, batch="OTHER", key="other-sheet")
        token = client.post(target + "/lease").json()["token"]
        assert client.post(other + "/lease").is_success
        uploaded = client.post("/api/v1/projects/PROJECT-P/previews", headers={"Idempotency-Key": "upload"},
                               files={"file": (source.name, source.read_bytes())},
                               data={"context": context.model_dump_json()})
        assert uploaded.is_success, uploaded.text
        preview = uploaded.json()
        candidate, = preview["candidates"]
        path = "/api/v1/previews/" + preview["preview_id"]
        received = client.post(path + "/receive", json={"base_revision": preview["base_revision"],
            "idempotency_key": "receive", "candidate_ids": [candidate["id"]], "defer_confirmation": True})
        assert received.is_success, received.text
        section = candidate["location"]["section"]
        check = client.get(path + "/v5/preflight", params={"section": section}).json()
        command = {"section": section, "base_revision": check["base_revision"], "fingerprint": check["fingerprint"],
                   "reason": "核对 Excel 原件", "idempotency_key": "commit"}
        locked = client.post(path + "/v5/commit", json=command)
        assert locked.status_code == 409 and locked.json()["code"] == "worksheet_occupied", locked.text
        assert client.get(target).json()["cells"] == []
        client.post(target + "/lease/release", json={"token": token}).raise_for_status()
        committed = client.post(path + "/v5/commit", json=command)
        assert committed.is_success, committed.text
        assert client.get(target).json()["cells"][0]["adopted"]["text"] == "98.1%"


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


def test_create_with_specification_links_is_atomic_and_preserves_each_criterion(tmp_path, config, context):
    app = create_app(Store(tmp_path / "store"), [config], actor_for_role("local", "本地", Role.ADMIN))
    with TestClient(app) as client:
        document = Document()
        table = document.add_table(rows=0, cols=3)
        for values in (("Test Item", "Method", "Specification"),
                       ("Purity", "NR CE-SDS", "≥90.00%"),
                       ("Total Minor Species", "NR CE-SDS", "≤10.00%")):
            for cell, value in zip(table.add_row().cells, values, strict=True):
                cell.text = value
        buf = BytesIO()
        document.save(buf)
        source = client.post("/api/v1/specifications", files={"file": ("standard.docx", buf.getvalue())}).json()
        items = [{"id": "local_" + str(index), "label": row["item"], "method": row["method"],
                  "unit": "%", "result_type": "numeric", "specification_links": [
                      {"specification_id": source["id"], "table": row["table"], "row": row["row"]}]}
                 for index, row in enumerate(source["rows"])]
        assert len(items) == 2
        body = {"idempotency_key": "from-standard", "name": "标准建表", "identity": context.defaults,
                "start_date": "2024-02-29", "items": [item["id"] for item in items], "custom_items": items,
                "points": [{"unit": "M", "amount": 0, "required_items": [item["id"] for item in items]}]}
        items[1]["specification_links"][0]["row"] = 999
        invalid = client.post(PROJECT, json=body)
        assert invalid.json()["code"] == "specification_criterion_missing"
        items[1]["specification_links"][0]["row"] = source["rows"][1]["row"]
        created = client.post(PROJECT, json=body)
        assert created.status_code == 201, created.text
        assert client.post(PROJECT, json=body).json() == created.json()
        sheet = client.get("/api/v1/worksheets/" + created.json()["id"]).json()
        assert [row["standards"][0]["criterion"] for row in sheet["rows"]] == ["≥90.00%", "≤10.00%"]
        assert all(row["standards"][0]["specification_id"] == source["id"] for row in sheet["rows"])
