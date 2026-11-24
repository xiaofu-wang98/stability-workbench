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


def _sort_rows_by_category(rows):
    if any(r.get("category_code") or r.get("category_name_zh") for r in rows):
        return sorted(rows, key=lambda r: (r.get("category_order", 99), r.get("display_order", 999)))
    return list(rows)


def format_location_checkboxes(location: str) -> str:
    """Format standard 3-plant checkboxes using Unicode ☑/□ (never Wingdings2 R)."""
    raw = re.sub(r"[☑□✓✔√]+", "", (location or "")).strip()
    lower = raw.lower()
    upper = raw.upper()
    plants = (
        ("相城工厂X", ("相城", "xiangcheng"), "X"),
        ("常熟工厂C", ("常熟", "changshu"), "C"),
        ("园区工厂S", ("园区", "智享", "yuanqu", "sip"), "S"),
    )
    matched_any = False
    rendered = []
    for display, keywords, code in plants:
        is_hit = bool(raw) and (any(k in lower for k in keywords) or upper == code)
        if is_hit:
            matched_any = True
        rendered.append(("☑" if is_hit else "□") + display)
    if raw and not matched_any:
        rendered.append("☑其他：" + raw)
    return "  ".join(rendered)


def format_study_type_checkboxes(study: str, *, multiline: bool = False) -> str:
    """Format standard stability type checkboxes using Unicode ☑/□."""
    val = (study or "").strip()
    items = [
        ("☑" if val == "accelerated" else "□") + "加速试验 Accelerated study",
        ("☑" if val == "long_term" else "□") + "长期试验 Long-term study",
        (
            "☑其他 Other：强力破坏 Stress study"
            if val == "stress"
            else (f"☑其他 Other：{val}" if val and val not in {"accelerated", "long_term"} else "□其他 Other")
        ),
    ]
    return ("\n" if multiline else "  ").join(items)


