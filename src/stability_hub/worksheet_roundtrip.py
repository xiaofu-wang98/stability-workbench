"""Round-trip a single research scope through the normal worksheet result writer."""

import hashlib
import json
from io import BytesIO
from pathlib import Path
from typing import Annotated, Literal
from zipfile import BadZipFile

from fastapi import APIRouter, File, Form, UploadFile
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.utils.exceptions import InvalidFileException
from lxml.etree import XMLSyntaxError
from pydantic import Field

from .business_worksheets import CellChange, SaveWorksheet, WorksheetMutation
from .models import Model, digest
from .permissions import Capability, require
from .stability_summary import (TEMPLATE_VERSION, TITLE, controlled_block, freeze_worksheet_table, visible_rows,
                                write_controlled_workbook, write_summary)
from .workflow_common import WorkflowError, encoded, project_row, remember, replay, uid


class WorkbookCommit(WorksheetMutation):
    fingerprint: str
    reason: str = Field(max_length=2000)
    decisions: dict[str, Literal["keep", "correct", "retest"]] = Field(default_factory=dict, max_length=5000)
    coordinates: tuple[str, ...] | None = Field(default=None, min_length=1, max_length=5000)


class ControlledWorkbook(Model):
    base_revision: int = Field(ge=0)
    sheet_ids: tuple[str, ...] = Field(min_length=1, max_length=120)


def identify_book(book):
    if "_stability_study" not in book.sheetnames:
        return None
    meta = book["_stability_study"]
    if meta["A1"].value != TEMPLATE_VERSION or not isinstance(meta["A2"].value, str):
        raise WorkflowError("worksheet_file_version", "无法识别稳定性研究表模板版本。")
    return meta["A2"].value


def open_book(data, *, data_only=False):
    try:
        return load_workbook(BytesIO(data), data_only=data_only, read_only=False, keep_links=False)
    except (BadZipFile, InvalidFileException, KeyError, ValueError, XMLSyntaxError) as exc:
        raise WorkflowError("worksheet_file_invalid", "无法读取 Excel 研究表。") from exc


def read_values(data, book, summary):
    if TITLE not in book:
        raise WorkflowError("worksheet_file_structure", "找不到稳定性研究表工作页。")
    sheet = book[TITLE]
    expected = visible_rows(summary)
    width = max(8, len(summary["points"]) + 2)
    if (sheet.max_row > len(expected) or sheet.max_column > width
            or sheet.max_row < len(expected) or sheet.max_column < len(summary["points"]) + 2):
        raise WorkflowError("worksheet_file_structure", "工作表行列数量已变化，请核对检项和月份。", 409)
    cached = None
    try:
        values = []
        for r, row in enumerate(expected, 1):
            for c in range(1, width + 1):
                cell = sheet.cell(r, c)
                if r <= 5 or c <= 2 or c > len(summary["points"]) + 2:
                    text = row[c - 1] if c <= len(row) else ""
                    if cell.data_type == "f" or (cell.value if cell.value is not None else "") != text:
                        raise WorkflowError("worksheet_file_structure",
                                            f"研究表结构已变化，请核对 {cell.coordinate} 的表头、检项、标准或月份。", 409)
                    continue
                value = cell.value
                if cell.data_type == "f":
                    if cached is None:
                        cached = open_book(data, data_only=True)
                    cached_cell = cached[TITLE][cell.coordinate]
                    value = cached_cell.value
                    if value is None or cached_cell.data_type == "e":
                        raise WorkflowError("worksheet_file_formula", f"{cell.coordinate} 缺少可用计算值，请用 Excel 计算保存或填入结果。")
                if value is None or (isinstance(value, str) and not value.strip()):
                    continue
                if cell.data_type == "e" or isinstance(value, bool) or not isinstance(value, (str, int, float)):
                    raise WorkflowError("worksheet_file_value", f"请核对 {cell.coordinate} 的放行或检测值格式。")
                if isinstance(value, (int, float)) and "%" in cell.number_format:
                    from decimal import Decimal
                    value = format(Decimal(str(value)) * 100, "f") + "%"
                values.append((r - 6, c - 3, str(value).strip()))
        return values
    finally:
        if cached:
            cached.close()


def invalidated_positions(conn, sheet_id):
    positions = {}
    for row in conn.execute("""
        SELECT p.row_id,p.point_id,i.version_id
        FROM worksheet_result_positions p JOIN workflow_invalidated_versions i ON i.slot_id=p.slot_id
        WHERE p.sheet_id=? ORDER BY i.version_id
    """, (sheet_id,)):
        positions.setdefault((row["row_id"], row["point_id"]), []).append(row["version_id"])
    return positions


