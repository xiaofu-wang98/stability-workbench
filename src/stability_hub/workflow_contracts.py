"""V4 commands and read models; JSON schemas are shared with the frontend."""

from typing import Literal

from pydantic import Field, model_validator

from .contracts import Candidate, ResultCell
from .models import ItemDefinition, Location, Model, ProjectConfig, RecordKey, Text
from .v5_contracts import ReviewIssue


class ConfigurationView(Model):
    config: ProjectConfig
    base_revision: int


class ProjectCreation(Model):
    project: Text
    name: Text


class ItemCreation(ItemDefinition):
    id: Text | None = None


class NewItems(Model):
    base_revision: int = Field(ge=0)
    idempotency_key: Text
    reason: Text
    items: tuple[ItemDefinition | ItemCreation, ...] = Field(min_length=1, max_length=500)


class SharedEdit(Model):
    base_revision: int = Field(ge=0)
    idempotency_key: Text
    reason: Text
    candidate_ids: tuple[Text, ...] = Field(min_length=1, max_length=20000)
    fields: dict[str, str] = Field(min_length=1)


class ReceiveRequest(Model):
    defer_confirmation: bool = False
    base_revision: int = Field(ge=0)
    idempotency_key: Text
    candidate_ids: tuple[Text, ...] = Field(max_length=20000)

    @model_validator(mode="after")
    def unique_candidates(self):
        if len(set(self.candidate_ids)) != len(self.candidate_ids):
            raise ValueError("重复候选")
        return self


class SheetConfirmRequest(Model):
    base_revision: int = Field(ge=0)
    idempotency_key: Text
    section: Text
    reason: Text = '核对工作表有效内容后整表确认'


class LedgerDecision(Model):
    base_revision: int = Field(ge=0)
    idempotency_key: Text
    candidate_ids: tuple[Text, ...] = Field(min_length=1, max_length=20000)
    action: Literal['review', 'exclude']
    reason: str = ''

    @model_validator(mode='after')
    def unique_candidates(self):
        if len(set(self.candidate_ids)) != len(self.candidate_ids):
            raise ValueError('重复候选')
        return self


class RevisionResolution(Model):
    base_revision: int = Field(ge=0)
    idempotency_key: Text
    candidate_ids: tuple[Text, ...] = Field(min_length=1, max_length=20000)
    label: str = ''
    method: str = ''
    remember_alias: bool = False
    reason: Text = '确认保留未划线内容，排除整项删除'


class ConfigurationChange(Model):
    base_revision: int = Field(ge=0)
    idempotency_key: Text
    reason: Text
    item: ItemDefinition | None = None
    sample_type: Literal["DS", "DP"] | None = None
    presentation: Text | None = None

    @model_validator(mode="after")
    def one_change(self):
        if self.item is not None:
            if self.sample_type is not None or self.presentation is not None:
                raise ValueError("一次只能修改一个检测项目或一个默认规格")
        elif self.sample_type is None or self.presentation is None:
            raise ValueError("默认规格需要样品类型和规格")
        return self


class CandidateEdit(Model):
    candidate_revision: int = Field(ge=1)
    base_revision: int = Field(ge=0)
    idempotency_key: Text
    reason: Text
    fields: dict[str, str] = Field(min_length=1)
    acknowledge_source_warning: bool = False


class CandidateMapping(Model):
    base_revision: int = Field(ge=0)
    idempotency_key: Text
    reason: Text
    candidate_ids: tuple[Text, ...] = Field(min_length=1, max_length=500)
    item_id: Text

    @model_validator(mode="after")
    def unique_candidates(self):
        if len(set(self.candidate_ids)) != len(self.candidate_ids):
            raise ValueError("不能重复选择同一候选")
        return self


class CandidateView(Candidate):
    superseded_by: tuple[str, ...] = ()
    result_anchor: dict = Field(default_factory=dict)
    mapping_source: dict[str,str] = Field(default_factory=dict)
    rule_history: tuple[dict,...] = ()
    issues: tuple[ReviewIssue, ...] = ()
    source_value: dict[str,str] = Field(default_factory=dict)
    logical_row: int | None = None
    source_span: tuple[int, int, int, int] | None = None
    reviewed: bool = False
    row_signature: str = ''
    revisions: dict[str, dict] = Field(default_factory=dict)
    suggested_fields: dict[str, str] = Field(default_factory=dict)
    can_receive: bool = False
    received: bool = False
    candidate_revision: int
    lifecycle: Literal["draft", "submitted", "withdrawn"]
    detection_id: Text
    source_number: str | None = None
    existing_detection_id: str | None = None
    adopted_detection_id: str | None = None
    test_date: str | None = None
    evidence: dict[str, Location] = Field(default_factory=dict)
    interpreted_fields: dict[str, str] = Field(default_factory=dict)
    applied_defaults: dict[str, str] = Field(default_factory=dict)
    corrections: dict[str, str]
    allowed_actions: tuple[str, ...]


