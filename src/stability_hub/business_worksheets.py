"""Plan, project and save stability worksheets with server-side editing leases."""

import calendar
import json
import secrets
import time
from contextlib import nullcontext
from datetime import date, timedelta
from typing import Literal

from pydantic import Field

from .business_notes import frozen_block_events
from .identity_associations import adopted, adoption_choices, current, invalidation, versions
from .identity_links import linked_config
from .matrix_fields import selected_condition
from .models import ItemDefinition, Model, ProjectConfig, RecordKey, StabilityRecord, Text, digest
from .normalize import NeedsReview, normalize_worksheet_result
from .permissions import Capability, require
from .result_queries import result_value, selected_rows
from .specification_limits import worksheet_assessment
from .workflow_common import WorkflowError, bump, encoded, ensure_worksheet_slot_unlocked, event, project_row, remember, replay, uid
from .workflow_contracts import ResultFilter

LEASE_SECONDS = 90


HEADER_METADATA_FIELDS = ("product_name", "equipment", "protocol_number", "specification_text", "location", "storage_location")
ADDITIONAL_HEADER_FIELDS = ("product_name", "protocol_number", "specification_text", "storage_location")


def adopted_scope(conn, project, batch):
    return [(value, StabilityRecord.model_validate_json(value['record_json']))
            for _, value in selected_rows(conn, project, ResultFilter(batch=batch)) if value]


def scope_result_entries(entries, identity):
    fields = {**identity.model_dump(exclude={'orientation', 'reporting_basis'}), 'condition': identity.record_condition()}
    return [(source, record) for source, record in entries
            if all(getattr(record.key, field) == value for field, value in fields.items())]


def scope_records(records, identity):
    return [record for _, record in scope_result_entries(records, identity)]


def historical_row_key(key):
    return 'history_' + digest([key.item, key.method, key.method_version, key.reporting_basis])


def historical_raw_sources(version):
    for source in json.loads(version["source_json"]):
        if raw := source.get("raw_json"):
            yield raw


def original_test_items(sources):
    return (text for raw in sources if (text := raw.get("fields", {}).get("test_item", "").strip()))


def historical_rows(entries):
    grouped = {}
    for version, record in entries:
        grouped.setdefault(historical_row_key(record.key), []).append((version, record))
    rows = {}
    for identifier, entries in grouped.items():
        group = [record for _, record in entries]
        key = group[0].key
        units = {record.result.unit for record in group if record.result.unit}
        if len(units) > 1:
            raise WorkflowError('worksheet_unit_conflict', f'检项 {key.item} 的结果单位不一致，请先核对来源。')
        definition = ItemDefinition(id=identifier, label=key.item, method=key.method, method_version=key.method_version,
                                    unit=next(iter(units), ''), result_type='numeric' if any(
                                        record.result.status == 'numeric' for record in group) else 'qualitative')
        rows[identifier] = {**definition.model_dump(exclude={'id', 'aliases'}), 'item_id': key.item,
                            'plan_key': identifier, 'reporting_basis': key.reporting_basis}
        criteria = sorted({standard.criterion_text for record in group for standard in record.standards
                           if standard.criterion_text.strip()})
        if criteria:
            rows[identifier]["source_criteria"] = criteria
        labels = list(dict.fromkeys(text for version, _ in entries
                                    for text in original_test_items(historical_raw_sources(version))))
        if labels:
            rows[identifier]["source_test_item"] = labels
    return rows


def historical_items(records):
    grouped = {}
    for record in records:
        grouped.setdefault(record.key.item, []).append(record)
    items = {}
    for item, group in grouped.items():
        methods = {(row.key.method, row.key.method_version) for row in group}
        units = {row.result.unit for row in group if row.result.unit}
        if len(methods) != 1 or len(units) > 1:
            continue
        method, method_version = methods.pop()
        items[item] = ItemDefinition(id=item, label=item, method=method, method_version=method_version,
                                     unit=next(iter(units), ''), result_type='numeric' if any(
                                         row.result.status == 'numeric' for row in group) else 'qualitative')
    return items


def backfill_worksheet_positions(conn):
    """Link existing slots only when the saved plan reproduces the same slot ID."""
    sheets = conn.execute("""SELECT w.*,p.plan_json FROM business_worksheets w
                             JOIN business_worksheet_plans p ON p.sheet_id=w.id AND p.version=w.plan_version""")
    for sheet in sheets:
        identity = StudyIdentity.model_validate_json(sheet["identity_json"])
        plan = json.loads(sheet["plan_json"])
        for row in plan["rows"]:
            for point in plan["points"]:
                slot_id = BusinessWorksheets._key(sheet, identity, row, point).slot_id
                registered = conn.execute("SELECT sheet_id FROM business_worksheet_slots WHERE slot_id=?",
                                          (slot_id,)).fetchone()
                position = (sheet["id"], row["id"], point["id"], slot_id)
                if (registered and registered["sheet_id"] == sheet["id"]
                        and not conn.execute("SELECT 1 FROM worksheet_result_positions WHERE slot_id=?", (slot_id,)).fetchone()):
                    conn.execute("INSERT INTO worksheet_result_positions VALUES (?,?,?,?)", position)
                else:
                    conn.execute("INSERT INTO worksheet_position_conflicts VALUES (?,?,?,?,?)",
                                 (*position, registered["sheet_id"] if registered else None))


class StudyIdentity(Model):
    sample_id: Text | None = None
    sample_type: Literal["DS", "DP", "placebo"]
    purpose: Literal["unknown", "routine", "reference"] = "unknown"
    reporting_basis: str = ""
    batch: Text
    presentation: Text
    study: Literal["long_term", "accelerated", "stress"]
    study_id: Text
    condition: Text
    orientation: Literal["", "正置", "倒置", "平放"] = ""

    def record_condition(self):
        if not self.orientation:
            return self.condition
        condition = selected_condition(f"{self.condition} · {self.orientation}")
        placements = set(condition.split(" · ")) & {"正置", "倒置", "平放"}
        if placements != {self.orientation}:
            raise WorkflowError("worksheet_orientation_identity", "放置方向须与可识别的温度条件一致，例如 25℃ 和正置。")
        return condition


SignedStatus = Literal["", "已签", "已签报告"]


class PlannedPoint(Model):
    unit: Literal["M", "D"]
    amount: int = Field(ge=0, le=3650, strict=True)
    planned_date: date | None = None
    signed_status: SignedStatus = ""
    required_items: tuple[Text, ...] = Field(max_length=150)


class SpecificationCriterion(Model):
    specification_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    table: int = Field(ge=0)
    row: int = Field(ge=1)
    lower: float | None = Field(default=None, allow_inf_nan=False)
    upper: float | None = Field(default=None, allow_inf_nan=False)


class CustomWorksheetItem(ItemDefinition):
    specification_links: tuple[SpecificationCriterion, ...] = Field(default=(), max_length=50)
    criteria_text: str = Field(default="", max_length=4000)


class WorksheetCriteria(Model):
    specification_links: tuple[SpecificationCriterion, ...] = Field(default=(), max_length=50)
    criteria_text: str = Field(default="", max_length=4000)


class CreateWorksheet(Model):
    custom_items: tuple[CustomWorksheetItem, ...] = Field(default=(), max_length=150)
    equipment: str = Field(default="", max_length=500)
    location: str = Field(default="", max_length=500)
    product_name: str = Field(default="", max_length=500)
    protocol_number: str = Field(default="", max_length=500)
    specification_text: str = Field(default="", max_length=500)
    storage_location: str = Field(default="", max_length=500)
    idempotency_key: Text
    name: Text
    identity: StudyIdentity
    start_date: date
    basis: str = ""
    items: tuple[Text, ...] = Field(min_length=1, max_length=150)
    points: tuple[PlannedPoint, ...] = Field(min_length=1, max_length=120)
    protocol_draft_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    protocol_table: int | None = Field(default=None, ge=1)
    copy_source_id: Text | None = None
    copy_source_plan_version: int | None = Field(default=None, ge=1)
    copy_standards: bool = False
    worksheet_draft_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    seed_slot_id: Text | None = None
    metadata_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    sample_id: Text | None = None
    material_stage: str = Field(default="", max_length=250)
    packaging: str = Field(default="", max_length=250)
    item_standards: dict[str, tuple[SpecificationCriterion, ...]] = Field(default_factory=dict, max_length=150)
    row_criteria: dict[str, WorksheetCriteria] = Field(default_factory=dict, max_length=150)
    protocol_row_items: dict[int, tuple[Text, ...]] = Field(default_factory=dict, max_length=150)


class LeaseToken(Model):
    token: Text


class WorksheetMutation(LeaseToken):
    idempotency_key: Text
    revision: int = Field(ge=1, strict=True)


class EditWorksheetMetadata(WorksheetMutation):
    equipment: str = Field(max_length=500)
    location: str = Field(max_length=500)
    product_name: str | None = Field(default=None, max_length=500)
    protocol_number: str | None = Field(default=None, max_length=500)
    specification_text: str | None = Field(default=None, max_length=500)
    storage_location: str | None = Field(default=None, max_length=500)
    basis: Text
    metadata_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class EditPointMetadata(WorksheetMutation):
    point_id: Text
    planned_date: date
    signed_status: SignedStatus = ""


class ExtendPlan(WorksheetMutation):
    basis: Text
    points: tuple[PlannedPoint, ...] = Field(min_length=1, max_length=120)


class IncludeHistoricalRows(WorksheetMutation):
    basis: Text


class ConfirmProtocolStandards(WorksheetMutation):
    basis: Text
    fingerprint: Text


class LinkWorksheetSample(WorksheetMutation):
    sample_id: Text
    basis: Text


def sample_matches_study(sample, identity):
    return (all(sample[field] == getattr(identity, field) for field in ("batch", "sample_type", "presentation"))
            and (sample["material_stage"] == "参考品") == (identity.purpose == "reference"))




class SetWorksheetStandards(WorksheetMutation):
    row_id: Text
    basis: Text
    criteria: tuple[SpecificationCriterion, ...] = Field(max_length=50)
    criteria_text: str | None = Field(default=None, max_length=4000)


class RemoveWorksheetRow(WorksheetMutation):
    row_id: Text
    basis: Text
    fingerprint: str = ""


class ArchiveStudyObject(Model):
    basis: Text
    fingerprint: Text
    idempotency_key: Text


class StopWorksheetChecks(RemoveWorksheetRow):
    point_ids: tuple[Text, ...] = Field(min_length=1, max_length=120)


class SetReleaseReference(WorksheetMutation):
    row_id: Text
    basis: Text
    result_version_id: Text | None = None
    release_completed: date | None = None
    coa_specification_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    coa_version: int | None = Field(default=None, ge=1)


class CopyCoaToZero(WorksheetMutation):
    row_id: Text
    coa_fingerprint: Text
    expected_version_id: str | None = None
    intent: Literal["initial", "correction", "retest", "keep"]
    basis: str = Field(default="", max_length=2000)


class PreviewCoaZeroBatch(Model):
    release_completed: date


class CopyCoaZeroBatch(WorksheetMutation):
    release_completed: date
    preview_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    row_ids: tuple[Text, ...] = Field(min_length=1, max_length=150)
    basis: Text = Field(max_length=2000)


class CoaZeroBatchSource(Model):
    specification_id: str
    version: int
    source_id: str
    value: str
    filename: str
    criterion_key: str


class CoaZeroBatchRow(Model):
    row_id: str
    item_id: str
    label: str
    method: str
    method_version: str
    unit: str
    slot_id: str
    status: Literal["matched", "unmatched", "existing", "conflict"]
    reason: Literal["sample_missing", "no_applicable_coa", "standard_scope_conflict", "multiple_coa_matches", "invalid_coa_value",
                    "release_reference_exists", "result_history_exists", "worksheet_slot_conflict", "worksheet_occupied"] | None
    adopted_version_id: str | None
    adopted_value: str | None
    candidates: list[CoaZeroBatchSource]


class CoaZeroBatchPreview(Model):
    id: str
    revision: int
    sample_id: str | None
    point_id: str | None
    release_completed: date
    preview_fingerprint: str
    rows: list[CoaZeroBatchRow]


