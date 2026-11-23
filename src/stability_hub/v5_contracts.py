"""V5 additive worksheet contract. Commands are drafts until sheet commit."""
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints

from .models import Location, Model, Text

SectionName = Annotated[str, StringConstraints(min_length=1)]  # Preserve exact Excel worksheet names, including trailing spaces.

RegionRole = Literal['result', 'identity', 'standard', 'note', 'auxiliary', 'excluded', 'unclassified']
DecisionAction = Literal['initial', 'repeat_source', 'keep_old', 'correction', 'retest', 'exclude', 'restore']


class SourceRegion(Model):
    id: Text
    section: str
    coordinates: tuple[str, ...]
    hidden: bool = False
    role: RegionRole
    source_hash: str = ''
    basis: str
    revision: int = 1


class ReviewIssue(Model):
    source_kind: Literal['original_comment','system_hint','source_revision','extraction'] = 'extraction'
    id: Text
    code: Text
    candidate_ids: tuple[str, ...] = ()
    region_ids: tuple[str, ...] = ()
    fields: tuple[str, ...] = ()
    evidence: tuple[Location, ...] = ()
    detail: str
    actions: tuple[str, ...]
    state: Literal['open', 'resolved'] = 'open'
    revision: int = 1
    source_field: str | None = None
    revision_kind: str | None = None
    proposal: dict = Field(default_factory=dict)


class DraftCommand(Model):
    base_revision: int = Field(ge=0)
    idempotency_key: Text
    reason: Text


class WorksheetDraftState(Model):
    base_revision: int = Field(ge=0)
    decisions: dict[str,dict]


class DraftDecision(DraftCommand):
    candidate_id: Text
    candidate_revision: int = Field(ge=1)
    action: DecisionAction
    previous_version_id: str | None = None


class IdentityLink(DraftCommand):
    item_id: Text
    historical_item: Text


class ItemDisplayName(DraftCommand):
    label: Text


class IdentityAssociationScope(Model):
    slot_ids: tuple[Text, ...] = Field(min_length=1, max_length=20000)
    fields: dict[str, str] = Field(min_length=1)


class IdentityAssociationApply(DraftCommand):
    scope: IdentityAssociationScope
    fingerprint: Text
    adoptions: dict[str, str]


class ProjectRule(DraftCommand):
    upload_id: Text
    candidate_id: Text
    source_name: Text
    method: str
    method_version: str = ''
    unit: str
    subresult: str = ''
    item_id: Text


class Reinterpret(DraftCommand):
    fingerprint: Text


class RuleRetirement(DraftCommand):
    rule_version: Text


class IssueDecision(DraftCommand):
    batch_id: str | None = None
    candidate_id: Text
    candidate_revision: int = Field(ge=1)
    issue_id: Text
    action: Literal['keep_body', 'keep_active', 'manual', 'exclude', 'keep_single']
    fields: dict[str, str] = Field(default_factory=dict)


class IssueSelection(Model):
    candidate_id: Text
    candidate_revision: int = Field(ge=1)
    issue_id: Text
    action: Literal['keep_body','keep_active','manual','exclude','keep_single']
    fields: dict[str,str] = Field(default_factory=dict)


class IssueBatchPreview(Model):
    issues: tuple[IssueSelection,...] = Field(min_length=1,max_length=20000)


class IssueBatchApply(DraftCommand):
    fingerprint: Text
    scope: IssueBatchPreview


class RegionDecision(DraftCommand):
    region_id: Text
    role: Literal['identity', 'standard', 'note', 'auxiliary', 'excluded', 'unclassified']


class StructuralDecision(DraftCommand):
    candidate_id: str | None = None
    candidate_revision: int | None = Field(default=None, ge=1)
    section: SectionName
    coordinate: Text
    hazard: str | None = None
    results: tuple[dict[str, str], ...] = Field(min_length=1, max_length=100)


class BulkPreview(Model):
    candidate_ids: tuple[Text, ...] = Field(min_length=1, max_length=20000)
    fields: dict[str, str] = Field(min_length=1)


class RegionParseScope(Model):
    section: SectionName
    range_ref: Text
    header_row: int = Field(ge=1, le=1048576)
    columns: dict[str, str] = Field(min_length=1)
    common_fields: dict[str, str] = Field(default_factory=dict)


class RegionParseApply(DraftCommand):
    fingerprint: Text
    scope: RegionParseScope


class BulkApply(DraftCommand):
    fingerprint: Text
    scope: BulkPreview


class DraftReview(DraftCommand):
    candidate_id: Text
    decision_fingerprint: Text
    action: Literal['confirm_fact', 'adopt']


class SheetPreflight(Model):
    decision_fingerprints: dict[str,str] = Field(default_factory=dict)
    previous_versions: dict[str,str] = Field(default_factory=dict)
    adopted_versions: dict[str,str] = Field(default_factory=dict)
    section: str
    fingerprint: str
    base_revision: int
    counts: dict[str, int]
    unresolved: tuple[str, ...]
    review_required: tuple[str, ...]
    can_commit: bool
    state: Literal['needs_review', 'needs_approval', 'ready', 'committed', 'historical_partial']
    historical_count: int = 0
    pending_decision_count: int = 0
    resolved_decision_count: int = 0
    affected_result_count: int = 0
    blocker_reasons: tuple[str, ...] = ()


