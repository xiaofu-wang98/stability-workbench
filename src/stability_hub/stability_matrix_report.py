"""Condition-based stability report with traceable result matrices and charts."""
from collections import defaultdict
from datetime import date

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt

from .models import StabilityRecord
from .reporting import reported_text, source_result_notes
from .trend_export import analysis_scope
from .workflow_common import WorkflowError

TEMPLATE = "stability-matrix-7"
STUDIES = {"long_term": "长期稳定性 / Long-term stability", "accelerated": "加速稳定性 / Accelerated stability",
           "stress": "强力破坏 / Stress study"}


def validate_stability_scope(snapshot):
    keys = [StabilityRecord.model_validate_json((entry["adopted"] or entry["display"])["record_json"]).key
            for entry in snapshot["entries"]]
    if any(key.study == "release" for key in keys):
        raise WorkflowError("report_stability_scope", "此模板仅接受稳定性结果。请筛选考察类型或排除放行记录。")
    if len({(key.batch, key.sample_type, key.presentation, key.purpose, key.sample_id) for key in keys}) != 1:
        raise WorkflowError("report_stability_scope", "请为此模板选择单一批次、材料、规格和用途。")


def freeze_worksheet_evidence(flow, conn, snapshot, actor, *, include_release, include_planned):
    from .business_worksheets import BusinessWorksheets
    from .result_versions import ResultVersions

    project = snapshot["project"]
    slots = {entry["display"]["slot_id"] for entry in snapshot["entries"]}
    sheet_ids = {row["sheet_id"] for row in conn.execute(
        """SELECT s.slot_id,s.sheet_id FROM business_worksheet_slots s
           JOIN business_worksheets w ON w.id=s.sheet_id WHERE w.project=?""", (project,)
    ) if row["slot_id"] in slots}
    worksheets = BusinessWorksheets(flow, ResultVersions(flow))
    evidence = {}
    as_of = date.today().isoformat()
    for sheet_id in sorted(sheet_ids):
        view = worksheets.get(sheet_id, actor, _conn=conn)
        rows = {row["id"]: row for row in view["rows"]}
        release = {cell["row_id"]: cell for cell in view["cells"] if cell.get("release_reference")}
        if include_planned:
            planned_by_row = defaultdict(list)
            occupied = {(cell["row_id"], cell["point_id"]) for cell in view["cells"]}
            for point in view["points"]:
                for row_id in point["required_row_ids"]:
                    if (row_id, point["id"]) not in occupied:
                        planned_by_row[row_id].append({key: point[key] for key in ("unit", "amount", "planned_date")})
        for cell in view["cells"]:
            slot = cell["slot_id"]
            if slot not in slots:
                continue
            row_id = cell["row_id"]
            item = {"worksheet_id": sheet_id, "row_id": row_id, "plan_version": view["plan_version"],
                    "criteria_text": rows[row_id].get("criteria_text", ""),
                    "standards": rows[row_id].get("standards", [])}
            if include_planned and row_id in planned_by_row:
                item["planned_missing"] = planned_by_row[row_id]
                item["plan_as_of"] = as_of
            item["events"] = [entry for entry in view.get("events", [])
                              if entry["row_id"] in (None, row_id)
                              and entry["point_id"] in (None, cell["point_id"])]
            if include_release and row_id in release:
                origin = release[row_id]
                item["release"] = {**origin["release_reference"], "raw": origin["raw"],
                                   "source_ids": origin["display"]["source_ids"]}
            evidence[slot] = item
    return evidence


