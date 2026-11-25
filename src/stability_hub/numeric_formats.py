"""Small explicit Excel format subset, not an Excel display engine."""

import re
from decimal import Decimal

from openpyxl.styles.numbers import is_date_format

from .normalize import NUMBER, NeedsReview, normalize_result as _normalize_result, SEMANTIC_VERSION


def normalize_for_source(raw, text, unit, result_type):
    return _normalize_result(text, unit, result_type,
        legacy_reporting=raw.source_value.get('semantic_rule_version') != SEMANTIC_VERSION)

VERSION = "excel-number-2"
LEGACY_VERSION = "excel-number-1"
PERCENT = re.compile(r"0(?:\.0+)?%")
PLAIN = re.compile(r"(?:General|0|0\.0+|0\.0+[Ee][+-]0+)")
PURE_DECIMAL = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?$")
INLINE_UNIT_NUMBER = re.compile(
    r"^(<=|>=|<|>|≤|≥|＜|＞)?\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*"
    r"([A-Za-zμµ℃\u4e00-\u9fff][^\n()]*?)$"
)
_KNOWN_UNIT_PATTERN = re.compile(
    r"^(?:(?:m|u|μ|µ|n|k|c)?(?:g|l|ml|mL|L|mol|osmol|osm|pa|mpa|s|m|mm|nm|da|kda|iu|eu|u|n|cfu|pfu|vp)"
    r"(?:\s*/\s*(?:\d+\s*)?(?:m|u|μ|µ|n|k)?(?:g|kg|l|ml|mL|L|mg|瓶|支|剂|容器|vial|dose|container))?"
    r"|(?:个|粒|颗)\s*/\s*(?:瓶|支|剂|容器|ml|mL|10\s*ml|10\s*mL)|℃|°c)$",
    re.I,
)


def _looks_like_physical_unit(token: str) -> bool:
    from .platform_catalog import load_seed_catalog

    cleaned = re.sub(r"\s+", " ", token).replace("µ", "μ").strip()
    if not cleaned:
        return False
    unit_map = load_seed_catalog().get("unit_canonical_map", {})
    lowered = cleaned.casefold()
    if cleaned in unit_map or any(k.casefold() == lowered or v.casefold() == lowered for k, v in unit_map.items()):
        return True
    return bool(_KNOWN_UNIT_PATTERN.fullmatch(cleaned))


def _try_inline_unit_numeric(raw, text, stripped, unit, result_type, basis):
    match = INLINE_UNIT_NUMBER.fullmatch(stripped)
    if not match or not unit or not unit.strip():
        return None
    comp = match.group(1) or ""
    num = match.group(2)
    inline_unit = match.group(3).strip()
    if not inline_unit or inline_unit in {"%", "％"}:
        return None
    from .platform_catalog import units_equivalent

    if units_equivalent(inline_unit, unit):
        result = normalize_for_source(raw, f"{comp}{num}", unit, result_type)
        return result.model_copy(
            update={
                "raw_text": text,
                "interpretation": {**basis, "rule": "inline_unit", "inline_unit": inline_unit},
            }
        )
    if _looks_like_physical_unit(inline_unit):
        raise NeedsReview(
            "unit_conflict",
            f"结果内联单位 {inline_unit!r} 与标准单位 {unit!r} 不一致，需人工核对或换算。",
        )
    return None


def source_value(cell):
    kind = (
        "blank"
        if cell.value is None
        else "date"
        if cell.is_date
        else "number"
        if cell.data_type == "n" and not isinstance(cell.value, bool)
        else cell.data_type
    )
    return {
        "raw_value": "" if cell.value is None else str(cell.value),
        "raw_type": kind,
        "number_format": cell.number_format,
        "rule_version": VERSION,
    }