def diff_values(sheet, summary, values, *, verified_baseline, invalidated):
    rows = {row["id"]: row for row in sheet["rows"]}
    points = {point["id"]: point for point in sheet["points"]}
    cells = {(cell["row_id"], cell["point_id"]): cell for cell in sheet["cells"]}
    changes = []
    for r, c, value in values:
        row = summary["contract"]["rows"][r]
        position = row["positions"][c]
        target = (row["row_id"], position["point_id"])
        cell = cells.get(target, {})
        adopted = (cell.get("adopted") or {}).get("result_version_id")
        latest = (cell.get("display") or {}).get("result_version_id") if not cell.get("release_reference") else None
        voided_since_export = invalidated.get(target, []) != position.get("invalidated", [])
        if (verified_baseline and value == position["visible"]) or (adopted and value == cell.get("raw")):
            status = "unchanged"
        elif (not position["required"] or cell.get("release_reference") or (latest and not adopted)
              or (points[target[1]]["label"] == "0M" and (
                  target[0] in sheet.get("pending_release_references", {})
                  or target[0] in sheet.get("invalidated_release_rows", ())
              ))):
            status = "blocked"
        elif (not verified_baseline and latest or adopted != position["adopted"] or latest != position["latest"]
              or voided_since_export):
            status = "conflict"
        else:
            status = "correction" if adopted else "new"
        changes.append({"coordinate": f"{get_column_letter(c + 3)}{r + 6}", "row_id": target[0], "point_id": target[1],
                        "item": rows[target[0]]["label"], "timepoint": points[target[1]]["label"],
                        "current": cell.get("raw", ""), "value": value, "status": status,
                        "expected_version_id": adopted, "latest_version_id": latest,
                        **({"voided_since_export": True} if status == "conflict" and voided_since_export else {})})
    return changes


def find_visible_snapshot(conn, data, book, expected_sheet):
    """Recover an unambiguous research layout and result baseline."""
    matches = []
    exports = conn.execute("""SELECT id,snapshot_json FROM workflow_exports
                              WHERE json_extract(snapshot_json,'$.stability_summary.contract.version')=?
                              ORDER BY revision,created_at,id""", (TEMPLATE_VERSION,))
    for export in exports:
        summary = json.loads(export["snapshot_json"])["stability_summary"]
        if expected_sheet and summary["contract"]["sheet_id"] != expected_sheet:
            continue
        try:
            values = read_values(data, book, summary)
        except WorkflowError as exc:
            if exc.code == "worksheet_file_structure":
                continue
            raise
        matches.append((export["id"], summary, values))
    scopes = {(summary["contract"]["sheet_id"], summary["contract"]["plan_version"]) for _, summary, _ in matches}
    if not matches:
        raise WorkflowError("worksheet_file_missing", "模板信息缺失，可见表头及检项未匹配到已下载的研究表。")
    if len(scopes) != 1:
        raise WorkflowError("worksheet_file_ambiguous", "模板信息缺失，表头匹配多个研究或计划版本，请从目标研究打开或重新下载。", 409)
    baselines = {
        digest([row["positions"] for row in summary["contract"]["rows"]])
        for _, summary, _ in matches
    }
    if len(baselines) != 1:
        raise WorkflowError("worksheet_file_ambiguous",
                            "模板信息缺失，同一研究有多个结果基线。请使用保留模板信息的原文件，或重新下载后填写。", 409)
    return matches[0]