class CoaZeroBatchResult(Model):
    id: str
    revision: int
    plan_version: int
    project_revision: int
    result_version_ids: list[str]
    copied: dict[str, str]


class WorksheetListEntry(Model):
    id: str
    name: str
    revision: int
    plan_version: int
    sample_id: str | None
    batch: str
    condition: str
    study: Literal["long_term", "accelerated", "stress"]


class AttachProtocol(WorksheetMutation):
    protocol_draft_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    protocol_table: int = Field(ge=1)
    basis: Text
    row_mapping: dict[int, tuple[Text, ...]] = Field(default_factory=dict, max_length=150)
    add_months: bool = False


class AddWorksheetEvent(WorksheetMutation):
    category: Literal["异常调查", "复测", "方法变更", "标准变更", "设备转移", "备注"]
    reference: str = Field(default="", max_length=200)
    description: Text = Field(max_length=4000)
    row_id: Text | None = None
    point_id: Text | None = None
    scan_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class CellChange(Model):
    row_id: Text
    point_id: Text
    expected_version_id: str | None = None
    value: str = Field(max_length=8000)
    intent: Literal["initial", "correction", "retest"]


class WorksheetLayout(Model):
    row_heights: dict[int, int] = Field(default_factory=dict)
    column_widths: dict[int, int] = Field(default_factory=dict)
    title_merge_to: int = Field(default=0, ge=0, strict=True)


class SaveWorksheet(WorksheetMutation):
    reason: str = Field(default="", max_length=2000)
    changes: tuple[CellChange, ...] = Field(default=(), max_length=5000)
    layout: WorksheetLayout | None = None


def planned_date(start, point):
    if point.planned_date:
        return point.planned_date.isoformat()
    try:
        if point.unit == "D":
            return (start + timedelta(days=point.amount)).isoformat()
        year, month_index = divmod(start.year * 12 + start.month - 1 + point.amount, 12)
        month = month_index + 1
        return date(year, month, min(start.day, calendar.monthrange(year, month)[1])).isoformat()
    except (OverflowError, ValueError) as exc:
        raise WorkflowError("plan_date_range", "计划日期超出支持范围。") from exc


def add_points(existing, requests, rows, start):
    known = {(point["unit"], point["amount"]) for point in existing}
    if len(existing) + len(requests) > 120:
        raise WorkflowError("plan_size", "每张工作表最多支持 120 个时间点。")
    result = list(existing)
    for point in requests:
        label = (point.unit, point.amount)
        if label in known:
            raise WorkflowError("duplicate_plan_point", "同一计划不能重复添加时间点。")
        known.add(label)
        required = set(point.required_items)
        if not required <= rows.keys() or len(required) != len(point.required_items):
            raise WorkflowError("plan_item_scope", "应检项目必须来自当前工作表且不能重复。")
        result.append({
            "id": uid("point"), "unit": point.unit, "amount": point.amount,
            "label": f"{point.amount}{point.unit}", "planned_date": planned_date(start, point),
            **({"signed_status": point.signed_status} if point.signed_status else {}),
            "required_row_ids": [rows[item]["id"] for item in point.required_items],
        })
    return result


def ordered_points(points):
    if len({point["unit"] for point in points}) <= 1:
        return sorted(points, key=lambda point: point["amount"])
    return sorted(points, key=lambda point: (point["planned_date"], point["unit"], point["amount"]))


