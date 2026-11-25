"""Build a controlled confirmation workbook from a frozen business worksheet plan and adopted results."""

import hashlib
import json
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .models import StabilityRecord, digest
from .permissions import Capability, require
from .reporting import _put_text, reported_text, source_result_notes
from .stability_summary import (
    controlled_conditions,
    format_location_checkboxes,
    format_study_type_checkboxes,
    visible,
)
from .workflow_common import WorkflowError, encoded, project_row, uid

TEMPLATE_VERSION = "worksheet-confirmation-8"

_NAVY_FILL = PatternFill("solid", fgColor="1E3A5F")
_SUBHEADER_FILL = PatternFill("solid", fgColor="E2ECF5")
_META_LABEL_FILL = PatternFill("solid", fgColor="EEF4FA")
_CAT_FILL = PatternFill("solid", fgColor="F8FAFC")
_SECTION_FILL = PatternFill("solid", fgColor="DCE7F3")
_APPENDIX_FILL = PatternFill("solid", fgColor="F1F5F9")

_THIN = Side(style="thin", color="CBD5E1")
_CAT_DIVIDER = Side(style="medium", color="94A3B8")
_GRID_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_CAT_TOP_BORDER = Border(left=_THIN, right=_THIN, top=_CAT_DIVIDER, bottom=_THIN)

_TITLE_FONT = Font(name="Microsoft YaHei", size=13, bold=True, color="FFFFFF")
_HEADER_FONT = Font(name="Microsoft YaHei", size=10, bold=True, color="FFFFFF")
_SUBHEADER_FONT = Font(name="Microsoft YaHei", size=9, bold=True, color="1E3A5F")
_META_LABEL_FONT = Font(name="Microsoft YaHei", size=9, bold=True, color="0F172A")
_META_VAL_FONT = Font(name="Microsoft YaHei", size=9.5, color="0F172A")
_CAT_FIRST_FONT = Font(name="Microsoft YaHei", size=9.5, bold=True, color="1E3A5F")
_CAT_REPEAT_FONT = Font(name="Microsoft YaHei", size=9, color="64748B")
_BODY_FONT = Font(name="Microsoft YaHei", size=9.5, color="1E293B")
_SECTION_FONT = Font(name="Microsoft YaHei", size=10, bold=True, color="1E3A5F")
_HINT_FONT = Font(name="Microsoft YaHei", size=8.5, color="475569")

_REMARK_PROMPT = (
    "稳定性取样及检测过程中是否出现异常，是否对此次稳定性考察产生影响。"
    "\nWhether there are any abnormalities in the stability sampling and testing process, "
    "and whether they will impact the stability study."
)
_SUMMARY_PROMPT = (
    "稳定性总结：（对所有检项完成的结果进行总结及评估，对检测结果进行趋势分析，是否有超标风险。）"
    "\nStability summary: (summarize and evaluate the results of all test items, "
    "conduct trend analysis on the test results to determine if there is a risk of OOS.)"
)
_APPENDIX_HEADERS = {
    "结果来源备注",
    "0M 放行引用",
    "异常事件明细",
    "质量标准溯源",
}


def _format_item_method(row) -> str:
    label = (row.get("label") or "").strip()
    method = visible(row.get("method") or "").strip()
    version = visible(row.get("method_version") or "").strip()
    method_parts = [p for p in (method, version) if p and p not in label]
    if method_parts:
        return f"{label}\n（{' / '.join(method_parts)}）" if label else " / ".join(method_parts)
    return label


def _format_subitem_unit(row, default_basis: str = "") -> str:
    sub = (row.get("subcategory_name_zh") or "").strip()
    basis = (row.get("reporting_basis") or default_basis or "").strip()
    unit = (row.get("unit") or "").strip()
    parts: list[str] = []
    for part in (sub, basis, unit):
        if part and part not in parts:
            parts.append(part)
    return " / ".join(parts)


def _format_criteria(row) -> str:
    criteria = list(
        dict.fromkeys(
            filter(
                None,
                [
                    (row.get("criteria_text") or "").strip(),
                    *((s.get("criterion") or "").strip() for s in row.get("standards", [])),
                ],
            )
        )
    )
    if not criteria:
        criteria = list(dict.fromkeys(c.strip() for c in row.get("source_criteria", []) if c and c.strip()))
    return "\n".join(criteria)