def router_for(store, worksheets, actor_dependency):
    router = APIRouter(prefix="/api/v1")

    @router.post("/worksheets/{sheet_id}/study-workbook")
    def export_study(sheet_id: str, operator: actor_dependency, filled: bool = False):
        require(operator, Capability.EXPORT)
        export_id = uid("export")
        with store.read() as conn:
            sheet = worksheets.get(sheet_id, operator, _conn=conn)
            project = project_row(conn, sheet["project"])
            summary = freeze_worksheet_table(sheet, json.loads(project["config_json"]).get("name", ""), filled=filled,
                                             invalidated_positions=invalidated_positions(conn, sheet_id))
            summary["export_id"] = export_id
            revision = project["revision"]
        snapshot = {"stability_summary": summary, "project": sheet["project"], "revision": revision,
                    "format": "worksheet_summary" if filled else "worksheet_template"}
        snapshot["snapshot_id"] = digest(snapshot)
        folder = store.home / "workflow-exports" / export_id
        folder.mkdir(parents=True)
        path = folder / ("stability-summary.xlsx" if filled else "stability-template.xlsx")
        errors = write_summary(path, summary)
        if errors:
            raise WorkflowError("verification_failed", "研究表导出反读失败。", 409)
        (folder / "snapshot.json").write_text(encoded(snapshot), encoding="utf-8")
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        with store.connect() as conn:
            conn.execute("INSERT INTO workflow_exports(id,project,revision,path,sha256,snapshot_json,created_by) VALUES (?,?,?,?,?,?,?)",
                         (export_id, sheet["project"], revision, str(path), sha, encoded(snapshot), operator.id))
        return {"download_url": "/api/v1/exports/" + export_id + "/file"}

    @router.post("/projects/{project}/controlled-workbook")
    def export_controlled(project: str, command: ControlledWorkbook, operator: actor_dependency):
        require(operator, Capability.EXPORT)
        if len(set(command.sheet_ids)) != len(command.sheet_ids):
            raise WorkflowError("controlled_scope", "请选择不重复的研究表。")
        with store.read() as conn:
            config = project_row(conn, project)
            if config["revision"] != command.base_revision:
                raise WorkflowError("controlled_scope_changed", "项目数据已更新，请重新预览导出范围。", 409)
            name = json.loads(config["config_json"]).get("name", "")
            groups = {}
            for sheet_id in command.sheet_ids:
                sheet = worksheets.get(sheet_id, operator, _conn=conn)
                if sheet["project"] != config["project"]:
                    raise WorkflowError("controlled_scope", "所选研究表不属于当前项目。")
                identity = sheet["identity"]
                condition = controlled_block(sheet, name)
                key = (identity["sample_type"], identity["purpose"], condition["condition"])
                group = groups.setdefault(key, {"sample_type": identity["sample_type"], "condition": key[2], "blocks": []})
                group["blocks"].append(condition)
            project = config["project"]
        export_id = uid("export")
        snapshot = {"project": project, "revision": command.base_revision, "format": "controlled_workbook",
                    "worksheet_ids": command.sheet_ids, "groups": list(groups.values())}
        snapshot["snapshot_id"] = digest(snapshot)
        folder = store.home / "workflow-exports" / export_id
        folder.mkdir(parents=True)
        path = folder / "controlled-workbook.xlsx"
        if write_controlled_workbook(path, snapshot["groups"]):
            raise WorkflowError("verification_failed", "受控汇总表反读失败。", 409)
        (folder / "snapshot.json").write_text(encoded(snapshot), encoding="utf-8")
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        with store.connect() as conn:
            conn.execute("INSERT INTO workflow_exports(id,project,revision,path,sha256,snapshot_json,created_by) VALUES (?,?,?,?,?,?,?)",
                         (export_id, project, command.base_revision, str(path), sha, encoded(snapshot), operator.id))
        return {"download_url": "/api/v1/exports/" + export_id + "/file"}

    def inspect(conn, data, actor, expected_sheet=None):
        book = open_book(data)
        try:
            export_id = identify_book(book)
            verified_baseline = bool(export_id)
            if not export_id:
                export_id, summary, values = find_visible_snapshot(conn, data, book, expected_sheet)
                contract = summary["contract"]
            else:
                export = conn.execute("SELECT snapshot_json FROM workflow_exports WHERE id=?", (export_id,)).fetchone()
                summary = json.loads(export["snapshot_json"]).get("stability_summary", {}) if export else {}
                contract = summary.get("contract")
                if not contract:
                    raise WorkflowError("worksheet_file_snapshot", "本库找不到该研究表的导出快照。", 404)
                meta = book["_stability_study"]
                if meta["A3"].value != contract["sheet_id"] or meta["A4"].value != str(contract["plan_version"]):
                    raise WorkflowError("worksheet_file_scope", "上传文件的模板信息与导出快照不一致。", 409)
                values = read_values(data, book, summary)
            sheet_id = contract["sheet_id"]
            if expected_sheet and expected_sheet != sheet_id:
                raise WorkflowError("worksheet_file_scope", "上传文件与所选研究范围不一致。", 409)
            sheet = worksheets.get(sheet_id, actor, _conn=conn)
            if sheet["plan_version"] != contract["plan_version"]:
                raise WorkflowError("worksheet_file_plan", "研究计划已更新，请下载当前版本后核对结构。", 409)
            changes = diff_values(sheet, summary, values, verified_baseline=verified_baseline,
                                  invalidated=invalidated_positions(conn, sheet_id))
            return {"sheet_id": sheet_id, "name": sheet["name"], "revision": sheet["revision"], "changes": changes,
                    "fingerprint": digest([export_id, hashlib.sha256(data).hexdigest(), sheet["revision"], changes])}
        finally:
            book.close()

    @router.post("/study-workbooks/preview")
    def preview(operator: actor_dependency, file: Annotated[UploadFile, File()], sheet_id: Annotated[str, Form()] = ""):
        require(operator, Capability.PREVIEW)
        data = file.file.read(20 * 1024 * 1024 + 1)
        filename = Path(file.filename or "").name
        if len(data) > 20 * 1024 * 1024 or Path(filename).suffix.lower() != ".xlsx":
            raise WorkflowError("worksheet_file_invalid", "请选择不超过 20 MB 的 Excel 研究表。")
        with store.read() as conn:
            result = inspect(conn, data, operator, expected_sheet=sheet_id)
        source_id = hashlib.sha256(data).hexdigest()
        with store.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO worksheet_return_sources VALUES (?,?,?)", (source_id, filename, data))
        return {**result, "source_id": source_id}

    @router.post("/worksheets/{sheet_id}/study-workbooks/{source_id}")
    def commit(sheet_id: str, source_id: str, command: WorkbookCommit, operator: actor_dependency):
        require(operator, Capability.CONFIRM_FACT)
        with store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            sheet = worksheets._sheet(conn, sheet_id)
            excluded = {"token"} | ({"coordinates"} if command.coordinates is None else set())
            payload = {"worksheet_return": [sheet_id, source_id], **command.model_dump(mode="json", exclude=excluded)}
            if (prior := replay(conn, sheet["project"], operator, command.idempotency_key, payload,
                                legacy_payload={**payload, "token": command.token})) is not None:
                return prior
            worksheets._check_mutation(conn, sheet, command, operator)
            source = conn.execute("SELECT * FROM worksheet_return_sources WHERE id=?", (source_id,)).fetchone()
            if source is None or hashlib.sha256(source["source"]).hexdigest() != source_id:
                raise WorkflowError("worksheet_file_source", "找不到回传原件或原件校验失败。", 404)
            view = inspect(conn, source["source"], operator, expected_sheet=sheet_id)
            if view["fingerprint"] != command.fingerprint:
                raise WorkflowError("worksheet_file_changed", "结果已变化，请重新预览回传差异。", 409)
            changes, provenance = [], {}
            available = {cell["coordinate"] for cell in view["changes"]}
            selected = set(command.coordinates) if command.coordinates is not None else available
            if (not selected <= available or not set(command.decisions) <= selected
                    or (command.coordinates is not None and len(selected) != len(command.coordinates))):
                raise WorkflowError("worksheet_file_scope", "提交范围与当前回传预览不一致。", 409)
            for cell in view["changes"]:
                if cell["coordinate"] not in selected:
                    continue
                status = cell["status"]
                choice = command.decisions.get(cell["coordinate"])
                if status == "blocked" or (status == "conflict" and not choice):
                    raise WorkflowError("worksheet_file_conflict", f"请先核对 {cell['coordinate']} 的回传冲突。", 409)
                if status == "unchanged" or (status in {"conflict", "correction"} and choice == "keep"):
                    continue
                if cell.get("voided_since_export"):
                    raise WorkflowError("worksheet_file_voided", f"{cell['coordinate']} 在导出后已作废，请保留当前状态或重新下载模板。", 409)
                if status == "conflict" and not command.reason.strip():
                    raise WorkflowError("worksheet_file_conflict", f"请填写提交 {cell['coordinate']} 冲突值的原因。", 409)
                intent = "correction" if cell["expected_version_id"] else "initial"
                expected = cell["expected_version_id"]
                if choice == "retest":
                    if not expected or not command.reason.strip():
                        raise WorkflowError("worksheet_file_retest", "复测需有已采用的原结果并填写说明。", 409)
                    intent, expected = "retest", cell["latest_version_id"]
                if expected:
                    require(operator, Capability.CORRECT_RESULT)
                changes.append(CellChange(row_id=cell["row_id"], point_id=cell["point_id"], value=cell["value"],
                                          expected_version_id=expected, intent=intent))
                provenance[cell["row_id"], cell["point_id"]] = {
                    "kind": "worksheet_return", "archive_id": source_id, "filename": source["filename"],
                    "section": TITLE, "coordinate": cell["coordinate"],
                }
            result = {"id": sheet_id, "revision": sheet["revision"], "result_version_ids": []}
            if changes:
                result = worksheets.save(sheet_id, SaveWorksheet(token=command.token, revision=command.revision,
                                         reason=command.reason, changes=tuple(changes), idempotency_key=uid("return_write")),
                                         operator, _conn=conn, file_source=provenance)
            result["resolved_coordinates"] = sorted(selected)
            result["remaining_coordinates"] = sorted(
                cell["coordinate"] for cell in view["changes"]
                if cell["coordinate"] not in selected and cell["status"] != "unchanged")
            remember(conn, sheet["project"], operator, command.idempotency_key, payload, result)
            return result

    return router
