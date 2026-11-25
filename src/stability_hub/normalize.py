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


SEMANTIC_VERSION = 'v020-result-1'
NOT_REPORTED = re.compile(r'不(?:进行|做|予|再)?(?:汇报|报告)(?:检测)?(?:结果|数据)?|\bnot\s+(?:be\s+)?reported\b', re.I)


def ambiguous_reporting(text: str) -> bool:
    """Historical/negated reporting prose cannot decide the current result."""
    return bool(NOT_REPORTED.search(text) and re.search(
        r'原(?:来|先|结果|数据)|曾经|此前|之前|现(?:在|报告|汇报|结果)|改为|恢复|不是|并非|而非|'
        r'\b(?:previously|previous|formerly|historical|now|final|instead|resume[ds]?)\b', text, re.I))


def is_not_reported(text: str) -> bool:
    return bool(NOT_REPORTED.search(text)) and not ambiguous_reporting(text)


def normalize_result(raw: str, unit: str, result_type: str, *, legacy_reporting=False) -> Result:
    text = raw.strip()
    token = text.casefold()
    if not legacy_reporting and ambiguous_reporting(text):
        raise NeedsReview('reporting_semantics', '原文同时包含历史、否定或当前汇报说明，请明确本次结果；原文完整保留。')
    if (bool(NOT_REPORTED.search(text)) if legacy_reporting else is_not_reported(text)):
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


def normalize_worksheet_result(raw: str, unit: str, result_type: str) -> Result:
    """Preserve the typed expression while interpreting a planned cell's unit."""
    text = raw.strip()
    if unit == "%" and text.endswith("%"):
        text = text[:-1].rstrip()
    if text.casefold() in {"na*", "n/a*"}:
        text = "N/A"
    return normalize_result(text, unit, result_type).model_copy(update={"raw_text": raw})


def normalize_month(raw: str) -> int:
    match = re.fullmatch(r"(\d+)\s*(?:m|months?|个月|月)?", raw.strip(), re.I)
    if not match:
        raise NeedsReview(
            "unknown_time_point",
            f"未识别到稳定性时间点（收到 {raw!r}），请核对并补录时间点（如 0M / 3M / 14D）或排除非稳定性样品。",
        )
    return int(match[1])
