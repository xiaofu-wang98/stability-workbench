"""A bounded Excel numeric-format interpreter; never discards unknown semantics.

Reference: Microsoft, Review guidelines for customizing a number format.
"""

import re
from decimal import Decimal, ROUND_HALF_UP, localcontext


class UnsupportedNumberFormat(ValueError):
    pass


def sections(code):
    parts = [""]
    quoted = False
    escaped = False
    for char in code:
        if escaped:
            parts[-1] += char
            escaped = False
            continue
        if char in {"\\", "_", "*"} and not quoted:
            parts[-1] += char
            escaped = True
            continue
        if char == '"':
            quoted = not quoted
            parts[-1] += char
            continue
        if char == ";" and not quoted:
            parts.append("")
        else:
            parts[-1] += char
    if quoted or escaped or len(parts) > 4:
        raise UnsupportedNumberFormat("格式片段不完整")
    return parts


def tokens(section):
    result = []
    i = 0
    while i < len(section):
        char = section[i]
        if char == '"':
            end = section.find('"', i + 1)
            if end < 0:
                raise UnsupportedNumberFormat("文字格式未闭合")
            result.append(("literal", section[i + 1 : end]))
            i = end + 1
        elif char == "\\":
            if i + 1 >= len(section):
                raise UnsupportedNumberFormat("转义格式未闭合")
            result.append(("literal", section[i + 1]))
            i += 2
        elif char in {"_", "*"}:
            if i + 1 >= len(section):
                raise UnsupportedNumberFormat("排版占位未闭合")
            i += 2  # Width/repetition changes layout only, never the analytical value.
        elif char == "[":
            end = section.find("]", i + 1)
            if end < 0:
                raise UnsupportedNumberFormat("格式标记未闭合")
            bracket = section[i + 1 : end]
            if not re.fullmatch(
                r"(?:Black|Blue|Cyan|Green|Magenta|Red|White|Yellow|Color(?:[1-9]|[1-4][0-9]|5[0-6]))",
                bracket,
                re.I,
            ):
                raise UnsupportedNumberFormat("包含条件或未知的格式段")
            i = end + 1
        else:
            result.append(("code", char))
            i += 1
    return result


def render(number, code):
    """Return display and factor while keeping the unrounded analytical value."""
    parts = sections(code)
    for part in parts[:3]:
        tokens(part)  # Conditions in another sign section still affect selection.
    negative = number < 0
    index = 1 if negative and len(parts) >= 2 else 2 if number == 0 and len(parts) >= 3 else 0
    section = parts[index]
    if section.casefold() == "general":
        return str(number), Decimal(1), "plain"
    ts = tokens(section)
    semantic = "".join(v for kind, v in ts if kind == "code")
    if "/" in semantic:
        raise UnsupportedNumberFormat("分数格式需要明确解释")
    if re.search(r"[,]+(?:[.%]*$|[Ee])", semantic):
        raise UnsupportedNumberFormat("缩放逗号可能改变显示量级")
    placeholders = [i for i, (kind, v) in enumerate(ts) if kind == "code" and v in "0#?"]
    if not placeholders:
        if number == 0 and all(kind == "literal" or v.isspace() for kind, v in ts):
            return "".join(v for _, v in ts), Decimal(1), "zero_literal"
        raise UnsupportedNumberFormat("数值被隐藏或没有明确的数字格式")
    first, last = placeholders[0], placeholders[-1]
    core = ts[first : last + 1]
    if any(kind != "code" for kind, _ in core):
        raise UnsupportedNumberFormat("数字内部存在未知文字分隔")
    skeleton = "".join(v for _, v in core)
    percent = sum(kind == "code" and v == "%" for kind, v in ts)
    if percent > 1:
        raise UnsupportedNumberFormat("多重百分比缩放需要确认")
    factor = Decimal(100) if percent else Decimal(1)
    if any(kind == "code" and v not in "0#?.,Ee+-()% " for kind, v in ts):
        raise UnsupportedNumberFormat("包含未支持的数字语义")
    if any(kind == "code" and v == "," for kind, v in ts[last + 1 :]):
        raise UnsupportedNumberFormat("缩放逗号可能改变显示量级")
    value = abs(number) * factor
    scientific = re.fullmatch(r"0(?:\.([0#?]+))?([Ee])([+-])(0+)", skeleton)
    if scientific:
        decimals = len(scientific[1] or "")
        with localcontext() as context:
            context.rounding = ROUND_HALF_UP
            shown = format(value, f".{decimals}E")
        mantissa, exp = shown.split("E")
        exponent = int(exp)
        sign = "-" if exponent < 0 else "+" if scientific[3] == "+" else ""
        shown = mantissa + scientific[2] + sign + str(abs(exponent)).zfill(len(scientific[4]))
    else:
        plain = re.fullmatch(r"([0#?]+(?:,[0#?]+)*)(?:\.([0#?]+))?", skeleton)
        if not plain:
            raise UnsupportedNumberFormat("数字占位结构需要明确解释")
        integer, decimal = plain[1], plain[2] or ""
        if "," in integer and not re.fullmatch(r"[#0?]{1,3}(?:,[#0?]{3})+", integer):
            raise UnsupportedNumberFormat("逗号分组或缩放含义不明确")
        places = len(decimal)
        with localcontext() as context:
            context.prec = max(28, len(value.as_tuple().digits) + abs(value.adjusted()) + places + 10)
            rounded = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
        shown = format(rounded, ("," if "," in integer else "") + f".{places}f")
        optional = len(decimal) - len(decimal.rstrip("#?"))
        while optional and shown.endswith("0"):
            shown = shown[:-1]
            optional -= 1
        if shown.endswith("."):
            shown = shown[:-1]
    prefix = "".join(v for _, v in ts[:first])
    suffix = "".join(v for _, v in ts[last + 1 :])
    if index == 0 and negative:
        prefix = "-" + prefix
    return (
        prefix + shown + suffix,
        factor,
        "numeric_percent"
        if percent
        else "literal_percent"
        if any(kind == "literal" and "%" in v for kind, v in ts)
        else "plain",
    )
