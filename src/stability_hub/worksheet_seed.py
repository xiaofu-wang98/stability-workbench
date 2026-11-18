"""Project each supported source into the same editable study plan."""

import hashlib
import json
import re
from contextlib import ExitStack
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from unicodedata import normalize

from openpyxl import load_workbook

from .business_worksheets import CreateWorksheet, StudyIdentity, historical_row_key, historical_rows, scope_result_entries
from .matrix_fields import timepoint_token
from .models import ProjectConfig, StabilityRecord, digest
from .specification_preview import matching_standards, protocol_numbers, specification_material_scope, unambiguous_standard_sources
from .result_queries import selected_rows
from .workflow_common import WorkflowError, project_row
from .workflow_contracts import ResultFilter
from .xlsx_matrix import parse_matrix_sheet, text


def _matching_component(link, method, label):
    if not method:
        return False
    text = normalize("NFKC", link["method"]).casefold()
    key = " ".join(normalize("NFKC", method).casefold().split())
    if not re.search(r"(?<![a-z0-9])" + re.escape(key) + r"(?![a-z0-9])", " ".join(text.split())):
        return False
    if key == "r ce-sds" and re.search(r"\bnr\s+ce-sds|非还原", text):
        return False
    if key == "ce-sds" and re.search(r"\b(?:nr|r)\s+ce-sds|还原", text):
        return False
    label = " ".join(normalize("NFKC", label).casefold().split())
    return any(
        len(name) > 1 and re.search(r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])", label)
        for value in link["assay_names"] if (name := " ".join(normalize("NFKC", value).casefold().split()))
    )

DEFAULT_MONTHS = (0, 3, 6, 9, 12, 18, 24, 36)


def header_date(value):
    match = re.search(r"(?<!\d)(\d{4})[./年-](\d{1,2})[./月-](\d{1,2})(?!\d)", value)
    if match:
        try:
            return date(*map(int, match.groups())).isoformat()
        except ValueError:
            return ""
    return ""


METADATA_FIELDS = ("equipment", "location")
HEADER_FIELDS = {
    "设备编号": "equipment",
    "设备名称及编号": "equipment",
    "稳定性试验箱/温控设备名称和编号": "equipment",
    "稳定性开展地点": "location",
}


def historical_results(conn, project, batch):
    return [(value, StabilityRecord.model_validate_json(value["record_json"]))
            for _, value in selected_rows(conn, project, ResultFilter(batch=batch)) if value]


def historical_blocks(conn, results, stack):
    books, layouts = {}, {}
    seen = set()
    for version, _ in results:
        for source in json.loads(version["source_json"]):
            raw = source.get("raw_json") or {}
            evidence = raw.get("evidence", {}).get("time_point", {})
            if raw.get("adapter") != "stability_matrix" or not evidence:
                continue
            section = evidence["section"]
            sha = source.get("document_hash")
            if sha not in books:
                document = conn.execute("SELECT filename,stored_path FROM documents WHERE sha256=?", (sha,)).fetchone()
                path = Path(document["stored_path"]) if document else None
                if not path or not path.is_file():
                    books[sha] = None
                else:
                    data = path.read_bytes()
                    if hashlib.sha256(data).hexdigest() != sha:
                        raise WorkflowError("source_integrity", "历史原件校验失败，无法读取研究表头。", 409)
                    book = load_workbook(BytesIO(data), keep_links=False)
                    stack.callback(book.close)
                    books[sha] = (book, document["filename"])
            if books[sha] is None:
                continue
            book, filename = books[sha]
            sheet = book[section]
            if (sha, section) not in layouts:
                layouts[sha, section] = parse_matrix_sheet(sheet)
            observations, layout = layouts[sha, section]
            for block in layout["blocks"]:
                if ("header_row" not in block or not block["header_row"] <= evidence["row"] <= block["header_row"] + 1
                        or evidence["column"] not in block["time_columns"]
                        or not evidence["row"] < raw["location"]["row"] <= block["end_row"]):
                    continue
                start = raw.get("evidence", {}).get("batch", {}).get("row", block["header_row"])
                key = (sha, section, start, evidence["row"], block["header_row"])
                if key not in seen:
                    seen.add(key)
                    yield version, sha, filename, sheet, block, evidence, start, observations


