"""T1 controlled export gate. Set T1_ACCEPTANCE_ROOT and T1_ACCEPTANCE_HOME for real files."""
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from contextlib import closing
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from stability_hub.api import create_app
from stability_hub.business_notes import frozen_block_events
from stability_hub.models import RawObservation
from stability_hub.stability_summary import controlled_block, write_controlled_workbook
from stability_hub.storage import Store
from stability_hub.verification import check_original_fields, reread_source_rows
from test_worksheet_header_metadata import ADMIN, create_historical, historical_source, seed_of, upload_history
from test_workflow import confirm

REPO = Path(__file__).resolve().parents[1]


def checked(response):
    assert response.is_success, response.text
    return response


def export(client, project, sheet_ids):
    base = f"/api/v1/projects/{project}"
    revision = checked(client.get(base + "/configuration")).json()["base_revision"]
    response = checked(client.post(base + "/controlled-workbook", json={"base_revision": revision, "sheet_ids": sheet_ids}))
    return checked(client.get(response.json()["download_url"])).content


def test_controlled_labels_for_new_and_existing_historical_plans(tmp_path, config, context):
    store = Store(tmp_path / "store")
    app = create_app(store, [config], ADMIN)
    batch = context.defaults["batch"]
    path = historical_source(tmp_path / "source.xlsx", batch, "UNIT-1", "☑常熟工厂")
    original = "纯度 Purity\nSize-exclusion chromatography"
    with closing(load_workbook(path)) as book:
        book.active["A6"] = original
        book.save(path)
    confirm(app.state.workflow, upload_history(app.state.workflow, path, context))
    with TestClient(app) as client:
        slot, seed = seed_of(client, batch)
        assert seed["items"][0]["source_test_item"] == [original]
        worksheet = create_historical(client, slot, seed, confirm_metadata=True)
        view = checked(client.get(worksheet)).json()
        with store.read() as conn:
            plan_text = conn.execute("SELECT plan_json FROM business_worksheet_plans WHERE sheet_id=?",
                                     (view["id"],)).fetchone()[0]
        assert json.loads(plan_text)["rows"][0]["source_test_item"] == [original]
        # Represent a plan saved before the source-label property existed.
        plan = json.loads(plan_text)
        del plan["rows"][0]["source_test_item"]
        with store.connect() as conn:
            conn.execute("UPDATE business_worksheet_plans SET plan_json=? WHERE sheet_id=?",
                         (json.dumps(plan), view["id"]))
        old = checked(client.get(worksheet)).json()
        assert old["rows"] == view["rows"] and old["cells"] == view["cells"]
        content = export(client, config.project, [view["id"]])
        with closing(load_workbook(BytesIO(content))) as book:
            grid = book.active
            assert grid["A6"].value.startswith(original)
            assert grid["D2"].value == "UNIT-1" and grid["J3"].value == "☑ 常熟工厂"
            assert [grid[cell].value for cell in ("C6", "D6")] == ["98", "97"]


def test_controlled_frozen_events_stay_in_the_matched_batch_and_point(tmp_path):
    ae = "24M肽图：发起AE调查，详见：AE-C-123"
    ar = "3M肽图：详见 AR-C-456"
    oos = "24M肽图：OOS-C-789"
    raw = {
        "location": {"section": "source", "row": 6, "column": 9},
        "fields": {"block_note_1": f"{ae}\n{ar}\n{oos}"},
        "evidence": {"block_note_1": {"section": "source", "row": 49, "column": 9}},
        "comment": f"表后说明（未自动修改结果）：\nI49: {ae}\nI49: {oos}\nI49: outside the frozen cell",
    }
    point = {"id": "p24", "amount": 24, "unit": "M", "label": "24M",
             "planned_date": "2026-01-01", "required_row_ids": ["r"]}
    assert list(frozen_block_events(raw, point)) == [ae, oos]
    assert list(frozen_block_events(raw, {**point, "amount": 3})) == []
    raw["comment"] += f"\nI49: {ar}"
    assert list(frozen_block_events(raw, {**point, "amount": 3})) == [ar]
    row = {"id": "r", "item_id": "ccit", "label": "CCIT", "source_test_item": ["CCIT"],
           "method": "", "method_version": "not_reported", "unit": ""}
    sheet = {
        "id": "sheet", "plan_version": 1, "project": "P", "start_date": "2024-01-01",
        "identity": {"sample_type": "DP", "study": "long_term", "batch": "first", "presentation": "1mg",
                     "study_id": "protocol", "condition": "5℃±3℃", "reporting_basis": ""},
        "rows": [row], "points": [{**point, "id": "p3", "amount": 3, "label": "3M"}, point], "cells": [],
        "source_events": {"p24": [ae, oos], "p3": [ar]},
    }
    first = controlled_block(sheet)
    second = controlled_block({**sheet, "identity": {**sheet["identity"], "batch": "second"},
                               "source_events": {}, "events": []})
    path = tmp_path / "controlled.xlsx"
    assert not write_controlled_workbook(path, [{"sample_type": "DP", "condition": "5℃±3℃", "blocks": [first, second]}])
    with closing(load_workbook(path)) as book:
        grid = book.active
        assert grid["A6"].value == "CCIT"
        assert grid["C7"].value == ar and grid["D7"].value == f"{ae}\n{oos}"
        assert all(grid.cell(row_number, 1).value is None for row_number in (8, 9))
        assert grid["C16"].value is None and grid["D16"].value is None


