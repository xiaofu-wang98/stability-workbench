"""Standard report generation only. Existing user reports are never overwritten."""

from collections import defaultdict
from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.shared import Inches, Pt
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

GROUP_FIELDS = ("project", "sample_type", "batch", "presentation", "study", "study_id", "condition")
GROUP_LABELS = ("Project", "Sample type", "Batch", "Presentation", "Study", "Study ID", "Condition")
ROW_FIELDS = ("item", "method", "method_version")
RECORD_HEADERS = [
    "Project",
    "Sample type",
    "Batch",
    "Presentation",
    "Study",
    "Study ID",
    "Condition",
    "Month",
    "Item",
    "Method",
    "Method version",
    "Result",
    "Status",
    "Value",
    "Comparator",
    "Unit",
    "Reference",
]


def display(result: dict) -> str:
    if result["status"] == "numeric":
        prefix = {"eq": "", "lt": "<", "le": "<=", "gt": ">", "ge": ">="}[result["comparator"]]
        return prefix + result["value"]
    return {"na": "N/A", "nd": "ND", "complies": "Complies"}.get(result["status"], result["raw_text"])


def groups(snapshot: list[dict]):
    buckets: dict[tuple, list] = defaultdict(list)
    for entry in snapshot:
        key = entry["record"]["key"]
        buckets[tuple(key[f] for f in GROUP_FIELDS)].append(entry)
    for group_key in sorted(buckets):
        entries = buckets[group_key]
        periods = sorted({e["record"]["key"]["month"] for e in entries}, key=lambda x: -1 if x is None else x)
        # Small, readable tables. Long studies continue in additional tables/sheets.
        for start in range(0, len(periods), 4):
            times = periods[start : start + 4]
            row_keys = sorted(
                {
                    tuple(e["record"]["key"][f] for f in ROW_FIELDS) + (e["record"]["result"]["unit"],)
                    for e in entries
                }
            )
            header = ["Item", "Method", "Version", "Unit"] + [
                "Release" if t is None else f"{t}M" for t in times
            ]
            lookup = {
                (tuple(e["record"]["key"][f] for f in ROW_FIELDS), e["record"]["key"]["month"]): display(
                    e["record"]["result"]
                )
                for e in entries
            }
            rows = [list(row_key) + [lookup.get((row_key[:3], t), "") for t in times] for row_key in row_keys]
            yield group_key, header, rows


def _put_text(cell, value):
    # Prevent leading = in source labels/qualitative results becoming Excel formulas.
    cell.value = str(value)
    cell.data_type = "s"


def _style(sheet):
    sheet.freeze_panes = "E10" if sheet.title.startswith("Study ") else "A2"
    for row in sheet:
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            cell.font = Font(name="Calibri", size=11)
    for cell in sheet[9 if sheet.title.startswith("Study ") else 1]:
        cell.fill = PatternFill("solid", fgColor="163C4D")
        cell.font = Font(name="Calibri", size=11, color="FFFFFF", bold=True)
    for col in sheet.columns:
        sheet.column_dimensions[col[0].column_letter].width = 24
    sheet.sheet_view.showGridLines = False


def write_reports(snapshot: list[dict], output: Path, project: str, snapshot_id: str) -> None:
    book = Workbook()
    records = book.active
    records.title = "Records"
    records.append(RECORD_HEADERS)
    for index, entry in enumerate(snapshot, 2):
        key, result = entry["record"]["key"], entry["record"]["result"]
        values = [key[f] for f in GROUP_FIELDS] + ["" if key["month"] is None else str(key["month"])]
        values += [key[f] for f in ROW_FIELDS]
        values += [
            display(result),
            result["status"],
            result["value"] or "",
            result["comparator"] or "",
            result["unit"],
            entry["observation_id"],
        ]
        for ci, value in enumerate(values, 1):
            _put_text(records.cell(index, ci), value)
    records.auto_filter.ref = records.dimensions
    _style(records)
    doc = Document()
    section = doc.sections[0]
    section.orientation = WD_ORIENT.LANDSCAPE
    section.page_width, section.page_height = Inches(11.7), Inches(8.3)
    section.left_margin = section.right_margin = Inches(0.6)
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(10)
    doc.add_heading("Stability Data Hub", 0)
    doc.add_paragraph(f"Project: {project}")
    doc.add_paragraph(f"Data revision: {snapshot_id[:12]}")
    doc.add_paragraph(
        "Blank cells mean no accepted result was supplied. Release results are not automatically 0M baselines."
    )
    for gi, (group_key, headers, rows) in enumerate(groups(snapshot), 1):
        if gi > 1:
            doc.add_page_break()
        doc.add_heading(f"{group_key[2]} — {group_key[4]}", 1)
        meta = doc.add_table(rows=0, cols=2)
        meta.style = "Table Grid"
        sheet = book.create_sheet(f"Study {gi}")
        for ri, (label, value) in enumerate(zip(GROUP_LABELS, group_key, strict=True), 1):
            cells = meta.add_row().cells
            cells[0].text, cells[1].text = label, value
            _put_text(sheet.cell(ri, 1), label)
            _put_text(sheet.cell(ri, 2), value)
        table = doc.add_table(rows=1, cols=len(headers))
        table.style = "Table Grid"
        for ci, label in enumerate(headers):
            table.rows[0].cells[ci].text = label
        for row in rows:
            for cell, value in zip(table.add_row().cells, row, strict=True):
                cell.text = value
        for ri, row in enumerate([headers, *rows], 9):
            for ci, value in enumerate(row, 1):
                _put_text(sheet.cell(ri, ci), value)
        _style(sheet)
    sources = book.create_sheet("Sources")
    sources.append(["Reference", "Source file", "SHA-256", "Location"])
    ri = 2
    for entry in snapshot:
        for source in entry["sources"]:
            loc = source["raw_json"]["location"]
            vals = [
                entry["observation_id"],
                source["filename"],
                source["document_hash"],
                f"{loc['section']}; row {loc['row']}, column {loc['column']}",
            ]
            for ci, value in enumerate(vals, 1):
                _put_text(sources.cell(ri, ci), value)
            ri += 1
    _style(sources)
    book.save(output / "stability.xlsx")
    book.close()
    doc.save(output / "stability.docx")
