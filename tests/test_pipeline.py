import json

import pytest
from docx import Document
from openpyxl import load_workbook
from openpyxl.comments import Comment

from stability_hub.models import ImportContext, ProjectConfig
from stability_hub.service import Hub
from stability_hub.verification import verify_bundle


def by_item_month(hub, project="PROJECT-P"):
    return {
        (e["record"]["key"]["item"], e["record"]["key"]["month"]): e["record"]["result"]
        for e in hub.store.snapshot(project)
    }


def test_coa_and_partial_months_accumulate_and_export(tmp_path, config, context, coa, xlsx):
    hub = Hub(tmp_path / "db")
    release = ImportContext(role="release_coa", defaults={"study_id": "RELEASE-P-01"})
    assert hub.import_file(coa(), config, release, legacy_compatibility=True)["counts"] == {"accepted": 1}
    early = hub.export(config.project, tmp_path / "reports")
    assert hub.import_file(xlsx([["Protein content", "6M", "2.05", "mg/ml"]]), config, context, legacy_compatibility=True)["counts"] == {
        "accepted": 1
    }
    # Late-arriving partial results must fill the correct earlier point, without deleting 6M.
    assert hub.import_file(xlsx([["Purity", "3M", "98.2", "%"]], "month3.xlsx"), config, context, legacy_compatibility=True)[
        "counts"
    ] == {"accepted": 1}
    late = hub.export(config.project, tmp_path / "reports")
    values = by_item_month(hub)
    assert values[("protein", None)]["value"] == "2.10"
    assert values[("protein", 6)]["value"] == "2.05"
    assert values[("purity", 3)]["value"] == "98.2"
    assert early != late and early.exists()
    assert verify_bundle(late)["passed"]
    wb = load_workbook(late / "stability.xlsx")
    assert wb["Records"].max_row == 4
    assert {wb["Records"].cell(r, 8).value for r in range(2, 5)} == {None, "3", "6"}
    wb.close()
    doc = Document(late / "stability.docx")
    assert any("6M" == cell.text for table in doc.tables for row in table.rows for cell in row.cells)
    changes = json.loads((late / "manifest.json").read_text(encoding="utf8"))["changes"]
    assert sorted(c["action"] for c in changes) == ["added", "added", "unchanged"]


def test_idempotence_and_equivalent_sources(tmp_path, config, context, xlsx):
    hub = Hub(tmp_path / "db")
    first = xlsx([["Protein content", "3M", "2.10", "mg/ml"]])
    hub.import_file(first, config, context, legacy_compatibility=True)
    assert hub.import_file(first, config, context, legacy_compatibility=True)["duplicate"]
    other = xlsx([["蛋白含量", "3", "2.1", "mg/ml"]], "other.xlsx")
    assert hub.import_file(other, config, context, legacy_compatibility=True)["counts"] == {"equivalent": 1}
    snapshot = hub.store.snapshot(config.project)
    assert len(snapshot) == 1 and len(snapshot[0]["sources"]) == 2
    assert snapshot[0]["record"]["result"]["value"] == "2.10"
    assert verify_bundle(hub.export(config.project, tmp_path / "reports"))["passed"]


@pytest.mark.parametrize("missing", [None, "", "/", "\\", "---"])
def test_fragment_placeholder_does_not_erase_numeric_history(tmp_path, config, context, xlsx, missing):
    hub = Hub(tmp_path / "db")
    hub.import_file(xlsx([["Large Fragment", "3M", "0.1", "%"]]), config, context, legacy_compatibility=True)
    outcome = hub.import_file(xlsx([["Large Fragment", "3M", missing, "%"]], "partial.xlsx"), config, context, legacy_compatibility=True)
    assert outcome["counts"] == {"ignored": 1}
    assert by_item_month(hub)[("large_fragment", 3)]["value"] == "0.1"


