"""Full Excel matrices from the same selected result snapshot as the API."""

import hashlib
import json
from collections import defaultdict
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from .models import StabilityRecord
from .permissions import Capability, require
from .reporting import _put_text
from .result_queries import cells_from_rows
from .result_snapshot import create_snapshot
from .workflow_common import WorkflowError, check_revision, encoded, uid
from .workflow_contracts import ExportResult

PENDING_SUFFIX = "（待确认）"
PENDING_COLOR = "FFDC2626"
GROUP_FIELDS = ("project", "sample_type", "presentation", "study", "study_id", "condition", "purpose", "reporting_basis")
RECORD_HEADERS = (
    "slot_id", "project", "sample_type", "batch", "presentation", "study", "study_id", "condition", "month",
    "item", "method", "method_version", "display", "display_version", "display_state", "adopted", "adopted_version",
    "raw_result", "decimal_value", "comparator", "unit", "detection_id", "upload_sequence", "source_ids", "numeric_value", "purpose", "reporting_basis", "standards",
)


def text_row(sheet, values):
    row = sheet.max_row + 1 if sheet.cell(1, 1).value is not None else 1
    for col, value in enumerate(values, 1):
        _put_text(sheet.cell(row, col), "" if value is None else value)
    return row


def cell_text(cell, adopted=False):
    value = cell.adopted if adopted else cell.display
    return "" if value is None else value.text + (PENDING_SUFFIX if value.pending_confirmation else "")


def mark(cell, pending):
    cell.font = Font(name="Calibri", size=11, color=PENDING_COLOR if pending else "FF243746")


