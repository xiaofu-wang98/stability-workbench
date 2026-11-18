from pathlib import Path
from io import BytesIO
from contextlib import closing
import hashlib
import json

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from stability_hub.api import create_app
from stability_hub.storage import Store
from test_t1_controlled_export_gate import checked, export, real_t1
from test_worksheet_header_metadata import ADMIN, create_historical, historical_source, seed_of, upload_history
from test_workflow import confirm


def save_header(client, view, preview, fields):
    base = "/api/v1/worksheets/" + view["id"]
    token = checked(client.post(base + "/lease")).json()["token"]
    saved = checked(client.post(base + "/header-metadata", json={
        "token": token, "revision": view["revision"], "idempotency_key": "confirm-header:" + view["id"],
        "basis": "隔离核对标签与原件；存放位置与开展地点分别处理",
        "metadata_fingerprint": preview["metadata_fingerprint"], **fields,
    })).json()
    checked(client.post(base + "/lease/release", json={"token": token}))
    result = checked(client.get(base)).json()
    assert result["plan_version"] == saved["plan_version"] == view["plan_version"] + 1
    assert result["identity"] == view["identity"] and result["cells"] == view["cells"]
    return result


def test_explicit_header_fields_are_projected_without_changing_study_identity(tmp_path, config, context):
    store = Store(tmp_path / "store")
    app = create_app(store, [config], ADMIN)
    batch = context.defaults["batch"]
    path = historical_source(tmp_path / "example.xlsx", batch, "EQ-1", "☑常熟工厂")
    with closing(load_workbook(path)) as book:
        sheet = book.active
        sheet["G1"] = "产品名称\nProduct Name"
        sheet["H1"] = "产品甲\nProduct A"
        book.save(path)
    confirm(app.state.workflow, upload_history(app.state.workflow, path, context))
    with TestClient(app) as client:
        slot, seed = seed_of(client, batch)
        assert seed["product_name"] == "产品甲\nProduct A"
        assert seed["storage_location"] == "FREEZER-1" and seed["location"] == "常熟工厂"
        assert seed["protocol_number"] == "STUDY-P-01"
        sheet_url = create_historical(client, slot, seed, confirm_metadata=True)
        before = checked(client.get(sheet_url)).json()
        assert before["product_name"] == seed["product_name"]
        assert before["storage_location"] == seed["storage_location"]
        assert before["protocol_number"] == seed["protocol_number"]
        preview = checked(client.get(sheet_url + "/header-metadata")).json()
        assert preview["metadata_sources"]["product_name"][0]["cell"] == "H1"
        fields = {field: preview[field] for field in ("product_name", "protocol_number", "equipment", "location", "storage_location")}
        fields["specification_text"] = "经人工确认的交付规格"
        after = save_header(client, before, preview, fields)
        assert all(after[field] == value for field, value in fields.items())
        with closing(load_workbook(BytesIO(export(client, config.project, [before["id"]])))) as book:
            grid = book.active
            assert [grid[cell].value for cell in ("D1", "H1", "D2", "F2", "J3", "K3", "L3")] == [
                "产品甲\nProduct A", "经人工确认的交付规格", "EQ-1", "STUDY-P-01", "☑ 常熟工厂",
                "存放位置\nStorage Location", "FREEZER-1",
            ]


