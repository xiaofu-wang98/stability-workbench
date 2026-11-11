"""Plan, project and save stability worksheets with server-side editing leases."""

import calendar
import json
import secrets
import time
from contextlib import nullcontext
from datetime import date, timedelta
from typing import Literal

from pydantic import Field

from .identity_associations import adopted, adoption_choices, current, versions
from .identity_links import linked_config
from .matrix_fields import selected_condition
from .models import Model, ProjectConfig, RecordKey, StabilityRecord, Text, digest
from .normalize import NeedsReview, normalize_worksheet_result
from .permissions import Capability, require
from .result_queries import result_value
from .workflow_common import WorkflowError, bump, encoded, event, project_row, remember, replay, uid

LEASE_SECONDS = 90


class StudyIdentity(Model):
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


class PlannedPoint(Model):
    unit: Literal["M", "D"]
    amount: int = Field(ge=0, le=3650, strict=True)
    planned_date: date | None = None
    required_items: tuple[Text, ...] = Field(max_length=150)


class CreateWorksheet(Model):
    idempotency_key: Text
    name: Text
    identity: StudyIdentity
    start_date: date
    basis: str = ""
    items: tuple[Text, ...] = Field(min_length=1, max_length=150)
    points: tuple[PlannedPoint, ...] = Field(min_length=1, max_length=120)


class LeaseToken(Model):
    token: Text


class WorksheetMutation(LeaseToken):
    idempotency_key: Text
    revision: int = Field(ge=1, strict=True)


class ExtendPlan(WorksheetMutation):
    basis: Text
    points: tuple[PlannedPoint, ...] = Field(min_length=1, max_length=120)


class CellChange(Model):
    row_id: Text
    point_id: Text
    expected_version_id: str | None = None
    value: str = Field(min_length=1, max_length=8000)
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
            "required_row_ids": [rows[item]["id"] for item in point.required_items],
        })
    return result