class ReviewBlock(Model):
    block_id: Text
    section: str
    range_refs: tuple[str, ...]
    role: RegionRole
    evidence: tuple[Location, ...] = ()
    candidate_ids: tuple[str, ...] = ()
    task_ids: tuple[str, ...] = ()
    summary: str
    requires_human: bool = False
    collapsed: bool = False


class ReviewProgress(Model):
    section: str
    pending_decision_count: int = Field(default=0, ge=0)
    resolved_decision_count: int = Field(default=0, ge=0)
    affected_result_count: int = Field(default=0, ge=0)
    can_commit: bool
    state: Literal['needs_review', 'needs_approval', 'ready', 'committed', 'historical_partial']
    blocker_reasons: tuple[str, ...] = ()


class ReviewTask(Model):
    id: Text
    code: Text
    section: str
    fields: tuple[str, ...] = ()
    evidence: tuple[Location, ...] = ()
    members: tuple[dict, ...] = ()
    detail: str
    actions: tuple[str, ...]
    proposal: dict = Field(default_factory=dict)
    blockers: tuple[str, ...] = ()
    base_revision: int
    batch_key: str | None = None
    exception: str | None = None
    diagnostics: tuple[dict, ...] = ()


class CandidateReviewState(Model):
    candidate_id: Text
    state: Literal['normal_pending','pending','suggested','saved_draft','excluded','committed']
    modified: bool = False
    has_unresolved: bool = False
    task_ids: tuple[str,...] = ()
    suggestion_ids: tuple[str,...] = ()


class TaskBatchScope(Model):
    section: SectionName
    task_ids: tuple[Text, ...] = Field(default=(),max_length=20000)
    candidate_ids: tuple[Text, ...] = Field(default=(),max_length=20000)
    include_sections: tuple[SectionName, ...] = ()
    operation: Literal['keep_active','manual','keep_body','exclude','suggested','exclude_selected','classify','keep_single','repeat_source']
    fields: dict[str,str] = Field(default_factory=dict)
    exclude_candidate_ids: tuple[Text, ...] = Field(default=(),max_length=20000)
    region_ids: tuple[Text, ...] = Field(default=(),max_length=20000)
    role: str | None = None


class ActionGroup(Model):
    group_id: Text
    title: str
    reason: str
    scope: TaskBatchScope
    decision_count: int = Field(default=0, ge=0)
    affected_result_count: int = Field(default=0, ge=0)
    members: tuple[dict, ...] = ()
    exceptions: tuple[dict, ...] = ()
    subplans: tuple[dict, ...] = ()
    base_revision: int


class TaskBatchApply(DraftCommand):
    scope: TaskBatchScope
    fingerprint: Text


class TaskBatchUndo(DraftCommand):
    batch_id: Text


class SourceStructureApply(DraftCommand):
    fingerprint: Text


class SheetCommit(DraftCommand):
    section: SectionName
    fingerprint: Text


class SheetCommitResult(Model):
    commit_id: str
    project: str
    base_revision: int
    section: str
    counts: dict[str, int]
    historical_count: int
    result_version_ids: tuple[str, ...]
    state: Literal['committed'] = 'committed'
    worksheet_sync: tuple[dict[str, Any], ...] = ()


class SyncWorksheetsRequest(Model):
    material_stages: dict[str, str] = Field(default_factory=dict)


class ManualResult(Model):
    sources: tuple[Text, ...] = Field(min_length=1, max_length=100)
    fields: dict[str, str]


class ManualSheetScope(Model):
    section: SectionName
    cells: dict[str, str] = Field(default_factory=dict, max_length=4000)
    merges: tuple[Text, ...] = Field(default=(), max_length=1000)
    candidate_ids: tuple[Text, ...] = Field(default=(), max_length=1000)
    results: tuple[ManualResult, ...] = Field(default=(), max_length=1000)


class ManualSheetApply(DraftCommand):
    scope: ManualSheetScope
    fingerprint: Text
    reviewed: bool = False


class ManualSheetUndo(DraftCommand):
    edit_id: Text


class V5Schema(Model):
    manual_scope: ManualSheetScope
    manual_apply: ManualSheetApply
    manual_undo: ManualSheetUndo
    candidate_review_state: CandidateReviewState
    source_structure_apply: SourceStructureApply
    review_task: ReviewTask
    task_batch_scope: TaskBatchScope
    task_batch_apply: TaskBatchApply
    task_batch_undo: TaskBatchUndo
    worksheet_draft_state: WorksheetDraftState
    item_display_name: ItemDisplayName
    identity_association_scope: IdentityAssociationScope
    identity_association_apply: IdentityAssociationApply
    region_parse_scope: RegionParseScope
    region_parse_apply: RegionParseApply
    issue_batch_preview: IssueBatchPreview
    issue_batch_apply: IssueBatchApply
    retire_rule: RuleRetirement
    project_rule: ProjectRule
    reinterpret: Reinterpret
    identity_link: IdentityLink
    region: SourceRegion
    issue: ReviewIssue
    decision: DraftDecision
    issue_decision: IssueDecision
    region_decision: RegionDecision
    structure: StructuralDecision
    bulk_preview: BulkPreview
    bulk_apply: BulkApply
    review: DraftReview
    preflight: SheetPreflight
    commit: SheetCommit
    commit_result: SheetCommitResult
    review_block: ReviewBlock
    action_group: ActionGroup
    review_progress: ReviewProgress