def test_real_header_fidelity_keeps_missing_and_storage_fields_distinct(real_t1):
    root, store = real_t1
    expected = json.loads((root / "run-summary.json").read_text(encoding="utf8"))["projects"]
    with TestClient(create_app(store, [], ADMIN, enforce_formal_sample_binding=True)) as client:
        for project, entry in expected.items():
            with store.read() as conn:
                stored = conn.execute("SELECT stored_path FROM documents WHERE sha256=?", (entry["original_sha256"],)).fetchone()
                original_file = Path(stored[0])
                old_export = conn.execute("""
                    SELECT id,sha256,snapshot_json FROM workflow_exports WHERE project=?
                    AND json_extract(snapshot_json,'$.format')='controlled_workbook' ORDER BY created_at DESC LIMIT 1
                """, (project,)).fetchone()
            assert hashlib.sha256(original_file.read_bytes()).hexdigest() == entry["original_sha256"]
            old_download = f"/api/v1/exports/{old_export['id']}/file"
            assert hashlib.sha256(checked(client.get(old_download)).content).hexdigest() == old_export["sha256"]
            summaries = checked(client.get(f"/api/v1/projects/{project}/worksheets")).json()
            ids = []
            with closing(load_workbook(original_file, data_only=True)) as original:
                source = original["DP长期"]
                for idx, block in enumerate(entry["blocks"]):
                    sheet_id = next(s["id"] for s in summaries if s["batch"] == block["batch"] and s["condition"] == block["condition"])
                    path = "/api/v1/worksheets/" + sheet_id
                    view = checked(client.get(path)).json()
                    preview = checked(client.get(path + "/header-metadata")).json()
                    fields = {field: preview[field] for field in ("product_name", "protocol_number", "equipment",
                                                                   "specification_text", "location", "storage_location")}
                    if project == "IP281":
                        for field, coord in (("product_name", "D1"), ("equipment", "D2"), ("protocol_number", "F2"),
                                             ("location", "J3"), ("specification_text", "H1")):
                            ref, = preview["metadata_sources"][field]
                            assert ref["cell"] == coord and ref["raw"] == source[coord].value.strip()
                        assert preview["location"] == "常熟工厂" and not view.get("legacy_location")
                    else:
                        title, storage = (("A1", "G2"), ("A43", "F44"))[idx]
                        item, = preview["metadata_sources"]["product_name"]
                        assert item["reference_only"] and item["cell"] == title and item["raw"] == source[title].value.strip()
                        item, = preview["metadata_sources"]["storage_location"]
                        assert item["cell"] == storage and item["raw"] == source[storage].value.strip()
                        assert view["legacy_location"] == preview["storage_location"] == source[storage].value.strip()
                        assert not view.get("location")
                        assert all(not fields[field] for field in fields if field != "storage_location")
                    after = save_header(client, view, preview, fields)
                    assert all(after[field] == value for field, value in fields.items() if field in after)
                    assert sum(bool(cell.get("adopted")) for cell in after["cells"]) == block["adopted"]
                    ids.append(sheet_id)
                with closing(load_workbook(BytesIO(export(client, project, ids)))) as book:
                    grid = book.active
                    if project == "IP281":
                        assert all(grid[coord].value == source[coord].value.strip() for coord in ("D1", "D2", "F2", "H1"))
                        assert grid["J3"].value == "☑ 常熟工厂"
                        assert grid["I43"].value == source["I49"].value.strip()
                        assert "Water content" in grid["A13"].value
                    else:
                        for start, batch, date, storage in ((1, "B2", "J2", "G2"), (43, "B44", "I44", "F44")):
                            assert grid[f"F{start}"].value == source[batch].value
                            assert grid[f"B{start + 1}"].value == source[date].value.replace(".", "-")
                            assert all(grid[f"{column}{row}"].value is None for column, row in
                                       (("D", start), ("H", start), ("D", start + 1), ("F", start + 1)))
                            assert grid[f"J{start + 2}"].value == "□ 未填写 / Unspecified"
                            assert grid[f"K{start + 2}"].value == "存放位置\nStorage Location"
                            assert grid[f"L{start + 2}"].value == source[storage].value
                        assert all(cell.value is None for row in grid["A41:L42"] for cell in row)
            assert hashlib.sha256(checked(client.get(old_download)).content).hexdigest() == old_export["sha256"]
            with store.read() as conn:
                current = conn.execute("SELECT sha256,snapshot_json FROM workflow_exports WHERE id=?", (old_export["id"],)).fetchone()
                assert tuple(current) == (old_export["sha256"], old_export["snapshot_json"])
                assert conn.execute("PRAGMA user_version").fetchone()[0] == 20
