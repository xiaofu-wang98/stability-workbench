"""Source role is separate from result interpretation and approval."""

from openpyxl import Workbook
from stability_hub.parsing import parse_source
from test_v52_review_tasks import matrix_file
from openpyxl import load_workbook


def test_footer_without_digits_does_not_hide_possible_unparsed_results(tmp_path):
    path = matrix_file(tmp_path)
    book = load_workbook(path, rich_text=True)
    s = book.active
    s["A9"] = "符合规定"
    s["B9"] = "澄明液体"
    s["C10"] = "未见异常"
    s["A11"] = "备注：6M外观暂停，发起AR-001"
    book.save(path)
    parsed = parse_source(path, allow_partial=True)
    roles = {c: r["role"] for r in parsed.coverage[0]["regions"] for c in r["coordinates"]}
    assert all(roles[c] not in {"note", "auxiliary"} for c in ["A9", "B9", "C10"])
    assert any(r["role"] == "unclassified" for r in parsed.coverage[0]["regions"])
    assert roles["A11"] == "note"


def test_pause_note_can_have_a_note_role_and_still_require_source_decision(tmp_path):
    path = matrix_file(tmp_path)
    b = load_workbook(path, rich_text=True)
    s = b.active
    s["C4"] = 0
    s["D4"] = 6
    s["A9"] = "6M暂停"
    b.save(path)
    parsed = parse_source(path, allow_partial=True)
    roles = {c: r["role"] for r in parsed.coverage[0]["regions"] for c in r["coordinates"]}
    assert roles["A9"] == "note"
    from stability_hub.matrix_fields import month_token

    at_six = [r for r in parsed.observations if month_token(r.fields["time_point"]) == 6]
    assert at_six and all("暂停" in r.comment for r in at_six)
    assert all("暂停" not in r.comment for r in parsed.observations if r not in at_six)