def write_workbook(path, snapshot):
    book = Workbook()
    info = book.active
    info.title = "Export"
    for key in ("project", "revision", "snapshot_id", "orientation", "filters"):
        text_row(info, (key, encoded(snapshot[key]) if isinstance(snapshot[key], dict) else snapshot[key]))
    text_row(info, ('selected_slot_ids', encoded(snapshot.get('selected_slot_ids', []))))
    text_row(info, ("说明", "红色（待确认）为已核对身份后提交的候选；正式采用值独立保留。未提交草稿不进入结果汇总。"))
    records = book.create_sheet("Records")
    text_row(records, RECORD_HEADERS)
    grouped = defaultdict(list)
    source_index = {}
    for entry in snapshot["entries"]:
        current = entry["display"]
        adopted = entry["adopted"]
        record = StabilityRecord.model_validate_json(current["record_json"])
        cell = cells_from_rows([(current, adopted)])[0]
        k = record.key
        result = record.result
        source_ids = []
        for version in (current, adopted):
            if version:
                for source in json.loads(version["source_json"]):
                    source_index[source["source_id"]] = source
                    if version is current:
                        source_ids.append(source["source_id"])
        values = [cell.slot_id, k.project, k.sample_type, k.batch, k.presentation, k.study, k.study_id, k.condition, k.time_label if k.day is not None else k.month,
                  k.item, k.method, k.method_version, cell_text(cell), current["id"], current["state"], cell_text(cell, True),
                  adopted["id"] if adopted else "", result.raw_text, str(result.value) if result.value is not None else "",
                  result.comparator or "", result.unit, current["detection_id"], current["upload_sequence"], ";".join(source_ids), "", k.purpose, k.reporting_basis, encoded([s.model_dump(mode="json") for s in record.standards])]
        ri = text_row(records, values)
        mark(records.cell(ri, 13), current["state"] == "pending")
        # Numeric convenience column only when conversion round-trips exactly; text/Decimal remain authoritative.
        if result.value is not None and result.comparator == "eq" and len(result.value.as_tuple().digits) <= 15:
            from decimal import Decimal
            numeric = float(result.value)
            if numeric not in (float("inf"), float("-inf")) and Decimal(str(numeric)) == result.value:
                records.cell(ri, 25).value = numeric
        grouped[tuple(getattr(k, f) for f in GROUP_FIELDS)].append((record, cell))
    for index, (group, entries) in enumerate(sorted(grouped.items()), 1):
        release = group[3] == "release"
        for adopted in (False, True):
            sheet = book.create_sheet(("Adopted" if adopted else "Display") + str(index))
            metadata = [(field,value) for field,value in zip(GROUP_FIELDS, group, strict=True) if (field != "purpose" or value != "unknown") and (field != "reporting_basis" or value)]
            for field,value in metadata:
                text_row(sheet, (field, value))
            header_row = len(metadata)+2
            layout = "release_items" if release and snapshot["orientation"] == "items" else "release_batches" if release else "stability"
            text_row(sheet, ("layout", layout))
            lookup = {(r.key.batch, r.key.item, r.key.method, r.key.method_version, r.result.unit, r.key.time_label): c for r, c in entries}
            items = sorted({(r.key.item, r.key.method, r.key.method_version, r.result.unit) for r, _ in entries})
            batches = sorted({r.key.batch for r, _ in entries})
            if layout == "release_items":
                text_row(sheet, ["item", "method", "method_version", "unit", *batches])
                for item in items:
                    ri = text_row(sheet, item)
                    for ci, batch in enumerate(batches, 5):
                        cell = lookup.get((batch, *item, ""))
                        if cell:
                            _put_text(sheet.cell(ri, ci), cell_text(cell, adopted))
                            mark(sheet.cell(ri, ci), not adopted and cell.display.pending_confirmation)
            elif layout == "release_batches":
                text_row(sheet, ["batch", *[encoded(list(item)) for item in items]])
                for batch in batches:
                    ri = text_row(sheet, [batch])
                    for ci, item in enumerate(items, 2):
                        cell = lookup.get((batch, *item, ""))
                        if cell:
                            _put_text(sheet.cell(ri, ci), cell_text(cell, adopted))
                            mark(sheet.cell(ri, ci), not adopted and cell.display.pending_confirmation)
            else:
                times = sorted({r.key.time_label for r, _ in entries}, key=lambda t: (int(t[:-1]) * (30 if t.endswith("M") else 1), t))
                text_row(sheet, ["batch", "item", "method", "method_version", "unit", *times])
                row_keys = sorted({(r.key.batch, r.key.item, r.key.method, r.key.method_version, r.result.unit) for r, _ in entries})
                for row_key in row_keys:
                    ri = text_row(sheet, row_key)
                    for ci, time in enumerate(times, 6):
                        cell = lookup.get((*row_key, time))
                        if cell:
                            _put_text(sheet.cell(ri, ci), cell_text(cell, adopted))
                            mark(sheet.cell(ri, ci), not adopted and cell.display.pending_confirmation)
            sheet.freeze_panes = f"F{header_row+1}" if layout == "stability" else f"E{header_row+1}" if layout == "release_items" else f"B{header_row+1}"
    sources = book.create_sheet("Sources")
    text_row(sources, ["source_id", "filename", "sha256", "location", "raw_fields", "corrections", "identity_association"])
    entries = [source for source in source_index.values() if source.get("kind") == "worksheet_entry"]
    if entries:
        direct = book.create_sheet("WorksheetEntries")
        text_row(direct, ["source_id", "worksheet_id", "row_id", "point_id", "plan_version", "raw_text", "unit", "result_type"])
        for source in sorted(entries, key=lambda item: item["source_id"]):
            text_row(direct, [source[key] for key in
                              ("source_id", "worksheet_id", "row_id", "point_id", "plan_version", "raw_text", "unit", "result_type")])
    for sid, source in sorted(source_index.items()):
        if source.get("kind") == "worksheet_entry":
            continue
        text_row(sources, [sid, source["filename"], source["document_hash"], encoded(source["raw_json"]["location"]),
                           encoded(source["raw_json"]["fields"]), encoded(source.get("corrections", {})),encoded(source.get('identity_association',{}))])
    drafts = book.create_sheet("Unsubmitted")
    links = book.create_sheet('IdentityLinks')
    text_row(links,['version_id','physical_slot_id','logical_slot_id','event_sequence','actor_id','reason','original_key','logical_key'])
    seen_links = set()
    for entry in snapshot['entries']:
        for version in (entry['display'],entry['adopted']):
            if version and version.get('identity_association') and version['id'] not in seen_links:
                seen_links.add(version['id'])
                link = version['identity_association']
                text_row(links,[version['id'],version['physical_slot_id'],version['slot_id'],link['event_sequence'],
                    link['actor_id'],link['reason'],encoded(json.loads(version['original_record_json'])['key']),encoded(link['key'])])
    text_row(drafts, ["candidate_id", "filename", "scope", "original", "corrections"])
    for draft in snapshot["drafts"]:
        text_row(drafts, [draft["id"], draft["filename"], '无法确定是否属于筛选范围：' + ','.join(draft.get('unknown_dimensions',[])) if draft.get('scope') == 'unassigned' else '本次范围内未提交草稿；不计入正式结果',
                         draft["raw_json"], draft["corrections_json"]])
    status = book.create_sheet('SourceStatus')
    text_row(status,['upload_id','filename','section','scope','unresolved_regions'])
    for sheet in snapshot.get('pending_source_regions',[]):
        text_row(status,[sheet['upload_id'],sheet['filename'],sheet['section'],
            '未知区域无法判定是否属于本次筛选，不计入正式结果',encoded(sheet['regions'])])
    for sheet in book:
        header = next(r[0].row+1 for r in sheet if r[0].value == "layout") if sheet.title.startswith(("Display", "Adopted")) else 1
        for row in sheet:
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        for cell in sheet[header]:
            cell.fill = PatternFill("solid", fgColor="163C4D")
            cell.font = Font(name="Calibri", bold=True, color="FFFFFFFF")
        for col in sheet.columns:
            sheet.column_dimensions[col[0].column_letter].width = 22
        if sheet.freeze_panes is None:
            sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = f"A{header}:{sheet.cell(sheet.max_row,sheet.max_column).coordinate}"
    book.save(path)
    book.close()