class BusinessWorksheets:
    def __init__(self, workflow, result_versions, *, enforce_formal_sample_binding=False):
        self.store = workflow.store
        self.result_versions = result_versions
        self.enforce_formal_sample_binding = enforce_formal_sample_binding

    def list(self, project, actor, *, sample_id=None, study=None):
        require(actor, Capability.VIEW)
        with self.store.read() as conn:
            project_row(conn, project)
            query = """SELECT id,name,revision,plan_version,sample_id,
                              json_extract(identity_json,'$.batch') AS batch,
                              json_extract(identity_json,'$.condition') AS condition,
                              json_extract(identity_json,'$.study') AS study
                       FROM business_worksheets WHERE project=? AND deleted_at IS NULL"""
            params = [project]
            if sample_id is not None:
                query += " AND sample_id=?"
                params.append(sample_id)
            if study is not None:
                query += " AND json_extract(identity_json,'$.study')=?"
                params.append(study)
            return [dict(row) for row in conn.execute(
                query + " ORDER BY name,id", params
            )]

    def _sheet(self, conn, sheet_id):
        sheet = conn.execute("SELECT * FROM business_worksheets WHERE id=? AND deleted_at IS NULL", (sheet_id,)).fetchone()
        if sheet is None:
            raise WorkflowError("not_found", "研究工作表不存在。", 404)
        return sheet

    def _plan(self, conn, sheet):
        row = conn.execute(
            "SELECT plan_json,basis FROM business_worksheet_plans WHERE sheet_id=? AND version=?",
            (sheet["id"], sheet["plan_version"]),
        ).fetchone()
        return {**json.loads(row["plan_json"]), "basis": row["basis"]}

    def _lease(self, conn, sheet_id):
        return conn.execute(
            "SELECT * FROM business_worksheet_leases WHERE sheet_id=? AND expires_at>?", (sheet_id, int(time.time()))
        ).fetchone()

    def _require_lease(self, conn, sheet_id, actor, token):
        lease = self._lease(conn, sheet_id)
        if not lease or lease["actor_id"] != actor.id or not secrets.compare_digest(lease["token_hash"], digest(token)):
            raise WorkflowError("worksheet_lease_lost", "编辑占用已失效，请重新打开工作表并核对最新数据。", 409)

    def create(self, project, request, actor, *, _conn=None):
        require(actor, Capability.CONFIRM_FACT)
        with (self.store.connect() if _conn is None else nullcontext(_conn)) as conn:
            if _conn is None:
                conn.execute("BEGIN IMMEDIATE")
            creation = request.model_dump(mode="json")
            for point in creation["points"]:
                if not point["signed_status"]:
                    point.pop("signed_status")
            if creation["identity"]["sample_id"] is None:
                del creation["identity"]["sample_id"]
            for key in ("protocol_draft_id", "protocol_table", "copy_source_id", "copy_source_plan_version", "worksheet_draft_id", "seed_slot_id", "sample_id", "metadata_fingerprint"):
                if creation[key] is None:
                    del creation[key]
            if not creation["copy_standards"]:
                del creation["copy_standards"]
            for key in (*HEADER_METADATA_FIELDS, "custom_items", "item_standards", "protocol_row_items", "row_criteria",
                        "material_stage", "packaging"):
                if not creation[key]:
                    del creation[key]
            payload = {"worksheet_create": creation}
            prior = replay(conn, project, actor, request.idempotency_key, payload)
            if prior is not None:
                return prior
            if request.material_stage.strip() and not request.sample_id:
                from .coa import SampleInput, ensure_sample
                identity = request.identity
                if (request.material_stage.strip() == "参考品") != (identity.purpose == "reference"):
                    raise WorkflowError("worksheet_sample_scope", "样品材料阶段与参考品用途不一致。")
                sample = ensure_sample(conn, project, SampleInput(
                    batch=identity.batch, material_stage=request.material_stage,
                    sample_type=identity.sample_type, presentation=identity.presentation,
                    packaging=request.packaging, purpose=identity.purpose,
                ), actor)
                request = request.model_copy(update={"sample_id": sample["id"]})
            if (self.enforce_formal_sample_binding and not request.sample_id
                    and project_row(conn, project)["purpose"] == "formal"):
                raise WorkflowError("worksheet_sample_required", "正式项目请先选择样品，或填写材料阶段以建档。", 409)
            draft_id = request.worksheet_draft_id
            if draft_id:
                draft = conn.execute("SELECT draft_json FROM worksheet_drafts WHERE id=? AND project=?",
                                     (draft_id, project)).fetchone()
                if draft is None:
                    raise WorkflowError("worksheet_draft_missing", "找不到此项目的待补全计划。", 404)
                if conn.execute("""SELECT 1 FROM workflow_events WHERE project=? AND subject_id=?
                                   AND action='worksheet_draft_converted'""", (project, draft_id)).fetchone():
                    raise WorkflowError("worksheet_draft_converted", "此计划已转换为研究表，请从项目打开。", 409)
                stored = json.loads(draft["draft_json"])
                if (stored["protocol_draft"], stored["protocol_table"], stored["copy_source"]) != (
                    request.protocol_draft_id or "", str(request.protocol_table or ""), request.copy_source_id or ""
                ):
                    raise WorkflowError("worksheet_draft_source", "建表来源与待补全计划不一致。", 409)
            config = linked_config(conn, ProjectConfig.model_validate_json(project_row(conn, project)["config_json"]))
            source = {}
            if request.protocol_draft_id or request.protocol_table:
                draft = conn.execute('SELECT preview_json FROM protocol_drafts WHERE id=?',
                                     (request.protocol_draft_id,)).fetchone()
                if not draft or (request.protocol_table is not None and not any(
                    matrix["table"] == request.protocol_table for matrix in json.loads(draft["preview_json"])["matrices"]
                )):
                    raise WorkflowError("protocol_source_missing", "找不到对应的原方案表。")
                parsed = json.loads(draft["preview_json"])
                from .protocol_preview import HUMIDITY_CONFLICT

                matrix = next((m for m in parsed["matrices"] if m["table"] == request.protocol_table), None)
                if matrix and HUMIDITY_CONFLICT in matrix.get("warnings", ()) and not request.basis.strip():
                    raise WorkflowError("protocol_condition_review", "相对湿度有冲突，请在计划依据注明采用的原件位置及理由。")
                source = {"protocol_draft_id": request.protocol_draft_id, "protocol_table": request.protocol_table,
                          "protocol_standard_numbers": parsed.get("standard_numbers", [])}
                if request.protocol_row_items:
                    if (matrix is None or set(request.protocol_row_items) - {r["row"] for r in matrix["items"]}
                            or any(set(ids) - set(request.items) or len(ids) != len(set(ids))
                                   for ids in request.protocol_row_items.values())):
                        raise WorkflowError("protocol_mapping", "方案行关联须引用原方案行和本研究检项。")
                    source["protocol_row_items"] = request.protocol_row_items
            elif request.protocol_row_items:
                raise WorkflowError("protocol_mapping", "请先选择来源方案表。")
            request.identity.record_condition()
            if request.identity.sample_id and request.identity.sample_id != request.sample_id:
                raise WorkflowError("worksheet_sample_scope", "研究预填的样品与所选样品不一致。")
            if request.sample_id:
                sample = conn.execute("SELECT * FROM sample_entities WHERE project=? AND id=? AND deleted_at IS NULL",
                                      (project, request.sample_id)).fetchone()
                if sample is None or not sample_matches_study(sample, request.identity):
                    raise WorkflowError("worksheet_sample_scope", "所选样品的批号、类别、规格或用途与研究表不符。")
                existing_scope = request.identity.model_dump(exclude={"sample_id"})
                for candidate in conn.execute("SELECT sample_id,identity_json FROM business_worksheets WHERE project=? AND sample_id=? AND deleted_at IS NULL",
                                              (project, request.sample_id)):
                    if StudyIdentity.model_validate_json(candidate["identity_json"]).model_dump(exclude={"sample_id"}) == existing_scope:
                        raise WorkflowError("worksheet_exists", "此样品与研究条件已建立工作表，请打开现有工作表。", 409)
            items = {item.id: item for item in config.items}
            custom = {item.id: item for item in request.custom_items}
            if (len(custom) != len(request.custom_items) or custom.keys() & items.keys()
                    or not custom.keys() <= set(request.items)
                    or any(not key.startswith("local_") for key in custom)):
                raise WorkflowError("plan_item_scope", "插入的检项身份冲突，请重新打开草稿核对。")
            items.update(custom)
            source_rows = {}
            if request.seed_slot_id:
                if source or request.copy_source_id:
                    raise WorkflowError("worksheet_seed_scope", "请选择一个研究表来源。")
                records = adopted_scope(conn, project, request.identity.batch)
                selected = next((record for version, record in records if version["slot_id"] == request.seed_slot_id), None)
                if selected is None or (selected.key.sample_id and selected.key.sample_id != request.sample_id):
                    raise WorkflowError("worksheet_seed_scope", "所选样品与采用结果不符。")
                scoped = scope_result_entries(records, request.identity.model_copy(update={"sample_id": selected.key.sample_id}))
                if not any(version["slot_id"] == request.seed_slot_id for version, _ in scoped):
                    raise WorkflowError("worksheet_seed_scope", "所填样品或研究范围与采用结果不符。")
                source_rows = historical_rows(scoped)
            if request.copy_source_id:
                original = self._sheet(conn, request.copy_source_id)
                if original["project"] != project or original["plan_version"] != request.copy_source_plan_version:
                    raise WorkflowError("worksheet_copy_source_changed", "来源表的项目或计划版本已变化，请重新打开。", 409)
                source_rows = {
                    row.get("plan_key", row["item_id"]): {
                        k: row[k] for k in ("label", "item_id", "method", "method_version", "unit", "result_type",
                                            "plan_key", "reporting_basis") if k in row
                    }
                    for row in self._plan(conn, original)["rows"]
                }
            missing = set(request.items) - items.keys() - source_rows.keys()
            if missing:
                history = historical_items(scope_records(adopted_scope(conn, project, request.identity.batch), request.identity))
                items.update({key: history[key] for key in missing & history.keys()})
            if len(set(request.items)) != len(request.items) or not set(request.items) <= items.keys() | source_rows.keys():
                raise WorkflowError("plan_item_scope", "请选择项目中已配置且不重复的检测项目。")
            rows = {}
            for key in request.items:
                row = source_rows.get(key)
                if row is None:
                    row = {"item_id": key, **items[key].model_dump(mode="json", exclude={"id", "aliases"})}
                    component = conn.execute("SELECT reporting_basis FROM assay_components WHERE project=? AND item_id=?",
                                             (project, key)).fetchone()
                    if component and component["reporting_basis"]:
                        row["reporting_basis"] = component["reporting_basis"]
                rows[key] = {**row, "id": uid("row")}
            if request.copy_source_id:
                if source:
                    raise WorkflowError("worksheet_copy_source", "新建时请选择一个结构来源。")
                source = {"copied_from": {"sheet_id": original["id"], "plan_version": original["plan_version"]}}
                if request.copy_standards:
                    self._copy_standards(conn, original, request.identity, rows)
            elif request.copy_source_plan_version is not None or request.copy_standards:
                raise WorkflowError("worksheet_copy_source", "请选择来源表和计划版本。")
            for key, item in custom.items():
                if key in rows and key not in request.row_criteria:
                    rows[key]["standards"] = self._resolve_standards(conn, project, request.identity, rows[key], item.specification_links)
            for key, links in request.item_standards.items():
                if key not in rows or len(links) > 50 or key in custom:
                    raise WorkflowError("plan_item_scope", "标准引用须对应当前研究的共享检项。")
                if key not in request.row_criteria:
                    rows[key]["standards"] = self._resolve_standards(conn, project, request.identity, rows[key], links)
            for key, criteria in request.row_criteria.items():
                if key not in rows:
                    raise WorkflowError("plan_item_scope", "可接受标准须对应当前研究的检项。")
                rows[key]["standards"] = self._resolve_standards(conn, project, request.identity, rows[key], criteria.specification_links)
                rows[key]["criteria_text"] = criteria.criteria_text.strip()
            if draft_id:
                source["worksheet_draft_id"] = draft_id
            metadata = {key: getattr(request, key).strip() for key in HEADER_METADATA_FIELDS if getattr(request, key).strip()}
            metadata_sources = {}
            if metadata and (request.seed_slot_id or any(field in metadata for field in ADDITIONAL_HEADER_FIELDS)):
                if not request.basis.strip():
                    raise WorkflowError("worksheet_metadata_basis", "请记录确认研究表头的依据。")
            if request.seed_slot_id and metadata:
                from .worksheet_seed import confirmed_metadata_sources
                metadata_sources = confirmed_metadata_sources(
                    conn, project, request.identity.model_copy(update={"sample_id": selected.key.sample_id}),
                    metadata, request.metadata_fingerprint,
                )
            source.update(metadata)
            points = ordered_points(add_points([], request.points, rows, request.start_date))
            if request.protocol_row_items:
                source["pending_release_references"] = self._pending_protocol_release(
                    matrix, request.protocol_row_items, rows, points,
                )
            sheet_id = uid("worksheet")
            slot_sample = request.sample_id
            if request.seed_slot_id:
                slot_sample = selected.key.sample_id
            identity = request.identity.model_copy(update={"sample_id": slot_sample})
            identity_json = identity.model_dump_json(exclude={"sample_id"} if not slot_sample else set())
            if conn.execute("SELECT 1 FROM business_worksheets WHERE project=? AND identity_json=?",
                            (project, identity_json)).fetchone():
                raise WorkflowError("worksheet_exists", "此研究条件已建立工作表，请打开现有工作表。", 409)
            conn.execute("""INSERT INTO business_worksheets(id,project,name,identity_json,start_date,sample_id)
                            VALUES (?,?,?,?,?,?)""",
                         (sheet_id, project, request.name, identity_json, request.start_date.isoformat(), request.sample_id))
            conn.execute("""INSERT INTO business_worksheet_plans(sheet_id,version,plan_json,basis,created_by)
                            VALUES (?,1,?,?,?)""",
                         (sheet_id, encoded({"rows": list(rows.values()), "points": points, **source}), request.basis, actor.id))
            self._register_slots(conn, self._sheet(conn, sheet_id), identity, rows.values(), points)
            detail = {"plan_version": 1}
            if metadata:
                detail.update(metadata=metadata, metadata_sources=metadata_sources)
            event(conn, project, sheet_id, "worksheet_create", actor, request.basis, detail)
            if draft_id:
                event(conn, project, draft_id, "worksheet_draft_converted", actor, request.basis, {"worksheet_id": sheet_id})
            result = {"id": sheet_id, "revision": 1, "project_revision": bump(conn, project)}
            remember(conn, project, actor, request.idempotency_key, payload, result)
            return result

    @staticmethod
    def _pending_protocol_release(matrix, mapping, rows, points):
        zero = next((point for point in points if point["unit"] == "M" and point["amount"] == 0), None)
        if not zero:
            return {}
        columns = {point["column"] for point in matrix["points"] if point["unit"] == "M" and point["amount"] == 0}
        return {
            rows[key]["id"]: {"table": matrix["table"], "row": source["row"], "column": cell["column"], "raw": cell["raw"]}
            for source in matrix["items"] for cell in source["cells"]
            if cell["column"] in columns and cell["state"] == "release_reference"
            for key in mapping.get(source["row"], ())
            if rows[key]["id"] in zero["required_row_ids"]
        }

    def _copy_standards(self, conn, original, identity, rows):
        original_identity = StudyIdentity.model_validate_json(original["identity_json"])
        if original_identity.model_dump(exclude={"batch", "sample_id"}) != identity.model_dump(exclude={"batch", "sample_id"}):
            raise WorkflowError("worksheet_copy_standard_scope", "复用标准时，除批号外的研究身份须与来源表一致。")
        for old in self._plan(conn, original)["rows"]:
            target = rows.get(old.get("plan_key", old["item_id"]))
            if not target or not (old.get("standards") or old.get("criteria_text")):
                continue
            if any(target[key] != old[key] for key in ("method", "method_version", "unit")):
                raise WorkflowError("worksheet_copy_standard_scope", "检项的方法、版本或单位已变化，请重新关联标准。")
            target["standards"] = old.get("standards", [])
            target["criteria_text"] = old.get("criteria_text", "")

    def acquire(self, sheet_id, actor):
        require(actor, Capability.CONFIRM_FACT)
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._sheet(conn, sheet_id)
            lease = self._lease(conn, sheet_id)
            if lease and (lease["actor_id"] != actor.id or actor.id != "local-user"):
                raise WorkflowError("worksheet_occupied", f'{lease["actor_name"]}正在编辑此工作表。', 409)
            token = secrets.token_urlsafe(32)
            expires = int(time.time()) + LEASE_SECONDS
            conn.execute("REPLACE INTO business_worksheet_leases VALUES (?,?,?,?,?)",
                         (sheet_id, actor.id, actor.display_name, digest(token), expires))
            return {"token": token, "expires_at": expires}

    def heartbeat(self, sheet_id, request, actor):
        require(actor, Capability.CONFIRM_FACT)
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_lease(conn, sheet_id, actor, request.token)
            expires = int(time.time()) + LEASE_SECONDS
            conn.execute("UPDATE business_worksheet_leases SET expires_at=? WHERE sheet_id=?", (expires, sheet_id))
            return {"expires_at": expires}

    def release(self, sheet_id, request, actor):
        require(actor, Capability.CONFIRM_FACT)
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_lease(conn, sheet_id, actor, request.token)
            conn.execute("DELETE FROM business_worksheet_leases WHERE sheet_id=?", (sheet_id,))
            return {"released": True}

    @staticmethod
    def _key(sheet, identity, row, point):
        return RecordKey(
            project=sheet["project"], **identity.model_dump(exclude={"orientation", "condition", "reporting_basis"}),
            condition=identity.record_condition(),
            reporting_basis=row.get("reporting_basis", identity.reporting_basis),
            item=row["item_id"], method=row["method"], method_version=row["method_version"],
            month=point["amount"] if point["unit"] == "M" else None,
            day=point["amount"] if point["unit"] == "D" else None,
        )

    def _register_slots(self, conn, sheet, identity, rows, points):
        for row in rows:
            for point in points:
                slot = self._key(sheet, identity, row, point).slot_id
                if conn.execute("SELECT 1 FROM business_worksheet_slots WHERE slot_id=?", (slot,)).fetchone():
                    raise WorkflowError("worksheet_slot_overlap", "所选范围和另一张研究工作表重叠。", 409)
                conn.execute("INSERT INTO business_worksheet_slots VALUES (?,?)", (slot, sheet["id"]))
                conn.execute("INSERT INTO worksheet_result_positions VALUES (?,?,?,?)",
                             (sheet["id"], row["id"], point["id"], slot))

    @staticmethod
    def _legacy_location(conn, sheet, plan):
        location = plan.get("location")
        if not location or not any(row.get("plan_key", "").startswith("history_") for row in plan["rows"]):
            return ""
        confirmed = conn.execute("""SELECT 1 FROM workflow_events WHERE project=? AND subject_id=? AND (
            (action='worksheet_create' AND json_extract(detail_json,'$.metadata.location')=?) OR
            (action='worksheet_header_metadata' AND json_extract(detail_json,'$.after.location')=?))
            LIMIT 1""", (sheet["project"], sheet["id"], location, location)).fetchone()
        return "" if confirmed else location

    def get(self, sheet_id, actor, *, _conn=None):
        require(actor, Capability.VIEW)
        with (self.store.read() if _conn is None else nullcontext(_conn)) as conn:
            sheet = self._sheet(conn, sheet_id)
            plan = self._plan(conn, sheet)
            identity = StudyIdentity.model_validate_json(sheet["identity_json"])
            grouped = {}
            for version in versions(conn, sheet["project"]):
                grouped.setdefault(version["slot_id"], []).append(version)
            choices = adoption_choices(conn, sheet["project"])
            reference_ids = {ref["result_version_id"] for ref in plan.get("release_references", {}).values()
                             if not ref.get("coa")}
            invalidated_references = [
                row_id for row_id, ref in plan.get("release_references", {}).items()
                if not ref.get("coa") and invalidation(conn, ref["result_version_id"])
            ]
            release_versions = {member["id"]: member for members in grouped.values() for member in members
                                if member["id"] in reference_ids}
            cells = []
            source_events = {}
            historical_protocol = False
            from .platform_catalog import resolve_category_metadata
            for row in plan["rows"]:
                cat_meta = resolve_category_metadata(
                    conn=conn,
                    raw_category=row.get("category_code") or row.get("category_name_zh", ""),
                    item_text=row.get("label", ""),
                    method_text=row.get("method", ""),
                )
                row.setdefault("category_code", cat_meta["category_code"])
                row["category_name_zh"] = cat_meta["category_name_zh"]
                row["category_order"] = cat_meta["category_order"]
                row["subcategory_name_zh"] = cat_meta["subcategory_name_zh"]
                row["display_order"] = cat_meta["display_order"]
                original_labels = {}
                historical_criteria = set(row.get("source_criteria", ()))
                from_history = row.get("plan_key", "").startswith("history_")
                for point in plan["points"]:
                    slot = self._key(sheet, identity, row, point).slot_id
                    members = grouped.get(slot, ())
                    if not members:
                        reference = plan.get("release_references", {}).get(row["id"]) if point["unit"] == "M" and point["amount"] == 0 else None
                        if reference and (coa := reference.get("coa")):
                            options = self._coa_release_options(conn, sheet, row)
                            current_reference = any(option["version"] == coa["version"]
                                                    and option["source_id"] == coa["source_id"] and option["value"] == coa["value"]
                                                    and option["specification_id"] == coa["specification_id"] for option in options)
                            cells.append({"row_id": row["id"], "point_id": point["id"], "slot_id": slot,
                                          "display": {"text": coa["value"], "result_version_id": reference["result_version_id"],
                                                      "source_ids": [coa["source_id"]] if coa["source_id"] else []},
                                          "adopted": None, "raw": coa["value"],
                                          "release_reference": {**reference, "current": current_reference}})
                            continue
                        source = release_versions.get(reference["result_version_id"]) if reference else None
                        if source:
                            record = StabilityRecord.model_validate_json(source["record_json"])
                            cells.append({"row_id": row["id"], "point_id": point["id"], "slot_id": slot,
                                          "display": result_value(source, preserve_special_text=True).model_dump(mode="json"), "adopted": None,
                                          "raw": record.result.raw_text,
                                          "release_reference": {**reference, "current": choices.get(source["slot_id"]) == source["id"]}})
                        continue
                    latest = current(members)
                    adopted_row = next((member for member in members if member["id"] == choices.get(slot)), None)
                    record = StabilityRecord.model_validate_json((adopted_row or latest)["record_json"])
                    if adopted_row:
                        sources = list(historical_raw_sources(adopted_row))
                        if from_history:
                            historical_criteria.update(standard.criterion_text for standard in record.standards
                                                       if standard.criterion_text.strip())
                            original_labels.update(dict.fromkeys(original_test_items(sources)))
                            if "protocol_number" not in plan and not historical_protocol:
                                historical_protocol = any(
                                    raw.get("fields", {}).get("study_id", "").strip() == identity.study_id
                                    and raw.get("evidence", {}).get("study_id") for raw in sources
                                )
                        notes = [line for raw in sources for line in frozen_block_events(raw, point)]
                        if notes:
                            source_events.setdefault(point["id"], {}).update(dict.fromkeys(notes))
                    assessment = worksheet_assessment(record, row) if adopted_row else None
                    cells.append({
                        "row_id": row["id"], "point_id": point["id"], "slot_id": slot,
                        "display": result_value(latest, preserve_special_text=True).model_dump(mode="json"),
                        "adopted": result_value(adopted_row, preserve_special_text=True).model_dump(mode="json") if adopted_row else None,
                        "raw": record.result.raw_text,
                        **({"standard_assessment": assessment} if assessment else {}),
                    })
                if historical_criteria:
                    row["source_criteria"] = sorted(historical_criteria)
                if original_labels:
                    row["source_test_item"] = list(original_labels)
                elif from_history:
                    row.pop("source_test_item", None)
            lease = self._lease(conn, sheet_id)
            available_coa = {}
            if sheet["sample_id"]:
                for row in plan["rows"]:
                    opts = self._coa_release_options(conn, sheet, row)
                    if opts:
                        available_coa[row["id"]] = opts

            legacy_location = self._legacy_location(conn, sheet, plan)
            reported_spec = "" if identity.presentation.startswith("原文未注明") or identity.presentation in {
                "未注明", "not_reported"
            } else identity.presentation
            return {
                "id": sheet_id, "project": sheet["project"], "name": sheet["name"], "revision": sheet["revision"],
                "sample_id": sheet["sample_id"],
                "plan_version": sheet["plan_version"], "start_date": sheet["start_date"], "identity": identity.model_dump(),
                **plan, "layout": json.loads(sheet["layout_json"]), "cells": cells,
                "specification_text": plan.get("specification_text", reported_spec),
                **({"location": "", "legacy_location": legacy_location} if legacy_location else {}),
                **({"protocol_number": identity.study_id} if historical_protocol else {}),
                "selection_id": digest({"revision": sheet["revision"], "cells": cells}),
                **({"source_events": {point_id: list(notes) for point_id, notes in source_events.items()}} if source_events else {}),
                "available_coa_references": available_coa,
                "invalidated_release_rows": invalidated_references,
                "editable": Capability.CONFIRM_FACT in actor.capabilities,
                "occupied_by": lease["actor_name"] if lease else None,
            }

    def _check_mutation(self, conn, sheet, request, actor):
        self._require_lease(conn, sheet["id"], actor, request.token)
        if sheet["revision"] != request.revision:
            raise WorkflowError("worksheet_revision_changed", "工作表已保存新版本，请刷新并核对修改。", 409)

    def preview_header_metadata(self, sheet_id, actor):
        from .worksheet_seed import worksheet_metadata_preview

        require(actor, Capability.VIEW)
        with self.store.read() as conn:
            sheet = self._sheet(conn, sheet_id)
            identity = StudyIdentity.model_validate_json(sheet["identity_json"])
            return {"revision": sheet["revision"], **worksheet_metadata_preview(conn, sheet["project"], identity)}

    def edit_header_metadata(self, sheet_id, request, actor):
        from .worksheet_seed import confirmed_metadata_sources

        require(actor, Capability.CONFIRM_FACT)
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            sheet = self._sheet(conn, sheet_id)
            project = sheet["project"]
            changes_requested = request.model_dump(mode="json", exclude={
                field for field in ADDITIONAL_HEADER_FIELDS if getattr(request, field) is None
            })
            payload = {"worksheet_header_metadata": sheet_id, **changes_requested}
            prior = replay(conn, project, actor, request.idempotency_key, payload)
            if prior is not None:
                return prior
            self._check_mutation(conn, sheet, request, actor)
            plan = self._plan(conn, sheet)
            before = {field: plan.get(field, "") for field in HEADER_METADATA_FIELDS}
            after = {**before, **{field: changes_requested[field].strip()
                                 for field in HEADER_METADATA_FIELDS if field in changes_requested}}
            changed = {field: value for field, value in after.items() if value != before[field]}
            for field in ("protocol_number", "specification_text"):
                if field in changes_requested and field not in plan:
                    changed[field] = after[field]  # An explicit blank also suppresses a legacy display fallback.
            if after["location"] and self._legacy_location(conn, sheet, plan):
                changed["location"] = after["location"]
            if not changed:
                response = {"id": sheet_id, "revision": sheet["revision"], "plan_version": sheet["plan_version"],
                            "project_revision": project_row(conn, project)["revision"]}
            else:
                identity = StudyIdentity.model_validate_json(sheet["identity_json"])
                sources = confirmed_metadata_sources(conn, project, identity, changed, request.metadata_fingerprint)
                plan.update(changed)
                response = self._write_plan(conn, sheet, plan, actor, request.basis, "worksheet_header_metadata",
                                            detail={"before": before, "after": after, "metadata_sources": sources})
            remember(conn, project, actor, request.idempotency_key, payload, response)
            return response

    def edit_point_metadata(self, sheet_id, request, actor):
        require(actor, Capability.CONFIRM_FACT)
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            sheet = self._sheet(conn, sheet_id)
            project = sheet["project"]
            payload = {"worksheet_point_metadata": sheet_id, **request.model_dump(mode="json")}
            prior = replay(conn, project, actor, request.idempotency_key, payload)
            if prior is not None:
                return prior
            self._check_mutation(conn, sheet, request, actor)
            plan = self._plan(conn, sheet)
            point = next((point for point in plan["points"] if point["id"] == request.point_id), None)
            if point is None:
                raise WorkflowError("worksheet_point_scope", "请选择当前研究表的时间点。", 404)
            point["planned_date"] = request.planned_date.isoformat()
            point["signed_status"] = request.signed_status
            plan["points"] = ordered_points(plan["points"])
            response = self._write_plan(conn, sheet, plan, actor, "维护计划取样日期与签署文字", "worksheet_point_metadata")
            remember(conn, project, actor, request.idempotency_key, payload, response)
            return response

    def extend(self, sheet_id, request, actor):
        require(actor, Capability.CONFIRM_FACT)
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            sheet = self._sheet(conn, sheet_id)
            project = sheet["project"]
            payload = {"worksheet_extend": sheet_id, **request.model_dump(mode="json")}
            for point in payload["points"]:
                if not point["signed_status"]:
                    point.pop("signed_status")
            prior = replay(conn, project, actor, request.idempotency_key, payload)
            if prior is not None:
                return prior
            self._check_mutation(conn, sheet, request, actor)
            plan = self._plan(conn, sheet)
            rows = {row.get("plan_key", row["item_id"]): row for row in plan["rows"]}
            points = add_points(plan["points"], request.points, rows, date.fromisoformat(sheet["start_date"]))
            self._register_slots(conn, sheet, StudyIdentity.model_validate_json(sheet["identity_json"]), rows.values(),
                                 points[len(plan["points"]):])
            plan["points"] = ordered_points(points)
            response = self._write_plan(conn, sheet, plan, actor, request.basis, "worksheet_extend")
            remember(conn, project, actor, request.idempotency_key, payload, response)
            return response

    def _historical_additions(self, conn, sheet, plan):
        identity = StudyIdentity.model_validate_json(sheet["identity_json"])
        records = scope_result_entries(adopted_scope(conn, sheet["project"], identity.batch), identity)
        existing = {(row["item_id"], row["method"], row["method_version"],
                     row.get("reporting_basis", identity.reporting_basis)) for row in plan["rows"]}
        candidates = historical_rows(records)
        point_labels = {point["label"] for point in plan["points"]}
        matches = {}
        for _, record in records:
            if record.key.time_label in point_labels:
                matches.setdefault(historical_row_key(record.key), set()).add(record.key.time_label)
        return identity, [
            {**row, "points": sorted(matches[key])} for key, row in candidates.items()
            if key in matches and (row["item_id"], row["method"], row["method_version"], row["reporting_basis"]) not in existing
        ]

    def preview_historical_rows(self, sheet_id, actor):
        require(actor, Capability.VIEW)
        with self.store.read() as conn:
            sheet = self._sheet(conn, sheet_id)
            _, additions = self._historical_additions(conn, sheet, self._plan(conn, sheet))
            return {"revision": sheet["revision"], "rows": additions, "fingerprint": digest(additions)}

    def include_historical_rows(self, sheet_id, request, fingerprint, actor):
        require(actor, Capability.CONFIRM_FACT)
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            sheet = self._sheet(conn, sheet_id)
            project = sheet["project"]
            payload = {"worksheet_include_historical": sheet_id, "fingerprint": fingerprint, **request.model_dump(mode="json")}
            if (prior := replay(conn, project, actor, request.idempotency_key, payload)) is not None:
                return prior
            self._check_mutation(conn, sheet, request, actor)
            plan = self._plan(conn, sheet)
            identity, additions = self._historical_additions(conn, sheet, plan)
            if not additions or digest(additions) != fingerprint:
                raise WorkflowError("worksheet_history_changed", "可关联的历史行已变化，请重新核对。", 409)
            for addition in additions:
                labels = addition.pop("points")
                row = {**addition, "id": uid("row")}
                plan["rows"].append(row)
                for point in plan["points"]:
                    if point["label"] in labels:
                        point["required_row_ids"].append(row["id"])
                self._register_slots(conn, sheet, identity, [row], plan["points"])
            response = self._write_plan(conn, sheet, plan, actor, request.basis, "worksheet_include_historical")
            remember(conn, project, actor, request.idempotency_key, payload, response)
            return response

    def _write_plan(self, conn, sheet, plan, actor, basis, action, *, detail=None):
        sheet_id, project = sheet["id"], sheet["project"]
        version = sheet["plan_version"] + 1
        data = {key: value for key, value in plan.items() if key != "basis"}
        conn.execute("""INSERT INTO business_worksheet_plans(sheet_id,version,plan_json,basis,created_by)
                        VALUES (?,?,?,?,?)""", (sheet_id, version, encoded(data), basis, actor.id))
        conn.execute("UPDATE business_worksheets SET plan_version=?,revision=revision+1 WHERE id=?", (version, sheet_id))
        event(conn, project, sheet_id, action, actor, basis, {"plan_version": version, **(detail or {})})
        return {"id": sheet_id, "revision": sheet["revision"] + 1, "plan_version": version,
                "project_revision": bump(conn, project)}

    def sample_candidates(self, sheet_id, actor):
        require(actor, Capability.VIEW)
        with self.store.read() as conn:
            sheet = self._sheet(conn, sheet_id)
            identity = StudyIdentity.model_validate_json(sheet["identity_json"])
            return [{**dict(sample), "compatible": sample_matches_study(sample, identity)} for sample in conn.execute(
                "SELECT * FROM sample_entities WHERE project=? AND batch=? AND deleted_at IS NULL ORDER BY id",
                (sheet["project"], identity.batch))]

    def link_sample(self, sheet_id, command, actor):
        require(actor, Capability.CONFIRM_FACT)
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            sheet = self._sheet(conn, sheet_id)
            project = sheet["project"]
            payload = {"worksheet_sample": sheet_id, **command.model_dump(mode="json")}
            if (prior := replay(conn, project, actor, command.idempotency_key, payload)) is not None:
                return prior
            self._check_mutation(conn, sheet, command, actor)
            identity = StudyIdentity.model_validate_json(sheet["identity_json"])
            sample = conn.execute("SELECT * FROM sample_entities WHERE project=? AND id=? AND deleted_at IS NULL",
                                  (project, command.sample_id)).fetchone()
            if sample is None or not sample_matches_study(sample, identity):
                raise WorkflowError("worksheet_sample_scope", "样品的项目、批号、类别、规格或用途与研究表不符。")
            if sheet["sample_id"]:
                raise WorkflowError("worksheet_sample_linked", "研究已关联样品。更换归属需要先核对历史结果。", 409)
            plan = self._plan(conn, sheet)
            plan["sample_link"] = {"sample_id": sample["id"], "basis": command.basis}
            conn.execute("UPDATE business_worksheets SET sample_id=? WHERE id=?", (sample["id"], sheet_id))
            result = self._write_plan(conn, sheet, plan, actor, command.basis, "worksheet_sample")
            remember(conn, project, actor, command.idempotency_key, payload, result)
            return result

    def _protocol_standard_preview(self, conn, sheet, plan):
        from .specification_preview import matching_standards, protocol_numbers, unambiguous_standard_sources
        draft = conn.execute("SELECT source,preview_json FROM protocol_drafts WHERE id=?",
                             (plan.get("protocol_draft_id"),)).fetchone()
        numbers = protocol_numbers(draft) if draft else []
        identity = StudyIdentity.model_validate_json(sheet["identity_json"])
        required_scope = "reference" if identity.purpose == "reference" else identity.sample_type
        matches = matching_standards(conn, sheet["project"], numbers, required_scope)
        additions, issues = [], []
        for number in numbers:
            sources = [source for source in matches if source["number"] == number]
            selected_sources = unambiguous_standard_sources(sources)
            if not selected_sources:
                issues.append({"number": number, "message": "发现多个标准版本，请人工选择。" if sources else "缺少方案引用标准。"})
                continue
            matched = False
            for row in plan["rows"]:
                for source, link in ((source, link) for source in selected_sources for link in source["links"]):
                    if link["item_id"] != row["item_id"]:
                        continue
                    matched = True
                    criterion = SpecificationCriterion(specification_id=source["id"], table=link["table"], row=link["row"])
                    if any((old["specification_id"], old["table"], old["row"]) ==
                           (criterion.specification_id, criterion.table, criterion.row) for old in row.get("standards", [])):
                        continue
                    try:
                        standard = self._resolve_standards(conn, sheet["project"], identity, row, [criterion])[0]
                        self._check_standard_versions(conn, [
                            *row.get("standards", []), standard,
                            *(item["standard"] for item in additions if item["row_id"] == row["id"]),
                        ])
                    except WorkflowError as exc:
                        issues.append({"number": number, "message": str(exc)})
                        continue
                    additions.append({"row_id": row["id"], "label": row["label"], "standard": standard})
            if not matched:
                issues.append({"number": number, "message": "请先在质量标准页确认与本研究检项对应的条款。"})
        additions = list({(item["row_id"], item["standard"]["specification_id"], item["standard"]["table"],
                           item["standard"]["row"]): item for item in additions}.values())
        issues = list({(issue["number"], issue["message"]): issue for issue in issues}.values())
        return {"revision": sheet["revision"], "additions": additions, "issues": issues,
                "fingerprint": digest([sheet["revision"], additions, issues])}

    def preview_protocol_standards(self, sheet_id, actor):
        require(actor, Capability.VIEW)
        with self.store.read() as conn:
            sheet = self._sheet(conn, sheet_id)
            return self._protocol_standard_preview(conn, sheet, self._plan(conn, sheet))

    def confirm_protocol_standards(self, sheet_id, command, actor):
        require(actor, Capability.CONFIRM_FACT)
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            sheet = self._sheet(conn, sheet_id)
            project = sheet["project"]
            payload = {"protocol_standard_confirmation": sheet_id, **command.model_dump(mode="json")}
            if (prior := replay(conn, project, actor, command.idempotency_key, payload)) is not None:
                return prior
            self._check_mutation(conn, sheet, command, actor)
            plan = self._plan(conn, sheet)
            preview = self._protocol_standard_preview(conn, sheet, plan)
            if preview["fingerprint"] != command.fingerprint or not preview["additions"]:
                raise WorkflowError("protocol_standards_changed", "可关联标准已变化，请重新预览。", 409)
            rows = {row["id"]: row for row in plan["rows"]}
            for addition in preview["additions"]:
                rows[addition["row_id"]].setdefault("standards", []).append(addition["standard"])
            result = self._write_plan(conn, sheet, plan, actor, command.basis, "worksheet_protocol_standards")
            remember(conn, project, actor, command.idempotency_key, payload, result)
            return result

    def attach_protocol(self, sheet_id, request, actor):
        require(actor, Capability.CONFIRM_FACT)
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            sheet = self._sheet(conn, sheet_id)
            project = sheet["project"]
            payload = {"worksheet_protocol": sheet_id, **request.model_dump(mode="json")}
            if not request.row_mapping and not request.add_months:
                del payload["row_mapping"], payload["add_months"]
            prior = replay(conn, project, actor, request.idempotency_key, payload)
            if prior is not None:
                return prior
            self._check_mutation(conn, sheet, request, actor)
            draft = conn.execute("SELECT source,preview_json FROM protocol_drafts WHERE id=?",
                                 (request.protocol_draft_id,)).fetchone()
            if draft is None:
                raise WorkflowError("protocol_source_missing", "找不到原方案。", 404)
            parsed = json.loads(draft["preview_json"])
            matrix = next((entry for entry in parsed["matrices"] if entry["table"] == request.protocol_table), None)
            if matrix is None:
                raise WorkflowError("protocol_source_missing", "找不到对应的原方案表。", 404)
            identity = StudyIdentity.model_validate_json(sheet["identity_json"])
            batches = {entry["batch"] for entry in parsed["batches"] if entry["batch"]}
            if (matrix["study"] and matrix["study"] != identity.study) or (batches and identity.batch not in batches):
                raise WorkflowError("protocol_worksheet_identity", "方案中的已知批号或考察类型与研究表不一致。")
            plan = self._plan(conn, sheet)
            from .specification_preview import protocol_numbers
            if (plan.get("protocol_draft_id"), plan.get("protocol_table")) != (request.protocol_draft_id, request.protocol_table):
                plan.pop("protocol_row_items", None)
            plan["protocol_standard_numbers"] = protocol_numbers(draft)
            if request.add_months:
                rows = {row.get("plan_key", row["item_id"]): row for row in plan["rows"]}
                original_rows = {entry["row"]: entry for entry in matrix["items"]}
                if set(request.row_mapping) != original_rows.keys() or any(
                    len(ids) != len(set(ids)) or set(ids) - rows.keys() for ids in request.row_mapping.values()
                ):
                    raise WorkflowError("protocol_mapping", "请核对每个原方案行与本研究检项的关联。")
                known = {(point["amount"], point["unit"]) for point in plan["points"]}
                added = []
                for point in matrix["points"]:
                    if (point["amount"], point["unit"]) in known:
                        continue
                    states = [(ids, next((cell["state"] for cell in original_rows[number]["cells"]
                                         if cell["column"] == point["column"]), "unclear"))
                              for number, ids in request.row_mapping.items() if ids]
                    if any(state == "unclear" for _, state in states):
                        raise WorkflowError("protocol_matrix_unclear", "新增时间点存在含义未确定的应检标记，请先核对原件。")
                    required = tuple(dict.fromkeys(item for ids, state in states
                                                   if state in {"required", "release_reference"} for item in ids))
                    added.append(PlannedPoint(amount=point["amount"], unit=point["unit"], required_items=required))
                if not added:
                    raise WorkflowError("protocol_no_new_points", "所选方案没有新增时间点。")
                points = add_points(plan["points"], added, rows, date.fromisoformat(sheet["start_date"]))
                new_points = points[len(plan["points"]):]
                self._register_slots(conn, sheet, identity, rows.values(), new_points)
                plan.setdefault("pending_release_references", {}).update(
                    self._pending_protocol_release(matrix, request.row_mapping, rows, new_points)
                )
                plan["points"] = ordered_points(points)
                plan["protocol_row_items"] = request.row_mapping
            plan["protocol_draft_id"] = request.protocol_draft_id
            plan["protocol_table"] = request.protocol_table
            response = self._write_plan(conn, sheet, plan, actor, request.basis, "worksheet_attach_protocol")
            remember(conn, project, actor, request.idempotency_key, payload, response)
            return response

    def add_event(self, sheet_id, request, actor):
        require(actor, Capability.CONFIRM_FACT)
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            sheet = self._sheet(conn, sheet_id)
            project = sheet["project"]
            payload = {"worksheet_event": sheet_id, **request.model_dump(mode="json")}
            prior = replay(conn, project, actor, request.idempotency_key, payload)
            if prior is not None:
                return prior
            self._check_mutation(conn, sheet, request, actor)
            plan = self._plan(conn, sheet)
            row = next((row for row in plan["rows"] if row["id"] == request.row_id), None)
            point = next((point for point in plan["points"] if point["id"] == request.point_id), None)
            if (request.row_id and row is None) or (request.point_id and point is None):
                raise WorkflowError("worksheet_event_scope", "请选择本研究表的检项和时间点。")
            if request.scan_id:
                from hashlib import sha256
                source = self.store.home / "pdf-ocr" / request.scan_id / "source.pdf"
                if not source.is_file() or sha256(source.read_bytes()).hexdigest() != request.scan_id:
                    raise WorkflowError("worksheet_event_source", "找不到完整的扫描附件。")
            if not request.description.strip():
                raise WorkflowError("worksheet_event_description", "请填写事件说明。")
            entry = request.model_dump(mode="json", exclude={"revision", "idempotency_key", "token"})
            entry.update(id=uid("event"), item_label=row["label"] if row else "", point_label=point["label"] if point else "")
            plan.setdefault("events", []).append(entry)
            response = self._write_plan(conn, sheet, plan, actor, plan["basis"], "worksheet_add_event")
            remember(conn, project, actor, request.idempotency_key, payload, response)
            return response

    @staticmethod
    def _check_standard_versions(conn, criteria):
        from .specification_preview import split_number, standard_numbers

        documents, pages = {}, {}
        for criterion in criteria:
            identifier = criterion["specification_id"]
            number = criterion.get("document_number")
            source = criterion.get("document_source")
            if number is None or source is None:
                numbers = standard_numbers(criterion["filename"])
                number = split_number(numbers[0]) if len(numbers) == 1 else ""
                stored = conn.execute("SELECT preview_json FROM specifications WHERE id=?", (identifier,)).fetchone()
                source = (json.loads(stored[0]).get("scan_id") if stored else None) or identifier
            if not number:
                continue
            page = (number, criterion.get("page"))
            if documents.setdefault(number, source) != source or pages.setdefault(page, identifier) != identifier:
                raise WorkflowError("specification_version_conflict",
                                    f"同一检项已引用 {number} 的另一版本，请核对后替换原引用。", 409)

    def _resolve_standards(self, conn, project, identity, item, links):
        from .specification_preview import specification_material_scope, split_number, standard_numbers
        seen, criteria = set(), []
        for link in links:
            key = (link.specification_id, link.table, link.row)
            if key in seen:
                raise WorkflowError("duplicate_specification_criterion", "同一标准条款不能重复关联。")
            seen.add(key)
            source = conn.execute("SELECT filename,preview_json FROM specifications WHERE id=?",
                                  (link.specification_id,)).fetchone()
            if source is None:
                raise WorkflowError("specification_missing", "找不到标准原件。")
            preview = json.loads(source["preview_json"])
            registered_projects = {entry[0] for entry in conn.execute(
                "SELECT DISTINCT project FROM specification_assay_components WHERE specification_id=?",
                (link.specification_id,),
            )}
            if ((preview.get("project") and project_row(conn, preview["project"])["project"] != project)
                    or (registered_projects and project not in registered_projects)):
                raise WorkflowError("specification_scope", "此标准尚未登记到当前项目。")
            material_scope = specification_material_scope(source["filename"], preview)
            if material_scope == "reference" and identity.purpose != "reference":
                raise WorkflowError("specification_scope", "参考品标准只能关联到参考品研究表。")
            if material_scope in {"DS", "DP", "placebo"} and (
                identity.sample_type != material_scope or identity.purpose == "reference"
            ):
                raise WorkflowError("specification_scope", "所选标准的适用对象与研究表不符。")
            criterion = next((row for row in preview["rows"] if (row["table"], row["row"]) == key[1:]), None)
            if criterion is None:
                raise WorkflowError("specification_criterion_missing", "找不到指定标准条款。")
            if link.lower is not None and link.upper is not None and link.lower > link.upper:
                raise WorkflowError("specification_plot_limits", "趋势图下限不能大于上限。")
            bounds = {}
            if link.lower is not None or link.upper is not None:
                if item["result_type"] != "numeric" or not item["unit"]:
                    raise WorkflowError("specification_plot_limits", "只有单位明确的数值检项可以设置趋势标准线。")
                bounds = {"plot_limits": {"lower": link.lower, "upper": link.upper, "unit": item["unit"]}}
            criteria.append({**criterion, "specification_id": link.specification_id,
                             "filename": source["filename"], "scope": preview["scope"], **bounds})
            numbers = standard_numbers(source["filename"])
            criteria[-1]["document_number"] = preview.get("document_number") or (
                split_number(numbers[0]) if len(numbers) == 1 else ""
            )
            criteria[-1]["document_source"] = preview.get("scan_id") or link.specification_id
        self._check_standard_versions(conn, criteria)
        return criteria

    def _archive_impact(self, conn, target, kind):
        project, target_id = target["project"], target["id"]
        sheets = [target] if kind == "worksheet" else conn.execute(
            "SELECT * FROM business_worksheets WHERE sample_id=? AND deleted_at IS NULL ORDER BY id",
            (target_id,),
        ).fetchall()
        sheet_ids = {sheet["id"] for sheet in sheets}
        slots = {row["slot_id"] for sheet in sheets for row in conn.execute(
            "SELECT slot_id FROM business_worksheet_slots WHERE sheet_id=?", (sheet["id"],))}
        records = [version for version in versions(conn, project)
                   if version["state"] in {"confirmed", "legacy"}
                   and (version["slot_id"] in slots or (kind == "sample" and
                        StabilityRecord.model_validate_json(version["record_json"]).key.sample_id == target_id))]
        impact = {
            "id": target_id, "kind": kind, "project_revision": project_row(conn, project)["revision"],
            "worksheets": [{"id": sheet["id"], "name": sheet["name"], "revision": sheet["revision"]}
                           for sheet in sheets],
            "results": [{"id": version["id"],
                         "value": StabilityRecord.model_validate_json(version["record_json"]).result.raw_text,
                         "studies": self.result_versions._invalidation_studies(
                             conn, project, version["slot_id"], version["id"])}
                        for version in sorted(records, key=lambda entry: entry["id"])],
        }
        return sheet_ids, records, {**impact, "fingerprint": digest(impact)}

    def archive_object(self, object_id, kind, actor, command=None):
        require(actor, Capability.CONFIRM_FACT)
        table = {"worksheet": "business_worksheets", "sample": "sample_entities"}[kind]
        with (self.store.read() if command is None else self.store.connect()) as conn:
            if command is not None:
                conn.execute("BEGIN IMMEDIATE")
            target = conn.execute(f"SELECT * FROM {table} WHERE id=?", (object_id,)).fetchone()
            if target is None:
                raise WorkflowError("not_found", "找不到要删除的对象。", 404)
            project = target["project"]
            if command is not None:
                payload = {"archive": kind, "id": object_id, **command.model_dump(mode="json")}
                prior = replay(conn, project, actor, command.idempotency_key, payload)
                if prior is not None:
                    return prior
            if target["deleted_at"] is not None:
                raise WorkflowError("not_found", "此对象已删除。", 404)
            sheet_ids, records, impact = self._archive_impact(conn, target, kind)
            if command is None:
                return impact
            if command.fingerprint != impact["fingerprint"]:
                raise WorkflowError("archive_impact_changed", "删除影响已变化，请重新预览。", 409)
            if any(self._lease(conn, sheet_id) for sheet_id in sheet_ids):
                raise WorkflowError("worksheet_occupied", "请退出受影响研究表的编辑模式后重试。", 409)
            affected = set(sheet_ids)
            for version in records:
                affected.update(study["id"] for study in self.result_versions.invalidate_in_transaction(
                    conn, version, reason=command.basis, actor=actor))
            for sheet_id in sorted(sheet_ids):
                positions = [dict(row) for row in conn.execute(
                    "SELECT row_id,point_id,slot_id FROM worksheet_result_positions WHERE sheet_id=? ORDER BY row_id,point_id",
                    (sheet_id,))]
                original = self._sheet(conn, sheet_id)
                event(conn, project, sheet_id, "worksheet_archive_positions", actor, command.basis, {
                    "identity": json.loads(original["identity_json"]), "positions": positions,
                    "plan_version": original["plan_version"],
                })
                conn.execute("DELETE FROM worksheet_result_positions WHERE sheet_id=?", (sheet_id,))
                conn.execute("DELETE FROM business_worksheet_slots WHERE sheet_id=?", (sheet_id,))
                # Keep the identity fields readable while freeing the legacy unique key for a new plan.
                conn.execute("""UPDATE business_worksheets
                    SET deleted_at=CURRENT_TIMESTAMP, identity_json=json_set(identity_json,'$.archived_sheet_id',id)
                    WHERE id=?""", (sheet_id,))
            conn.executemany("UPDATE business_worksheets SET revision=revision+1 WHERE id=?",
                             [(sheet_id,) for sheet_id in sorted(affected)])
            if kind == "sample":
                conn.execute("UPDATE sample_entities SET deleted_at=CURRENT_TIMESTAMP WHERE id=?", (object_id,))
            event(conn, project, object_id, kind + "_archive", actor, command.basis, impact)
            response = {"id": object_id, "project_revision": bump(conn, project),
                        "invalidated_version_ids": [item["id"] for item in impact["results"]]}
            remember(conn, project, actor, command.idempotency_key, payload, response)
            return response

    def _row_removal(self, conn, sheet, row_id):
        plan = self._plan(conn, sheet)
        row = next((item for item in plan["rows"] if item["id"] == row_id), None)
        if row is None or len(plan["rows"]) <= 1:
            raise WorkflowError("plan_item_scope", "请选择本表检项，且至少保留一个检项。")
        project = sheet["project"]
        identity = StudyIdentity.model_validate_json(sheet["identity_json"])
        slots = sorted({self._key(sheet, identity, row, point).slot_id for point in plan["points"]})
        records = [version for slot in slots for version in versions(conn, project, slot)
                   if version["state"] in {"confirmed", "legacy"}]
        impact = {
            "sheet_revision": sheet["revision"], "project_revision": project_row(conn, project)["revision"],
            "row_id": row_id, "label": row["label"],
            "release_reference": plan.get("release_references", {}).get(row_id),
            "events": [item for item in plan.get("events", []) if item.get("row_id") == row_id],
            "results": [{"id": version["id"],
                         "value": StabilityRecord.model_validate_json(version["record_json"]).result.raw_text,
                         "studies": self.result_versions._invalidation_studies(
                             conn, project, version["slot_id"], version["id"])}
                        for version in records],
        }
        return plan, row, slots, records, {**impact, "fingerprint": digest(impact)}

    def preview_remove_row(self, sheet_id, row_id, actor):
        require(actor, Capability.CONFIRM_FACT)
        with self.store.read() as conn:
            return self._row_removal(conn, self._sheet(conn, sheet_id), row_id)[-1]

    def remove_row(self, sheet_id, request, actor):
        require(actor, Capability.CONFIRM_FACT)
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            sheet = self._sheet(conn, sheet_id)
            project = sheet["project"]
            payload = {"worksheet_remove_row": sheet_id, **request.model_dump(mode="json")}
            if (prior := replay(conn, project, actor, request.idempotency_key, payload)) is not None:
                return prior
            self._check_mutation(conn, sheet, request, actor)
            plan, row, slots, records, impact = self._row_removal(conn, sheet, request.row_id)
            if ((request.fingerprint or records or impact["release_reference"] or impact["events"]
                 or any(item["row_id"] == row["id"] for item in plan.get("stopped_checks", [])))
                    and request.fingerprint != impact["fingerprint"]):
                raise WorkflowError("worksheet_row_impact_changed", "请重新预览并确认移除检项的影响。", 409)
            affected = set()
            for version in records:
                studies = self.result_versions.invalidate_in_transaction(
                    conn, version, reason=request.basis, actor=actor, locked_sheet_id=sheet_id)
                affected.update(study["id"] for study in studies if study["id"] != sheet_id)
            conn.executemany("UPDATE business_worksheets SET revision=revision+1 WHERE id=?",
                             [(item,) for item in sorted(affected)])
            plan["rows"] = [item for item in plan["rows"] if item["id"] != row["id"]]
            plan.get("pending_release_references", {}).pop(row["id"], None)
            plan.get("release_references", {}).pop(row["id"], None)
            for field in ("events", "stopped_checks"):
                if field in plan:
                    plan[field] = [item for item in plan[field] if item.get("row_id") != row["id"]]
            removed_key = row.get("plan_key", row["item_id"])
            for source_row, keys in plan.get("protocol_row_items", {}).items():
                plan["protocol_row_items"][source_row] = [key for key in keys if key != removed_key]
            for point in plan["points"]:
                point["required_row_ids"] = [key for key in point["required_row_ids"] if key != row["id"]]
            for slot in slots:
                conn.execute("DELETE FROM worksheet_result_positions WHERE sheet_id=? AND slot_id=?", (sheet_id, slot))
                conn.execute("DELETE FROM business_worksheet_slots WHERE sheet_id=? AND slot_id=?", (sheet_id, slot))
            response = self._write_plan(conn, sheet, plan, actor, request.basis, "worksheet_remove_row")
            response["invalidated_version_ids"] = [version["id"] for version in records]
            remember(conn, project, actor, request.idempotency_key, payload, response)
            return response

    def stop_checks(self, sheet_id, request, actor):
        require(actor, Capability.CONFIRM_FACT)
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            sheet = self._sheet(conn, sheet_id)
            project = sheet["project"]
            payload = {"worksheet_stop_checks": sheet_id, **request.model_dump(mode="json")}
            if (prior := replay(conn, project, actor, request.idempotency_key, payload)) is not None:
                return prior
            self._check_mutation(conn, sheet, request, actor)
            plan = self._plan(conn, sheet)
            row = next((item for item in plan["rows"] if item["id"] == request.row_id), None)
            ids = set(request.point_ids)
            points = [point for point in plan["points"] if point["id"] in ids]
            if not row or len(points) != len(request.point_ids):
                raise WorkflowError("plan_item_scope", "请选择本表的一个检项及不重复的时间点。")
            identity = StudyIdentity.model_validate_json(sheet["identity_json"])
            today = date.today().isoformat()
            for point in points:
                if row["id"] not in point["required_row_ids"] or point["planned_date"] < today:
                    raise WorkflowError("plan_point_scope", "仅能停检尚未过期的应检时间点。")
                slot = self._key(sheet, identity, row, point).slot_id
                zero = point["unit"] == "M" and point["amount"] == 0
                if (versions(conn, project, slot, include_invalidated=True)
                        or (zero and any(row["id"] in plan.get(key, {}) for key in
                                         ("release_references", "pending_release_references")))
                        or any(entry.get("point_id") == point["id"] and entry.get("row_id") in (None, row["id"])
                               for entry in plan.get("events", []))):
                    raise WorkflowError("plan_point_has_history", "所选时间点已有结果、放行引用或研究事件，不能停检。", 409)
            for point in points:
                point["required_row_ids"].remove(row["id"])
            plan.setdefault("stopped_checks", []).append({
                "row_id": row["id"], "point_ids": list(request.point_ids), "basis": request.basis,
            })
            response = self._write_plan(conn, sheet, plan, actor, request.basis, "worksheet_stop_checks")
            remember(conn, project, actor, request.idempotency_key, payload, response)
            return response

    def set_standards(self, sheet_id, request, actor):
        require(actor, Capability.CONFIRM_FACT)
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            sheet = self._sheet(conn, sheet_id)
            project = sheet["project"]
            payload = {"worksheet_standards": sheet_id, **request.model_dump(mode="json")}
            prior = replay(conn, project, actor, request.idempotency_key, payload)
            if prior is not None:
                return prior
            self._check_mutation(conn, sheet, request, actor)
            plan = self._plan(conn, sheet)
            item = next((row for row in plan["rows"] if row["id"] == request.row_id), None)
            if item is None:
                raise WorkflowError("plan_item_scope", "请选择本研究表的检项。")
            identity = StudyIdentity.model_validate_json(sheet["identity_json"])
            criteria = self._resolve_standards(conn, project, identity, item, request.criteria)
            item["standards"] = criteria
            if request.criteria_text is not None:
                item["criteria_text"] = request.criteria_text.strip()
            response = self._write_plan(conn, sheet, plan, actor, request.basis, "worksheet_standards")
            remember(conn, project, actor, request.idempotency_key, payload, response)
            return response

    def _coa_release_options(self, conn, sheet, row, *, exact_standard=False):
        if not sheet["sample_id"]:
            return []
        identity = StudyIdentity.model_validate_json(sheet["identity_json"])
        if row.get("reporting_basis", identity.reporting_basis):
            return []
        config = ProjectConfig.model_validate_json(project_row(conn, sheet["project"])["config_json"])
        item = next((item for item in config.items if item.id == row["item_id"]), None)
        if not item or any(getattr(item, field) != row[field] for field in ("method", "method_version", "unit", "result_type")):
            return []
        options = []
        standards = row.get("standards", [])
        if exact_standard:
            registered = conn.execute("""SELECT a.method,a.method_version,c.unit,c.result_type,c.reporting_basis
                FROM assay_components c JOIN assay_definitions a ON a.id=c.assay_id AND a.project=c.project
                WHERE c.project=? AND c.item_id=?""", (sheet["project"], row["item_id"])).fetchone()
            if (registered is None or registered["reporting_basis"]
                    or any(registered[field] != row[field] for field in ("method", "method_version", "unit", "result_type"))):
                return []
            links = [SpecificationCriterion(specification_id=s["specification_id"], table=s["table"], row=s["row"])
                     for s in standards]
            standards = self._resolve_standards(conn, sheet["project"], identity, row, links)
        for standard in standards:
            spec_id = standard["specification_id"]
            component = conn.execute("""SELECT item_id FROM specification_assay_components
                WHERE project=? AND specification_id=? AND table_number=? AND row_number=?""",
                (sheet["project"], spec_id, standard["table"], standard["row"])).fetchone()
            if component is None or component["item_id"] != row["item_id"]:
                continue
            key = f"{standard['table']}:{standard['row']}"
            for entry in conn.execute("""SELECT version,changed_json,source_id FROM coa_versions
                WHERE sample_id=? AND specification_id=? ORDER BY version DESC""", (sheet["sample_id"], spec_id)):
                changes = json.loads(entry["changed_json"])
                if key in changes:
                    if entry["source_id"] or exact_standard:
                        options.append({"specification_id": spec_id, "version": entry["version"], "source_id": entry["source_id"] or "",
                                        "value": changes[key], "filename": standard["filename"], "criterion_key": key})
                    break

        if exact_standard:
            return options

        # Preserve the dictionary fallback for individually reviewed choices.
        generic = conn.execute("""
            SELECT r.specification_id, r.coa_version AS version, r.source_id, r.raw_value AS value, s.filename
            FROM sample_release_results r
            LEFT JOIN specifications s ON s.id=r.specification_id
            WHERE r.sample_id=? AND r.item_id=?
            ORDER BY r.updated_at DESC
        """, (sheet["sample_id"], row["item_id"])).fetchall()
        for g in generic:
            if not any(opt["specification_id"] == g["specification_id"] and opt["value"] == g["value"] for opt in options):
                options.append({
                    "specification_id": g["specification_id"] or "",
                    "version": g["version"],
                    "source_id": g["source_id"] or "",
                    "value": g["value"],
                    "filename": g["filename"] or "",
                    "criterion_key": ""
                })
        return options

    def coa_release_options(self, sheet_id, row_id, actor):
        require(actor, Capability.VIEW)
        with self.store.read() as conn:
            sheet = self._sheet(conn, sheet_id)
            row = next((item for item in self._plan(conn, sheet)["rows"] if item["id"] == row_id), None)
            return [{**option, "fingerprint": digest(option)}
                    for option in self._coa_release_options(conn, sheet, row)] if row else []

    @staticmethod
    def _release_completed_date(sheet, completed):
        start = date.fromisoformat(sheet["start_date"])
        if completed is None or not completed <= start <= date.fromisoformat(
            planned_date(completed, PlannedPoint(unit="M", amount=1, required_items=()))
        ):
            raise WorkflowError("release_reference_date", "请核对放行完成日期，开始日期须在放行完成后一个月内。")

    @staticmethod
    def _normalize_coa_result(coa, row):
        try:
            result = normalize_worksheet_result(coa["value"], row["unit"], row["result_type"])
        except NeedsReview as exc:
            raise WorkflowError(exc.code, str(exc)) from exc
        if result.status == "blank":
            raise WorkflowError("coa_blank", "不能复制空白放行结果。")
        return result

    def _record_coa_snapshot(self, conn, sheet, plan, row, point, record, coa, *, actor, previous,
                             latest, intent, basis, release_completed=None):
        ensure_worksheet_slot_unlocked(conn, record.key.slot_id, except_sheet_id=sheet["id"])
        source = {"kind": "coa_snapshot", "worksheet_id": sheet["id"], "row_id": row["id"],
                  "point_id": point["id"], "plan_version": sheet["plan_version"], "sample_id": sheet["sample_id"],
                  "coa": coa, "raw_text": coa["value"], "unit": row["unit"]}
        if release_completed is not None:
            source["release_completed"] = release_completed.isoformat()
        version_id = self.result_versions.record_worksheet_result(
            conn, record, actor=actor, previous=previous, current=latest, intent=intent, reason=basis.strip(), source=source,
        )
        for name in ("release_references", "pending_release_references"):
            plan.get(name, {}).pop(row["id"], None)
        return version_id

    def copy_coa_to_zero(self, sheet_id, request, actor):
        require(actor, Capability.CONFIRM_FACT)
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            sheet = self._sheet(conn, sheet_id)
            project = sheet["project"]
            payload = {"worksheet_copy_coa": sheet_id, **request.model_dump(mode="json")}
            prior = replay(conn, project, actor, request.idempotency_key, payload)
            if prior is not None:
                return prior
            self._check_mutation(conn, sheet, request, actor)
            plan = self._plan(conn, sheet)
            row = next((item for item in plan["rows"] if item["id"] == request.row_id), None)
            point = next((item for item in plan["points"] if item["unit"] == "M" and item["amount"] == 0
                          and request.row_id in item["required_row_ids"]), None)
            if row is None or point is None:
                raise WorkflowError("worksheet_cell_scope", "请选择本表 0M 应检项目。")
            matches = [option for option in self._coa_release_options(conn, sheet, row)
                       if digest(option) == request.coa_fingerprint]
            if len(matches) != 1:
                raise WorkflowError("coa_source_changed", "放行结果已变化，请重新选择。", 409)
            coa = matches[0]
            identity = StudyIdentity.model_validate_json(sheet["identity_json"])
            key = self._key(sheet, identity, row, point)
            previous = adopted(conn, project, key.slot_id)
            latest = current(versions(conn, project, key.slot_id))
            if request.expected_version_id != (previous["id"] if previous else None):
                raise WorkflowError("worksheet_result_changed", "0M 采用结果已变化，请重新核对。", 409)
            result = self._normalize_coa_result(coa, row)
            old = StabilityRecord.model_validate_json(previous["record_json"]) if previous else None
            if request.intent == "keep" or (old and old.result == result):
                response = {"id": sheet_id, "revision": sheet["revision"], "result_version_ids": []}
            else:
                record = StabilityRecord(key=key, result=result, standards=old.standards if old else ())
                version_id = self._record_coa_snapshot(
                    conn, sheet, plan, row, point, record, coa, actor=actor, previous=previous,
                    latest=latest, intent=request.intent, basis=request.basis,
                )
                response = self._write_plan(conn, sheet, plan, actor, request.basis, "worksheet_copy_coa")
                response["result_version_ids"] = [version_id]
            remember(conn, project, actor, request.idempotency_key, payload, response)
            return response

    def _coa_batch_options(self, conn, sheet, row, sample):
        if sample is None:
            return [], "sample_missing"
        try:
            options = self._coa_release_options(conn, sheet, row, exact_standard=True)
        except WorkflowError:
            return [], "standard_scope_conflict"
        if len(options) > 1:
            return options, "multiple_coa_matches"
        if not options:
            return [], "no_applicable_coa"
        try:
            self._normalize_coa_result(options[0], row)
        except WorkflowError:
            return options, "invalid_coa_value"
        return options, None

    def _coa_batch_rows(self, conn, sheet, plan, point, identity):
        project = sheet["project"]
        sample = conn.execute("""SELECT * FROM sample_entities
            WHERE project=? AND id=? AND deleted_at IS NULL""", (project, sheet["sample_id"])).fetchone()
        if sample is not None and not sample_matches_study(sample, identity):
            sample = None
        positions = dict(conn.execute("""SELECT row_id,slot_id FROM worksheet_result_positions
            WHERE sheet_id=? AND point_id=?""", (sheet["id"], point["id"])))
        for row in plan["rows"]:
            row_id = row["id"]
            if row_id not in point["required_row_ids"]:
                continue
            slot_id = self._key(sheet, identity, row, point).slot_id
            previous = adopted(conn, project, slot_id)
            candidates, reason = self._coa_batch_options(conn, sheet, row, sample)
            if previous:
                status, reason = "existing", None
            elif any(row_id in plan.get(key, {}) for key in ("release_references", "pending_release_references")):
                status, reason = "conflict", "release_reference_exists"
            elif versions(conn, project, slot_id, include_invalidated=True):
                status, reason = "conflict", "result_history_exists"
            elif positions.get(row_id) != slot_id:
                status, reason = "conflict", "worksheet_slot_conflict"
            elif reason in {"sample_missing", "no_applicable_coa"}:
                status = "unmatched"
            elif reason:
                status = "conflict"
            else:
                try:
                    ensure_worksheet_slot_unlocked(conn, slot_id, except_sheet_id=sheet["id"])
                    status = "matched"
                except WorkflowError:
                    status, reason = "conflict", "worksheet_occupied"
            yield {
                "row_id": row_id, "item_id": row["item_id"], "label": row["label"], "method": row["method"],
                "method_version": row["method_version"], "unit": row["unit"], "slot_id": slot_id,
                "status": status, "reason": reason, "candidates": candidates,
                "adopted_version_id": previous["id"] if previous else None,
                "adopted_value": StabilityRecord.model_validate_json(previous["record_json"]).result.raw_text if previous else None,
            }

    def _coa_batch_preview(self, conn, sheet, plan, point, identity, completed):
        self._release_completed_date(sheet, completed)
        preview = {"id": sheet["id"], "revision": sheet["revision"], "sample_id": sheet["sample_id"],
                   "point_id": point["id"] if point else None, "release_completed": completed.isoformat(),
                   "rows": list(self._coa_batch_rows(conn, sheet, plan, point, identity)) if point else []}
        return {**preview, "preview_fingerprint": digest(preview)}

    def preview_copy_coa_batch(self, sheet_id, request, actor):
        require(actor, Capability.VIEW)
        with self.store.read() as conn:
            sheet = self._sheet(conn, sheet_id)
            plan = self._plan(conn, sheet)
            point = next((p for p in plan["points"] if p["unit"] == "M" and p["amount"] == 0), None)
            identity = StudyIdentity.model_validate_json(sheet["identity_json"])
            return self._coa_batch_preview(conn, sheet, plan, point, identity, request.release_completed)

    def copy_coa_batch_to_zero(self, sheet_id, request, actor):
        require(actor, Capability.CONFIRM_FACT)
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            sheet = self._sheet(conn, sheet_id)
            project = sheet["project"]
            payload = {"worksheet_copy_coa_batch": sheet_id, **request.model_dump(mode="json", exclude={"token"})}
            if (prior := replay(conn, project, actor, request.idempotency_key, payload)) is not None:
                return prior
            self._check_mutation(conn, sheet, request, actor)
            plan = self._plan(conn, sheet)
            point = next((p for p in plan["points"] if p["unit"] == "M" and p["amount"] == 0), None)
            identity = StudyIdentity.model_validate_json(sheet["identity_json"])
            preview = self._coa_batch_preview(conn, sheet, plan, point, identity, request.release_completed)
            if preview["preview_fingerprint"] != request.preview_fingerprint:
                raise WorkflowError("coa_batch_preview_changed", "放行来源或 0M 状态已变化，请重新预览。", 409)
            matched = {row["row_id"]: row["candidates"][0] for row in preview["rows"] if row["status"] == "matched"}
            selected = set(request.row_ids)
            if len(selected) != len(request.row_ids) or not selected <= matched.keys():
                raise WorkflowError("coa_batch_selection", "仅可批量复制预览中唯一匹配且无正式记录的 0M 检项。", 409)
            copied = {}
            for row in plan["rows"]:
                if row["id"] not in selected:
                    continue
                coa = matched[row["id"]]
                record = StabilityRecord(key=self._key(sheet, identity, row, point),
                                         result=self._normalize_coa_result(coa, row))
                copied[row["id"]] = self._record_coa_snapshot(
                    conn, sheet, plan, row, point, record, coa, actor=actor, previous=None, latest=None,
                    intent="initial", basis=request.basis, release_completed=request.release_completed,
                )
            response = self._write_plan(conn, sheet, plan, actor, request.basis, "worksheet_copy_coa_batch",
                                        detail={"copied": copied, "preview_fingerprint": request.preview_fingerprint})
            response.update(copied=copied, result_version_ids=list(copied.values()))
            remember(conn, project, actor, request.idempotency_key, payload, response)
            return response

    def set_release_reference(self, sheet_id, request, actor):
        require(actor, Capability.CONFIRM_FACT)
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            sheet = self._sheet(conn, sheet_id)
            project = sheet["project"]
            payload = {"worksheet_release_reference": sheet_id, **request.model_dump(mode="json")}
            if request.coa_specification_id is None and request.coa_version is None:
                del payload["coa_specification_id"], payload["coa_version"]
            prior = replay(conn, project, actor, request.idempotency_key, payload)
            if prior is not None:
                return prior
            self._check_mutation(conn, sheet, request, actor)
            plan = self._plan(conn, sheet)
            row = next((r for r in plan["rows"] if r["id"] == request.row_id), None)
            point = next((p for p in plan["points"] if p["unit"] == "M" and p["amount"] == 0), None)
            if row is None or point is None or row["id"] not in point["required_row_ids"]:
                raise WorkflowError("release_reference_scope", "请选择本表的 0M 应检项目。")
            references = plan.setdefault("release_references", {})
            if bool(request.coa_specification_id) != bool(request.coa_version) or (request.coa_specification_id and request.result_version_id):
                raise WorkflowError("release_reference_scope", "请选择一条可核验的放行来源。")
            if request.result_version_id is None and request.coa_specification_id is None:
                if request.release_completed is not None:
                    raise WorkflowError("release_reference_date", "解除引用时无需填写放行完成日期。")
                references.pop(row["id"], None)
            else:
                identity = StudyIdentity.model_validate_json(sheet["identity_json"])
                expected = self._key(sheet, identity, row, point)
                if versions(conn, project, expected.slot_id, include_invalidated=True):
                    raise WorkflowError("release_reference_result_exists", "此格已有检测记录，不能用放行引用替换。")
                provenance = {}
                if request.coa_specification_id:
                    matches = [option for option in self._coa_release_options(conn, sheet, row)
                               if (option["specification_id"], option["version"]) ==
                               (request.coa_specification_id, request.coa_version)]
                    if len(matches) != 1:
                        raise WorkflowError("release_reference_identity", "没有唯一匹配且可追溯的 COA 条款。")
                    coa = matches[0]
                    version_id = "coa_" + digest([sheet["sample_id"], coa])
                    provenance = {"coa": coa}
                else:
                    source = next((result for result in versions(conn, project) if result["id"] == request.result_version_id), None)
                    if source is None or adoption_choices(conn, project).get(source["slot_id"]) != source["id"]:
                        raise WorkflowError("release_reference_not_adopted", "请选择当前正式采用的放行结果。")
                    record = StabilityRecord.model_validate_json(source["record_json"])
                    fields = ("project", "sample_type", "batch", "presentation", "purpose", "item",
                              "method", "method_version", "reporting_basis")
                    if record.key.study != "release" or record.result.unit != row["unit"] or any(
                        getattr(record.key, field) != getattr(expected, field) for field in fields
                    ):
                        raise WorkflowError("release_reference_identity", "放行结果与 0M 检项的样品、方法或单位不符。")
                    version_id = source["id"]
                completed = request.release_completed
                self._release_completed_date(sheet, completed)
                references[row["id"]] = {"result_version_id": version_id, "release_completed": completed.isoformat(),
                                        "basis": request.basis, **provenance}
            plan.get("pending_release_references", {}).pop(row["id"], None)
            response = self._write_plan(conn, sheet, plan, actor, request.basis, "worksheet_release_reference")
            remember(conn, project, actor, request.idempotency_key, payload, response)
            return response

    def save(self, sheet_id, request, actor, *, _conn=None, file_source=None):
        require(actor, Capability.CONFIRM_FACT)
        with (self.store.connect() if _conn is None else nullcontext(_conn)) as conn:
            if _conn is None:
                conn.execute("BEGIN IMMEDIATE")
            sheet = self._sheet(conn, sheet_id)
            project = sheet["project"]
            payload = {"worksheet_save": sheet_id, **request.model_dump(mode="json")}
            if file_source:
                payload["file_source"] = [[*position, source] for position, source in sorted(file_source.items())]
            prior = replay(conn, project, actor, request.idempotency_key, payload)
            if prior is not None:
                return prior
            self._check_mutation(conn, sheet, request, actor)
            if not request.changes and request.layout is None:
                raise WorkflowError("worksheet_no_changes", "没有需要保存的改动。")
            plan = self._plan(conn, sheet)
            identity = StudyIdentity.model_validate_json(sheet["identity_json"])
            rows = {row["id"]: row for row in plan["rows"]}
            points = {point["id"]: point for point in plan["points"]}
            changed_cells = set()
            version_ids = []
            invalidated_ids = []
            affected_sheets = set()
            for change in request.changes:
                cell = (change.row_id, change.point_id)
                row, point = rows.get(change.row_id), points.get(change.point_id)
                if cell in changed_cells or not row or not point or change.row_id not in point["required_row_ids"]:
                    raise WorkflowError("worksheet_cell_scope", "保存范围含重复单元格或非应检单元格。")
                changed_cells.add(cell)
                key = self._key(sheet, identity, row, point)
                members = versions(conn, project, key.slot_id)
                latest = current(members)
                if point["unit"] == "M" and point["amount"] == 0 and (
                    change.row_id in plan.get("release_references", {}) or change.row_id in plan.get("pending_release_references", {})
                ):
                    raise WorkflowError("release_reference_locked", "此格引用放行结果，请先解除引用。", 409)
                previous = adopted(conn, project, key.slot_id)
                expected = (previous or latest) if change.intent == "correction" else latest
                if change.expected_version_id != (expected["id"] if expected else None):
                    raise WorkflowError("worksheet_result_changed", "该单元格结果已变化，请重新打开核对。", 409)
                raw = change.value
                unit = (StabilityRecord.model_validate_json(previous["record_json"]).result.unit
                        if change.intent == "correction" and previous else row["unit"])
                try:
                    result = normalize_worksheet_result(raw, unit, row["result_type"])
                except NeedsReview as exc:
                    raise WorkflowError(exc.code, f'{row["label"]} {point["label"]}：{exc}') from exc
                if result.status == "blank":
                    if file_source is not None or change.intent != "correction" or previous is None:
                        raise WorkflowError("worksheet_blank", "请选择当前采用结果后明确清空。")
                    studies = self.result_versions.invalidate_in_transaction(
                        conn, previous, reason=request.reason.strip() or "人工清空单元格",
                        actor=actor, locked_sheet_id=sheet_id,
                    )
                    invalidated_ids.append(previous["id"])
                    affected_sheets.update(study["id"] for study in studies if study["id"] != sheet_id)
                    continue
                standards = StabilityRecord.model_validate_json(previous["record_json"]).standards if previous else ()
                record = StabilityRecord(key=key, result=result, standards=standards)
                source = {"kind": "worksheet_entry", "worksheet_id": sheet_id,
                          "row_id": change.row_id, "point_id": change.point_id, "plan_version": sheet["plan_version"],
                          "raw_text": raw, "unit": unit, "result_type": row["result_type"]}
                if file_source:
                    source.update(file_source[(change.row_id, change.point_id)])
                version_ids.append(self.result_versions.record_worksheet_result(
                    conn, record, actor=actor, previous=previous, current=latest, intent=change.intent,
                    reason=request.reason.strip(), source=source,
                ))
            if request.layout is not None:
                layout = request.layout
                if (any(not 0 <= index < len(rows) + 2 or not 18 <= size <= 350 for index, size in layout.row_heights.items())
                        or any(not 0 <= index < len(points) + 1 or not 40 <= size <= 700 for index, size in layout.column_widths.items())
                        or layout.title_merge_to > len(points)):
                    raise WorkflowError("worksheet_layout_scope", "行高、列宽或表头合并超出支持范围。")
                conn.execute("UPDATE business_worksheets SET layout_json=? WHERE id=?", (layout.model_dump_json(), sheet_id))
            conn.execute("UPDATE business_worksheets SET revision=revision+1 WHERE id=?", (sheet_id,))
            conn.executemany("UPDATE business_worksheets SET revision=revision+1 WHERE id=?",
                             [(item,) for item in sorted(affected_sheets)])
            event(conn, project, sheet_id, "worksheet_save", actor, request.reason,
                  {"result_version_ids": version_ids, "invalidated_version_ids": invalidated_ids,
                   "previous_revision": sheet["revision"]})
            response = {"id": sheet_id, "revision": sheet["revision"] + 1, "result_version_ids": version_ids,
                        "project_revision": bump(conn, project)}
            remember(conn, project, actor, request.idempotency_key, payload, response)
            return response
