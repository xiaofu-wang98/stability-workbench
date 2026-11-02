"""Numeric meaning and display layout remain separate; no test depends on project IDs."""

from decimal import Decimal
import pytest
from stability_hub.models import RawObservation, Location
from stability_hub.numeric_formats import interpret_result, LEGACY_VERSION, VERSION
from stability_hub.normalize import NeedsReview


def raw(value, fmt, version=VERSION):
    return RawObservation(
        fields={"result": str(value), "unit": ""},
        location=Location(format="xlsx", section="Any", row=1, column=1),
        number_format=fmt,
        source_value={"raw_type": "number", "rule_version": version},
    )


@pytest.mark.parametrize(
    "value,fmt,display",
    [
        ("5.6", "0.0_ ", "5.6"),
        ("-5.6", "0.0_ ", "-5.6"),
        ("0", "0.0_ ", "0.0"),
        ("5.6", r"0.0_);[Red]\(0.0\)", "5.6"),
        ("-5.6", r"0.0_);[Red]\(0.0\)", "(5.6)"),
        ("0", r"0.0_);[Red]\(0.0\)", "0.0"),
        ("1234.567", "#,##0.00", "1,234.57"),
        ("0", '0.0;[Red](0.0);"-"', "-"),
        ("123.456", "0.00E+00", "1.23E+02"),
        ("0.995", r"0.0\%", "1.0%"),
        ("1.234567", "0.0", "1.2"),
    ],
)
def test_safe_layout_keeps_unrounded_value(value, fmt, display):
    result = interpret_result(raw(value, fmt), value, "", "numeric")
    assert result.value == Decimal(value) and result.interpretation["display_text"] == display
    assert result.interpretation["version"] == VERSION


def test_percent_and_literal_percent_do_not_share_scaling():
    percent = interpret_result(raw(".995", "0.0%"), ".995", "%", "numeric")
    literal = interpret_result(raw(".995", r"0.0\%"), ".995", "%", "numeric")
    assert percent.value == Decimal("99.5") and literal.value == Decimal(".995")
    assert percent.interpretation["factor"] == "100" and literal.interpretation["factor"] == "1"


@pytest.mark.parametrize("fmt", ["0.0,,", "#,##0,", "0.0/0", "[<0]0.0;0.0", "yyyy-mm-dd", "0;;", "0.0%%"])
def test_real_scale_or_unsupported_semantics_remain_questions(fmt):
    with pytest.raises(NeedsReview):
        interpret_result(raw("-5.6", fmt), "-5.6", "", "numeric")


def test_old_frozen_rule_does_not_silently_upgrade():
    with pytest.raises(NeedsReview):
        interpret_result(raw("5.6", "0.0_ ", LEGACY_VERSION), "5.6", "", "numeric")


def test_existing_draft_numeric_upgrade_is_previewed_versioned_and_preserves_raw(tmp_path, xlsx, context):
    from uuid import uuid4
    from openpyxl import load_workbook
    from stability_hub.parsing import parse_source
    from stability_hub.storage import Store
    from stability_hub.workflow import Workflow
    from stability_hub.models import ProjectConfig
    from stability_hub import file_rules
    from stability_hub.v5_contracts import Reinterpret
    from test_v5_regressions import TECH
    from test_v51_interpretation import receive

    path = xlsx([["pH", "0M", 5.6, ""]])
    book = load_workbook(path)
    book.active["C2"].number_format = "0.0_ "
    book.save(path)
    parsed = parse_source(path)
    parsed.observations = [
        r.model_copy(update={"source_value": {**r.source_value, "rule_version": LEGACY_VERSION}})
        for r in parsed.observations
    ]
    flow = Workflow(Store(tmp_path / "old-home"))
    flow.configure(ProjectConfig(project="PROJECT-P"))
    p = flow.upload(
        "PROJECT-P", path.name, path.read_bytes(), context, uuid4().hex, actor=TECH, _parsed=parsed
    )
    p = receive(flow, p)
    assert p.candidates[0].issue_code == "numeric_format"
    with flow.store.connect() as conn:
        raw_before = [tuple(r) for r in conn.execute("SELECT id,raw_json FROM workflow_candidates")]
        events = conn.execute("SELECT count(*) FROM workflow_events").fetchone()[0]
        info = file_rules.reinterpret_preview(flow, conn, flow._upload(conn, p.preview_id), TECH)
        assert info["numeric_rule_version"] == VERSION and info["changes"]
        assert conn.execute("SELECT count(*) FROM workflow_events").fetchone()[0] == events
    assert flow.preview(p.preview_id, actor=TECH).candidates[0].issue_code == "numeric_format"
    file_rules.reinterpret(
        flow,
        p.preview_id,
        Reinterpret(
            base_revision=p.base_revision,
            idempotency_key=uuid4().hex,
            reason="核对本文件数字显示解释升级差异",
            fingerprint=info["fingerprint"],
        ),
        actor=TECH,
    )
    after = flow.preview(p.preview_id, actor=TECH)
    assert not after.candidates[0].issues and after.candidates[0].record.result.value == Decimal("5.6")
    with flow.store.connect() as conn:
        assert [tuple(r) for r in conn.execute("SELECT id,raw_json FROM workflow_candidates")] == raw_before
        assert conn.execute("SELECT count(*) FROM workflow_versions").fetchone()[0] == 0