def header_fields(sheet, start, end):
    merged_ends = {(area.min_row, area.min_col): area.max_col
                   for area in sheet.merged_cells.ranges if start <= area.min_row <= end}
    for row in sheet.iter_rows(min_row=start, max_row=end):
        for label, value in zip(row, row[1:]):
            title = text(label).strip().split("\n")[0].strip().rstrip("：:").strip()
            if not title:
                continue
            last = merged_ends.get((label.row, label.column))
            if last and last < len(row):
                value = row[last]
            yield title, value


def location_value(raw):
    markers = list(re.finditer(r"[☐□☒☑✓√£]|(?m:^[ \t]*R(?=[ \t]*[^\x00-\x7f]))", raw))
    if not markers:
        return raw if len(raw.splitlines()) == 1 else ""
    marked = []
    for index, marker in enumerate(markers):
        symbol = marker.group().strip()
        if symbol not in {"☑", "✓", "√", "R"}:
            continue
        end = markers[index + 1].start() if index + 1 < len(markers) else len(raw)
        value = raw[marker.end():end].partition("\n")[0].strip()
        if symbol == "R":
            # The legacy checkbox template appends an option code to the Chinese name.
            value = re.sub(r"(?<=[^\x00-\x7f])\s*[A-Z]$", "", value).strip()
        marked.append(value)
    return marked[0] if len(marked) == 1 else ""


def historical_header(conn, results, slot_id=None):
    axes = set()
    sources = {field: {} for field in HEADER_FIELDS.values()}
    with ExitStack() as stack:
        for version, sha, filename, sheet, block, evidence, start, observations in historical_blocks(conn, results, stack):
            fields = list(header_fields(sheet, start, block["header_row"] - 1))
            for title, cell in fields:
                field = HEADER_FIELDS.get(title)
                raw = text(cell).strip()
                if field and raw:
                    value = raw
                    if cell.data_type == "f":
                        value = ""
                    elif field == "location":
                        value = location_value(raw)
                    sources[field][sha, sheet.title, cell.coordinate] = {
                        "filename": filename, "sha256": sha, "sheet": sheet.title, "cell": cell.coordinate,
                        "raw": raw, "value": value if len(value) <= 500 else "",
                    }
            if version["slot_id"] != slot_id:
                continue
            labels = [text(sheet.cell(evidence["row"], column)).strip() for column in block["time_columns"]]
            axis = tuple(timepoint_token(label) for label in labels if label)
            if not axis or not all(axis) or len(axis) != len(set(axis)):
                continue
            dates, stages, packaging = set(), set(), set()
            for title, cell in fields:
                if title in {"开始日期", "放入时间"}:
                    if isinstance(cell.value, date):
                        dates.add(cell.value.strftime("%Y-%m-%d"))
                    elif isinstance(cell.value, str) and re.fullmatch(
                            r"\d{4}[./年-]\d{1,2}[./月-]\d{1,2}日?", cell.value.strip()):
                        if day := header_date(cell.value):
                            dates.add(day)
                if title in {"材料阶段", "物料阶段"} and text(cell).strip():
                    stages.add(text(cell).strip())
                if title in {"包装", "包装形式", "包装方式"} and text(cell).strip():
                    packaging.add(text(cell).strip())
            planned_dates = {}
            for observation in observations:
                time_source = observation.evidence.get("time_point")
                date_source = observation.evidence.get("planned_date")
                if (time_source is None or date_source is None or time_source.row != evidence["row"]
                        or date_source.column != time_source.column):
                    continue
                value = observation.fields.get("planned_date", "").strip()
                if value and not value.startswith("=") and (day := header_date(value)):
                    planned_dates.setdefault(time_source.column, set()).add(day)
            metadata = tuple((header_date(label) or (
                                  next(iter(planned_dates[column])) if len(planned_dates.get(column, ())) == 1 else ""),
                              "已签报告" if "已签报告" in label else "已签" if "已签" in label else "")
                             for column, label in zip(block["time_columns"], labels, strict=True) if label)
            axes.add((axis, metadata, *(next(iter(values)) if len(values) == 1 else ""
                                       for values in (dates, stages, packaging))))
    result = {"metadata_sources": {field: [entries[key] for key in sorted(entries)] for field, entries in sources.items()}}
    for field, entries in sources.items():
        values = {entry["value"] for entry in entries.values()}
        result[field] = next(iter(values)) if len(values) == 1 else ""
    if len(axes) == 1:
        axis, metadata, start_date, material_stage, packaging = next(iter(axes))
        result.update(source_timepoints=list(axis), start_date=start_date, material_stage=material_stage, packaging=packaging,
                      point_metadata={label: {"planned_date": planned or None, "signed_status": signed}
                                      for label, (planned, signed) in zip(axis, metadata, strict=True)})
    return result