class ExcelExports:
    def __init__(self, workflow):
        self.workflow = workflow
        self.store = workflow.store

    def create(self, request, *, actor):
        require(actor, Capability.EXPORT)
        with self.store.connect() as conn:
            conn.execute("BEGIN")
            check_revision(conn, request.project, request.base_revision)
            snapshot = create_snapshot(self.workflow,conn,request)
            snapshot['axis'] = {k:getattr(request,k) for k in ('axis_mode','axis_min','axis_max')}
            from .models import digest
            snapshot['snapshot_id'] = digest({k:v for k,v in snapshot.items() if k!='snapshot_id'})
            if not snapshot['entries']:
                raise WorkflowError("empty_export", "当前范围没有可汇总结果。")
        export_id = uid("export")
        folder = self.store.home / "workflow-exports" / export_id
        folder.mkdir(parents=True)
        path = folder / "results.xlsx"
        write_workbook(path, snapshot)
        from .workflow_excel_verify import verify_workbook
        errors = verify_workbook(path, snapshot)
        (folder / "snapshot.json").write_text(encoded(snapshot), encoding="utf-8")
        (folder / "verification.json").write_text(encoded({"passed": not errors, "errors": errors}), encoding="utf-8")
        if errors:
            raise WorkflowError("verification_failed", "导出反读失败：" + "; ".join(errors), 409)
        if request.include_png:
            from .trend_export import write_png
            write_png(folder / 'trend.png',snapshot)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            check_revision(conn, request.project, request.base_revision)
            conn.execute("INSERT INTO workflow_exports(id,project,revision,path,sha256,snapshot_json,created_by) VALUES (?,?,?,?,?,?,?)",
                         (export_id, request.project, request.base_revision, str(path), sha, encoded(snapshot), actor.id))
        return ExportResult(export_id=export_id, project=request.project, base_revision=request.base_revision,
                            image_download_url=f'/api/v1/exports/{export_id}/image' if request.include_png else None,
                            filename="results.xlsx", download_url=f"/api/v1/exports/{export_id}/file")

    def download(self, export_id, *, actor):
        require(actor, Capability.EXPORT)
        with self.store.connect() as conn:
            row = conn.execute("SELECT * FROM workflow_exports WHERE id=?", (export_id,)).fetchone()
        if row is None:
            raise WorkflowError("not_found", "导出记录不存在或未通过验证。", 404)
        path = Path(row["path"])
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
            raise WorkflowError("export_changed", "导出文件已损坏或发生变化。", 409)
        return path

    def download_image(self,export_id,*,actor):
        workbook = self.download(export_id,actor=actor)
        path = workbook.parent / 'trend.png'
        manifest_path = path.with_suffix('.json')
        if not path.is_file() or not manifest_path.is_file():
            raise WorkflowError('image_unavailable','该导出没有PNG。',404)
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        snapshot = json.loads((workbook.parent / 'snapshot.json').read_text(encoding='utf-8'))
        if manifest['snapshot_id'] != snapshot['snapshot_id'] or hashlib.sha256(path.read_bytes()).hexdigest() != manifest['sha256']:
            raise WorkflowError('export_changed','PNG或范围证据已变化。',409)
        return path
