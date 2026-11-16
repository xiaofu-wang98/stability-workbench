import json

import pytest
from docx import Document
from openpyxl import load_workbook

from stability_hub.service import Hub
from stability_hub.verification import verify_bundle


@pytest.fixture
def report(tmp_path, config, context, xlsx):
    hub = Hub(tmp_path / "db")
    hub.import_file(
        xlsx([["Protein content", "3M", "2.10", "mg/ml"], ["Purity", "6M", "98.2", "%"]]), config, context
    , legacy_compatibility=True)
    return hub.export(config.project, tmp_path / "reports")


def test_wrong_word_cell_detected_even_when_manifest_is_forged(report):
    doc = Document(report / "stability.docx")
    doc.tables[1].cell(1, 4).text = "999.0"
    doc.save(report / "stability.docx")
    manifest = json.loads((report / "manifest.json").read_text(encoding="utf8"))
    for change in manifest["changes"]:
        change["after"]["result"]["value"] = "999.0"
    (report / "manifest.json").write_text(json.dumps(manifest), encoding="utf8")
    result = verify_bundle(report)
    assert not result["passed"]
    assert any("Word" in error for error in result["errors"])


def test_excel_summary_and_matrix_checked_independently(report):
    wb = load_workbook(report / "stability.xlsx")
    wb["Records"]["L2"] = "999.0"
    wb.save(report / "stability.xlsx")
    wb.close()
    result = verify_bundle(report)
    assert not result["passed"]
    assert any("Summary result" in error for error in result["errors"])


def test_deleted_tail_row_is_not_hidden_by_zip_truncation(report):
    doc = Document(report / "stability.docx")
    row = doc.tables[-1].rows[-1]._tr
    row.getparent().remove(row)
    doc.save(report / "stability.docx")
    assert not verify_bundle(report)["passed"]


def test_modified_source_is_detected(report):
    snapshot = json.loads((report / "records.json").read_text(encoding="utf8"))
    from pathlib import Path

    source = Path(snapshot[0]["sources"][0]["stored_path"])
    source.write_bytes(source.read_bytes() + b"modified")
    result = verify_bundle(report)
    assert not result["passed"]
    assert any("Source hash mismatch" in error for error in result["errors"])


def test_forged_evidence_and_snapshot_cannot_replace_original_source(report):
    snapshot_file = report / "records.json"
    snapshot = json.loads(snapshot_file.read_text(encoding="utf8"))
    snapshot[0]["record"]["result"]["value"] = "777.0"
    snapshot[0]["record"]["result"]["raw_text"] = "777.0"
    snapshot[0]["sources"][0]["raw_json"]["fields"]["result"] = "777.0"
    snapshot_file.write_text(json.dumps(snapshot), encoding="utf8")
    result = verify_bundle(report)
    assert not result["passed"]
    assert any("Original source fields" in error for error in result["errors"])
