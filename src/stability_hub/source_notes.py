"""Recognize complete writing instructions, retaining any additional factual text."""

import re


SUMMARY_INSTRUCTIONS = (
    "稳定性总结：（对所有检项完成的结果进行总结及评估，对检测结果进行趋势分析，是否有超标风险。）",
    "Stability summary: (summarize and evaluate the results of all test items, conduct trend analysis on the test results to determine if there is a risk of OOS.)",
)
INSTRUCTION_PATTERNS = tuple(
    re.compile(r"\s*".join(re.escape(c) for c in text if not c.isspace()), re.I)
    for text in SUMMARY_INSTRUCTIONS
)


def review_text(text):
    """Only the complete known instructional sentence is removed, never a keyword."""
    value = str(text or "")
    for pattern in INSTRUCTION_PATTERNS:
        value = pattern.sub("", value)
    # These wrappers carry no factual content without their source text.
    value = re.sub(r"(?m)^\s*表后说明（未自动修改结果）：\s*$", "", value)
    value = re.sub(r"(?m)^\s*[A-Z]+\d+:\s*$", "", value)
    return value.strip()


def result_lines(text):
    """Collapse only known equivalent bilingual statements, never arbitrary translations."""
    lines = [line.strip() for line in str(text or '').splitlines() if line.strip()]
    if len(lines) != 2:
        return lines
    equivalents = (
        (r'与(?:阶段性)?(?:标准品或参考品|参考品|标准品)图谱一致[。.]?',
         r'(?:The\s+)?profile\s+is\s+consistent\s+with\s+(?:the\s+)?(?:interim\s+)?(?:Reference\s+Standard|reference)[.]?'),
        (r'符合(?:规定|要求)[。.]?', r'(?:Complies|Conforms)(?:\s+(?:with|to)\s+(?:the\s+)?(?:requirements|specification))?[.]?'),
    )
    for cn, en in equivalents:
        if any(re.fullmatch(cn, lines[a]) and re.fullmatch(en, lines[b], re.I)
               for a, b in ((0, 1), (1, 0))):
            return lines[:1]
    return lines


def comment_parts(raw):
    """Separate explicitly addressed notes; unknown scope remains one source question."""
    from openpyxl.utils.cell import coordinate_from_string, column_index_from_string

    text = review_text(raw.comment)
    markers = list(re.finditer(r"(?m)^([A-Z]+\d+):[ \t]*", text))
    if not markers or raw.location.format != "xlsx":
        return [(text, raw.location)] if text else []
    parts = []
    prefix = review_text(text[: markers[0].start()])
    if prefix:
        parts.append((prefix, raw.location))
    for index, marker in enumerate(markers):
        body = text[
            marker.end() : markers[index + 1].start() if index + 1 < len(markers) else len(text)
        ].strip()
        if not body:
            continue
        column, row = coordinate_from_string(marker[1])
        origin = raw.location.model_copy(update={"row": row, "column": column_index_from_string(column)})
        parts.append((marker[1] + ": " + body, origin))
    return parts


def informational_reference(text):
    """User-confirmed: a bare AR identifier is a sheet reminder, not an adoption instruction."""
    body = review_text(text)
    body = re.sub(r"^[A-Z]+\d+:\s*", "", body)
    return bool(re.fullmatch(r"(?:(?:AR|AE)-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+(?:[\s、,，;；]+|$))+", body, re.I))


def explicit_annotation(text):
    """Specific annotation syntax, never simply text/no digits or a broad keyword."""
    value = str(text or "").strip()
    return bool(
        re.fullmatch(r"(?:长期|加速)?\d+\s*M[^\n]{1,120}(?:详见|参见)(?:AR|AE)-[A-Za-z0-9-]+[。.]?",value,re.I)
        or re.match(r"^(?:备注|说明|Note|Remark)\s*[:：]", value, re.I)
        or re.fullmatch(r"(?:绿色|黄色|红色|蓝色)?表示已(?:签批|审核|复核)\s*[。.]?", value)
        or re.fullmatch(r"\d+\s*(?:M|个月|月|D|天)\s*(?:暂停|终止)(?:考察|检测|试验)?\s*[。.]?", value, re.I)
    )