@pytest.fixture
def real_t1(tmp_path):
    root = os.environ.get("T1_ACCEPTANCE_ROOT")
    home = os.environ.get("T1_ACCEPTANCE_HOME")
    if not root and not home:
        pytest.skip("Set T1_ACCEPTANCE_ROOT (E2) and T1_ACCEPTANCE_HOME (verified T1-F1 home) for real files")
    assert root and home, "Set both T1 acceptance paths"
    root, home = Path(root), Path(home)
    assert home.resolve() != (REPO / "data").resolve(), "Use the T1-F1 acceptance home"
    target = tmp_path / "home"
    subprocess.run([sys.executable, str(REPO / "scripts/backup.py"), "--source", str(home), "--target", str(target)],
                   check=True, cwd=REPO)
    return root, Store(target)


def test_real_t1_controlled_gate(real_t1):
    root, store = real_t1
    e2 = json.loads((root / "run-summary.json").read_text(encoding="utf8"))
    cells = json.loads((root / "cell-evidence.json").read_text(encoding="utf8"))
    labels = json.loads((root / "bilingual-item-source-check.json").read_text(encoding="utf8"))
    counts, labels_checked, checks = Counter(), Counter(), Counter()
    ccit = 0
    with TestClient(create_app(store, [], ADMIN, enforce_formal_sample_binding=True)) as client:
        for project, entry in e2["projects"].items():
            summaries = checked(client.get(f"/api/v1/projects/{project}/worksheets")).json()
            sheet_ids = [next(s["id"] for s in summaries if s["batch"] == block["batch"]
                              and s["condition"] == block["condition"]) for block in entry["blocks"]]
            views = [checked(client.get(f"/api/v1/worksheets/{sid}")).json() for sid in sheet_ids]
            adopted = {}
            for view, block in zip(views, entry["blocks"], strict=True):
                rows = {row["id"]: offset for offset, row in enumerate(view["rows"], block["header_row"] + 5)}
                points = {point["id"]: col for col, point in enumerate(view["points"], 3)}
                adopted.update({(rows[cell["row_id"]], points[cell["point_id"]]): cell
                                for cell in view["cells"] if cell.get("adopted")})
            content = export(client, project, sheet_ids)
            with closing(load_workbook(BytesIO(content))) as book, store.read() as conn:
                assert book.sheetnames == ["01 DP"]
                grid = book.active
                sha = entry["original_sha256"]
                source = Path(conn.execute("SELECT stored_path FROM documents WHERE sha256=?", (sha,)).fetchone()[0])
                assert source.is_relative_to(store.home) and hashlib.sha256(source.read_bytes()).hexdigest() == sha
                original_tables = reread_source_rows(source, archive_only=True)
                with closing(load_workbook(source, data_only=True)) as original:
                    for label in (item for item in labels if item["project"] == project):
                        text = grid[label["output"]].value
                        for ref in label["source_labels"]:
                            actual = original[ref["sheet"]].cell(ref["row"], ref["column"]).value.strip()
                            assert actual == ref["text"].strip()
                            assert actual in text, (project, label["output"], actual, text)
                            if actual == "CCIT":
                                assert "CCIT" in text.splitlines()
                                ccit += 1
                        labels_checked[project] += 1
                    if project == "IP281":
                        assert grid["D2"].value == original["DP长期"]["D2"].value == "A-8111N"
                        assert grid["J3"].value == "☑ 常熟工厂"
                        assert grid["I43"].value == original["DP长期"]["I49"].value.strip()
                        assert all("AE-C-IP281-2026006" not in str(grid.cell(43, col).value)
                                   for col in range(1, grid.max_column + 1) if col != 9)
                    else:
                        assert grid["D40"].value and "AE-C-IP281-2026006" not in str(grid["I81"].value)
                for item in (cell for cell in cells if cell["project"] == project):
                    kind = item["check"]
                    if kind not in {"adopted_result_and_source", "unadopted_blank", "criteria", "timepoint_date_signature",
                                    "row3_merge", "separator_empty", "point_event", "bilingual_header", "batch",
                                    "start_date", "condition", "long_term_selected"}:
                        continue
                    coord = item["output"]
                    checks[kind] += 1
                    if kind == "row3_merge":
                        assert coord in {str(area) for area in grid.merged_cells.ranges}
                    elif kind == "separator_empty":
                        assert all(cell.value is None for row in grid[coord] for cell in row)
                    elif kind == "condition":
                        assert all(token in grid[coord].value for token in item["expected"])
                    elif kind == "long_term_selected":
                        assert "☑长期" in grid[coord].value
                    elif kind in {"point_event", "bilingual_header"}:
                        assert item["actual"] == grid[coord].value
                    elif kind == "adopted_result_and_source":
                        expected, basis = item["expected"], item["basis"]
                        version = basis["version"]
                        dest = grid[coord]
                        actual = adopted[dest.row, dest.column]
                        assert actual["adopted"]["result_version_id"] == version
                        assert dest.value == actual["raw"] == expected["text"]
                        if expected["unit"]:
                            assert expected["unit"] in grid.cell(grid[coord].row, 1).value
                        db_version = conn.execute("SELECT source_json FROM workflow_versions WHERE id=?", (version,)).fetchone()
                        raw_sources = [RawObservation.model_validate(s["raw_json"])
                                       for s in json.loads(db_version["source_json"])
                                       if s.get("document_hash") == sha and "raw_json" in s]
                        assert raw_sources and all(check_original_fields(original_tables, raw) for raw in raw_sources)
                        counts[project] += 1
                    else:
                        assert grid[coord].value == item["expected"], (project, coord, kind)
    assert counts == {"IP281": 259, "IP275": 477}
    assert labels_checked == {"IP281": 15, "IP275": 12} and ccit == 2
    assert checks["unadopted_blank"] == 230 and checks["criteria"] == 104