def metadata_preview(header):
    data = {key: header[key] for key in (*METADATA_FIELDS, "metadata_sources")}
    return {**data, "metadata_fingerprint": digest(data)}


def worksheet_metadata_preview(conn, project, identity):
    results = historical_results(conn, project, identity.batch)
    return metadata_preview(historical_header(conn, scope_result_entries(results, identity)))


def confirmed_metadata_sources(conn, project, identity, values, fingerprint):
    if not fingerprint:
        return {}
    preview = worksheet_metadata_preview(conn, project, identity)
    if preview["metadata_fingerprint"] != fingerprint:
        raise WorkflowError("worksheet_metadata_source_changed", "设备或地点来源已变化，请重新核对原件。", 409)
    return {field: preview["metadata_sources"][field] for field, value in values.items()
            if value and value == preview[field]}


def provision_from_results(conn, worksheets, project, version_ids, actor):
    """Generate plans from newly confirmed matrix results within the submission transaction."""
    from .identity_associations import adopted

    outcomes = []
    seen = set()
    for version_id in dict.fromkeys(version_ids):
        version = worksheets.result_versions._version(conn, version_id)
        record = StabilityRecord.model_validate_json(version["record_json"])
        if record.key.study not in {"long_term", "accelerated", "stress"}:
            continue
        if not any((source.get("raw_json") or {}).get("adapter") == "stability_matrix"
                   for source in json.loads(version["source_json"])):
            continue
        selected = adopted(conn, project, version["slot_id"])
        if selected is None or selected["id"] != version_id:
            continue
        identity = StudyIdentity.model_validate(record.key.model_dump(
            include=set(StudyIdentity.model_fields) - {"reporting_basis"}))
        signature = identity.model_dump_json()
        if signature in seen:
            continue
        seen.add(signature)
        slot_id = version["slot_id"]
        outcome = {"slot_id": slot_id, "batch": identity.batch, "study": identity.study,
                   "condition": identity.condition}
        encoded_identity = identity.model_dump_json(exclude={"sample_id"} if not identity.sample_id else set())
        existing = conn.execute("""SELECT id FROM business_worksheets
            WHERE project=? AND identity_json=? AND deleted_at IS NULL""", (project, encoded_identity)).fetchone()
        if existing:
            outcomes.append({**outcome, "status": "existing", "id": existing["id"]})
            continue
        try:
            seed = plan_seed(conn, worksheets, project, actor, source="results", batch=identity.batch,
                             slot_id=slot_id, source_id=None, table=None)
            missing = []
            if not seed.get("start_date"):
                missing.append("开始日期")
            if not identity.sample_id and not seed.get("material_stage"):
                missing.append("样品材料阶段")
            if missing:
                outcomes.append({**outcome, "status": "needs_input",
                                 "reason": "请核对并补齐" + "、".join(missing) + "。"})
                continue
            points = [{"unit": point["label"][-1], "amount": int(point["label"][:-1]),
                       "required_items": point["items"],
                       **seed.get("point_metadata", {}).get(point["label"], {})} for point in seed["points"]]
            if len(points) > 120 or len(seed["items"]) > 150:
                outcomes.append({**outcome, "status": "needs_input", "reason": "研究范围超过单表容量。"})
                continue
            command = CreateWorksheet(
                name=" · ".join((identity.batch, identity.study_id, identity.condition)),
                identity=identity, start_date=date.fromisoformat(seed["start_date"]),
                seed_slot_id=slot_id, sample_id=identity.sample_id,
                items=tuple(item["id"] for item in seed["items"]), points=points,
                material_stage=seed.get("material_stage", ""), packaging=seed.get("packaging", ""),
                basis="从已确认的历史 Excel 结果生成", idempotency_key="history-provision:" + digest([project, slot_id, version_id]),
            )
            conn.execute("SAVEPOINT provision_history")
            try:
                created = worksheets.create(project, command, actor, _conn=conn)
            except WorkflowError:
                conn.execute("ROLLBACK TO provision_history")
                raise
            finally:
                conn.execute("RELEASE provision_history")
            outcomes.append({**outcome, "status": "created", "id": created["id"]})
        except WorkflowError as exc:
            outcomes.append({**outcome, "status": "needs_input", "reason": str(exc)})
    return outcomes


