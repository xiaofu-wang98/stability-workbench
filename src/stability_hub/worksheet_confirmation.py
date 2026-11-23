"""Build a confirmation workbook from a frozen business worksheet plan and adopted results."""

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .models import StabilityRecord, digest
from .permissions import Capability, require
from .reporting import _put_text, reported_text, source_result_notes
from .workflow_common import WorkflowError, encoded, project_row, uid

TEMPLATE_VERSION = "worksheet-confirmation-7"


def workbook_rows(snapshot):
    sheet = snapshot["worksheet"]
    identity = sheet["identity"]
    points = sheet["points"]
    entries = {(cell["row_id"], cell["point_id"]): cell for cell in sheet["cells"]}
    notes = []
    yield ("稳定性确认表", sheet["name"], "", "")
    yield ("项目", sheet["project"], "批号", identity["batch"])
    yield ("方案", identity["study_id"], "依据", sheet["basis"])
    yield ("条件", identity["condition"], "放置方向", identity["orientation"])
    yield ("开始日期", sheet["start_date"], "计划版本", str(sheet["plan_version"]))
    yield ("模板版本", TEMPLATE_VERSION, "数据快照", snapshot["snapshot_id"])
    yield ("检项", "方法", "方法版本", "单位", *(point["label"] for point in points), "质量大类", "细分小类")
    yield ("计划日期", "", "", "", *(point["planned_date"] for point in points), "", "")
    rows_sorted = sorted(
        sheet["rows"],
        key=lambda r: (r.get("category_order", 99), r.get("display_order", 999)),
    ) if any(r.get("category_code") for r in sheet["rows"]) else sheet["rows"]
    for row in rows_sorted:
        values = []
        for point in points:
            cell = entries.get((row["id"], point["id"]))
            if cell and cell["adopted"]:
                version = cell["adopted"]["result_version_id"]
                result = snapshot["results"][version]["result"]
                value = reported_text(result)
                if result["status"] == "numeric" and result["unit"] == "%":
                    value += "%"
                notes.extend((row["label"], row["method"], point["label"], origin, note)
                             for origin, note in snapshot.get("result_notes", {}).get(version, ()))
            elif cell and (reference := cell.get("release_reference")):
                value = cell["raw"] + "（引用放行）" if reference["current"] else "放行引用待复核"
            elif point["label"] == "0M" and row["id"] in sheet.get("invalidated_release_rows", ()):
                value = "放行结果已作废，待重新关联"
            elif point["label"] == "0M" and row["id"] in sheet.get("pending_release_references", {}):
                value = "方案要求引用放行结果，待关联"
            elif cell:
                value = "待采用"
            elif any(change["row_id"] == row["id"] and point["id"] in change["point_ids"]
                     for change in sheet.get("stopped_checks", [])):
                value = "已停检"
            elif row["id"] not in point["required_row_ids"]:
                value = "不要求"
            else:
                value = ""
            values.append(value)
        yield (
            row["label"], row["method"], row["method_version"], row["unit"], *values,
            row.get("category_name_zh", ""), row.get("subcategory_name_zh", ""),
        )
    if notes:
        yield ("结果来源备注", "方法", "时间点", "来源", "原文")
        yield from notes
    if sheet.get("release_references"):
        yield ("0M 放行引用", "放行完成日期", "核对依据", "放行结果版本")
        for row in rows_sorted:
            if reference := sheet["release_references"].get(row["id"]):
                yield (row["label"], reference["release_completed"], reference["basis"], reference["result_version_id"])
    if any(row.get("standards") or row.get("criteria_text") for row in sheet["rows"]):
        yield ("适用标准", "方法", "标准原文", "来源")
        for row in rows_sorted:
            if criterion := row.get("criteria_text"):
                yield (row["label"], row["method"], criterion, "本表手填标准")
            for standard in row.get("standards", []):
                location = (f"表 {standard['table']} 行 {standard['row']} 列 {standard['column']}"
                            if standard['table'] else f"人工录入条款 {standard['row']}")
                if standard.get("page"):
                    location = f"PDF 第 {standard['page']} 页 · {location}"
                yield (row["label"], standard["method"], standard["criterion"],
                       f"{standard['filename']} · {location} · {standard['specification_id']}")
    if sheet.get("events"):
        yield ("事件类别", "检项／时间点", "事件编号", "说明", "扫描附件 SHA256")
        for entry in sheet["events"]:
            scope = " · ".join(filter(None, (entry["item_label"], entry["point_label"]))) or "整表"
            yield (entry["category"], scope, entry["reference"], entry["description"], entry["scan_id"] or "")
    yield ("说明", "空白为尚无采用结果的应检格。适用标准和专业评价由业务人员核对。")
    yield ("人工总结", "")
    if protocol := snapshot.get("protocol_source"):
        location = f"原方案表 {protocol['table']}" if protocol["table"] else "对照原方案人工编制"
        yield ("方案原件", protocol["filename"], "SHA256", protocol["sha256"], location)
    if attachment := snapshot.get("attachment"):
        yield ("扫描附件", attachment["filename"], "SHA256", attachment["id"])


