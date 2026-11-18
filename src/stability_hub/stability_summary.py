"""IP281-style business summary from the frozen adopted-result scope."""
import json
import re
from datetime import date

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .business_worksheets import BusinessWorksheets, StudyIdentity
from .models import StabilityRecord
from .reporting import _put_text, reported_text
from .result_versions import ResultVersions
from .workflow_common import WorkflowError, project_row

SCOPE = ("batch", "sample_type", "presentation", "study", "study_id", "condition", "purpose", "sample_id")
TITLE = "稳定性汇总表"
TEMPLATE_VERSION = "stability-study-table-1"


def freeze_worksheet_table(sheet, project_name, *, filled, invalidated_positions=None):
    identity = sheet["identity"]
    units = {point["unit"] for point in sheet["points"]}
    cells = {(cell["row_id"], cell["point_id"]): cell for cell in sheet["cells"]}
    body, metadata = [], []
    for row in sheet["rows"]:
        basis = row.get("reporting_basis", identity["reporting_basis"])
        criteria = list(dict.fromkeys(filter(None, [row.get("criteria_text"),
                       *(standard["criterion"] for standard in row.get("standards", []))])))
        if not criteria:
            criteria = row.get("source_criteria", [])
        label = "\n".join(filter(None, (row["label"], visible(row["method"]), visible(row["method_version"]),
                                        basis, row["unit"])))
        values, positions = [], []
        for point in sheet["points"]:
            cell = cells.get((row["id"], point["id"]), {})
            adopted = cell.get("adopted")
            required = row["id"] in point["required_row_ids"]
            value = cell["raw"] if filled and adopted else ""
            values.append(value)
            positions.append({"point_id": point["id"], "required": required, "visible": value,
                              "adopted": adopted["result_version_id"] if adopted else None,
                              "latest": (cell.get("display") or {}).get("result_version_id") if not cell.get("release_reference") else None})
            invalidated = (invalidated_positions or {}).get((row["id"], point["id"]))
            if invalidated:
                positions[-1]["invalidated"] = invalidated
        body.append([label, "\n".join(criteria), *values])
        metadata.append({"row_id": row["id"], "item_id": row["item_id"], "method": row["method"],
                         "method_version": row["method_version"], "reporting_basis": basis, "unit": row["unit"],
                         "standards": row.get("standards", []), "positions": positions})
    return {
        "header": [
            ["项目编号", sheet["project"], "产品名称", project_name, "产品批号", identity["batch"], "规格", visible(identity["presentation"])],
            ["开始日期", sheet["start_date"], "设备名称及编号", sheet.get("equipment", ""),
             "稳定性试验方案编号", visible(identity["study_id"]), "稳定性类型",
             {"long_term": "长期", "accelerated": "加速", "stress": "强力破坏"}[identity["study"]]],
            ["考察条件", StudyIdentity.model_validate(identity).record_condition(),
             "稳定性开展地点", sheet.get("location", ""), "材料类别", identity["sample_type"]],
        ],
        "time_unit": next(iter(units)) if len(units) == 1 else "mixed",
        "points": ["\n".join(filter(None, (point["label"], point["planned_date"], point.get("signed_status"))))
                   for point in sheet["points"]],
        "body": body,
        "contract": {"version": TEMPLATE_VERSION, "sheet_id": sheet["id"], "plan_version": sheet["plan_version"],
                     "identity": identity, "rows": metadata, "filled": filled},
    }


def visible(value):
    return "" if value.startswith("原文未注明") or value in {"未注明", "not_reported"} else value


