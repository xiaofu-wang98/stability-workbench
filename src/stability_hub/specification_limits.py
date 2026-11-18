"""Evaluate an unambiguous numerical criterion against the recorded result."""

import re
from decimal import Decimal
from unicodedata import normalize


NUMBER = r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)"


def numeric_bounds(criterion, unit, *, implicit_unit=False):
    text = normalize("NFKC", criterion).strip()
    unit = normalize("NFKC", unit).strip()
    suffix = rf"\s*({re.escape(unit)})?" if unit else "()"
    comparison = re.fullmatch(rf"(>=|<=|≥|≤|>|<)\s*({NUMBER}){suffix}", text)
    if comparison:
        operator, value, reported_unit = comparison.groups()
        if unit and not reported_unit and not implicit_unit:
            return None
        bound = (Decimal(value), operator in {">=", "<=", "≥", "≤"})
        return (bound, None) if operator in {">", ">=", "≥"} else (None, bound)
    interval = re.fullmatch(rf"({NUMBER}){suffix}\s*(?:~|–|—|-|至)\s*({NUMBER}){suffix}", text)
    if interval:
        lower, left_unit, upper, right_unit = interval.groups()
        if unit and not (left_unit or right_unit or implicit_unit):
            return None
        a, b = Decimal(lower), Decimal(upper)
        if a <= b:
            return (a, True), (b, True)
    return None


def assess_result(result, standards):
    if not standards:
        return None
    if result.status != "numeric" or result.comparator != "eq":
        return {"status": "undetermined"}
    parsed = {}
    for criterion, version, implicit_unit in standards:
        bounds = numeric_bounds(criterion, result.unit, implicit_unit=implicit_unit)
        if bounds is None:
            return {"status": "undetermined", "criterion": criterion, "version": version}
        parsed.setdefault(bounds, (criterion, version))
    if len(parsed) != 1:
        return {"status": "undetermined"}
    (lower, upper), (criterion, version) = next(iter(parsed.items()))
    value = result.value
    below = lower is not None and (value < lower[0] or (value == lower[0] and not lower[1]))
    above = upper is not None and (value > upper[0] or (value == upper[0] and not upper[1]))
    return {"status": "outside" if below or above else "within", "criterion": criterion, "version": version}


def worksheet_assessment(record, row):
    if row:
        if row["unit"] != record.result.unit:
            return {"status": "undetermined"}
        standards = [(entry["criterion"], entry.get("document_number") or entry["specification_id"], True)
                     for entry in row.get("standards", []) if entry.get("criterion")]
        if row.get("criteria_text"):
            standards.append((row["criteria_text"], "", True))
        if standards:
            return assess_result(record.result, standards)
    return assess_result(record.result, [
        (entry.criterion_text, entry.version, False) for entry in record.standards
    ])
