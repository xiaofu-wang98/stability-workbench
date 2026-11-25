from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Result(Model):
    interpretation: dict[str, str] = Field(default_factory=dict)
    status: Literal["numeric", "na", "nd", "placeholder", "blank", "complies", "text", "not_reported"]
    raw_text: str
    report_references: tuple[str, ...] = ()
    value: Decimal | None = Field(default=None, allow_inf_nan=False)
    comparator: Literal["eq", "lt", "le", "gt", "ge"] | None = None
    unit: str = ""

    @model_validator(mode="after")
    def check_payload(self):
        if self.status == "numeric":
            if self.value is None or self.comparator is None:
                raise ValueError("numeric result requires value and comparator")
        elif self.value is not None or self.comparator is not None:
            raise ValueError("non-numeric result cannot carry a numeric value")
        return self

    def equivalent(self, other: Result) -> bool:
        if self.status != other.status or self.unit != other.unit:
            return False
        if self.status == "numeric":
            return self.value == other.value and self.comparator == other.comparator
        if self.status in {"na", "nd", "complies"}:
            return True
        return self.raw_text.strip() == other.raw_text.strip()


class RecordKey(Model):
    project: Text
    sample_id: Text | None = Field(default=None, exclude_if=lambda value: value is None)
    sample_type: Literal["DS", "DP", "placebo"]
    purpose: Literal["unknown", "routine", "reference"] = "unknown"
    reporting_basis: str = ""
    batch: Text
    presentation: Text
    study: Literal["release", "long_term", "accelerated", "stress"]
    study_id: Text
    condition: Text
    month: int | None = Field(default=None, ge=0, strict=True)
    day: int | None = Field(default=None, ge=0, strict=True)
    item: Text
    method: Text
    method_version: Text = "not_reported"

    @model_validator(mode="after")
    def validate_time(self):
        if self.month is not None and self.day is not None:
            raise ValueError("time point cannot be both days and months")
        if (self.study == "release") != (self.month is None and self.day is None):
            raise ValueError("release has no stability month; stability requires a month")
        return self

    @property
    def time_label(self) -> str:
        return f"{self.day}D" if self.day is not None else f"{self.month}M" if self.month is not None else ""

    @property
    def slot_id(self) -> str:
        payload = self.model_dump(mode="json")
        if self.day is None:
            payload.pop("day")  # Preserve all previously issued slot identities.
        if self.purpose == "unknown":
            payload.pop("purpose")  # Unspecified legacy purpose is never inferred as routine.
        if not self.reporting_basis:
            payload.pop("reporting_basis")
        return digest(payload)


class Location(Model):
    format: Literal["xlsx", "docx"]
    section: Annotated[str, StringConstraints(min_length=1)]  # Preserve exact worksheet names, including trailing spaces.
    row: int = Field(ge=1)
    column: int = Field(ge=1)


class RawObservation(Model):
    system_hints: tuple[dict, ...] = ()
    reporting_basis: str = ''
    result_anchor: dict = Field(default_factory=dict)
    source_value: dict[str, str] = Field(default_factory=dict)
    logical_row: int | None = None
    source_span: tuple[int, int, int, int] | None = None
    subresult_hint: str = ''
    revisions: dict[str, dict] = Field(default_factory=dict)
    adapter: Literal["long_table", "stability_matrix", "manual_projection"] = "long_table"
    evidence: dict[str, Location] = Field(default_factory=dict)
    fields: dict[str, str]
    location: Location
    comment: str = ""
    formula: str = ""
    number_format: str = ""
    hazards: tuple[str, ...] = ()


class StandardApplicability(Model):
    basis: str = ''
    criterion_text: str
    version: str = ''
    effective_status: Literal['not_reported'] = 'not_reported'
    applicability: Literal['source_reported'] = 'source_reported'
    source: Location


class StabilityRecord(Model):
    key: RecordKey
    result: Result
    standards: tuple[StandardApplicability, ...] = ()

    def equivalent(self, other):
        def semantic(items):
            return sorted((s.basis,s.criterion_text,s.version,s.effective_status,s.applicability) for s in items)
        return self.result.equivalent(other.result) and semantic(self.standards) == semantic(other.standards)


class ImportContext(Model):
    role: Literal["stability_source", "release_coa"] = "stability_source"
    sample_id: Text | None = None
    defaults: dict[str, str] = Field(default_factory=dict)


class ItemDefinition(Model):
    id: Text
    label: Text
    aliases: tuple[Text, ...] = ()
    unit: str = ""
    result_type: Literal["numeric", "qualitative"] = "numeric"
    method: Text
    method_version: Text = "not_reported"
    category_code: str = ""
    subcategory_code: str = ""
    display_order: int = 999
    platform_assay_id: str = ""


class ProjectConfig(Model):
    schema_version: Literal[1] = 1
    project: Text
    name: str = ""
    items: tuple[ItemDefinition, ...] = ()
    presentation_defaults: dict[Literal["DS", "DP"], Text] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_items_and_aliases(self):
        seen: dict[str, str] = {}
        ids: set[str] = set()
        for item in self.items:
            if item.id in ids:
                raise ValueError(f"duplicate item id: {item.id}")
            ids.add(item.id)
            for alias in (item.id, item.label, *item.aliases):
                normalized = " ".join(alias.split()).casefold()
                if normalized in seen and seen[normalized] != item.id:
                    raise ValueError(f"ambiguous alias: {alias}")
                seen[normalized] = item.id
        return self