def legacy_interpret_result(raw, text, unit, result_type):
    fmt = raw.number_format or "General"
    kind = raw.source_value.get("raw_type", "")
    stripped = text.strip()
    basis = {
        "version": LEGACY_VERSION,
        "raw_value": raw.fields.get("result", ""),
        "number_format": fmt,
        "rule": "literal",
    }
    # A text percent is already in percentage points; never multiply it by 100.
    if stripped.endswith(("%", "％")) and NUMBER.fullmatch(stripped[:-1].strip()):
        if unit not in {"", "%", "％"}:
            raise NeedsReview("numeric_format", "百分号文字与单位冲突，请明确填写解释值及单位。")
        result = normalize_for_source(raw, stripped[:-1].strip(), "%", result_type)
        return result.model_copy(
            update={"raw_text": text, "interpretation": {**basis, "rule": "text_percent"}}
        )
    if kind == "date" or is_date_format(fmt) and PURE_DECIMAL.fullmatch(stripped):
        raise NeedsReview("numeric_format", "日期格式不能直接作为检测数值，请核对来源后明确解释。")
    if PURE_DECIMAL.fullmatch(stripped) and kind not in {"s", "inlineStr"}:
        if PERCENT.fullmatch(fmt):
            if unit not in {"", "%", "％"}:
                raise NeedsReview("numeric_format", "百分比格式与检测单位冲突，请明确解释值及单位。")
            value = Decimal(stripped) * 100
            decimals = len(fmt.split(".")[1].rstrip("%")) if "." in fmt else 0
            shown = f"{value:.{decimals}f}%"
            # Stored analytical value retains precision; display rounding never edits it.
            result = normalize_for_source(raw, str(value), "%", result_type)
            return result.model_copy(
                update={
                    "raw_text": text,
                    "interpretation": {
                        **basis,
                        "rule": "numeric_percent",
                        "factor": "100",
                        "display_text": shown,
                    },
                }
            )
        if not PLAIN.fullmatch(fmt):
            raise NeedsReview(
                "numeric_format", "未支持的Excel数字格式（缩放/自定义），请明确解释值及单位：" + fmt
            )
    result = normalize_for_source(raw, text, unit, result_type)
    return result.model_copy(update={"interpretation": basis})


def interpret_result(raw, text, unit, result_type):
    if raw.source_value.get("rule_version") == LEGACY_VERSION:
        return legacy_interpret_result(raw, text, unit, result_type)
    fmt = raw.number_format or "General"
    kind = raw.source_value.get("raw_type", "")
    stripped = text.strip()
    basis = {
        "version": VERSION,
        "raw_value": raw.fields.get("result", ""),
        "number_format": fmt,
        "rule": "literal",
    }
    if stripped.endswith(("%", "％")) and NUMBER.fullmatch(stripped[:-1].strip()):
        if unit not in {"", "%", "％"}:
            raise NeedsReview("numeric_format", "百分号文字与单位冲突，请明确解释值及单位。")
        result = normalize_for_source(raw, stripped[:-1].strip(), "%", result_type)
        return result.model_copy(
            update={"raw_text": text, "interpretation": {**basis, "rule": "text_percent"}}
        )
    if kind == "date" or is_date_format(fmt) and PURE_DECIMAL.fullmatch(stripped):
        raise NeedsReview("numeric_format", f"Excel按日期显示；系统读取 {stripped}，需确认这是否为检测数值。")
    if PURE_DECIMAL.fullmatch(stripped) and kind not in {"s", "inlineStr"}:
        from .excel_formats import render, UnsupportedNumberFormat

        try:
            shown, factor, rule = render(Decimal(stripped), fmt)
        except UnsupportedNumberFormat as exc:
            raise NeedsReview(
                "numeric_format", f"系统读取 {stripped}；需确认Excel显示的缩放/格式含义：{exc}。"
            ) from exc
        if factor == 100 and unit not in {"", "%", "％"}:
            raise NeedsReview(
                "numeric_format", f"Excel显示 {shown}，系统读取 {stripped}；百分比与检测单位冲突。"
            )
        result = normalize_for_source(
            raw, str(Decimal(stripped) * factor), "%" if factor == 100 else unit, result_type
        )
        return result.model_copy(
            update={
                "raw_text": text,
                "interpretation": {**basis, "rule": rule, "display_text": shown, "factor": str(factor)},
            }
        )
    inline = _try_inline_unit_numeric(raw, text, stripped, unit, result_type, basis)
    if inline is not None:
        return inline
    result = normalize_for_source(raw, text, unit, result_type)
    return result.model_copy(update={"interpretation": basis})