def test_na_is_conflict_not_automatic_baseline_overwrite(tmp_path, config, context, xlsx):
    hub = Hub(tmp_path / "db")
    hub.import_file(xlsx([["N-terminal", "0M", "3.7", "%"]]), config, context, legacy_compatibility=True)
    outcome = hub.import_file(xlsx([["N-terminal", "0M", "N/A", "%"]], "na.xlsx"), config, context, legacy_compatibility=True)
    assert outcome["counts"] == {"conflict": 1}
    assert by_item_month(hub)[("n_terminal", 0)]["value"] == "3.7"
    with pytest.raises(ValueError, match="unresolved"):
        hub.export(config.project, tmp_path / "reports")


def test_known_wrong_fragment_reversion_is_conflict(tmp_path, config, context, xlsx):
    hub = Hub(tmp_path / "db")
    hub.import_file(xlsx([["Large Fragment", "0M", "0.1", "%"]]), config, context, legacy_compatibility=True)
    assert hub.import_file(xlsx([["Large Fragment", "0M", "0.0", "%"]], "old-repair.xlsx"), config, context, legacy_compatibility=True)[
        "counts"
    ] == {"conflict": 1}
    assert by_item_month(hub)[("large_fragment", 0)]["value"] == "0.1"


def test_comment_amendment_is_visible_pending_not_silently_ignored(tmp_path, config, context, xlsx):
    path = xlsx([["Large Fragment", "1M", "0.1", "%"]])
    wb = load_workbook(path)
    wb.active["C2"].comment = Comment("0.1修改为1.0", "Synthetic fixture")
    wb.save(path)
    wb.close()
    hub = Hub(tmp_path / "db")
    assert hub.import_file(path, config, context, legacy_compatibility=True)["counts"] == {"pending": 1}
    issue = hub.list_issues(config.project)[0]
    assert issue["issue_code"] == "source_comment"
    assert "1.0" in json.loads(issue["raw_json"])["comment"]
    assert hub.store.snapshot(config.project) == []


@pytest.mark.parametrize(
    "bad_value,code",
    [("=1+1", "formula_result"), (True, "unsupported_structure"), ("98.3 98.8", "unrecognized_result")],
)
def test_unsafe_results_are_pending(tmp_path, config, context, xlsx, bad_value, code):
    hub = Hub(tmp_path / "db")
    outcome = hub.import_file(xlsx([["Purity", "3M", bad_value, "%"]]), config, context, legacy_compatibility=True)
    assert outcome["counts"] == {"pending": 1}
    assert hub.list_issues(config.project)[0]["issue_code"] == code


def test_reordered_columns_and_rows_keep_business_identity(tmp_path, config, context, xlsx):
    hub = Hub(tmp_path / "db")
    source = xlsx(
        [["3M", "%", "98.2", "Purity"], ["6M", "mg/ml", "2.1", "Protein content"]],
        headers=["Time point", "Unit", "Result", "Test item"],
    )
    assert hub.import_file(source, config, context, legacy_compatibility=True)["counts"] == {"accepted": 2}
    assert by_item_month(hub)[("protein", 6)]["value"] == "2.1"
    assert by_item_month(hub)[("purity", 3)]["value"] == "98.2"


def test_unknown_item_and_missing_identity_are_retained(tmp_path, config, xlsx):
    hub = Hub(tmp_path / "db")
    source = xlsx([["Mystery test", "3M", "1.0", ""], ["Protein content", "3M", "2.1", "mg/ml"]])
    assert hub.import_file(source, config, ImportContext(), legacy_compatibility=True)["counts"] == {"pending": 2}
    assert {i["issue_code"] for i in hub.list_issues(config.project)} == {"unknown_item", "missing_identity"}


def test_file_and_context_contradiction_is_not_overwritten(tmp_path, config, coa):
    hub = Hub(tmp_path / "db")
    context = ImportContext(role="release_coa", defaults={"batch": "OTHER", "study_id": "R1"})
    assert hub.import_file(coa(), config, context, legacy_compatibility=True)["counts"] == {"pending": 1}
    assert hub.list_issues(config.project)[0]["issue_code"] == "context_conflict"


