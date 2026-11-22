"""R3-B verification: Tier-A deterministic cleaning, Tier-B GMP redlines, and targeted footer notes."""
from __future__ import annotations

from decimal import Decimal

from stability_hub.business_notes import note_scope
from stability_hub.cleaning import (
    clean_observation_events,
    extract_exception_refs,
    repair_english_wrap_spaces,
    summarize_cleaning_audit,
)
from stability_hub.models import Location, RawObservation
from stability_hub.numeric_formats import interpret_result
from stability_hub.review_issues import source_issues


def _raw(fields: dict[str, str], *, comment: str = "", raw_type: str = "") -> RawObservation:
    return RawObservation(
        adapter="stability_matrix",
        fields=fields,
        location=Location(format="xlsx", section="长期DS", row=10, column=6),
        comment=comment,
        source_value={"raw_type": raw_type},
    )


def test_a1_comparator_numeric_does_not_crash():
    for text, expected_cmp, expected_val in [
        ("<0.2", "lt", Decimal("0.2")),
        ("＜0.45", "lt", Decimal("0.45")),
        ("≤10", "le", Decimal("10")),
        (">100", "gt", Decimal("100")),
        ("＜0.01", "lt", Decimal("0.01")),
    ]:
        raw = _raw({"test_item": "细菌内毒素", "result": text, "unit": "EU/mL"}, raw_type="")
        res = interpret_result(raw, text, "EU/mL", "numeric")
        assert res.status == "numeric"
        assert res.comparator == expected_cmp
        assert res.value == expected_val
        assert res.raw_text == text


def test_a2_a3_a6_cleaning_events_preserve_raw_and_extract_refs():
    repaired, changed = repair_english_wrap_spaces("Theabsolute\ndifference is0.0 for main peak pl")
    assert changed
    assert repaired == "The absolute difference is 0.0 for main peak pI"

    refs = extract_exception_refs("暂未出结果（详见AR-C-IP318-2026003，关联AE－2026-01）")
    assert refs == ["AR-C-IP318-2026003", "AE-2026-01"]

    obs = _raw({
        "test_item": "细菌内毒素",
        "method": "无菌检查法\n薄膜过滤法",
        "criterion": "Thedifference between main peak pI is≤0.2",
        "result": "＜0.45 (AR-C-IP318-2026003)",
        "unit": "EU/ml",
    })
    summary = summarize_cleaning_audit([obs])
    codes = {entry["rule_code"] for entry in summary["by_rule"]}
    assert "comparator_numeric_fix" in codes
    assert "unit_and_width_normalize" in codes
    assert "english_wrap_space_repair" in codes
    assert "catalog_synonym_canonicalize" in codes
    assert "exception_ref_extraction" in codes


def test_a5_footer_note_targeted_no_broadcast():
    obs = [
        _raw({"test_item": "纯度", "method": "非还原型CE-SDS法", "criterion": "低分子量组分LMW(%): ≤5.0", "time_point": "3M"}),
        _raw({"test_item": "纯度", "method": "非还原型CE-SDS法", "criterion": "主峰Main Peak(%): ≥90.0", "time_point": "3M"}),
        _raw({"test_item": "等电点", "method": "icIEF法", "criterion": "主峰pI差值≤0.2", "time_point": "3M"}),
        _raw({"test_item": "电荷变异体", "method": "icIEF法", "criterion": "酸性峰(%): 报告数据", "time_point": "3M"}),
    ]
    # Glued shorthand nrceLMW targets ONLY nrCE + LMW (index 0), never broadcasting to all 4 rows
    assert note_scope('nrceLMW:"0.2"修改为"0.3"', obs) == {0}
    # Targeted charge variant note hits ONLY index 3, never 等电点 (index 2)
    assert note_scope("长期3M电荷变异体：发起AR-C-IP318-2026003复测", obs) == {3}
    # Generic unaddressed narrative remark never broadcasts to all cells
    assert note_scope("检测组提供本次复核修订说明", obs) == set()


def test_tier_b_substantive_conflicts_always_intercepted():
    # Bilingual method contradiction MUST still raise method_conflict
    raw_method = _raw({
        "test_item": "纯度",
        "method": "还原型CE-SDS法(NR CE-SDS Method)",
        "criterion": "主峰≥90.0%",
        "result": "97.5",
    })
    assert "method_conflict" in {i.code for i in source_issues("c1", raw_method)}

    # Bilingual purity subresult contradiction MUST still raise identity_conflict
    raw_purity = _raw({
        "test_item": "纯度",
        "method": "SEC-HPLC法",
        "criterion": "单体(%) / HMW(%)",
        "result": "99.1",
    })
    assert "identity_conflict" in {i.code for i in source_issues("c2", raw_purity)}

    # Genuine dual-method switch MUST still raise identity_ambiguity
    raw_dual = _raw({
        "test_item": "生物学活性",
        "method": "电化学发光法\n报告基因法",
        "criterion": "70%~130%",
        "result": "102",
    })
    assert "identity_ambiguity" in {i.code for i in source_issues("c3", raw_dual)}