def freeze_worksheet_table(sheet, project_name, *, filled, invalidated_positions=None, retain_source_items=False):
    identity = sheet["identity"]
    units = {point["unit"] for point in sheet["points"]}
    cells = {(cell["row_id"], cell["point_id"]): cell for cell in sheet["cells"]}
    body, metadata = [], []
    for row in _sort_rows_by_category(sheet["rows"]):
        basis = row.get("reporting_basis", identity["reporting_basis"])
        criteria = list(dict.fromkeys(filter(None, [row.get("criteria_text"),
                       *(standard["criterion"] for standard in row.get("standards", []))])))
        if not criteria:
            criteria = row.get("source_criteria", [])
        originals = row.get("source_test_item", []) if retain_source_items else []
        normalized = "" if any(row["label"] in original for original in originals) else row["label"]
        category_name = row.get("category_name_zh", "")
        cat_tag = (
            f"【{category_name}】"
            if category_name and not any(category_name in original for original in originals)
            else ""
        )
        label_parts = filter(None, (*originals, normalized, visible(row["method"]),
                                    visible(row["method_version"]), basis, row["unit"], cat_tag))
        label = "\n".join(dict.fromkeys(label_parts) if originals else label_parts)
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
                         "category_code": row.get("category_code", ""), "category_name_zh": category_name,
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
    for row in _sort_rows_by_category(plan.get("rows", [])):
        identity = (row["item_id"], row["method"], row["method_version"],
                    row.get("reporting_basis", plan["identity"].get("reporting_basis", "")))
        if any(point_filters.get(field) not in (None, value)
               for field, value in zip(("item", "method", "method_version"), identity[:3], strict=True)):
            continue
        rows[identity] = {
            "label": row["label"], "id": row["id"], "standards": [],
            "values": {}, "unit": row["unit"],
            "category_name_zh": row.get("category_name_zh", ""),
            "category_order": row.get("category_order", 99),
            "display_order": row.get("display_order", 999),
        }
        if criterion := row.get("criteria_text"):
            rows[identity]["standards"].append(criterion)
        rows[identity]["standards"].extend(s["criterion"] for s in row.get("standards", []))
    for record in records:
        k = record.key
        row = rows.setdefault((k.item, k.method, k.method_version, k.reporting_basis), {
            "label": snapshot["item_labels"].get(k.item, k.item), "id": "", "standards": [],
            "values": {}, "unit": record.result.unit,
            "category_name_zh": "", "category_order": 99, "display_order": 999,
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
    ordered_items = sorted(
        rows.items(),
        key=lambda kv: (kv[1].get("category_order", 99), kv[1].get("display_order", 999)),
    ) if any(r.get("category_name_zh") for r in rows.values()) else rows.items()
    for (item, method, version, basis), row in ordered_items:
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
            cat_tag = f"【{row['category_name_zh']}】" if row.get("category_name_zh") else ""
            label = "\n".join(filter(None, (row["label"], visible(method), visible(version), basis, row["unit"], cat_tag)))
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
    width = max(*(len(row) for row in summary["header"]), 2 + len(summary["points"]))
    edge = Side(style="thin", color="BECFDD")
    for index, row in enumerate(rows, 1):
        for column in range(1, width + 1):
            value = row[column - 1] if column <= len(row) else ""
            cell = sheet.cell(offset + index, column)
            _put_text(cell, value)
            fixed = index == 4 or (index <= 3 and column <= len(row) and column % 2 == 1)
            if controlled and index == 3:
                fixed = column in {1, 8, 11}
            option = (index == 5 and column >= 3) or (index >= 6 and column <= 2) or (index <= 3 and (index, column) in {(1, 2), (1, 4), (2, 6), (2, 8), (3, 2), (3, 4), (3, 6)})
            if fixed or option:
                cell.fill = PatternFill("solid", fgColor="9EC4E2" if fixed else "D5E7F4")
            cell.font = Font(name="Microsoft YaHei", size=10, bold=fixed, color="102D49")
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            cell.border = Border(left=edge, right=edge, top=edge, bottom=edge)
        if controlled and index == 3:
            cond_lines = sum(max(1, (len(line) + 74) // 75) for line in (row[2] or "").split("\n"))
            loc_lines = sum(max(1, (len(line) + 18) // 19) for line in (row[9] if len(row) > 9 else "").split("\n"))
            sheet.row_dimensions[offset + index].height = min(140, max(62, max(cond_lines, loc_lines) * 19))
        else:
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


def controlled_block(sheet):
    summary = freeze_worksheet_table(sheet, sheet.get("product_name", ""), filled=True, retain_source_items=True)
    summary["header"][0][7] = sheet.get("specification_text", summary["header"][0][7])
    summary["header"][1][2] = "稳定性试验箱/温控设备名称和编号"
    summary["header"][1][5] = sheet.get("protocol_number", "")
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
    summary["header"][1][-1] = format_study_type_checkboxes(identity.study, multiline=True)
    location = sheet.get("location", "")
    condition_label, _, location_label, *_ = summary["header"][2]
    summary["header"][2] = [condition_label, "", controlled_conditions(condition, identity.orientation),
                            "", "", "", "", location_label, "",
                            format_location_checkboxes(location)]
    if storage := sheet.get("storage_location"):
        summary["header"][2].extend(("存放位置\nStorage Location", storage))
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
    for point_id, notes in sheet.get("source_events", {}).items():
        if point_id in points:
            column = points[point_id]
            remark[column] = "\n".join(dict.fromkeys(filter(None, (remark[column], *notes))))
    summary["body"].append(remark)
    return summary


def _normalize_condition_token(token: str) -> str:
    text = token.strip()
    if not text:
        return ""
    # Normalize "5±3℃" / "5°C±3°C" -> "5℃±3℃"
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*(?:℃|°C|°c|C)?\s*±\s*(\d+(?:\.\d+)?)\s*(?:℃|°C|°c|C)", text)
    if m:
        return f"{m.group(1)}℃±{m.group(2)}℃"
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*(?:℃|°C|°c)\s*±\s*(\d+(?:\.\d+)?)\s*(?:℃|°C|°c)?", text)
    if m:
        return f"{m.group(1)}℃±{m.group(2)}℃"
    # Normalize "60±5%RH" / "60%RH±5%" -> "60%RH±5%RH"
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*%?(?:RH)?\s*±\s*(\d+(?:\.\d+)?)\s*%(?:RH)?", text, re.I)
    if m and "rh" in text.lower():
        return f"{m.group(1)}%RH±{m.group(2)}%RH"
    return text.replace("°C", "℃").replace("°c", "℃")


def _normalize_placement(text: str) -> str:
    low = text.strip().lower()
    if low in {"正置", "正立", "upright", "正置 upright"}:
        return "正置"
    if low in {"倒置", "倒立", "inverted", "倒置 inverted"}:
        return "倒置"
    if low in {"平放", "水平", "horizontal", "平放 horizontal"}:
        return "平放"
    return ""


def controlled_conditions(condition, orientation=""):
    """Format standard 3-line QC-运营 condition checkboxes (Unicode ☑/□) without guessing."""
    raw_parts = [p.strip() for p in re.split(r"[·;；,，\n]+", condition or "") if p.strip()]
    temperatures = ("-80℃±10℃", "-40℃±10℃", "-40℃±5℃", "5℃±3℃", "25℃±2℃")
    placements = (("正置", "正置 Upright"), ("倒置", "倒置 Inverted"), ("平放", "平放 Horizontal"))

    matched_temps: set[str] = set()
    other_temps: list[str] = []
    matched_rh60 = False
    other_rhs: list[str] = []
    other_env: list[str] = []
    matched_places: set[str] = set()
    other_places: list[str] = []

    norm_ori = _normalize_placement(orientation or "")
    if norm_ori:
        matched_places.add(norm_ori)
    elif (orientation or "").strip():
        other_places.append(orientation.strip())

    for raw in raw_parts:
        token = _normalize_condition_token(raw)
        if not token:
            continue
        place = _normalize_placement(token)
        if place:
            if not matched_places and not other_places:
                matched_places.add(place)
            continue
        if token in temperatures:
            matched_temps.add(token)
        elif re.search(r"℃|^-?\d+(?:\.\d+)?C$", token):
            if token not in other_temps:
                other_temps.append(token)
        elif token.lower() == "60%rh±5%rh":
            matched_rh60 = True
        elif re.search(r"%?\s*rh", token, re.I):
            if token not in other_rhs:
                other_rhs.append(token)
        else:
            if token not in other_env:
                other_env.append(token)

    def chk(selected: bool) -> str:
        return "☑" if selected else "□"

    temp_other = ("☑其他 Others：" + "、".join(other_temps)) if other_temps else "□其他 Others"
    temp_line = "温度 Temperature：" + "  ".join([*(chk(t in matched_temps) + t for t in temperatures), temp_other])

    rh_extras = list(dict.fromkeys([*other_rhs, *other_env]))
    rh_other = ("☑其他 Others：" + "、".join(rh_extras)) if rh_extras else "□其他 Others"
    rh_line = f"相对湿度 Relative humidity：{chk(matched_rh60)}60%RH±5%RH  {rh_other}"

    place_other = ("☑其他 Others：" + "、".join(other_places)) if other_places else "□其他 Others"
    place_line = "放置方式 Placement method：" + "  ".join(
        [*(chk(key in matched_places) + label for key, label in placements), place_other]
    )
    return "\n".join((temp_line, rh_line, place_line))


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
