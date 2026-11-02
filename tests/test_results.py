import pytest

from stability_hub.normalize import NeedsReview, normalize_result


@pytest.mark.parametrize(
    "raw,status",
    [
        ("0", "numeric"),
        ("0.0", "numeric"),
        ("N/A", "na"),
        ("ND", "nd"),
        ("---", "placeholder"),
        ("", "blank"),
        ("Complies", "complies"),
        ("<0.04", "numeric"),
    ],
)
def test_distinct_result_semantics(raw, status):
    assert normalize_result(raw, "", "numeric").status == status


def test_comparator_unit_and_precision():
    a = normalize_result("0.020", "%", "numeric")
    assert a.raw_text == "0.020" and str(a.value) == "0.020"
    assert a.equivalent(normalize_result("0.02", "%", "numeric"))
    assert not a.equivalent(normalize_result("<0.020", "%", "numeric"))
    assert not a.equivalent(normalize_result("0.020", "mg/ml", "numeric"))
    with pytest.raises(NeedsReview):
        normalize_result("NaN", "", "numeric")
