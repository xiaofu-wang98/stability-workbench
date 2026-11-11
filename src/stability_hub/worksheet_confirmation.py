"""Build a confirmation workbook from a frozen business worksheet plan and adopted results."""

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .models import StabilityRecord, digest
from .permissions import Capability, require
from .reporting import _put_text
from .workflow_common import WorkflowError, encoded, project_row, uid

TEMPLATE_VERSION = "worksheet-confirmation-1"


def workbook_rows(snapshot):
    sheet = snapshot["worksheet"]
    identity = sheet["identity"]
    points = sheet["points"]
    entries = {(cell["row_id"], cell["point_id"]): cell for cell in sheet["cells"]}
    yield ("稳定性确认表", sheet["name"], "", "")
    yield ("项目", sheet["project"], "批号", identity["batch"])
    yield ("方案", identity["study_id"], "依据", sheet["basis"])
    yield ("条件", identity["condition"], "放置方向", identity["orientation"])
    yield ("开始日期", sheet["start_date"], "计划版本", str(sheet["plan_version"]))
    yield ("模板版本", TEMPLATE_VERSION, "数据快照", snapshot["snapshot_id"])
    yield ("检项", "方法", "方法版本", "单位", *(point["label"] for point in points))
    yield ("计划日期", "", "", "", *(point["planned_date"] for point in points))
    for row in sheet["rows"]:
        values = []
        for point in points:
            cell = entries.get((row["id"], point["id"]))
            if cell and cell["adopted"]:
                value = snapshot["results"][cell["adopted"]["result_version_id"]]["result"]["raw_text"]
            elif cell:
                value = "待采用"
            elif row["id"] not in point["required_row_ids"]:
                value = "不要求"
            else:
                value = ""
            values.append(value)
        yield (row["label"], row["method"], row["method_version"], row["unit"], *values)
    yield ("说明", "空白为尚无采用结果的应检格。适用标准和专业评价由业务人员核对。")
    yield ("人工总结", "")


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


def create_confirmation(worksheets, sheet_id, revision, selection_id, actor):
    require(actor, Capability.EXPORT)
    store = worksheets.store
    with store.read() as conn:
        view = worksheets.get(sheet_id, actor, _conn=conn)
        if view["revision"] != revision or view["selection_id"] != selection_id:
            raise WorkflowError("worksheet_revision_changed", "工作表或采用结果已更新，请重新打开后导出。", 409)
        project = view["project"]
        project_revision = project_row(conn, project)["revision"]
        selected = {cell["adopted"]["result_version_id"] for cell in view["cells"] if cell["adopted"]}
        results = {}
        for version_id in sorted(selected):
            row = worksheets.result_versions._version(conn, version_id)
            results[version_id] = StabilityRecord.model_validate_json(row["record_json"]).model_dump(mode="json")
        sheet = {key: value for key, value in view.items() if key not in {"occupied_by", "editable"}}
        snapshot = {"worksheet": sheet, "results": results, "project_revision": project_revision,
                    "template_version": TEMPLATE_VERSION}
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