def write_confirmation(path, rows):
    book = Workbook()
    sheet = book.active
    sheet.title = "稳定性确认表"
    for index, values in enumerate(rows, 1):
        for column, value in enumerate(values, 1):
            cell = sheet.cell(index, column)
            _put_text(cell, value)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if index in {1, 7, 8}:
                cell.fill = PatternFill("solid", fgColor="163C4D" if index < 8 else "EDF3F8")
                cell.font = Font(bold=True, color="FFFFFF" if index < 8 else "163C4D")
    for index in range(1, sheet.max_column + 1):
        sheet.column_dimensions[get_column_letter(index)].width = 24 if index < 5 else 17
    sheet.freeze_panes = "E9"
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.print_title_rows = "7:8"
    sheet.print_options.horizontalCentered = True
    book.save(path)
    book.close()


def verify_confirmation(path, rows):
    book = load_workbook(path, data_only=False, read_only=True)
    try:
        actual = list(book["稳定性确认表"].iter_rows())
        width = max(map(len, rows))
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
            view["events"] = [event for event in view.get("events", [])
                              if not event.get("point_id") or event["point_id"] in requested]
            if not any(point["unit"] == "M" and point["amount"] == 0 for point in points):
                view["release_references"] = {}
        project = view["project"]
        project_revision = project_row(conn, project)["revision"]
        selected = {cell["adopted"]["result_version_id"] for cell in view["cells"] if cell["adopted"]}
        selected.update(reference["result_version_id"] for row_id, reference in view.get("release_references", {}).items()
                        if not reference.get("coa") and row_id not in view.get("invalidated_release_rows", ()))
        results = {}
        notes = {}
        for version_id in sorted(selected):
            row = worksheets.result_versions._version(conn, version_id)
            record = StabilityRecord.model_validate_json(row["record_json"])
            results[version_id] = record.model_dump(mode="json")
            if source_notes := source_result_notes(row, record):
                notes[version_id] = source_notes
        sheet = {key: value for key, value in view.items() if key not in {"occupied_by", "editable"}}
        snapshot = {"worksheet": sheet, "results": results, "project_revision": project_revision,
                    "template_version": TEMPLATE_VERSION}
        if notes:
            snapshot["result_notes"] = notes
        if draft_id := sheet.get("protocol_draft_id"):
            protocol = conn.execute("SELECT filename, source FROM protocol_drafts WHERE id=?", (draft_id,)).fetchone()
            if protocol is None or hashlib.sha256(protocol["source"]).hexdigest() != draft_id:
                raise WorkflowError("source_integrity", "方案原件校验失败。", 409)
            snapshot["protocol_source"] = {"filename": protocol["filename"], "sha256": draft_id,
                                           "table": sheet.get("protocol_table")}
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
                conn.execute("""INSERT INTO workflow_exports(id,project,revision,path,sha256,snapshot_json,created_by)
                                VALUES (?,?,?,?,?,?,?)""",
                             (export_id, project, project_revision, str(path), sha, encoded(snapshot), actor.id))
        except BaseException:
            path.unlink(missing_ok=True)
            target.rmdir()
            raise
    return {"export_id": export_id, "snapshot_id": snapshot["snapshot_id"],
            "download_url": f"/api/v1/exports/{export_id}/file"}