def workbook_rows(snapshot):
    sheet = snapshot["worksheet"]
    identity = sheet["identity"]
    points = sheet["points"]
    entries = {(cell["row_id"], cell["point_id"]): cell for cell in sheet["cells"]}
    notes = []

    project = sheet["project"]
    product_name = sheet.get("product_name") or snapshot.get("project_name") or project
    summary_no = sheet.get("summary_number") or f"STB(C)-{project}"
    version_no = str(sheet.get("document_version") or "00")
    spec_text = visible(sheet.get("specification_text") or identity.get("presentation", ""))
    protocol_no = visible(sheet.get("protocol_number") or identity.get("study_id", ""))
    basis_text = sheet.get("basis") or sheet.get("name", "")

    # Rows 1-6: Controlled title & bilingual metadata (keeps Row 7-8 as matrix header, E9 as first data cell)
    yield (f"{project} 稳定性试验数据确认表 / Stability Study Data Verification Form",)
    yield (
        "汇总表编号\nSummary Table No.",
        summary_no,
        "版本号\nVersion",
        version_no,
        "计划版本\nPlan Version",
        str(sheet["plan_version"]),
        "数据快照\nData Snapshot",
        snapshot["snapshot_id"],
    )
    yield (
        "项目编号\nProject No.",
        project,
        "产品名称\nProduct Name",
        product_name,
        "产品批号\nProduct Batch No.",
        identity.get("batch", ""),
        "规格\nStrength / Specification",
        spec_text,
    )
    yield (
        "开始日期\nStarting Date",
        sheet.get("start_date", ""),
        "稳定性开展地点\nStability Location",
        format_location_checkboxes(sheet.get("location", "")),
        "稳定性试验方案编号\nStability Study Protocol No.",
        protocol_no,
        "稳定性类型\nStability Type",
        format_study_type_checkboxes(identity.get("study", "")),
    )
    yield (
        "考察条件\nStudy Condition",
        controlled_conditions(identity.get("condition", ""), identity.get("orientation", "")),
    )
    yield (
        "编制依据 / 阶段说明\nBasis / Stage",
        basis_text,
        "温控设备编号\nEquipment No.",
        sheet.get("equipment", ""),
        "存放位置\nStorage Location",
        sheet.get("storage_location", ""),
        "材料类别\nSample Type",
        identity.get("sample_type", ""),
    )

    # Row 7-8: Matrix headers & planned dates (first timepoint column = E, first data row = Row 9 -> E9)
    yield (
        "大类\nCategory",
        "检验项目（方法）\nTest Item (Method)",
        "子检项/单位\nSub-item & Unit",
        "可接受标准\nAcceptance Criteria",
        *(point["label"] for point in points),
    )
    yield ("计划日期 / Planned Date", "", "", "", *(point.get("planned_date", "") for point in points))

    rows_sorted = (
        sorted(
            sheet["rows"],
            key=lambda r: (r.get("category_order", 99), r.get("display_order", 999)),
        )
        if any(r.get("category_code") or r.get("category_name_zh") for r in sheet["rows"])
        else sheet["rows"]
    )
    default_basis = identity.get("reporting_basis", "")
    for row in rows_sorted:
        values = []
        for point in points:
            cell = entries.get((row["id"], point["id"]))
            if cell and cell.get("adopted"):
                version = cell["adopted"]["result_version_id"]
                result = snapshot["results"][version]["result"]
                value = reported_text(result)
                if result["status"] == "numeric" and result["unit"] == "%":
                    value += "%"
                elif result["status"] != "numeric":
                    value = value.strip()
                notes.extend(
                    (row["label"], row["method"], point["label"], origin, note)
                    for origin, note in snapshot.get("result_notes", {}).get(version, ())
                )
            elif cell and (reference := cell.get("release_reference")):
                value = cell["raw"] + "（引用放行）" if reference["current"] else "放行引用待复核"
            elif point["label"] == "0M" and row["id"] in sheet.get("invalidated_release_rows", ()):
                value = "放行结果已作废，待重新关联"
            elif point["label"] == "0M" and row["id"] in sheet.get("pending_release_references", {}):
                value = "方案要求引用放行结果，待关联"
            elif cell:
                value = "待采用"
            elif any(
                change["row_id"] == row["id"] and point["id"] in change["point_ids"]
                for change in sheet.get("stopped_checks", [])
            ):
                value = "已停检"
            elif row["id"] not in point.get("required_row_ids", ()):
                value = "不要求"
            else:
                value = ""
            values.append(value)
        yield (
            row.get("category_name_zh", ""),
            _format_item_method(row),
            _format_subitem_unit(row, default_basis),
            _format_criteria(row),
            *values,
        )

    # Controlled footer: Remark -> Stability Summary -> 3-level Sign-off -> Change History
    remark_lines: list[str] = []
    for entry in sheet.get("events", []):
        scope = " · ".join(filter(None, (entry.get("item_label"), entry.get("point_label")))) or "整表"
        line = " · ".join(filter(None, (f"[{entry.get('category', '事件')}]", scope, entry.get("reference"), entry.get("description"))))
        if line:
            remark_lines.append(line)
    point_labels = {p["id"]: p["label"] for p in points}
    for point_id, src_notes in sheet.get("source_events", {}).items():
        if point_id in point_labels:
            for src_note in src_notes:
                line = f"[{point_labels[point_id]} 来源记录] {src_note}"
                if line not in remark_lines:
                    remark_lines.append(line)
    for item_lbl, _, pt_lbl, origin, note in notes:
        line = f"[{pt_lbl} {item_lbl}] {origin}：{note}"
        if line not in remark_lines:
            remark_lines.append(line)
    if remark_lines:
        remark_text = "\n".join(remark_lines) + "\n" + _REMARK_PROMPT
    else:
        remark_text = _REMARK_PROMPT

    yield ("备注\nRemark", remark_text)
    yield (_SUMMARY_PROMPT,)
    yield ("起草人/日期\nAuthor/Date", "", "", "", "QC审核人/日期\nQC Reviewer/Date", "", "", "")
    yield ("QC批准人/日期\nQC Approver/Date", "")
    yield ("变更历史 / Change History",)
    yield ("版本号\nVersion", "", "变更内容\nChange")
    yield (
        version_no,
        "",
        f"新起草：{project} 稳定性试验数据确认表 / New document: {project} Stability Study Data Verification Form.",
    )

    # GMP traceability appendix (all 0 formulas, verified by verify_confirmation)
    if notes:
        yield ("结果来源备注", "方法", "时间点", "来源", "原文")
        yield from notes
    if sheet.get("release_references"):
        yield ("0M 放行引用", "放行完成日期", "核对依据", "放行结果版本")
        for row in rows_sorted:
            if reference := sheet["release_references"].get(row["id"]):
                yield (row["label"], reference["release_completed"], reference["basis"], reference["result_version_id"])
    if sheet.get("events"):
        yield ("异常事件明细", "检项／时间点", "事件编号", "说明", "扫描附件 SHA256")
        for entry in sheet["events"]:
            scope = " · ".join(filter(None, (entry["item_label"], entry["point_label"]))) or "整表"
            yield (entry["category"], scope, entry["reference"], entry["description"], entry["scan_id"] or "")
    if any(row.get("standards") for row in sheet["rows"]):
        yield ("质量标准溯源", "方法", "标准原文", "来源")
        for row in rows_sorted:
            for standard in row.get("standards", []):
                location = (
                    f"表 {standard['table']} 行 {standard['row']} 列 {standard['column']}"
                    if standard["table"]
                    else f"人工录入条款 {standard['row']}"
                )
                if standard.get("page"):
                    location = f"PDF 第 {standard['page']} 页 · {location}"
                yield (
                    row["label"],
                    standard["method"],
                    standard["criterion"],
                    f"{standard['filename']} · {location} · {standard['specification_id']}",
                )
    if protocol := snapshot.get("protocol_source"):
        location = f"原方案表 {protocol['table']}" if protocol["table"] else "对照原方案人工编制"
        yield ("方案原件", protocol["filename"], "SHA256", protocol["sha256"], location)
    if attachment := snapshot.get("attachment"):
        yield ("扫描附件", attachment["filename"], "SHA256", attachment["id"])
    yield (
        "填写与溯源说明",
        "空白为尚无采用结果的应检格；本表基于系统冻结快照导出（0公式格），适用标准和专业评价由业务人员核对签批。",
    )


