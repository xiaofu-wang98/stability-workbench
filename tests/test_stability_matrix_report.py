from io import BytesIO

from docx import Document
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from stability_hub.api import create_app
from stability_hub.permissions import Role, actor_for_role
from stability_hub.storage import Store
from test_workflow import LEAD, confirm, upload


def test_stability_document_separates_conditions_and_keeps_manual_evaluation(tmp_path, config, context, xlsx):
    app = create_app(Store(tmp_path / "report"), [config], LEAD)
    flow = app.state.workflow
    for study, condition, value in (("long_term", "5C", "98.1"), ("accelerated", "25C", "97.2")):
        scoped = context.model_copy(update={"defaults": {**context.defaults, "study": study, "condition": condition}})
        rows = [["Purity", "0M", value, "%"]]
        if study == "long_term":
            rows.append(["Purity", "3M", "本次结果不汇报，详见 AR-C-123", "%"])
            rows.append(["Purity", "6M", "≤0.10", "%"])
        path = xlsx(rows, name=f"{study}.xlsx")
        if study == "long_term":
            book = load_workbook(path)
            book.active["C2"] = 0.981
            book.active["C2"].number_format = "0.0%"
            book.save(path)
            book.close()
        confirm(flow, upload(flow, path, scoped))
    with TestClient(app) as client:
        revision = client.get("/api/v1/projects/PROJECT-P/result-cells").json()["base_revision"]
        response = client.post("/api/v1/reports", json={
            "project": "PROJECT-P", "base_revision": revision,
            "stability_document": {"equipment": "设备记录 A01", "events": "变更记录 C01",
                                   "evaluation": "人工核对范围，结论待复核", "version": "0.1"},
        })
        assert response.status_code == 200, response.text
        data = response.json()
        document = Document(BytesIO(client.get(data["word_download_url"]).content))
        paragraphs = [paragraph.text for paragraph in document.paragraphs]
        assert any(text.startswith("长期稳定性") for text in paragraphs)
        assert any(text.startswith("加速稳定性") for text in paragraphs)
        assert "设备记录 A01" in paragraphs and "变更记录 C01" in paragraphs
        assert "人工核对范围，结论待复核" in paragraphs
        matrices = [table for table in document.tables if table.cell(0, 0).text == "检项"]
        assert len(matrices) == 2
        assert {table.cell(1, 5).text for table in matrices} == {"98.100", "97.2"}
        assert any("本次结果不汇报，详见 AR-C-123" in cell.text
                   for table in matrices for row in table.rows for cell in row.cells)
        notes = next(table for table in document.tables if table.cell(0, 5).text == "原文")
        assert notes.cell(1, 5).text == "AR-C-123"
        assert len(document.inline_shapes) == data["chart_count"] == 2
        body = list(document.element.body)
        first_chart = next(i for i, block in enumerate(body) if block.xpath(".//w:drawing"))
        assert all(body.index(table._tbl) < first_chart for table in matrices)
        assert all(not row.cells[1].text for row in document.tables[0].rows[1:])
        general = client.post("/api/v1/reports", json={"project": "PROJECT-P", "base_revision": revision})
        assert general.is_success, general.text
        document = Document(BytesIO(client.get(general.json()["word_download_url"]).content))
        cells = {cell.text for table in document.tables for row in table.rows for cell in row.cells}
        assert {"98.100%", "≤0.10%", "本次结果不汇报，详见 AR-C-123", "AR-C-123"} <= cells


def test_stability_document_distinguishes_missing_plan_dates_from_na(tmp_path, config, context):
    actor = actor_for_role("local", "Local", Role.ADMIN)
    app = create_app(Store(tmp_path / "report"), [config], actor)
    with TestClient(app) as client:
        base = "/api/v1/projects/PROJECT-P"
        created = client.post(base + "/worksheets", json={
            "idempotency_key": "dated-plan", "name": "Dated plan", "identity": context.defaults,
            "start_date": "2024-01-01", "items": ["purity"],
            "points": [
                {"unit": "M", "amount": amount, "planned_date": date, "required_items": ["purity"]}
                for amount, date in ((0, "2024-01-01"), (3, "2024-04-01"), (6, "2099-01-01"))
            ],
        })
        assert created.status_code == 201, created.text
        path = "/api/v1/worksheets/" + created.json()["id"]
        view = client.get(path).json()
        token = client.post(path + "/lease").json()["token"]
        saved = client.post(path + "/save", json={
            "idempotency_key": "initial-na", "token": token, "revision": view["revision"],
            "changes": [{"row_id": view["rows"][0]["id"], "point_id": view["points"][0]["id"],
                         "intent": "initial", "value": "N/A"}],
        })
        assert saved.is_success, saved.text
        result = client.get(base + "/result-cells").json()
        command = {"project": "PROJECT-P", "base_revision": result["base_revision"],
                   "stability_document": {}, "include_charts": False}
        from openpyxl import load_workbook

        summary = client.post("/api/v1/exports", json={
            "project": "PROJECT-P", "base_revision": result["base_revision"],
            "format": "stability_summary",
        })
        assert summary.is_success, summary.text
        workbook = load_workbook(BytesIO(client.get(summary.json()["download_url"]).content))
        sheet = workbook.active
        assert sheet.title == "稳定性汇总表"
        assert [sheet.cell(6, c).value for c in (3, 4, 5)] == ["N/A", "已到期未录入", "未到期"]
        assert sheet.freeze_panes == "C6"
        assert sheet["A4"].fill.fgColor.rgb == "009EC4E2"
        assert sheet["C6"].fill.patternType is None
        workbook.close()
        for scope, expected in (({}, ("N/A", "已到期未录入", "未到期")),
                                ({"filters": {"month": 0}}, ("N/A",)),
                                ({"slot_ids": [result["cells"][0]["slot_id"]]}, ("N/A",))):
            report = client.post("/api/v1/reports", json={**command, **scope})
            assert report.is_success, report.text
            data = report.json()
            assert data["result_count"] == 1
            doc = Document(BytesIO(client.get(data["word_download_url"]).content))
            matrix = next(table for table in doc.tables if table.cell(0, 0).text == "检项")
            assert tuple(cell.text for cell in matrix.rows[1].cells[5:]) == expected
