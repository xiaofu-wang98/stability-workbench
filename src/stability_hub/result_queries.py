"""The single source of truth for table cells and Excel selection."""

import json

from .contracts import ResultValue
from .models import StabilityRecord
from .reporting import display
from .workflow_common import project_row
from .workflow_contracts import WorkflowResultCell
from .identity_associations import versions, current, adoption_choices


def result_value(row):
    record = StabilityRecord.model_validate_json(row["record_json"])
    unit = record.result.unit
    suffix = ("%" if unit == "%" else " " + unit) if unit and record.result.status == "numeric" else ""
    return ResultValue(
        result_version_id=row["id"], detection_id=row["detection_id"],
        text=display(record.result.model_dump(mode="json")) + suffix,
        pending_confirmation=row["state"] == "pending",
        source_ids=tuple(s["source_id"] for s in json.loads(row["source_json"])),
    )


def selected_rows(conn, project, filters):
    project_row(conn, project)
    rows = versions(conn,project)
    adoptions = adoption_choices(conn,project)
    all_by_id = {row["id"]: row for row in rows}
    by_slot = {}
    for row in rows:
        record = StabilityRecord.model_validate_json(row["record_json"])
        key = record.key.model_dump()
        if any(key[k] != v for k, v in filters.model_dump(exclude_none=True).items()):
            continue
        by_slot.setdefault(row['slot_id'],[]).append(row)
    return [(current(members), all_by_id.get(adoptions.get(slot))) for slot,members in sorted(by_slot.items())]


def cells_from_rows(rows):
    return tuple(WorkflowResultCell(
        slot_id=current["slot_id"], slot_key=StabilityRecord.model_validate_json(current["record_json"]).key,
        display=result_value(current), adopted=result_value(adopted) if adopted else None,
        display_status=current["state"], upload_sequence=current["upload_sequence"],
    ) for current, adopted in rows)