def write_stability_matrix(path, snapshot, charts):
    doc = Document()
    section = doc.sections[0]
    section.orientation = WD_ORIENT.LANDSCAPE
    section.page_width, section.page_height = Inches(11.69), Inches(8.27)
    section.left_margin = section.right_margin = Inches(.65)
    section.top_margin = section.bottom_margin = Inches(.6)
    normal = doc.styles["Normal"]
    normal.font.name = "Microsoft YaHei"
    normal.font.size = Pt(10)
    normal.paragraph_format.space_after = Pt(5)
    normal.element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    settings = snapshot["stability_document"]
    doc.add_heading(snapshot["project"] + " 稳定性考察报告", 0)
    doc.add_paragraph("Stability Study Report")
    doc.add_paragraph("数据版本 " + str(snapshot["revision"]) + " · 模板 " + TEMPLATE)
    doc.add_heading("签署 / Signatures", 1)
    expected = []

    def table(headers, rows):
        grid = doc.add_table(rows=1, cols=len(headers))
        grid.style = "Table Grid"
        grid.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
        for cell, value in zip(grid.rows[0].cells, headers, strict=True):
            cell.text = value
        for values in rows:
            row = grid.add_row()
            row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
            for cell, value in zip(row.cells, values, strict=True):
                cell.text = value
        expected.append([tuple(cell.text for cell in row.cells) for row in grid.rows])

    table(("职责 / Role", "签名 / Signature", "日期 / Date"),
          ((role, "", "") for role in ("起草", "审核", "批准")))
    doc.add_heading("目的 / Purpose", 1)
    doc.add_paragraph(settings["purpose"] or "汇总选定批次和考察条件的稳定性检测结果。")
    doc.add_heading("样品信息 / Sample information", 1)
    groups = defaultdict(list)
    for entry in snapshot["entries"]:
        record = StabilityRecord.model_validate_json((entry["adopted"] or entry["display"])["record_json"])
        scope = analysis_scope(record)
        groups[(*scope[:5], *scope[9:])].append((entry, record))
    first = next(iter(groups.values()))[0][1].key
    table(("项目", "批号", "材料", "规格", "用途"),
          [(first.project, first.batch, first.sample_type, first.presentation, first.purpose)])
    doc.add_heading("设备信息 / Equipment information", 1)
    doc.add_paragraph(settings["equipment"] or "待填写。")
    doc.add_heading("稳定性考察结果汇总及分析 / Summary and analysis", 1)
    reference_notes = {}
    event_notes = {}
    result_notes = []
    for group, entries in sorted(groups.items()):
        _, _, study, study_id, condition, time_unit, _, reporting_basis = group
        doc.add_heading(STUDIES[study] + " · " + condition, 2)
        doc.add_paragraph("方案 " + study_id + (f" · 报告体系 {reporting_basis}" if reporting_basis else ""))
        matrix = defaultdict(dict)
        applicable = defaultdict(list)
        references = {}
        planned_missing = defaultdict(dict)
        for entry, record in entries:
            key = record.key
            row = (key.item, key.method, key.method_version, record.result.unit)
            point = key.day if time_unit == "D" else key.month
            if point in matrix[row]:
                raise WorkflowError("report_result_collision", "同一报告表格位置有多条检测结果，请缩小范围。")
            text = reported_text(record.result.model_dump(mode="json")) if entry["adopted"] else "尚无采用值"
            if entry["adopted"]:
                result_notes.extend((condition, key.time_label, snapshot['item_labels'].get(key.item, key.item),
                                     key.method, origin, note)
                                    for origin, note in source_result_notes(entry["adopted"], record))
            standards = tuple(standard.criterion_text for standard in record.standards)
            matrix[row][point] = (text, standards)
            evidence = snapshot.get("worksheet_evidence", {}).get(entry["display"]["slot_id"], {})
            for planned in evidence.get("planned_missing", []):
                if planned["unit"] == time_unit:
                    status = "未到期" if planned["planned_date"] > evidence["plan_as_of"] else "已到期未录入"
                    planned_missing[row][planned["amount"]] = status
            for event in evidence.get("events", []):
                event_notes[event["id"]] = (study_id, condition, event["category"],
                    " · ".join(filter(None, (event["item_label"], event["point_label"]))) or "整表",
                    event["reference"], event["description"], event["scan_id"] or "")
            if criterion := evidence.get("criteria_text"):
                applicable[row].append(f"{criterion}（本表手填标准）")
            for standard in evidence.get("standards", []):
                location = (f"表 {standard['table']} 行 {standard['row']}"
                            if standard['table'] else f"人工录入条款 {standard['row']}")
                if standard.get("page"):
                    location = f"PDF 第 {standard['page']} 页 · " + location
                applicable[row].append(f"{standard['criterion']}（{standard['filename']} · {location}）")
            if time_unit == "M" and evidence.get("release"):
                references[row] = evidence
        for row, evidence in references.items():
            if 0 in matrix[row]:
                continue
            release = evidence["release"]
            value = release["raw"] + "（引用放行）" if release["current"] else "放行引用待复核"
            matrix[row][0] = (value, ())
            note_id = evidence["worksheet_id"], evidence["row_id"]
            reference_notes[note_id] = (study_id, condition, snapshot["item_labels"].get(row[0], row[0]),
                                       release["release_completed"], release["basis"], release["result_version_id"])
        for row, planned in planned_missing.items():
            for point, status in planned.items():
                matrix[row].setdefault(point, (status, ()))
        points = sorted({point for row in matrix.values() for point in row})
        for start in range(0, len(points), 7):
            columns = points[start:start + 7]
            body = []
            for row, values in sorted(matrix.items()):
                standards = list(dict.fromkeys(s for point in columns if point in values for s in values[point][1]))
                if applicable[row]:
                    standards.extend("研究表已关联：" + value for value in dict.fromkeys(applicable[row]))
                label = snapshot["item_labels"].get(row[0], row[0])
                body.append((label, *row[1:], "\n".join(standards), *(values.get(point, ("", ()))[0] for point in columns)))
            table(("检项", "方法", "方法版本", "单位", "来源标准", *(f"{point}{time_unit}" for point in columns)), body)
        doc.add_paragraph("空白表示所选范围内无该位置记录。已关联标准与来源标准分别列示。趋势图使用采用的稳定性检测结果。")
        if planned_missing:
            doc.add_paragraph("计划状态按报告生成时的本机日期和已保存计划判定。")
    if reference_notes:
        doc.add_heading("0M 放行引用 / Release references", 1)
        table(("方案", "条件", "检项", "放行完成日期", "核对依据", "引用结果版本"), reference_notes.values())
    doc.add_heading("变更及事件说明 / Changes and events", 1)
    if event_notes:
        table(("方案", "条件", "类别", "检项／时间点", "编号", "说明", "扫描附件 SHA256"), event_notes.values())
    doc.add_paragraph(settings["events"] or "待填写。")
    if result_notes:
        doc.add_heading("结果来源备注 / Source notes", 1)
        table(("条件", "时间点", "检项", "方法", "来源", "原文"), result_notes)
    chart_count = 0
    for label, image, data in charts:
        scope = data["scope"]
        if (*scope[:5], *scope[9:]) not in groups:
            continue
        if not chart_count:
            doc.add_heading("稳定性趋势图 / Stability trends", 1)
        chart_count += 1
        context = " · ".join(filter(None, (STUDIES[scope[2]], scope[4], scope[3], scope[11])))
        caption = doc.add_paragraph(f"图 {chart_count} · {label} · {scope[6]} · {context}")
        caption.paragraph_format.keep_with_next = True
        doc.add_picture(str(image), width=Inches(7.1))
        doc.paragraphs[-1].paragraph_format.keep_with_next = True
        doc.add_paragraph(f"未绘制 {len(data['omitted'])} 个结果。")
    doc.add_heading("人工评价 / Evaluation", 1)
    doc.add_paragraph(settings["evaluation"] or "待填写。")
    doc.add_heading("变更历史 / Revision history", 1)
    table(("版本", "变更内容"), [(settings["version"], settings["change_history"] or "待填写。")])
    # Word places a final paragraph after the last table. Keep it on the same page.
    tail = doc.add_paragraph()
    tail.paragraph_format.space_before = Pt(0)
    tail.paragraph_format.space_after = Pt(0)
    tail.paragraph_format.line_spacing = Pt(1)
    tail.add_run().font.size = Pt(1)
    doc.save(path)
    restored = Document(path)
    actual = [[tuple(cell.text for cell in row.cells) for row in grid.rows] for grid in restored.tables]
    if actual != expected or len(restored.inline_shapes) != chart_count:
        raise WorkflowError("report_verification", "Word 表格或趋势图反读不一致。", 409)
    return {"word_cells_checked": sum(len(row) for grid in expected for row in grid), "word_charts_checked": chart_count}