class PreviewSummary(Model):
    preview_id: Text
    filename: Text
    uploaded_at: Text
    uploaded_by: Text
    total: int
    draft: int
    submitted: int
    withdrawn: int


class UploadPreview(Model):
    duplicate_source: bool = False
    sheet_confirmation_required: bool = False
    contract_version: Literal["1"] = "1"
    contract_revision: Literal["2", "5"] = "2"
    preview_id: Text
    project: Text
    filename: Text
    source_hash: Text
    upload_sequence: int
    uploaded_by: Text
    uploaded_at: Text
    base_revision: int
    state: Literal["preview_ready"] = "preview_ready"
    candidates: tuple[CandidateView, ...]
    counts: dict[str, int]
    can_commit: bool
    parsing_summary: tuple[dict, ...] = ()


class ExternalReference(Model):
    kind: Literal["CC", "OOE", "OOS", "OTHER"]
    number: Text


class ConfirmItem(Model):
    candidate_id: Text
    candidate_revision: int = Field(ge=1)
    detection_id: Text
    identity_confirmed: Literal[True]
    intent: Literal["initial", "repeat_source", "retest", "correction"]
    reason: Text
    references: tuple[ExternalReference, ...] = ()


class ConfirmRequest(Model):
    base_revision: int = Field(ge=0)
    idempotency_key: Text
    items: tuple[ConfirmItem, ...] = Field(min_length=1, max_length=20000)

    @model_validator(mode="after")
    def unique_candidates(self):
        ids = [item.candidate_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("A candidate can be submitted only once in a request")
        return self


class ResultChange(Model):
    base_revision: int = Field(ge=0)
    idempotency_key: Text
    result: Text
    reason: Text
    references: tuple[ExternalReference, ...] = ()


class ReviewRequest(Model):
    base_revision: int = Field(ge=0)
    idempotency_key: Text
    action: Literal["confirm_fact", "adopt", "reject"]
    reason: Text


class WithdrawRequest(Model):
    base_revision: int = Field(ge=0)
    idempotency_key: Text
    reason: Text


class MutationResult(Model):
    project: Text
    base_revision: int
    result_version_ids: tuple[str, ...] = ()
    pending_confirmation_ids: tuple[str, ...] = ()


class WorkflowResultCell(ResultCell):
    slot_key: RecordKey
    display_status: Literal["pending", "confirmed", "legacy"]
    upload_sequence: int


class ResultPage(Model):
    project: Text
    base_revision: int
    total: int
    offset: int
    limit: int
    cells: tuple[WorkflowResultCell, ...]


class ResultFilter(Model):
    day: int | None = Field(default=None,ge=0)
    batch: str | None = None
    reporting_basis: str | None = None
    purpose: Literal["unknown", "routine", "reference"] | None = None
    sample_type: Literal["DS", "DP"] | None = None
    presentation: str | None = None
    study: Literal["release", "long_term", "accelerated", "stress"] | None = None
    study_id: str | None = None
    condition: str | None = None
    month: int | None = Field(default=None, ge=0)
    item: str | None = None
    method: str | None = None
    method_version: str | None = None


class ExportRequest(Model):
    slot_ids: tuple[str, ...] = ()
    include_png: bool = False
    project: Text
    base_revision: int = Field(ge=0)
    filters: ResultFilter = Field(default_factory=ResultFilter)
    orientation: Literal["items", "batches"] = "items"


class ResultQuery(ResultFilter):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=1000)


class ResultTrendQuery(ResultFilter):
    group_id: str
    base_revision: int = Field(ge=0)
    batches: list[str] = Field(min_length=1)


class ExportResult(Model):
    image_download_url: str | None = None
    export_id: Text
    project: Text
    base_revision: int
    state: Literal["verified"] = "verified"
    filename: Text
    download_url: Text


class WorkflowSchema(Model):
    ledger_decision: LedgerDecision
    sheet_confirm: SheetConfirmRequest
    revision_resolution: RevisionResolution
    receive_request: ReceiveRequest
    project_creation: ProjectCreation
    new_items: NewItems
    shared_edit: SharedEdit
    result_change: ResultChange
    configuration: ConfigurationView
    configuration_change: ConfigurationChange
    preview: UploadPreview
    preview_summary: PreviewSummary
    candidate_mapping: CandidateMapping
    candidate_edit: CandidateEdit
    confirmation: ConfirmRequest
    review: ReviewRequest
    withdrawal: WithdrawRequest
    mutation_result: MutationResult
    result_page: ResultPage
    export_request: ExportRequest
    export_result: ExportResult
