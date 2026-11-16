"""Extract reviewable stability plan matrices from Word, with source coordinates."""
import re
from io import BytesIO

from docx import Document
from docx.table import Table

TIME = re.compile(r"^\s*(\d+)\s*(月|天|[MD]\b|(?:st|nd|rd|th)?\s*(?:month|day))", re.I)
HUMIDITY = re.compile(r"(?<!\d)(\d+(?:\.\d+)?)\s*%\s*RH", re.I)
HUMIDITY_CONFLICT = "条件表与正文的相对湿度写法不一致，请选择依据。"
QUANTITY_LABEL = re.compile(r"^(?:取样量|检测量|检验量|(?:Sampling|Testing) Quantity\b|Test Samples\s*\()", re.I)
METHOD_HEADER = re.compile(r"检验方法|检测方法|\btest\s+method\b", re.I)
ITEM_HEADER = re.compile(r"检测项目|检验项目|考察项目|\b(?:test|study)\s+items\b", re.I)
METHOD_IN_LABEL = re.compile(r"\(\s*([^()\n]{1,100}?)\s+method\s*\)", re.I)
VISUAL_METHOD_IN_LABEL = re.compile(r"\(\s*(visual\s+inspection)\s*\)", re.I)
KINDS = {
    "long_term": re.compile(r"长期|long[- ]term", re.I),
    "accelerated": re.compile(r"加速|accelerated", re.I),
    "stress": re.compile(r"强力破坏|stress", re.I),
}


def _time(text):
    match = TIME.match(text)
    if not match:
        return None
    marker = match[2].lower()
    return int(match[1]), "D" if marker in {"天", "d"} or "day" in marker else "M"


def _kinds(text):
    return [name for name, pattern in KINDS.items() if pattern.search(text)]


def _state(text):
    normalized = text.strip().casefold()
    if normalized in {"√", "✓", "✔"}:
        return "required"
    if normalized in {"n/a", "na", "不适用", "不检测"}:
        return "not_required"
    if "引用放行" in normalized or re.search(r"refer to (?:the )?release", normalized):
        return "release_reference"
    return "unclear"


def _cell_text(cell):
    return "\n".join("".join(paragraph.xpath(".//w:t/text()"))
                     for paragraph in cell._tc.xpath("./w:p")).strip()


def _item_label(values):
    return " / ".join(dict.fromkeys(value for value in values if value))


def _label_key(label):
    return re.sub(r"\s+", "", label).casefold()


def _method_rows(rows, table):
    header = rows[0]
    for column, title in enumerate(header):
        if column and METHOD_HEADER.search(title) and any(ITEM_HEADER.search(value) for value in header[:column]):
            return [{"table": table, "row": index, "column": column + 1, "label": _item_label(row[:column]),
                     "raw": row[column]}
                    for index, row in enumerate(rows[1:], 2)
                    if len(row) > column and row[column] and any(row[:column])]
    return []


def _matrix(rows, table, study, caption):
    for head_index, header in enumerate(rows[:3]):
        columns = [(index, time) for index, value in enumerate(header) if (time := _time(value))]
        if len(columns) >= 2 and re.search(r"考察项目|study items|test items", " ".join(header[:columns[0][0]]), re.I):
            break
    else:
        return None
    first = columns[0][0]
    points = [{"column": column + 1, "amount": amount, "unit": unit, "raw": header[column]}
              for column, (amount, unit) in columns]
    warnings = []
    if len(set(time for _, time in columns)) != len(columns):
        warnings.append("原表有重复时间点，请按原件核对。")
    items = []
    for row_index, row in enumerate(rows[head_index + 1:], head_index + 2):
        label = _item_label(row[:first])
        cells = [{"column": column + 1, "raw": row[column] if column < len(row) else "",
                  "state": _state(row[column] if column < len(row) else "")} for column, _ in columns]
        if not label and not any(cell["raw"] for cell in cells):
            continue
        if QUANTITY_LABEL.match(label) or re.match(r"^(合计|总计|总量|备用|Total\b|Backup\b)", label, re.I):
            continue
        items.append({"row": row_index, "label": label, "cells": cells})
    if any(not item["label"] or any(cell["state"] == "unclear" for cell in item["cells"]) for item in items):
        warnings.append("部分检项或标记无法确定，请核对原文；空白不自动视为不检。")
    return {"table": table, "header_row": head_index + 1, "study": study, "caption": caption,
            "points": points, "items": items, "warnings": warnings}