class BusinessWorksheets:
    def __init__(self, workflow, result_versions):
        self.store = workflow.store
        self.result_versions = result_versions

    def list(self, project, actor):
        require(actor, Capability.VIEW)
        with self.store.read() as conn:
            project_row(conn, project)
            return [dict(row) for row in conn.execute(
                "SELECT id,name,revision,plan_version FROM business_worksheets WHERE project=? ORDER BY name,id", (project,)
            )]

    def _sheet(self, conn, sheet_id):
        sheet = conn.execute("SELECT * FROM business_worksheets WHERE id=?", (sheet_id,)).fetchone()
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

    def create(self, project, request, actor):
        require(actor, Capability.CONFIRM_FACT)
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            payload = {"worksheet_create": request.model_dump(mode="json")}
            prior = replay(conn, project, actor, request.idempotency_key, payload)
            if prior is not None:
                return prior
            config = linked_config(conn, ProjectConfig.model_validate_json(project_row(conn, project)["config_json"]))
            request.identity.record_condition()
            items = {item.id: item for item in config.items}
            if len(set(request.items)) != len(request.items) or not set(request.items) <= items.keys():
                raise WorkflowError("plan_item_scope", "请选择项目中已配置且不重复的检测项目。")
            rows = {key: {"id": uid("row"), "item_id": key, **items[key].model_dump(mode="json", exclude={"id", "aliases"})}
                    for key in request.items}
            points = add_points([], request.points, rows, request.start_date)
            sheet_id = uid("worksheet")
            identity_json = request.identity.model_dump_json()
            if conn.execute("SELECT 1 FROM business_worksheets WHERE project=? AND identity_json=?",
                            (project, identity_json)).fetchone():
                raise WorkflowError("worksheet_exists", "此研究条件已建立工作表，请打开现有工作表。", 409)
            conn.execute("""INSERT INTO business_worksheets(id,project,name,identity_json,start_date)
                            VALUES (?,?,?,?,?)""",
                         (sheet_id, project, request.name, identity_json, request.start_date.isoformat()))
            conn.execute("""INSERT INTO business_worksheet_plans(sheet_id,version,plan_json,basis,created_by)
                            VALUES (?,1,?,?,?)""",
                         (sheet_id, encoded({"rows": list(rows.values()), "points": points}), request.basis, actor.id))
            self._register_slots(conn, self._sheet(conn, sheet_id), request.identity, rows.values(), points)
            event(conn, project, sheet_id, "worksheet_create", actor, request.basis, {"plan_version": 1})
            result = {"id": sheet_id, "revision": 1, "project_revision": bump(conn, project)}
            remember(conn, project, actor, request.idempotency_key, payload, result)
            return result

    def acquire(self, sheet_id, actor):
        require(actor, Capability.CONFIRM_FACT)
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._sheet(conn, sheet_id)
            lease = self._lease(conn, sheet_id)
            if lease:
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
            project=sheet["project"], **identity.model_dump(exclude={"orientation", "condition"}),
            condition=identity.record_condition(),
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
            cells = []
            for row in plan["rows"]:
                for point in plan["points"]:
                    slot = self._key(sheet, identity, row, point).slot_id
                    members = grouped.get(slot, ())
                    if not members:
                        continue
                    latest = current(members)
                    adopted_row = next((member for member in members if member["id"] == choices.get(slot)), None)
                    cells.append({
                        "row_id": row["id"], "point_id": point["id"], "slot_id": slot,
                        "display": result_value(latest).model_dump(mode="json"),
                        "adopted": result_value(adopted_row).model_dump(mode="json") if adopted_row else None,
                        "raw": StabilityRecord.model_validate_json((adopted_row or latest)["record_json"]).result.raw_text,
                    })
            lease = self._lease(conn, sheet_id)
            return {
                "id": sheet_id, "project": sheet["project"], "name": sheet["name"], "revision": sheet["revision"],
                "plan_version": sheet["plan_version"], "start_date": sheet["start_date"], "identity": identity.model_dump(),
                **plan, "layout": json.loads(sheet["layout_json"]), "cells": cells,
                "selection_id": digest({"revision": sheet["revision"], "cells": cells}),
                "editable": Capability.CONFIRM_FACT in actor.capabilities,
                "occupied_by": lease["actor_name"] if lease else None,
            }

    def _check_mutation(self, conn, sheet, request, actor):
        self._require_lease(conn, sheet["id"], actor, request.token)
        if sheet["revision"] != request.revision:
            raise WorkflowError("worksheet_revision_changed", "工作表已保存新版本，请刷新并核对修改。", 409)

    def extend(self, sheet_id, request, actor):
        require(actor, Capability.CONFIRM_FACT)
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            sheet = self._sheet(conn, sheet_id)
            project = sheet["project"]
            payload = {"worksheet_extend": sheet_id, **request.model_dump(mode="json")}
            prior = replay(conn, project, actor, request.idempotency_key, payload)
            if prior is not None:
                return prior
            self._check_mutation(conn, sheet, request, actor)
            plan = self._plan(conn, sheet)
            rows = {row["item_id"]: row for row in plan["rows"]}
            points = add_points(plan["points"], request.points, rows, date.fromisoformat(sheet["start_date"]))
            self._register_slots(conn, sheet, StudyIdentity.model_validate_json(sheet["identity_json"]), rows.values(),
                                 points[len(plan["points"]):])
            version = sheet["plan_version"] + 1
            conn.execute("""INSERT INTO business_worksheet_plans(sheet_id,version,plan_json,basis,created_by)
                            VALUES (?,?,?,?,?)""",
                         (sheet_id, version, encoded({"rows": plan["rows"], "points": points}), request.basis, actor.id))
            conn.execute("UPDATE business_worksheets SET plan_version=?,revision=revision+1 WHERE id=?", (version, sheet_id))
            event(conn, project, sheet_id, "worksheet_extend", actor, request.basis, {"plan_version": version})
            response = {"id": sheet_id, "revision": sheet["revision"] + 1, "plan_version": version,
                        "project_revision": bump(conn, project)}
            remember(conn, project, actor, request.idempotency_key, payload, response)
            return response

    def save(self, sheet_id, request, actor):
        require(actor, Capability.CONFIRM_FACT)
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            sheet = self._sheet(conn, sheet_id)
            project = sheet["project"]
            payload = {"worksheet_save": sheet_id, **request.model_dump(mode="json")}
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
            for change in request.changes:
                cell = (change.row_id, change.point_id)
                row, point = rows.get(change.row_id), points.get(change.point_id)
                if cell in changed_cells or not row or not point or change.row_id not in point["required_row_ids"]:
                    raise WorkflowError("worksheet_cell_scope", "保存范围含重复单元格或非应检单元格。")
                changed_cells.add(cell)
                key = self._key(sheet, identity, row, point)
                members = versions(conn, project, key.slot_id)
                latest = current(members)
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
                    raise WorkflowError("worksheet_blank", "已有结果不能通过留空撤销。")
                standards = StabilityRecord.model_validate_json(previous["record_json"]).standards if previous else ()
                record = StabilityRecord(key=key, result=result, standards=standards)
                source = {"kind": "worksheet_entry", "worksheet_id": sheet_id,
                          "row_id": change.row_id, "point_id": change.point_id, "plan_version": sheet["plan_version"],
                          "raw_text": raw, "unit": unit, "result_type": row["result_type"]}
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
            event(conn, project, sheet_id, "worksheet_save", actor, request.reason,
                  {"result_version_ids": version_ids, "previous_revision": sheet["revision"]})
            response = {"id": sheet_id, "revision": sheet["revision"] + 1, "result_version_ids": version_ids,
                        "project_revision": bump(conn, project)}
            remember(conn, project, actor, request.idempotency_key, payload, response)
            return response