def _estimate_lines(text: str, chars_per_line: int) -> int:
    if not text:
        return 1
    cap = max(8, chars_per_line)
    total = 0
    for line in str(text).split("\n"):
        # Approximate wide CJK chars as 1.7 ASCII width units
        eff_len = sum(1.7 if ord(ch) > 127 else 1.0 for ch in line)
        total += max(1, int((eff_len + cap - 1) // cap))
    return total


def _build_confirmation_workbook(rows) -> Workbook:
    rows = tuple(rows)
    width = max(8, max(len(r) for r in rows))
    book = Workbook()
    sheet = book.active
    sheet.title = "稳定性确认表"

    # Locate the first footer row ("备注\nRemark") to separate matrix data rows from footer/appendix
    footer_start = next((idx for idx, r in enumerate(rows, 1) if r and r[0] == "备注\nRemark"), len(rows) + 1)

    # Set column widths first so row height estimates can use actual widths
    col_widths: dict[int, int] = {1: 16, 2: 28, 3: 20, 4: 30}
    for col in range(5, width + 1):
        col_widths[col] = 15 if width <= 10 else 13
    for col, w in col_widths.items():
        sheet.column_dimensions[get_column_letter(col)].width = w

    def _span_width(c1: int, c2: int) -> int:
        return sum(col_widths.get(c, 14) for c in range(c1, c2 + 1))

    prev_cat = None
    in_change_history_entry = False

    for index, values in enumerate(rows, 1):
        padded = values + ("",) * (width - len(values))
        first_cell = padded[0]

        # Determine merges for this row (only non-empty top-left cell gets text; siblings stay None)
        merges: list[tuple[int, int]] = []
        if index == 1:
            merges = [(1, width)]
        elif index in {2, 3, 4, 6}:
            if width > 8:
                merges = [(8, width)]
        elif index == 5:
            merges = [(2, width)]
        elif index == 8:
            merges = [(1, 4)]
        elif index >= footer_start:
            if first_cell in {"备注\nRemark", "填写与溯源说明"}:
                merges = [(2, width)]
            elif first_cell.startswith("稳定性总结") or first_cell == "变更历史 / Change History":
                merges = [(1, width)]
            elif first_cell == "起草人/日期\nAuthor/Date":
                merges = [(1, 2), (3, 4), (5, 6), (7, width)]
            elif first_cell == "QC批准人/日期\nQC Approver/Date":
                merges = [(1, 2), (3, width)]
            elif first_cell == "版本号\nVersion":
                merges = [(1, 2), (3, width)]
                in_change_history_entry = True
            elif in_change_history_entry:
                merges = [(1, 2), (3, width)]
                in_change_history_entry = False

        skip_write: set[int] = set()
        for c1, c2 in merges:
            for c in range(c1 + 1, c2 + 1):
                skip_write.add(c)

        is_data_row = 9 <= index < footer_start
        cat_changed = False
        if is_data_row:
            cat_val = first_cell
            cat_changed = bool(cat_val) and prev_cat is not None and cat_val != prev_cat
            is_first_of_cat = bool(cat_val) and cat_val != prev_cat
            prev_cat = cat_val
        else:
            is_first_of_cat = False

        for col in range(1, width + 1):
            cell = sheet.cell(index, col)
            if col not in skip_write and padded[col - 1] != "":
                _put_text(cell, padded[col - 1])
            cell.border = _CAT_TOP_BORDER if cat_changed else _GRID_BORDER
            cell.alignment = Alignment(vertical="center", horizontal="left", wrap_text=True)

            if index == 1:
                cell.fill = _NAVY_FILL
                cell.font = _TITLE_FONT
                cell.alignment = Alignment(vertical="center", horizontal="center", wrap_text=True)
            elif index in {2, 3, 4, 6}:
                is_label = col in {1, 3, 5, 7}
                cell.fill = _META_LABEL_FILL if is_label else PatternFill(fill_type=None)
                cell.font = _META_LABEL_FONT if is_label else _META_VAL_FONT
                if is_label:
                    cell.alignment = Alignment(vertical="center", horizontal="center", wrap_text=True)
            elif index == 5:
                is_label = col == 1
                cell.fill = _META_LABEL_FILL if is_label else PatternFill(fill_type=None)
                cell.font = _META_LABEL_FONT if is_label else _META_VAL_FONT
                if is_label:
                    cell.alignment = Alignment(vertical="center", horizontal="center", wrap_text=True)
            elif index == 7:
                cell.fill = _NAVY_FILL
                cell.font = _HEADER_FONT
                cell.alignment = Alignment(vertical="center", horizontal="center", wrap_text=True)
            elif index == 8:
                cell.fill = _SUBHEADER_FILL
                cell.font = _SUBHEADER_FONT
                cell.alignment = Alignment(vertical="center", horizontal="center", wrap_text=True)
            elif is_data_row:
                if col == 1:
                    cell.fill = _CAT_FILL
                    cell.font = _CAT_FIRST_FONT if is_first_of_cat else _CAT_REPEAT_FONT
                    cell.alignment = Alignment(vertical="center", horizontal="center", wrap_text=True)
                elif col >= 5:
                    cell.font = _BODY_FONT
                    cell.alignment = Alignment(vertical="center", horizontal="center", wrap_text=True)
                else:
                    cell.font = _BODY_FONT
            else:
                # Footer & traceability appendix styling
                if first_cell == "变更历史 / Change History":
                    cell.fill = _SECTION_FILL
                    cell.font = _SECTION_FONT
                    cell.alignment = Alignment(vertical="center", horizontal="left", wrap_text=True)
                elif first_cell in _APPENDIX_HEADERS:
                    cell.fill = _APPENDIX_FILL
                    cell.font = _META_LABEL_FONT
                    cell.alignment = Alignment(vertical="center", horizontal="center", wrap_text=True)
                elif first_cell == "版本号\nVersion":
                    cell.fill = _META_LABEL_FILL
                    cell.font = _META_LABEL_FONT
                    cell.alignment = Alignment(vertical="center", horizontal="center", wrap_text=True)
                elif first_cell in {
                    "备注\nRemark",
                    "起草人/日期\nAuthor/Date",
                    "QC批准人/日期\nQC Approver/Date",
                    "填写与溯源说明",
                    "方案原件",
                    "扫描附件",
                }:
                    is_lbl = (
                        col == 1
                        or (first_cell == "起草人/日期\nAuthor/Date" and col in {1, 2, 5, 6})
                        or (first_cell == "QC批准人/日期\nQC Approver/Date" and col in {1, 2})
                        or (first_cell in {"方案原件", "扫描附件"} and col in {1, 3})
                    )
                    cell.fill = _META_LABEL_FILL if is_lbl else PatternFill(fill_type=None)
                    cell.font = _META_LABEL_FONT if is_lbl else (_HINT_FONT if first_cell == "填写与溯源说明" else _BODY_FONT)
                    if is_lbl:
                        cell.alignment = Alignment(vertical="center", horizontal="center", wrap_text=True)
                elif first_cell.startswith("稳定性总结"):
                    cell.font = _BODY_FONT
                    cell.alignment = Alignment(vertical="top", horizontal="left", wrap_text=True)
                else:
                    cell.font = _BODY_FONT
                    if col == 1 and index == footer_start + 6:
                        cell.alignment = Alignment(vertical="center", horizontal="center", wrap_text=True)

        for c1, c2 in merges:
            if c2 > c1:
                sheet.merge_cells(start_row=index, start_column=c1, end_row=index, end_column=c2)

        # Compute clean row height accounting for merged spans
        if index == 1:
            height = 30
        elif index == 5:
            lines = _estimate_lines(padded[1], _span_width(2, width) - 4)
            height = min(140, max(60, lines * 18))
        elif index == 7:
            height = 30
        elif index == 8:
            height = 22
        elif first_cell.startswith("稳定性总结"):
            height = 58
        elif first_cell in {"起草人/日期\nAuthor/Date", "QC批准人/日期\nQC Approver/Date"}:
            height = 32
        elif first_cell == "变更历史 / Change History":
            height = 24
        else:
            merge_map = {c1: c2 for c1, c2 in merges}
            max_lines = 1
            for col in range(1, width + 1):
                if col in skip_write or not padded[col - 1]:
                    continue
                span_w = _span_width(col, merge_map.get(col, col)) - 2
                max_lines = max(max_lines, _estimate_lines(padded[col - 1], span_w))
            min_h = 26 if index in {2, 3, 4, 6} else 22
            height = min(180, max(min_h, max_lines * 16 + 4))
        sheet.row_dimensions[index].height = height

    sheet.freeze_panes = "E9"
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.print_title_rows = "7:8"
    sheet.print_options.horizontalCentered = True
    return book


def write_confirmation(path, rows):
    book = _build_confirmation_workbook(rows)
    try:
        book.save(path)
    finally:
        book.close()


def render_confirmation_bytes(snapshot) -> bytes:
    """Render a confirmation snapshot to xlsx bytes and run independent verification."""
    rows = tuple(workbook_rows(snapshot))
    buf = BytesIO()
    book = _build_confirmation_workbook(rows)
    try:
        book.save(buf)
    finally:
        book.close()
    raw = buf.getvalue()
    verify_confirmation(BytesIO(raw), rows)
    return raw


def verify_confirmation(path, rows):
    book = load_workbook(path, data_only=False, read_only=True)
    try:
        actual = list(book["稳定性确认表"].iter_rows())
        width = max(8, max(map(len, rows)))
        if len(actual) != len(rows):
            raise WorkflowError("worksheet_export_verification", "确认表行数反读不一致。", 409)
        for cells, expected in zip(actual, rows, strict=True):
            values = tuple(cell.value if cell.value is not None else "" for cell in cells)
            if values != expected + ("",) * (width - len(expected)) or any(cell.data_type == "f" for cell in cells):
                raise WorkflowError("worksheet_export_verification", "确认表内容反读不一致。", 409)
    finally:
        book.close()


def create_confirmation(worksheets, sheet_id, revision, selection_id, actor, *, point_ids=None, attachment=None):
    require(actor, Capability.EXPORT)
    store = worksheets.store
    with store.read() as conn:
        view = worksheets.get(sheet_id, actor, _conn=conn)
        if view["revision"] != revision or view["selection_id"] != selection_id:
            raise WorkflowError("worksheet_revision_changed", "工作表或采用结果已更新，请重新打开后导出。", 409)
        if point_ids is not None:
            requested = set(point_ids)
            points = [point for point in view["points"] if point["id"] in requested]
            if not points or len(points) != len(requested):
                raise WorkflowError("worksheet_export_scope", "请选择当前研究表中的计划时间点。")
            view["points"] = points
            view["cells"] = [cell for cell in view["cells"] if cell["point_id"] in requested]
            view["events"] = [
                event
                for event in view.get("events", [])
                if not event.get("point_id") or event["point_id"] in requested
            ]
            if not any(point["unit"] == "M" and point["amount"] == 0 for point in points):
                view["release_references"] = {}
        project = view["project"]
        proj = project_row(conn, project)
        project_revision = proj["revision"]
        project_name = json.loads(proj["config_json"]).get("name", "")
        selected = {cell["adopted"]["result_version_id"] for cell in view["cells"] if cell["adopted"]}
        selected.update(
            reference["result_version_id"]
            for row_id, reference in view.get("release_references", {}).items()
            if not reference.get("coa") and row_id not in view.get("invalidated_release_rows", ())
        )
        results = {}
        notes = {}
        for version_id in sorted(selected):
            row = worksheets.result_versions._version(conn, version_id)
            record = StabilityRecord.model_validate_json(row["record_json"])
            results[version_id] = record.model_dump(mode="json")
            if source_notes := source_result_notes(row, record):
                notes[version_id] = source_notes
        sheet = {key: value for key, value in view.items() if key not in {"occupied_by", "editable"}}
        snapshot = {
            "worksheet": sheet,
            "project_name": project_name,
            "results": results,
            "project_revision": project_revision,
            "template_version": TEMPLATE_VERSION,
        }
        if notes:
            snapshot["result_notes"] = notes
        if draft_id := sheet.get("protocol_draft_id"):
            protocol = conn.execute("SELECT filename, source FROM protocol_drafts WHERE id=?", (draft_id,)).fetchone()
            if protocol is None or hashlib.sha256(protocol["source"]).hexdigest() != draft_id:
                raise WorkflowError("source_integrity", "方案原件校验失败。", 409)
            snapshot["protocol_source"] = {
                "filename": protocol["filename"],
                "sha256": draft_id,
                "table": sheet.get("protocol_table"),
            }
        if attachment:
            snapshot["attachment"] = attachment
        snapshot["snapshot_id"] = digest(snapshot)

    export_id = uid("confirmation")
    target = store.home / "workflow-exports" / export_id
    target.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=target.parent) as temp:
        draft = Path(temp) / "confirmation.xlsx"
        rows = tuple(workbook_rows(snapshot))
        write_confirmation(draft, rows)
        verify_confirmation(draft, rows)
        sha = hashlib.sha256(draft.read_bytes()).hexdigest()
        path = target / draft.name
        target.mkdir()
        try:
            draft.replace(path)
            with store.connect() as conn:
                conn.execute(
                    """INSERT INTO workflow_exports(id,project,revision,path,sha256,snapshot_json,created_by)
                       VALUES (?,?,?,?,?,?,?)""",
                    (export_id, project, project_revision, str(path), sha, encoded(snapshot), actor.id),
                )
        except BaseException:
            path.unlink(missing_ok=True)
            target.rmdir()
            raise
    return {
        "export_id": export_id,
        "snapshot_id": snapshot["snapshot_id"],
        "download_url": f"/api/v1/exports/{export_id}/file",
    }