def inspect_protocol(data):
    from .specification_preview import standard_numbers
    document = Document(BytesIO(data))
    batches, conditions, matrices, references = [], [], [], []
    method_rows = []
    paragraphs = []
    table = 0
    for element in document.iter_inner_content():
        if not isinstance(element, Table):
            if text := element.text.strip():
                paragraphs.append(text)
            continue
        table += 1
        rows = [[_cell_text(cell) for cell in row.cells] for row in element.rows]
        if not rows:
            continue
        header = rows[0]
        method_rows.extend(_method_rows(rows, table))
        for index, text in enumerate(header):
            if re.search(r"批号|batch\s*no", text, re.I) and not any("储存条件" in h for h in header):
                batches.extend({"table": table, "row": r, "raw": row[index],
                                "batch": "" if re.search(r"\bTBD\b|待定", row[index], re.I) else row[index]}
                               for r, row in enumerate(rows[1:], 2) if len(row) > index and row[index])
        if len(header) >= 2 and re.search(r"考察条件|study conditions|conditions and inspection", header[1], re.I):
            for row_index, row in enumerate(rows[1:], 2):
                if len(row) >= 2 and len(kinds := _kinds(row[0])) == 1:
                    tracked = bool(element.rows[row_index - 1]._tr.xpath(".//w:ins|.//w:del"))
                    conditions.append({"study": kinds[0], "text": row[1], "location": f"表 {table} 行 {row_index}",
                                       "tracked_changes": tracked})
        caption = paragraphs[-1] if paragraphs else ""
        kinds = _kinds(caption)
        if matrix := _matrix(rows, table, kinds[0] if len(kinds) == 1 else None, caption):
            if element._tbl.xpath(".//w:ins|.//w:del"):
                matrix["warnings"].append("原表有修订痕迹，预览显示含插入文字的版本，请核对原件。")
            matrices.append(matrix)
    study = None
    for index, text in enumerate(paragraphs, 1):
        if len(kinds := _kinds(text)) == 1:
            study = kinds[0]
        if study and re.match(r"^(?:[\d.]+\s*)?(考察条件|Storage condition)\s*[:：]", text, re.I):
            conditions.append({"study": study, "text": text, "location": f"正文段落 {index}"})
        if re.search(r"引用放行|refer.*release", text, re.I):
            references.append({"paragraph": index, "text": text})
    for matrix in matrices:
        for item in matrix["items"]:
            evidence = [row for row in method_rows if _label_key(row["label"]) == _label_key(item["label"])]
            if evidence:
                item["method_evidence"] = evidence
            methods = {" ".join(match.split()) for pattern in (METHOD_IN_LABEL, VISUAL_METHOD_IN_LABEL)
                       for match in pattern.findall(item["label"])}
            if len(methods) == 1:
                item["method_suggestion"] = next(iter(methods))
        matching = [row for row in conditions if row["study"] == matrix["study"]]
        humidity = {float(value) for row in matching for value in HUMIDITY.findall(row["text"])}
        # The tolerance can repeat with an RH suffix. Compare the first setpoint per source.
        setpoints = {float(values[0]) for row in matching if (values := HUMIDITY.findall(row["text"]))}
        if len(humidity) > 1 and len(setpoints) > 1:
            matrix["warnings"].append(HUMIDITY_CONFLICT)
        matrix["conditions"] = matching
    table_text = "\n".join(_cell_text(cell) for table in document.tables for row in table.rows for cell in row.cells)
    return {"batches": batches, "matrices": matrices, "release_references": references,
            "method_rows": method_rows,
            "standard_numbers": standard_numbers("\n".join([*paragraphs, table_text]))}