def plan_seed(conn, worksheets, project, actor, *, source, batch, slot_id, source_id, table):
    project_config = project_row(conn, project)
    project = project_config["project"]
    plan = {"source": source, "identity": {"batch": batch}, "items": [],
            "points": [{"label": f"{month}M", "items": []} for month in DEFAULT_MONTHS],
            "item_standards": {}, "equipment": "", "location": ""}
    if source == "blank":
        return plan
    if source == "results":
        results = historical_results(conn, project, batch)
        key = next((record.key for version, record in results if version["slot_id"] == slot_id and record.key.study != "release"), None)
        if key is None:
            raise WorkflowError("worksheet_seed_missing", "找不到已采用的稳定性结果。", 404)
        identity = StudyIdentity.model_validate(key.model_dump(include=set(StudyIdentity.model_fields) - {"reporting_basis"}))
        results = scope_result_entries(results, identity)
        records = [record for _, record in results]
        points = {}
        for record in records:
            points.setdefault(record.key.time_label, set()).add(historical_row_key(record.key))
        header = historical_header(conn, sorted(results, key=lambda pair: pair[0]["slot_id"] != slot_id), slot_id)
        axis = header.get("source_timepoints", [])
        for label in axis:
            points.setdefault(label, set())
        # Preserve complete historical identities; aliases require explicit review.
        items_out = [{"id": key, **row} for key, row in historical_rows(records).items()]

        return {**plan, "identity": identity,
                "items": items_out,
                "points": [{"label": label, "items": sorted(points[label]),
                            **header.get("point_metadata", {}).get(label, {})} for label in
                           [*axis, *sorted(points.keys() - set(axis), key=lambda label: (label[-1], int(label[:-1])))]],
                **header, **metadata_preview(header)}
    if source == "copy":
        sheet = worksheets.get(source_id, actor, _conn=conn)
        if sheet["project"] != project:
            raise WorkflowError("worksheet_copy_source_changed", "来源表不属于当前项目。")
        rows = [{**row, "id": row.get("plan_key", row["item_id"])} for row in sheet["rows"]]
        points = [{"label": point["label"], "items": [row["id"] for row, original in zip(rows, sheet["rows"], strict=True)
                   if original["id"] in point["required_row_ids"]]} for point in sheet["points"]]
        return {**plan, "identity": {**sheet["identity"], "batch": batch, "sample_id": None}, "items": rows, "points": points,
                "equipment": sheet.get("equipment", ""), "location": sheet.get("location", ""),
                "copy": {"id": sheet["id"], "name": sheet["name"], "plan_version": sheet["plan_version"],
                         "has_standards": any(row.get("standards") for row in rows)}}
    if source == "standard":
        spec = conn.execute("SELECT filename,preview_json FROM specifications WHERE id=?", (source_id,)).fetchone()
        if spec is None:
            raise WorkflowError("specification_missing", "找不到质量标准。", 404)
        preview = json.loads(spec["preview_json"])
        if preview.get("project") and preview["project"] != project:
            raise WorkflowError("specification_scope", "此标准适用于其他项目。")
        links = {}
        for row in conn.execute("""SELECT item_id,table_number,row_number FROM specification_assay_components
                                  WHERE project=? AND specification_id=? ORDER BY table_number,row_number""",
                                (project, source_id)):
            links.setdefault(row["item_id"], []).append({"specification_id": source_id, "table": row["table_number"],
                                                          "row": row["row_number"]})
        items = [item.model_dump() for item in ProjectConfig.model_validate_json(project_config["config_json"]).items
                 if item.id in links]
        for point in plan["points"]:
            point["items"] = [item["id"] for item in items]
        scope = specification_material_scope(spec["filename"], preview)
        if scope in {"DS", "DP", "placebo"}:
            plan["identity"]["sample_type"] = scope
        elif scope == "reference":
            plan["identity"]["purpose"] = "reference"
        return {**plan, "specification_id": source_id, "items": items, "item_standards": links,
                "standard_candidate_count": len(preview.get("rows", []))}
    draft = conn.execute("SELECT filename,source,preview_json FROM protocol_drafts WHERE id=?", (source_id,)).fetchone()
    if draft is None:
        raise WorkflowError("protocol_source_missing", "找不到原方案。", 404)
    parsed = json.loads(draft["preview_json"])
    if "method_rows" not in parsed and draft["source"]:
        from .protocol_preview import inspect_protocol
        parsed = inspect_protocol(draft["source"])
    matrix = next((entry for entry in parsed["matrices"] if entry["table"] == table), None)
    if table and not matrix:
        raise WorkflowError("protocol_source_missing", "找不到选中的原方案表。", 404)
    batches = {entry["batch"] for entry in parsed["batches"] if entry["batch"]}
    identity = {"batch": batch or (next(iter(batches)) if len(batches) == 1 else "")}
    if matrix:
        plan["points"] = [{"label": f"{point['amount']}{point['unit']}", "items": []} for point in matrix["points"]]
        identity["study"] = matrix["study"]
        conditions = {entry["text"].strip() for entry in matrix["conditions"] if not entry.get("tracked_changes") and entry["text"].strip()}
        if len(conditions) == 1 and not matrix["warnings"]:
            identity["condition"] = next(iter(conditions))
    numbers = protocol_numbers(draft)
    study_type = matrix.get("study", "") if matrix else ""
    # 根据方案类型或标题、原件文本推断 scope
    req_scope = "unspecified"
    text_corpus = draft["filename"]
    if "source" in draft.keys() and draft["source"]:
        try:
            from docx import Document
            d_doc = Document(BytesIO(draft["source"]))
            text_corpus += " " + " ".join(p.text for p in d_doc.paragraphs[:10])
        except Exception:
            pass

    if re.search(r"DS\b|原液", text_corpus, re.I):
        req_scope = "DS"
    elif re.search(r"\bDP\b|\bInjection\b|制剂", text_corpus, re.I):
        req_scope = "DP"
    elif re.search(r"参考品|对照品", text_corpus, re.I):
        req_scope = "reference"

    found = matching_standards(conn, project, numbers, required_scope=req_scope)
    applicable_sources = []
    reference_status = []
    for number in numbers:
        sources = [entry for entry in found if entry["number"] == number]
        unambiguous = unambiguous_standard_sources(sources)
        if not sources:
            state = "missing"
        elif not unambiguous:
            state = "ambiguous"
        elif not any(source["links"] for source in sources):
            state = "unregistered"
        elif req_scope == "unspecified" or any(source["material_scope"] != req_scope for source in sources):
            state = "scope_review"
        else:
            state = "available"
            applicable_sources.extend(unambiguous)
        reference_status.append({"number": number, "state": state,
                                 "registered_links": sum(len(source["links"]) for source in sources)})

    # 1. source == "protocol"：方案建表自动展开子结果
    # 若方案引用的质量标准已入库，自动将方案行展开为标准中关联的多个子结果行，并自动继承应检标记
    expanded_items = []
    item_standards = {}
    point_items_map = {idx: [] for idx in range(len(plan["points"]))}
    release_references = {}

    # 获取当前项目的字典定义
    cfg = ProjectConfig.model_validate_json(project_config["config_json"])
    config_items = {it.id: it.model_dump() for it in cfg.items}

    # 收集已入库标准中的关联条目
    standard_links_map = {}  # norm_signature 或 label -> list of item_ids
    for s in found:
        spec_id = s["id"]
        for link in s["links"]:
            i_id = link["item_id"]
            standard_links_map.setdefault(spec_id, []).append((i_id, link))

    row_items = {}
    custom_items = []
    used_items = set()
    if req_scope in {"DS", "DP"}:
        identity["sample_type"] = req_scope
    elif req_scope == "reference":
        identity["purpose"] = "reference"

    if matrix:
        point_indices = {point["column"]: index for index, point in enumerate(matrix["points"])}
        for source_row in matrix["items"]:
            label = source_row["label"].strip()
            if not label:
                continue
            normalized = " ".join(label.casefold().split())
            candidates = [
                item for item in config_items.values()
                if any(" ".join(name.casefold().split()) == normalized
                       for name in (item["label"], *item["aliases"]))
            ]
            if len(candidates) == 1 and candidates[0]["id"] not in used_items:
                item = candidates[0]
            else:
                origin = f"{source_id}:{matrix['table']}:{source_row['row']}"
                item = {
                    "id": "local_" + hashlib.sha256(origin.encode()).hexdigest()[:24],
                    "label": label, "method": source_row.get("method_suggestion", ""), "unit": "",
                    "result_type": "numeric", "criteria_text": "",
                }
                custom_items.append(item)
            item_id = item["id"]
            used_items.add(item_id)
            expanded_items.append(item)
            row_items[source_row["row"]] = [item_id]
            source_row["suggested_component_ids"] = list(dict.fromkeys(
                link["item_id"] for standard in applicable_sources for link in standard["links"]
                if link["item_id"] in config_items
                and _matching_component(link, source_row.get("method_suggestion", ""), label)
            ))
            for standard in applicable_sources:
                for linked_id, link in standard_links_map.get(standard["id"], []):
                    if linked_id == item_id or linked_id in source_row["suggested_component_ids"]:
                        links = item_standards.setdefault(linked_id, [])
                        criterion = {
                            "specification_id": standard["id"], "table": link["table"],
                            "row": link["row"],
                        }
                        if criterion not in links:
                            links.append(criterion)
            for cell in source_row["cells"]:
                index = point_indices.get(cell["column"])
                if index is None:
                    continue
                if cell["state"] in {"required", "release_reference"}:
                    point_items_map[index].append(item_id)
                if cell["state"] == "release_reference":
                    release_references[item_id] = {"type": "release_reference", "raw": cell["raw"]}

        plan["items"] = expanded_items
        plan["item_standards"] = item_standards
        for index, point in enumerate(plan["points"]):
            point["items"] = point_items_map[index]

    return {**plan, "identity": identity, "matrix": matrix,
            "release_references": release_references,
            "protocol_row_items": row_items, "custom_items": custom_items,
            "protocol_standards": {"found": found, "references": reference_status,
                                   "missing": [entry["number"] for entry in reference_status if entry["state"] == "missing"]}}
