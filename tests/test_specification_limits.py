import pytest

from stability_hub.normalize import normalize_worksheet_result
from stability_hub.specification_limits import assess_result


@pytest.mark.parametrize(("criterion", "raw", "status"), [
    ("＞95.0%", "95.0%", "outside"),
    ("≥95.0%", "95.0%", "within"),
    ("5.20～5.80", "5.10%", "outside"),
    ("≤3.5 mg/mL", "3.0%", "undetermined"),
    ("≤3.5%", "<3.0%", "undetermined"),
    ("≤3.5%", "ND", "undetermined"),
])
def test_numeric_standard_boundaries(criterion, raw, status):
    result = normalize_worksheet_result(raw, "%", "numeric")
    assert assess_result(result, [(criterion, "v1", True)])["status"] == status