def test_second_project_isolated_without_new_pipeline(tmp_path, config, context, xlsx):
    hub = Hub(tmp_path / "db")
    source = xlsx([["Protein content", "3M", "2.1", "mg/ml"]])
    hub.import_file(source, config, context, legacy_compatibility=True)
    second = ProjectConfig(project="PROJECT-Q", items=config.items)
    hub.import_file(source, second, context, legacy_compatibility=True)
    assert len(hub.store.snapshot("PROJECT-P")) == len(hub.store.snapshot("PROJECT-Q")) == 1
    assert hub.store.snapshot("PROJECT-P")[0]["slot_id"] != hub.store.snapshot("PROJECT-Q")[0]["slot_id"]


def test_export_failure_retains_history_and_prior_report(tmp_path, config, context, xlsx, monkeypatch):
    hub = Hub(tmp_path / "db")
    hub.import_file(xlsx([["Protein content", "3M", "2.1", "mg/ml"]]), config, context, legacy_compatibility=True)
    old = hub.export(config.project, tmp_path / "reports")

    def fail(*args):
        raise OSError("simulated disk failure")

    monkeypatch.setattr("stability_hub.service.write_reports", fail)
    with pytest.raises(OSError, match="disk failure"):
        hub.export(config.project, tmp_path / "reports")
    assert old.exists() and len(hub.store.snapshot(config.project)) == 1
    with hub.store.connect() as conn:
        assert [r[0] for r in conn.execute("SELECT state FROM exports ORDER BY rowid")] == [
            "published",
            "failed",
        ]


def test_unsupported_file_rejected_without_history(tmp_path, config, context):
    hub = Hub(tmp_path / "db")
    with pytest.raises(ValueError, match="Only"):
        hub.import_file(tmp_path / "unread.pdf", config, context, legacy_compatibility=True)
    assert not hub.store.snapshot(config.project)


def test_many_time_points_new_batch_and_method_stay_distinct(tmp_path, config, context, xlsx):
    hub = Hub(tmp_path / "db")
    rows = [["Protein content", f"{m}M", "2.10", "mg/ml", "B001A1", "UV"] for m in (0, 3, 6, 9, 12, 18)]
    rows += [
        ["Protein content", "3M", "2.11", "mg/ml", "B001B1", "UV"],
        ["Protein content", "3M", "2.09", "mg/ml", "B001A1", "UV-2"],
    ]
    varied_context = ImportContext(defaults={k: v for k, v in context.defaults.items() if k != "batch"})
    source = xlsx(rows, headers=["Test item", "Time point", "Result", "Unit", "Batch", "Method"])
    assert hub.import_file(source, config, varied_context, legacy_compatibility=True)["counts"] == {"accepted": 8}
    report = hub.export(config.project, tmp_path / "reports")
    assert verify_bundle(report)["passed"]
    assert len(hub.store.snapshot(config.project)) == 8


def test_unknown_nonempty_sheet_prevents_partial_import(tmp_path, config, context, xlsx):
    source = xlsx([["Purity", "3M", "99", "%"]])
    book = load_workbook(source)
    book.create_sheet("Unknown").append(["Unrecognized analytical data", "123"])
    book.save(source)
    book.close()
    hub = Hub(tmp_path / "db")
    with pytest.raises(ValueError, match="Unsupported"):
        hub.import_file(source, config, context, legacy_compatibility=True)
    assert not hub.store.snapshot(config.project)


def test_formula_in_identity_cannot_create_a_batch(tmp_path, config, context, xlsx):
    source = xlsx(
        [["Purity", "3M", "99", "%", '=CONCAT("B", "001")']],
        headers=["Test item", "Time point", "Result", "Unit", "Batch"],
    )
    hub = Hub(tmp_path / "db")
    assert hub.import_file(source, config, context, legacy_compatibility=True)["counts"] == {"pending": 1}
    assert hub.list_issues(config.project)[0]["issue_code"] == "unsupported_structure"


def test_context_change_cannot_silently_reinterpret_import(tmp_path, config, context, xlsx):
    source = xlsx([["Purity", "3M", "99", "%"]])
    hub = Hub(tmp_path / "db")
    hub.import_file(source, config, context, legacy_compatibility=True)
    changed = ImportContext(defaults={**context.defaults, "batch": "OTHER"})
    with pytest.raises(ValueError, match="different context/config"):
        hub.import_file(source, config, changed, legacy_compatibility=True)
    assert len(hub.store.snapshot(config.project)) == 1
