import re
from decimal import Decimal

from .models import Result


class NeedsReview(ValueError):
    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(detail)


NUMBER = re.compile(r"^(<=|>=|<|>|≤|≥|＜|＞)?\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)$")
COMPARATORS = {
    None: "eq",
    "<": "lt",
    "＜": "lt",
    "<=": "le",
    "≤": "le",
    ">": "gt",
    "＞": "gt",
    ">=": "ge",
    "≥": "ge",
}


def is_not_reported(text: str) -> bool:
    return bool(re.search(r'不(?:进行|做|予|再)?(?:汇报|报告)(?:检测)?(?:结果|数据)?|\bnot\s+(?:be\s+)?reported\b', text, re.I))


def normalize_result(raw: str, unit: str, result_type: str) -> Result:
    text = raw.strip()
    token = text.casefold()
    if is_not_reported(text):
        return Result(status='not_reported', raw_text=raw, unit=unit,
                      report_references=tuple(dict.fromkeys(re.findall(r'\bAR-[A-Z0-9]+(?:-[A-Z0-9]+)+', text, re.I))))
    states = {
        "": "blank",
        "n/a": "na",
        "na": "na",
        "nd": "nd",
        "n.d.": "nd",
        "---": "placeholder",
        "/": "placeholder",
        "\\": "placeholder",
        "complies": "complies",
        "符合规定": "complies",
    }
    if token in states:
        return Result(status=states[token], raw_text=raw, unit=unit)
    match = NUMBER.fullmatch(text)
    if match:
        return Result(
            status="numeric",
            raw_text=raw,
            unit=unit,
            comparator=COMPARATORS[match[1]],
            value=Decimal(match[2]),
        )
    if result_type == "qualitative":
        return Result(status="text", raw_text=raw, unit=unit)
    raise NeedsReview("unrecognized_result", f"Cannot parse numeric result: {raw!r}")


def normalize_month(raw: str) -> int:
    match = re.fullmatch(r"(\d+)\s*(?:m|months?|个月|月)?", raw.strip(), re.I)
    if not match:
        raise NeedsReview("unknown_time_point", f"Expected integer months, received {raw!r}")
    return int(match[1])
