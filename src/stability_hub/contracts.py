"""UI contracts, independent from SQLite rows. Generate JSON Schema for the frontend."""

from typing import Literal

from pydantic import Field, model_validator

from .models import Location, Model, StabilityRecord, Text
from .permissions import Actor

CandidateStatus = Literal["new", "same_value", "empty", "different_value", "needs_review"]


class Candidate(Model):
    id: Text
    location: Location
    raw_fields: dict[str, str]
    status: CandidateStatus
    record: StabilityRecord | None = None
    existing_record: StabilityRecord | None = None
    issue_code: str | None = None
    detail: str
    comment: str = ""


class Preview(Model):
    contract_version: Literal["1"] = "1"
    preview_id: Text
    project: Text
    filename: Text
    source_hash: Text
    base_revision: Text
    state: Literal["preview_ready"] = "preview_ready"
    candidates: tuple[Candidate, ...]
    counts: dict[CandidateStatus, int]
    can_commit: Literal[False] = False


class ResultValue(Model):
    result_version_id: Text
    detection_id: Text
    text: Text
    pending_confirmation: bool
    source_ids: tuple[Text, ...] = ()


class ResultCell(Model):
    """Display and formal adoption are separate; selection lives in result_queries."""

    slot_id: Text
    display: ResultValue | None
    adopted: ResultValue | None

    @model_validator(mode="after")
    def adopted_is_confirmed(self):
        if self.adopted and self.adopted.pending_confirmation:
            raise ValueError("A formally adopted result cannot be pending confirmation")
        return self


class ApiError(Model):
    code: Text
    message: Text
    details: dict[str, str] = Field(default_factory=dict)


class ContractSchema(Model):
    """Schema index only; not an HTTP response envelope."""

    actor: Actor
    preview: Preview
    result_cell: ResultCell
    error: ApiError