def freeze_summary(flow, conn, snapshot, actor):
    records = [StabilityRecord.model_validate_json(entry["adopted"]["record_json"])
               for entry in snapshot["entries"] if entry["adopted"]]
    if not records:
        raise WorkflowError("summary_empty", "当前范围没有采用结果。")
    if records[0].key.study == "release" or any(
        tuple(getattr(r.key, field) for field in SCOPE) != tuple(getattr(records[0].key, field) for field in SCOPE)
        for r in records
    ):
        raise WorkflowError("summary_scope", "请选择一个稳定性批次、方案和考察条件。")
    key = records[0].key
    plan = {}
    worksheets = BusinessWorksheets(flow, ResultVersions(flow))
    for row in conn.execute("SELECT id, identity_json FROM business_worksheets WHERE project=? AND deleted_at IS NULL", (key.project,)):
        identity = StudyIdentity.model_validate_json(row["identity_json"])
        if all((identity.record_condition() if field == "condition" else getattr(identity, field))
               == getattr(key, field) for field in SCOPE):
            if plan:
                raise WorkflowError("summary_scope", "该范围匹配多个研究表，请核对研究身份。")
            plan = worksheets.get(row["id"], actor, _conn=conn)
    point_filters = snapshot["filters"]
    points = {record.key.time_label for record in records}
    if len({label[-1] for label in points}) > 1:
        raise WorkflowError("summary_time_unit", "所选范围同时包含天和月，请按单一时间单位导出。")
    plan_points = {} if snapshot["selected_slot_ids"] else {
        p["label"]: p for p in plan.get("points", [])
        if p["unit"] in {label[-1] for label in points}
        and all(point_filters.get(field) is None or (p["unit"], p["amount"]) == (unit, point_filters[field])
               for field, unit in (("month", "M"), ("day", "D")))
    }
    points = sorted(points | plan_points.keys(), key=lambda label: (label[-1], int(label[:-1])))
    rows = {}
    for row in plan.get("rows", []):
        identity = (row["item_id"], row["method"], row["method_version"],
                    row.get("reporting_basis", plan["identity"].get("reporting_basis", "")))
        if any(point_filters.get(field) not in (None, value)
               for field, value in zip(("item", "method", "method_version"), identity[:3], strict=True)):
            continue
        rows[identity] = {"label": row["label"], "id": row["id"], "standards": [],
                          "values": {}, "unit": row["unit"]}
        if criterion := row.get("criteria_text"):
            rows[identity]["standards"].append(criterion)
        rows[identity]["standards"].extend(s["criterion"] for s in row.get("standards", []))
    for record in records:
        k = record.key
        row = rows.setdefault((k.item, k.method, k.method_version, k.reporting_basis), {
            "label": snapshot["item_labels"].get(k.item, k.item), "id": "", "standards": [],
            "values": {}, "unit": record.result.unit,
        })
        if k.time_label in row["values"]:
            raise WorkflowError("summary_collision", "同一汇总格存在多条结果，请缩小范围。")
        row["values"][k.time_label] = reported_text(record.result.model_dump(mode="json"))
        row["standards"].extend(s.criterion_text for s in record.standards)
    pending = {(r.key.item, r.key.method, r.key.method_version, r.key.reporting_basis, r.key.time_label)
               for entry in snapshot["entries"] if not entry["adopted"]
               for r in [StabilityRecord.model_validate_json(entry["display"]["record_json"])]}
    today = date.today().isoformat()
    body = []
    for (item, method, version, basis), row in rows.items():
        values = []
        for label in points:
            point = plan_points.get(label)
            if label in row["values"]:
                value = row["values"][label]
            elif (item, method, version, basis, label) in pending:
                value = "待采用"
            elif point and row["id"]:
                if row["id"] not in point["required_row_ids"]:
                    value = "不要求"
                else:
                    value = "未到期" if point["planned_date"] > today else "已到期未录入"
            else:
                value = ""
            values.append(value)
        if row["values"] or (row["id"] and plan_points):
            label = "\n".join(filter(None, (row["label"], visible(method), visible(version), basis, row["unit"])))
            body.append([label, "\n".join(dict.fromkeys(row["standards"])), *values])
    config = json.loads(project_row(conn, key.project)["config_json"])
    return {
        "header": [
            ["项目编号", key.project, "产品名称", config.get("name", ""), "产品批号", key.batch, "规格", visible(key.presentation)],
            ["开始日期", plan.get("start_date", ""), "设备名称及编号", plan.get("equipment", ""),
             "稳定性试验方案编号", visible(key.study_id), "稳定性类型",
             {"long_term": "长期", "accelerated": "加速", "stress": "强力破坏"}[key.study]],
            ["考察条件", key.condition, "稳定性开展地点", plan.get("location", ""), "材料类别", key.sample_type],
        ],
        "time_unit": points[0][-1],
        "points": ["\n".join(filter(None, (label, plan_points.get(label, {}).get("planned_date"),
                                           plan_points.get(label, {}).get("signed_status")))) for label in points],
        "body": body,
    }


def visible_rows(summary):
    time_caption = {"M": "月", "D": "天", "mixed": "天／月"}[summary["time_unit"]]
    return [*summary["header"], ["检验项目\nTest Item", "可接受标准\nAcceptable Criteria",
                                f"考察时间（{time_caption}） / Study timepoints"],
            ["", "", *summary["points"]], *summary["body"]]


def _write_block(sheet, summary, offset=0):
    rows = visible_rows(summary)
    controlled = "condition" in summary
    width = max(10 if controlled else 8, 2 + len(summary["points"]))
    edge = Side(style="thin", color="BECFDD")
    for index, row in enumerate(rows, 1):
        for column in range(1, width + 1):
            value = row[column - 1] if column <= len(row) else ""
            cell = sheet.cell(offset + index, column)
            _put_text(cell, value)
            fixed = index == 4 or (index <= 3 and column <= len(row) and column % 2 == 1)
            if controlled and index == 3:
                fixed = column in {1, 8}
            option = (index == 5 and column >= 3) or (index >= 6 and column <= 2) or (index <= 3 and (index, column) in {(1, 2), (1, 4), (2, 6), (2, 8), (3, 2), (3, 4), (3, 6)})
            if fixed or option:
                cell.fill = PatternFill("solid", fgColor="9EC4E2" if fixed else "D5E7F4")
            cell.font = Font(name="Microsoft YaHei", size=10, bold=fixed, color="102D49")
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            cell.border = Border(left=edge, right=edge, top=edge, bottom=edge)
        wrap = max(sum(max(1, (len(line) + 14) // 15) for line in value.split("\n")) for value in row)
        sheet.row_dimensions[offset + index].height = min(220, max(27, wrap * 15))
    for index, row in enumerate(summary["header"], 1):
        if len(row) < width:
            sheet.merge_cells(start_row=offset + index, start_column=len(row), end_row=offset + index, end_column=width)
    if controlled:
        for first, last in ((1, 2), (3, 7), (8, 9)):
            sheet.merge_cells(start_row=offset + 3, start_column=first, end_row=offset + 3, end_column=last)
    sheet.merge_cells(start_row=offset + 4, start_column=1, end_row=offset + 5, end_column=1)
    sheet.merge_cells(start_row=offset + 4, start_column=2, end_row=offset + 5, end_column=2)
    if len(summary["points"]) > 1:
        sheet.merge_cells(start_row=offset + 4, start_column=3, end_row=offset + 4, end_column=2 + len(summary["points"]))
    for col in range(1, width + 1):
        sheet.column_dimensions[get_column_letter(col)].width = 30 if col <= 2 else 21
    return rows, width


def controlled_block(sheet, project_name):
    summary = freeze_worksheet_table(sheet, project_name, filled=True)
    summary.pop("contract")
    identity = StudyIdentity.model_validate(sheet["identity"])
    condition = identity.record_condition()
    for row, labels in zip(summary["header"], (
        ("Project No.", "Product Name", "Product Batch No.", "Specification"),
        ("Starting Date", "Stability Chamber/Temperature Control Equipment Name and No.",
         "Stability Study Protocol No.", "Stability Type"),
        ("Study Condition", "Stability Location", "Material Type"),
    ), strict=True):
        for index, label in enumerate(labels):
            row[index * 2] += "\n" + label
    summary["header"][1][-1] = "\n".join(
        ("☑" if value == identity.study else "□") + label
        for value, label in (("accelerated", "加速试验 Accelerated study"),
                             ("long_term", "长期试验 Long-term study"),
                             ("stress", "强力破坏 Stress study")))
    location = sheet.get("location", "")
    condition_label, _, location_label, *_ = summary["header"][2]
    summary["header"][2] = [condition_label, "", controlled_conditions(condition, identity.orientation),
                            "", "", "", "", location_label, "",
                            ("☑ " if location else "□ ") + (location or "未填写 / Unspecified")]
    summary["condition"] = condition
    summary["points"] = ["\n".join(filter(None, (
        point["label"].removesuffix("M") if summary["time_unit"] == "M" else point["label"],
        point["planned_date"].replace("-", "."), point.get("signed_status"),
    ))) for point in sheet["points"]]
    rows = {row["id"]: row["label"] for row in sheet["rows"]}
    points = {point["id"]: idx for idx, point in enumerate(sheet["points"], 2)}
    remark = ["备注 / Remark", "", *([""] * len(points))]
    for item in sheet.get("events", []):
        column = points.get(item.get("point_id"), 1)
        note = " · ".join(filter(None, (item.get("category"), rows.get(item.get("row_id")),
                                       item.get("reference"), item.get("description"), item.get("scan_id"))))
        if note:
            remark[column] = "\n".join(filter(None, (remark[column], note)))
    summary["body"].append(remark)
    return summary


def controlled_conditions(condition, orientation):
    """Expose recorded selections without assigning undocumented conditions."""
    parts = [part.strip() for part in condition.split("·") if part.strip()]
    temperatures = ("-80℃±10℃", "-40℃±10℃", "-40℃±5℃", "5℃±3℃", "25℃±2℃")
    placements = {"正置": "正置 Upright", "倒置": "倒置 Inverted", "平放": "平放 Horizontal"}
    if not orientation:
        recorded = placements.keys() & set(parts)
        orientation = next(iter(recorded)) if len(recorded) == 1 else ""
    humidity = [part for part in parts if re.fullmatch(r"\d+(?:\.\d+)?%RH(?:±\d+(?:\.\d+)?%(?:RH)?)?", part, re.I)]
    extra = [part for part in parts if part not in temperatures and part not in humidity and part != orientation]
    checked = lambda selected: "☑" if selected else "□"
    temperature_line = "  ".join(checked(value in parts) + value for value in temperatures)
    humidity_line = "  ".join(checked(value in humidity) + value for value in dict.fromkeys(["60%RH±5%RH", *humidity]))
    placement_line = "  ".join(checked(value == orientation) + text for value, text in placements.items())
    other_line = checked(bool(extra)) + "其他 Others" + (" · " + " · ".join(extra) if extra else "")
    return "\n".join(("温度 Temperature · " + temperature_line, "相对湿度 Relative humidity · " + humidity_line,
                      "放置方式 Placement method · " + placement_line, other_line))


def write_controlled_workbook(path, groups):
    book = Workbook()
    book.remove(book.active)
    checks = []
    for index, group in enumerate(groups, 1):
        sheet = book.create_sheet(f"{index:02d} {group['sample_type']}")
        offset = 0
        for block in group["blocks"]:
            rows, width = _write_block(sheet, block, offset)
            checks.append((sheet.title, offset, rows, width))
            offset += len(rows) + 2
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.page_setup.orientation = "landscape"
        sheet.page_setup.fitToWidth = 1
    book.save(path)
    book.close()
    restored = load_workbook(path, read_only=True)
    try:
        for title, offset, rows, width in checks:
            actual = list(restored[title].iter_rows(min_row=offset + 1, max_row=offset + len(rows),
                                                   max_col=width, values_only=True))
            expected = [tuple(value or None for value in row) + (None,) * (width - len(row)) for row in rows]
            if actual != expected:
                return ["受控汇总表反读不一致"]
        return []
    finally:
        restored.close()


def write_summary(path, summary):
    book = Workbook()
    sheet = book.active
    sheet.title = TITLE
    rows, width = _write_block(sheet, summary)
    sheet.freeze_panes = "C6"
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.print_title_rows = "4:5"
    sheet.print_area = f"A1:{get_column_letter(width)}{len(rows)}"
    if "contract" in summary:
        auxiliary = book.create_sheet("_stability_study")
        auxiliary.sheet_state = "veryHidden"
        for index, value in enumerate((TEMPLATE_VERSION, summary["export_id"], summary["contract"]["sheet_id"],
                                       str(summary["contract"]["plan_version"])), 1):
            _put_text(auxiliary.cell(index, 1), value)
        for index, row in enumerate(summary["contract"]["rows"], 6):
            for column, value in enumerate((row["row_id"], row["item_id"], row["method"], row["method_version"],
                                            row["reporting_basis"], row["unit"]), 1):
                _put_text(auxiliary.cell(index, column), value)
            for column, position in enumerate(row["positions"], 7):
                _put_text(auxiliary.cell(index, column), json.dumps(
                    [position["point_id"], position["adopted"], position["latest"]], ensure_ascii=False))
            for column, standard in enumerate(row["standards"], 7 + len(row["positions"])):
                _put_text(auxiliary.cell(index, column), json.dumps(
                    [standard["specification_id"], standard["table"], standard["row"]], ensure_ascii=False))
    book.save(path)
    book.close()
    restored = load_workbook(path, read_only=True)
    try:
        actual = [list(row) for row in restored[TITLE].values]
        expected = [[value or None for value in row] + [None] * (width - len(row)) for row in rows]
        return [] if actual == expected else ["稳定性汇总表反读不一致"]
    finally:
        restored.close()
