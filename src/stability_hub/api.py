"""Thin HTTP adapter. IT can replace get_actor without touching business services."""

import json
import re
import hashlib
import secrets
from contextlib import asynccontextmanager
from tempfile import TemporaryDirectory
from pathlib import Path
from typing import Annotated, Literal
from zipfile import BadZipFile

from fastapi import Depends, FastAPI, File, Form, Header, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StringConstraints, ValidationError
from lxml.etree import XMLSyntaxError

from .source_tables import SourceTables
from .business_worksheets import ArchiveStudyObject, EditPointMetadata, EditWorksheetMetadata, LinkWorksheetSample, RemoveWorksheetRow, StopWorksheetChecks
from .assay_catalog import RegisterComponents, catalog, register_components
from .source_contracts import ArchiveInfo, ArchiveView, ArchiveTablePage, SourcePackage, SourcePreviewRequest
from .models import ImportContext
from .parsing import parse_source
from .permissions import Actor, Capability, require
from .result_versions import ResultVersions
from .workflow import Workflow
from .business_worksheets import AddWorksheetEvent, AttachProtocol, BusinessWorksheets, ConfirmProtocolStandards, CopyCoaToZero, CreateWorksheet, ExtendPlan, IncludeHistoricalRows, LeaseToken, SaveWorksheet, SetWorksheetStandards, SetReleaseReference, CustomWorksheetItem, WorksheetCriteria
from .business_worksheets import CoaZeroBatchPreview, CoaZeroBatchResult, CopyCoaZeroBatch, PreviewCoaZeroBatch, WorksheetListEntry
from .workflow_common import WorkflowError, active_project_code, unique_project_code
from .workflow_contracts import (
    InvalidateRequest,
    ConfigurationChange, ConfigurationView, CandidateEdit, CandidateMapping, ConfirmRequest, ExportRequest, ExportResult, MutationResult, ResultFilter,
    ProjectCreation, NewItems, SharedEdit, ResultChange,
    PreviewSummary, ResultPage, ResultQuery, ReviewRequest, UploadPreview, WithdrawRequest, WorkspacePreview,
)
from .workflow_excel import ExcelExports
from .ledger_review import LedgerReview
from .workflow_contracts import ReceiveRequest, SheetConfirmRequest, RevisionResolution, LedgerDecision


def get_actor(request: Request) -> Actor:
    """Trusted local actor; never read a client-supplied role. Replace via IT identity adapter."""
    token = request.app.state.api_token
    if token and not secrets.compare_digest(request.headers.get("authorization", ""), "Bearer " + token):
        raise WorkflowError("unauthenticated", "需要有效的本地服务令牌。", 401)
    return request.app.state.actor


ActorDependency = Annotated[Actor, Depends(get_actor)]


class OcrPageReview(BaseModel):
    revision: int = Field(ge=0)
    lines: dict[int, Annotated[str, StringConstraints(max_length=4000)]] = Field(max_length=3000)


class ScanCriterion(BaseModel):
    table: int = Field(ge=0)
    row: int = Field(ge=1)
    column: int = Field(ge=0)
    item: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    method: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    sop: Annotated[str, StringConstraints(max_length=200)] = ''
    criterion: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class ScanSpecification(BaseModel):
    page: int = Field(ge=1)
    manual_page: bool = False
    material_scope: Literal['reference', 'DS', 'DP', 'placebo']
    scope: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    rows: list[ScanCriterion] = Field(min_length=1, max_length=100)


class CurrentSpecification(BaseModel):
    specification_id: str
    revision: int = Field(ge=0)
    idempotency_key: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class DraftWorksheetItem(CustomWorksheetItem):
    method: str = Field(default="", max_length=4000)


class WorksheetDraftInput(BaseModel):
    custom_items: list[DraftWorksheetItem] = Field(default_factory=list, max_length=150)
    row_criteria: dict[str, WorksheetCriteria] = Field(default_factory=dict, max_length=150)
    fields: dict[str, Annotated[str, StringConstraints(max_length=4000)]] = Field(max_length=140)
    selected: list[str] = Field(default_factory=list, max_length=150)
    row_items: dict[int, str | list[str]] = Field(default_factory=dict, max_length=150)
    required: dict[int, list[str]] = Field(default_factory=dict, max_length=120)
    protocol_draft: str = Field(default='', max_length=64)
    protocol_table: str = Field(default='', max_length=10)
    copy_source: str = Field(default='', max_length=100)


def create_app(store, configs, actor: Actor, *, api_token=None, frontend=None, workbench=None, origins=(),
               enforce_formal_sample_binding=False):
    from . import __version__

    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            ocr.close()

    app = FastAPI(title="Stability Data Hub", version=__version__, lifespan=lifespan)
    import subprocess
    code_root = Path(__file__).resolve().parents[2]
    try:
        build_commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=code_root, text=True, timeout=3).strip()
        dirty = bool(subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=code_root, text=True, timeout=3).strip())
    except (OSError, subprocess.SubprocessError):
        build_commit, dirty = 'unknown', None
    workflow = Workflow(store)
    for config in configs:
        workflow.configure(config)
    versions, exports = ResultVersions(workflow), ExcelExports(workflow)
    worksheets = BusinessWorksheets(workflow, versions, enforce_formal_sample_binding=enforce_formal_sample_binding)
    from .pdf_ocr import PdfOcr
    ocr = PdfOcr(store.home, code_root)
    app.state.actor, app.state.api_token, app.state.workflow = actor, api_token, workflow

    @app.get('/health', include_in_schema=False)
    def health():
        return {'application': 'stability-data-hub', 'version': __version__, 'build': '0.2.0-release-20261106',
                'delivery_state':'release', 'workbench_version': '0.1.0-dev'}

    @app.get('/favicon.ico', include_in_schema=False)
    def favicon():
        return Response(status_code=204)

    @app.get('/api/v1/runtime')
    def runtime(operator: ActorDependency):
        require(operator, Capability.VIEW)
        return {'version': __version__, 'data_home': str(store.home), 'actor': operator.display_name,
                'role': operator.role, 'mode': 'local', 'code_commit': build_commit, 'code_dirty': dirty,
                'code_root': str(code_root), 'delivery_state': 'release', 'workbench_version': '0.1.0-dev'}
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(origins), allow_methods=["GET", "POST", "PATCH"],
                           allow_headers=["Content-Type", "Authorization", "Idempotency-Key"])

    @app.middleware("http")
    async def origin_guard(request, call_next):
        origin = request.headers.get("origin")
        own = str(request.base_url).rstrip("/")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin not in {own, *origins}:
            return JSONResponse({"code": "origin_denied", "message": "请求来源未授权。", "details": {}}, status_code=403)
        return await call_next(request)

    def canonical_project(project: str, operator: ActorDependency):
        require(operator, Capability.VIEW)
        with store.read() as conn:
            return unique_project_code(conn, project)

    ProjectDependency = Annotated[str, Depends(canonical_project)]

    @app.exception_handler(WorkflowError)
    async def workflow_error(request, exc):
        return JSONResponse({"code": exc.code, "message": str(exc), "details": {}}, status_code=exc.status)

    @app.exception_handler(PermissionError)
    async def permission_error(request, exc):
        return JSONResponse({"code": "forbidden", "message": str(exc), "details": {}}, status_code=403)

    @app.exception_handler(RequestValidationError)
    async def request_error(request, exc):
        return JSONResponse({"code": "invalid_request", "message": "请求字段或类型不符合契约。", "details": {}}, status_code=422)

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse({"code": "invalid_input", "message": str(exc), "details": {}}, status_code=422)

    source_tables = SourceTables(workflow)

    @app.get("/api/v1/worksheets/{sheet_id}/protocol-standards")
    def preview_protocol_standards(sheet_id: str, operator: ActorDependency):
        return worksheets.preview_protocol_standards(sheet_id, operator)

    @app.post("/api/v1/worksheets/{sheet_id}/protocol-standards")
    def confirm_protocol_standards(sheet_id: str, command: ConfirmProtocolStandards, operator: ActorDependency):
        return worksheets.confirm_protocol_standards(sheet_id, command, operator)

    @app.get("/api/v1/projects/{project}/assays")
    def project_assays(project: ProjectDependency, operator: ActorDependency):
        return catalog(store, project, operator)

    @app.post("/api/v1/projects/{project}/specifications/{specification_id}/components")
    def confirm_specification_components(project: ProjectDependency, specification_id: str, command: RegisterComponents, operator: ActorDependency):
        return register_components(store, project, specification_id, command, operator)

    @app.get("/api/v1/projects/{project}/specifications/{specification_id}/components", response_model=list[dict])
    def read_specification_components(project: ProjectDependency, specification_id: str, operator: ActorDependency):
        from .assay_catalog import registered_components
        return registered_components(store, project, specification_id, operator)
    from .coa import router_for as coa_router
    app.include_router(coa_router(store, ActorDependency, ProjectDependency))
    from .worksheet_roundtrip import router_for as study_workbook_router
    app.include_router(study_workbook_router(store, worksheets, ActorDependency))

    @app.get("/api/v1/worksheets/{sheet_id}/historical-rows")
    def preview_historical_rows(sheet_id: str, operator: ActorDependency):
        return worksheets.preview_historical_rows(sheet_id, operator)

    @app.post("/api/v1/worksheets/{sheet_id}/historical-rows")
    def include_historical_rows(sheet_id: str, fingerprint: str, command: IncludeHistoricalRows, operator: ActorDependency):
        return worksheets.include_historical_rows(sheet_id, command, fingerprint, operator)

    @app.get("/api/v1/projects/{project}/batch-overview")
    def batch_overview(project: ProjectDependency, batch: str, operator: ActorDependency):
        require(operator, Capability.VIEW)
        from .workflow_common import project_row
        with store.read() as conn:
            project_row(conn, project)
            samples = [dict(row) for row in conn.execute(
                "SELECT * FROM sample_entities WHERE project=? AND batch=? AND deleted_at IS NULL ORDER BY id", (project, batch))]
            studies = [dict(row) for row in conn.execute("""
                SELECT w.id,w.name,w.sample_id,w.plan_version,w.identity_json,p.plan_json
                FROM business_worksheets w JOIN business_worksheet_plans p ON p.sheet_id=w.id AND p.version=w.plan_version
                WHERE w.project=? AND w.deleted_at IS NULL AND json_extract(w.identity_json,'$.batch')=? ORDER BY w.name,w.id
            """, (project, batch))]
            for study in studies:
                study["identity"] = json.loads(study.pop("identity_json"))
                plan = json.loads(study.pop("plan_json"))
                study["points"] = [point["label"] for point in plan["points"]]
                study["protocol_draft_id"] = plan.get("protocol_draft_id")
                study["standards"] = list({link["specification_id"]: link["filename"]
                                           for row in plan["rows"] for link in row.get("standards", [])}.items())
            return {"batch": batch, "samples": samples, "studies": studies}

    @app.get("/api/v1/worksheets/{sheet_id}/archive-impact")
    def worksheet_archive_impact(sheet_id: str, operator: ActorDependency):
        return worksheets.archive_object(sheet_id, "worksheet", operator)

    @app.delete("/api/v1/worksheets/{sheet_id}")
    def archive_worksheet(sheet_id: str, command: ArchiveStudyObject, operator: ActorDependency):
        return worksheets.archive_object(sheet_id, "worksheet", operator, command)

    @app.get("/api/v1/samples/{sample_id}/archive-impact")
    def sample_archive_impact(sample_id: str, operator: ActorDependency):
        return worksheets.archive_object(sample_id, "sample", operator)

    @app.delete("/api/v1/samples/{sample_id}")
    def archive_sample(sample_id: str, command: ArchiveStudyObject, operator: ActorDependency):
        return worksheets.archive_object(sample_id, "sample", operator, command)

    @app.get("/api/v1/projects/{project}/worksheets", response_model=list[WorksheetListEntry])
    def list_worksheets(project: ProjectDependency, operator: ActorDependency, sample_id: str | None = None,
                        study: Literal["long_term", "accelerated", "stress"] | None = None):
        return worksheets.list(project, operator, sample_id=sample_id, study=study)

    @app.post("/api/v1/projects/{project}/worksheets", status_code=201)
    def create_worksheet(project: ProjectDependency, command: CreateWorksheet, operator: ActorDependency):
        return worksheets.create(project, command, operator)

    @app.get("/api/v1/worksheets/{sheet_id}")
    def read_worksheet(sheet_id: str, operator: ActorDependency):
        return worksheets.get(sheet_id, operator)

    @app.post("/api/v1/worksheets/{sheet_id}/lease")
    def acquire_worksheet(sheet_id: str, operator: ActorDependency):
        return worksheets.acquire(sheet_id, operator)

    @app.post("/api/v1/worksheets/{sheet_id}/lease/renew")
    def renew_worksheet(sheet_id: str, command: LeaseToken, operator: ActorDependency):
        return worksheets.heartbeat(sheet_id, command, operator)

    @app.post("/api/v1/worksheets/{sheet_id}/lease/release")
    def release_worksheet(sheet_id: str, command: LeaseToken, operator: ActorDependency):
        return worksheets.release(sheet_id, command, operator)

    @app.post("/api/v1/worksheets/{sheet_id}/save")
    def save_worksheet(sheet_id: str, command: SaveWorksheet, operator: ActorDependency):
        return worksheets.save(sheet_id, command, operator)

    @app.get("/api/v1/worksheets/{sheet_id}/header-metadata")
    def preview_worksheet_header_metadata(sheet_id: str, operator: ActorDependency):
        return worksheets.preview_header_metadata(sheet_id, operator)

    @app.post("/api/v1/worksheets/{sheet_id}/header-metadata")
    def edit_worksheet_header_metadata(sheet_id: str, command: EditWorksheetMetadata, operator: ActorDependency):
        return worksheets.edit_header_metadata(sheet_id, command, operator)

    @app.post("/api/v1/worksheets/{sheet_id}/point-metadata")
    def edit_worksheet_point(sheet_id: str, command: EditPointMetadata, operator: ActorDependency):
        return worksheets.edit_point_metadata(sheet_id, command, operator)

    @app.post("/api/v1/worksheets/{sheet_id}/plan")
    def extend_worksheet(sheet_id: str, command: ExtendPlan, operator: ActorDependency):
        return worksheets.extend(sheet_id, command, operator)

    @app.post("/api/v1/worksheets/{sheet_id}/standards")
    def set_worksheet_standards(sheet_id: str, command: SetWorksheetStandards, operator: ActorDependency):
        return worksheets.set_standards(sheet_id, command, operator)

    @app.get("/api/v1/worksheets/{sheet_id}/remove-row")
    def preview_remove_worksheet_row(sheet_id: str, row_id: str, operator: ActorDependency):
        return worksheets.preview_remove_row(sheet_id, row_id, operator)

    @app.post("/api/v1/worksheets/{sheet_id}/remove-row")
    def remove_worksheet_row(sheet_id: str, command: RemoveWorksheetRow, operator: ActorDependency):
        return worksheets.remove_row(sheet_id, command, operator)

    @app.post("/api/v1/worksheets/{sheet_id}/stop-checks")
    def stop_worksheet_checks(sheet_id: str, command: StopWorksheetChecks, operator: ActorDependency):
        return worksheets.stop_checks(sheet_id, command, operator)

    @app.post("/api/v1/worksheets/{sheet_id}/events")
    def add_worksheet_event(sheet_id: str, command: AddWorksheetEvent, operator: ActorDependency):
        return worksheets.add_event(sheet_id, command, operator)

    @app.post("/api/v1/worksheets/{sheet_id}/protocol")
    def attach_worksheet_protocol(sheet_id: str, command: AttachProtocol, operator: ActorDependency):
        return worksheets.attach_protocol(sheet_id, command, operator)

    @app.post("/api/v1/worksheets/{sheet_id}/release-reference")
    def set_release_reference(sheet_id: str, command: SetReleaseReference, operator: ActorDependency):
        return worksheets.set_release_reference(sheet_id, command, operator)

    @app.post("/api/v1/worksheets/{sheet_id}/copy-coa-to-0m")
    def copy_coa_to_zero(sheet_id: str, command: CopyCoaToZero, operator: ActorDependency):
        return worksheets.copy_coa_to_zero(sheet_id, command, operator)

    @app.post("/api/v1/worksheets/{sheet_id}/copy-coa-to-0m/batch/preview", response_model=CoaZeroBatchPreview)
    def preview_copy_coa_batch(sheet_id: str, command: PreviewCoaZeroBatch, operator: ActorDependency):
        """Preview exact, applicable COA matches without writing results."""
        return worksheets.preview_copy_coa_batch(sheet_id, command, operator)

    @app.post("/api/v1/worksheets/{sheet_id}/copy-coa-to-0m/batch", response_model=CoaZeroBatchResult,
              responses={409: {"description": "Lease, revision, preview, selection or idempotency conflict. No results copied."}})
    def copy_coa_batch_to_zero(sheet_id: str, command: CopyCoaZeroBatch, operator: ActorDependency):
        """Copy selected eligible rows as independent first 0M results in one transaction."""
        return worksheets.copy_coa_batch_to_zero(sheet_id, command, operator)

    @app.get("/api/v1/worksheets/{sheet_id}/coa-release-options")
    def coa_release_options(sheet_id: str, row_id: str, operator: ActorDependency):
        return worksheets.coa_release_options(sheet_id, row_id, operator)

    @app.get("/api/v1/worksheets/{sheet_id}/sample-candidates")
    def worksheet_sample_candidates(sheet_id: str, operator: ActorDependency):
        return worksheets.sample_candidates(sheet_id, operator)

    @app.post("/api/v1/worksheets/{sheet_id}/sample")
    def link_worksheet_sample(sheet_id: str, command: LinkWorksheetSample, operator: ActorDependency):
        return worksheets.link_sample(sheet_id, command, operator)

    @app.post("/api/v1/worksheets/{sheet_id}/confirmation")
    def export_worksheet_confirmation(
        sheet_id: str, operator: ActorDependency, revision: Annotated[int, Query(ge=1)],
        selection_id: Annotated[str, Query(min_length=64, max_length=64)],
        point_ids: Annotated[list[str] | None, Query(max_length=120)] = None,
        scan_id: Annotated[str | None, Query(pattern=r'^[a-f0-9]{64}$')] = None,
    ):
        from .worksheet_confirmation import create_confirmation
        require(operator, Capability.EXPORT)
        attachment = None
        if scan_id:
            ocr.original(scan_id)
            attachment = {'id': scan_id, 'filename': ocr.filename(scan_id)}
        return create_confirmation(worksheets, sheet_id, revision, selection_id, operator,
                                   point_ids=point_ids, attachment=attachment)

    @app.get('/api/v1/previews/{preview_id}/file')
    def upload_original(preview_id: str,operator: ActorDependency):
        path,filename = versions.original_upload(preview_id,actor=operator)
        return FileResponse(path,filename=filename)

    @app.get('/api/v1/projects/{project}/v5/rules')
    def project_rules(project: ProjectDependency,operator: ActorDependency):
        require(operator,Capability.VIEW)
        from .file_rules import list_rules
        with store.connect() as conn:
            return list_rules(conn,project)
    from .v5_contracts import DraftDecision, DraftReview, SheetCommit, SheetPreflight, SheetCommitResult
    from .worksheet_commit import preflight, save_decision, review_decision, commit

    from .v5_contracts import WorksheetDraftState
    from .v5_contracts import TaskBatchScope,TaskBatchApply,TaskBatchUndo
    from .review_batches import preview_batch,apply_batch,undo_batch
    from .v5_contracts import SourceStructureApply
    from . import source_structure

    @app.get('/api/v1/previews/{preview_id}/v5/source-structure')
    def source_structure_preview(preview_id: str,operator: ActorDependency):
        require(operator,Capability.VIEW)
        with store.connect() as conn:
            conn.execute('BEGIN')
            return source_structure.preview(workflow,conn,preview_id)

    @app.post('/api/v1/previews/{preview_id}/v5/source-structure')
    def source_structure_apply(preview_id: str,command: SourceStructureApply,operator: ActorDependency):
        return source_structure.apply(workflow,preview_id,command,actor=operator)

    @app.get('/api/v1/previews/{preview_id}/v5/review-actions')
    def source_review_actions(preview_id: str, section: str, operator: ActorDependency,
                              category: str = "", focus: str = "", offset: int = Query(0, ge=0),
                              limit: int | None = Query(None, ge=1, le=100)):
        require(operator, Capability.VIEW)
        from .review_tasks import (blocker_cards, blocker_category, blocker_index, blocker_summary,
                                   build_action_groups, build_task_cards, current_review)
        with store.read() as conn:
            upload = workflow._upload(conn, preview_id)
            preview, sheets, flights, tasks, _ = current_review(workflow, conn, upload, operator, _shared=True)
            flight = next((flight for flight in flights if flight.section == section), None)
            if flight is None:
                raise WorkflowError('unknown_sheet', '该来源中不存在此工作表。', 404)
            index = blocker_index(flight, tasks)
            summary = blocker_summary(index)
            selected = [ref for ref, matching in index.items() if
                        (limit is None or not ref.startswith("not_received")) and
                        (not category or any(blocker_category(ref, task) == category for task in matching))]
            # Blockers and task cards are different lists; each keeps its own page
            # position so a focus hit in one can never shift the other.
            blocker_offset = offset
            if focus and limit:
                position = next((i for i, ref in enumerate(selected)
                                 if ref.partition(":")[2] == focus or any(task.id == focus for task in index[ref])), None)
                if position is not None:
                    blocker_offset = (position // limit) * limit
            if limit and blocker_offset and blocker_offset >= len(selected):
                blocker_offset = (max(len(selected) - 1, 0) // limit) * limit
            references = selected[blocker_offset:blocker_offset + limit] if limit is not None else selected
            active_tasks = {task.id for ref, matching in index.items() if not ref.startswith("not_received") for task in matching}
            manual = [
                {'id': task.id, 'detail': task.detail, 'fields': task.fields, 'evidence': task.evidence,
                 'affected_results': len(task.members),
                 'member_ids': [m["candidate_id"] for m in task.members if "candidate_id" in m],
                 'identity_options': task.proposal.get('identity_options', {}),
                 'guidance': task.proposal.get('guidance')}
                for task in tasks if task.id in active_tasks and task.fields and task.members
                and (task.code == 'identity' or ('manual' in task.actions
                     and all('issue_id' in member for member in task.members)))
            ]
            groups = build_action_groups(tasks, sheets, section, preview)
            members = {task.id: [m["candidate_id"] for m in task.members if "candidate_id" in m]
                       for task in tasks if task.section == section}
            batch_members = {g.group_id: list(dict.fromkeys(cid for tid in g.scope.task_ids for cid in members.get(tid, ())))
                             for g in groups}
            undo = conn.execute("""SELECT json_extract(b.detail_json,'$.batch_id') FROM workflow_events b
                WHERE b.subject_id=? AND b.action='review_batch' AND json_extract(b.detail_json,'$.scope.section')=?
                  AND NOT EXISTS(SELECT 1 FROM workflow_events u WHERE u.subject_id=b.subject_id
                    AND u.action='undo_review_batch'
                    AND json_extract(u.detail_json,'$.batch_id')=json_extract(b.detail_json,'$.batch_id'))
                ORDER BY b.sequence DESC LIMIT 1""", (preview_id, section)).fetchone()
            task_cards = build_task_cards(conn, upload, preview, flight, sheets, tasks, index=index)
            filtered_cards = [card for card in task_cards if card.get("category") == category] if category else task_cards
            task_cards_total = len(filtered_cards)
            card_offset = offset
            if focus and limit:
                pos = next((i for i, card in enumerate(filtered_cards)
                            if card["task_id"] == focus or any(m.get("candidate_id") == focus for m in card.get("members", []))), None)
                if pos is not None:
                    card_offset = (pos // limit) * limit
            if limit and card_offset and card_offset >= len(filtered_cards):
                card_offset = (max(len(filtered_cards) - 1, 0) // limit) * limit
            paged_cards = filtered_cards[card_offset:card_offset + limit] if limit is not None else filtered_cards

            # 'offset' stays the task-card page position consumed by the workbench;
            # blockers report their own position explicitly.
            return {'groups': groups, 'manual': manual, 'batch_members': batch_members,
                    'base_revision': flight.base_revision, 'undo_batch_id': undo[0] if undo and flight.state != 'committed' else None,
                    'summary': summary, 'total': len(selected), 'offset': card_offset,
                    'blockers_offset': blocker_offset, 'task_cards_offset': card_offset,
                    'blockers': blocker_cards(conn, upload, preview, flight, sheets, tasks, index=index, references=references),
                    'task_cards': paged_cards, 'task_cards_total': task_cards_total}

    @app.post('/api/v1/previews/{preview_id}/v5/tasks/preview')
    def source_task_preview(preview_id: str,command: TaskBatchScope,operator: ActorDependency):
        require(operator,Capability.VIEW)
        with store.connect() as conn:
            conn.execute('BEGIN')
            return preview_batch(workflow,conn,preview_id,command,operator)

    @app.post('/api/v1/previews/{preview_id}/v5/tasks/apply')
    def source_task_apply(preview_id: str,command: TaskBatchApply,operator: ActorDependency):
        return apply_batch(workflow,preview_id,command,actor=operator)

    @app.post('/api/v1/previews/{preview_id}/v5/tasks/undo')
    def source_task_undo(preview_id: str,command: TaskBatchUndo,operator: ActorDependency):
        return undo_batch(workflow,preview_id,command,actor=operator)

    @app.get('/api/v1/previews/{preview_id}/v5/draft-state',response_model=WorksheetDraftState)
    def worksheet_draft_state(preview_id: str,operator: ActorDependency):
        require(operator,Capability.VIEW)
        from .worksheet_commit import decisions
        with store.connect() as conn:
            conn.execute('BEGIN')
            upload = workflow._upload(conn,preview_id)
            from .workflow_common import project_row
            return {'decisions':decisions(conn,preview_id),'base_revision':project_row(conn,upload['project'])['revision']}

    @app.get('/api/v1/previews/{preview_id}/v5/preflight',response_model=SheetPreflight)
    def sheet_preflight(preview_id: str,section: str,operator: ActorDependency):
        require(operator,Capability.VIEW)
        with store.connect() as conn:
            conn.execute('BEGIN')
            upload = workflow._upload(conn,preview_id)
            pf = preflight(workflow,conn,upload,section,operator)[0]
            from .review_tasks import enrich_sheet_preflight
            p_obj = workflow._preview(conn,upload,operator)
            return enrich_sheet_preflight(conn,upload,p_obj,pf)

    @app.post('/api/v1/previews/{preview_id}/v5/decisions',response_model=MutationResult)
    def sheet_decision(preview_id: str,command: DraftDecision,operator: ActorDependency):
        return save_decision(workflow,preview_id,command,actor=operator)

    @app.post('/api/v1/previews/{preview_id}/v5/review',response_model=MutationResult)
    def sheet_draft_review(preview_id: str,command: DraftReview,operator: ActorDependency):
        return review_decision(workflow,preview_id,command,actor=operator)

    @app.post('/api/v1/previews/{preview_id}/v5/commit',response_model=SheetCommitResult)
    def sheet_commit(preview_id: str,command: SheetCommit,operator: ActorDependency):
        return commit(workflow,preview_id,command,actor=operator)

    @app.post('/api/v1/previews/{preview_id}/v5/auxiliary-source',response_model=SheetCommitResult)
    def auxiliary_source(preview_id: str,command: SheetCommit,operator: ActorDependency):
        from .worksheet_commit import archive_auxiliary
        return archive_auxiliary(workflow,preview_id,command,actor=operator)
    from .v5_contracts import StructuralDecision, BulkPreview, BulkApply
    from .structural_review import resolve_structure, restore
    from .bulk_drafts import preview as preview_bulk, apply as apply_bulk
    from .v5_contracts import RegionParseScope, RegionParseApply
    from .region_reparse import preview as preview_region, apply as apply_region
    from .v5_contracts import IdentityAssociationScope, IdentityAssociationApply
    from .identity_associations import preview as preview_association, apply as apply_association

    @app.post('/api/v1/projects/{project}/v5/identity-associations/preview')
    def identity_association_preview(project: ProjectDependency, command: IdentityAssociationScope, operator: ActorDependency):
        require(operator,Capability.VIEW)
        with store.connect() as conn:
            return preview_association(conn,project,command)

    @app.post('/api/v1/projects/{project}/v5/identity-associations/apply',response_model=MutationResult)
    def identity_association_apply(project: ProjectDependency, command: IdentityAssociationApply, operator: ActorDependency):
        return apply_association(workflow,project,command,actor=operator)

    @app.post('/api/v1/previews/{preview_id}/v5/region-parse/preview')
    def region_parse_preview(preview_id: str, command: RegionParseScope, operator: ActorDependency):
        require(operator,Capability.VIEW)
        with store.connect() as conn:
            return preview_region(workflow,conn,preview_id,command)

    @app.post('/api/v1/previews/{preview_id}/v5/region-parse/apply',response_model=MutationResult)
    def region_parse_apply(preview_id: str, command: RegionParseApply, operator: ActorDependency):
        return apply_region(workflow,preview_id,command,actor=operator)

    @app.get('/api/v1/previews/{preview_id}/v5/structure/preview')
    def structure_seed(preview_id: str, candidate_id: str, operator: ActorDependency):
        require(operator, Capability.VIEW)
        from .structural_review import projection_seed
        with store.connect() as conn:
            return projection_seed(workflow, conn, preview_id, candidate_id, operator)

    @app.post('/api/v1/previews/{preview_id}/v5/structure',response_model=MutationResult)
    def structure_decision(preview_id: str,command: StructuralDecision,operator: ActorDependency):
        return resolve_structure(workflow,preview_id,command,actor=operator)

    @app.post('/api/v1/previews/{preview_id}/v5/restore',response_model=MutationResult)
    def restore_draft(preview_id: str,command: DraftDecision,operator: ActorDependency):
        if command.action != 'restore':
            raise WorkflowError('invalid_action','恢复入口只接受restore。')
        return restore(workflow,preview_id,command,actor=operator)

    @app.post('/api/v1/previews/{preview_id}/v5/bulk-preview')
    def bulk_draft_preview(preview_id: str,command: BulkPreview,operator: ActorDependency):
        require(operator,Capability.VIEW)
        with store.connect() as conn:
            return preview_bulk(workflow,conn,preview_id,command)

    @app.post('/api/v1/previews/{preview_id}/v5/bulk-apply',response_model=MutationResult)
    def bulk_draft_apply(preview_id: str,command: BulkApply,operator: ActorDependency):
        return apply_bulk(workflow,preview_id,command,actor=operator)
    from .v5_contracts import RegionDecision
    from .worksheet_regions import coverage, classify

    @app.get('/api/v1/previews/{preview_id}/v5/sheets')
    def physical_sheets(preview_id: str,operator: ActorDependency):
        require(operator,Capability.VIEW)
        with store.connect() as conn:
            conn.execute('BEGIN')
            return coverage(conn,workflow._upload(conn,preview_id))

    @app.post('/api/v1/previews/{preview_id}/v5/regions',response_model=MutationResult)
    def classify_region(preview_id: str,command: RegionDecision,operator: ActorDependency):
        return classify(workflow,preview_id,command,actor=operator)
    from .v5_contracts import IssueDecision,IssueBatchPreview,IssueBatchApply
    from .review_issues import resolve_issue,preview_issue_batch,apply_issue_batch

    @app.post('/api/v1/previews/{preview_id}/v5/issues/preview')
    def issue_group_preview(preview_id: str,command: IssueBatchPreview,operator: ActorDependency):
        require(operator,Capability.VIEW)
        with store.connect() as conn:
            return preview_issue_batch(workflow,conn,preview_id,command)

    @app.post('/api/v1/previews/{preview_id}/v5/issues/apply',response_model=MutationResult)
    def issue_group_apply(preview_id: str,command: IssueBatchApply,operator: ActorDependency):
        return apply_issue_batch(workflow,preview_id,command,actor=operator)

    @app.post('/api/v1/previews/{preview_id}/v5/issues',response_model=MutationResult)
    def issue_decision(preview_id: str,command: IssueDecision,operator: ActorDependency):
        return resolve_issue(workflow,preview_id,command,actor=operator)
    from .v5_contracts import ProjectRule, Reinterpret, RuleRetirement
    from .file_rules import rule_impact, save_rule, reinterpret_preview, reinterpret, retire_rule

    @app.post('/api/v1/projects/{project}/v5/rules/retire',response_model=MutationResult)
    def retire_project_rule(project: ProjectDependency,command: RuleRetirement,operator: ActorDependency):
        return retire_rule(workflow,project,command,actor=operator)

    @app.get('/api/v1/previews/{preview_id}/v5/reinterpret')
    def draft_reinterpret_preview(preview_id: str, operator: ActorDependency):
        require(operator,Capability.VIEW)
        with store.connect() as conn:
            return reinterpret_preview(workflow,conn,workflow._upload(conn,preview_id),operator)

    @app.post('/api/v1/previews/{preview_id}/v5/reinterpret', response_model=MutationResult)
    def draft_reinterpret(preview_id: str, command: Reinterpret, operator: ActorDependency):
        return reinterpret(workflow,preview_id,command,actor=operator)

    @app.post('/api/v1/projects/{project}/v5/rules/preview')
    def project_rule_preview(project: ProjectDependency, command: ProjectRule, operator: ActorDependency):
        require(operator,Capability.VIEW)
        with store.connect() as conn:
            return rule_impact(conn,project,command)

    @app.post('/api/v1/projects/{project}/v5/rules', response_model=MutationResult)
    def project_rule_save(project: ProjectDependency, command: ProjectRule, operator: ActorDependency):
        return save_rule(workflow,project,command,actor=operator)
    from .v5_contracts import IdentityLink
    from .identity_links import link_preview, apply_link

    @app.get('/api/v1/projects/{project}/v5/identity-link')
    def identity_link_preview(project: ProjectDependency, item_id: str, historical_item: str, operator: ActorDependency):
        require(operator, Capability.VIEW)
        with store.connect() as conn:
            return link_preview(conn,project,item_id,historical_item)

    @app.post('/api/v1/projects/{project}/v5/identity-link', response_model=MutationResult)
    def identity_link(project: ProjectDependency, command: IdentityLink, operator: ActorDependency):
        return apply_link(workflow,project,command,actor=operator)

    @app.post('/api/v1/projects/{project}/source-imports', response_model=ArchiveInfo)
    def archive_source(project: ProjectDependency, operator: ActorDependency, file: Annotated[UploadFile, File()],
                       package: Annotated[UploadFile, File()],
                       idempotency_key: Annotated[str, Header(alias='Idempotency-Key', min_length=1, max_length=200)]):
        require(operator, Capability.PREVIEW)
        try:
            data = file.file.read(20 * 1024 * 1024 + 1)
            payload = package.file.read(80 * 1024 * 1024 + 1)
            if len(payload) > 80 * 1024 * 1024:
                raise WorkflowError('file_too_large', '转换包不得超过80MB', 413)
            result = source_tables.ingest(project, file.filename or 'input.docx', data,
                                        SourcePackage.model_validate_json(payload), idempotency_key, actor=operator)
            return {k: v for k, v in result.items() if k != 'tables'}
        except (BadZipFile, XMLSyntaxError, KeyError) as exc:
            raise ValueError('无效DOCX') from exc
        finally:
            file.file.close()
            package.file.close()

    @app.get('/api/v1/projects/{project}/source-imports', response_model=list[ArchiveInfo])
    def archives(project: ProjectDependency, operator: ActorDependency):
        return source_tables.list(project, actor=operator)

    @app.get('/api/v1/source-imports/{import_id}', response_model=ArchiveView)
    def archive_detail(import_id: str, operator: ActorDependency):
        return source_tables.get(import_id, actor=operator)

    @app.get('/api/v1/source-imports/{import_id}/file')
    def source_original(import_id: str, operator: ActorDependency):
        path, filename = source_tables.original(import_id, actor=operator)
        return FileResponse(path, filename=filename,
                            media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document')

    @app.get('/api/v1/source-imports/{import_id}/asset')
    def source_asset(import_id: str, part: str, operator: ActorDependency):
        content = source_tables.asset(import_id, part, actor=operator)
        types = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif'}
        mime = types.get(Path(part).suffix.lower(), 'application/octet-stream')
        return Response(content, media_type=mime, headers={'X-Content-Type-Options':'nosniff',
                        'Content-Disposition': 'inline' if mime.startswith('image/') else 'attachment'})

    @app.get('/api/v1/source-imports/{import_id}/tables/{table_id}', response_model=ArchiveTablePage)
    def archive_table(import_id: str, table_id: str, operator: ActorDependency, offset: int = 0, limit: int = 50):
        return source_tables.table(import_id, table_id, offset, limit, actor=operator)

    @app.post('/api/v1/source-imports/{import_id}/previews', response_model=UploadPreview)
    def archive_preview(import_id: str, command: SourcePreviewRequest, operator: ActorDependency):
        return source_tables.preview(import_id, command, actor=operator)

    @app.post('/api/v1/previews/{preview_id}/receive', response_model=MutationResult)
    def receive_source(preview_id: str, command: ReceiveRequest, operator: ActorDependency):
        return LedgerReview(workflow).receive(preview_id, command, actor=operator)

    @app.post('/api/v1/previews/{preview_id}/confirm-sheet', response_model=MutationResult)
    def confirm_sheet(preview_id: str, command: SheetConfirmRequest, operator: ActorDependency):
        return LedgerReview(workflow).confirm_sheet(preview_id, command, actor=operator)

    @app.post('/api/v1/previews/{preview_id}/resolve-revisions', response_model=MutationResult)
    def resolve_revisions(preview_id: str, command: RevisionResolution, operator: ActorDependency):
        return LedgerReview(workflow).resolve_revisions(preview_id, command, actor=operator)

    @app.post('/api/v1/previews/{preview_id}/ledger-decision', response_model=MutationResult)
    def ledger_decision(preview_id: str, command: LedgerDecision, operator: ActorDependency):
        return LedgerReview(workflow).decide(preview_id, command, actor=operator)

    @app.get('/api/v1/projects/{project}/ledger-sources')
    def ledger_sources(project: ProjectDependency, operator: ActorDependency):
        return LedgerReview(workflow).uploads(project, actor=operator)

    from .manual_sheet import state as manual_state, preview as manual_preview, apply as manual_apply, undo as manual_undo
    from .v5_contracts import ManualSheetScope, ManualSheetApply, ManualSheetUndo

    @app.get('/api/v1/previews/{preview_id}/v5/manual-sheet')
    def manual_sheet_state(preview_id: str, section: str, operator: ActorDependency):
        with workflow.store.connect() as conn:
            conn.execute('BEGIN')
            return manual_state(workflow, conn, preview_id, section, operator)

    @app.post('/api/v1/previews/{preview_id}/v5/manual-sheet/preview')
    def preview_manual_sheet(preview_id: str, scope: ManualSheetScope, operator: ActorDependency):
        with workflow.store.connect() as conn:
            conn.execute('BEGIN')
            return manual_preview(workflow, conn, preview_id, scope, operator)

    @app.post('/api/v1/previews/{preview_id}/v5/manual-sheet')
    def save_manual_sheet(preview_id: str, command: ManualSheetApply, operator: ActorDependency):
        return manual_apply(workflow, preview_id, command, operator)

    @app.post('/api/v1/previews/{preview_id}/v5/manual-sheet/undo')
    def undo_manual_sheet(preview_id: str, command: ManualSheetUndo, operator: ActorDependency):
        return manual_undo(workflow, preview_id, command, operator)

    @app.get('/api/v1/previews/{preview_id}/ledger-matrix')
    def ledger_matrix(preview_id: str, operator: ActorDependency,after_revision: int | None = None):
        return LedgerReview(workflow).matrix(preview_id, actor=operator,after_revision=after_revision)

    @app.get("/api/v1/me", response_model=Actor)
    def me(operator: ActorDependency):
        return operator

    @app.get("/api/v1/projects")
    def projects(operator: ActorDependency):
        require(operator, Capability.VIEW)
        with store.connect() as conn:
            return [{'project': row['project'], 'revision': row['revision'], 'purpose': row['purpose'],
                     'name': json.loads(row['config_json']).get('name', '')} for row in
                    conn.execute("SELECT project,revision,config_json,purpose FROM workflow_projects ORDER BY project")
                    if active_project_code(conn, row['project'])]

    @app.post('/api/v1/projects', response_model=ConfigurationView)
    def create_project(command: ProjectCreation, operator: ActorDependency):
        return workflow.create_project(command, actor=operator)

    @app.post('/api/v1/intake/protocol')
    def inspect_stability_protocol(operator: ActorDependency, file: Annotated[UploadFile, File()]):
        require(operator, Capability.PREVIEW)
        from .protocol_preview import inspect_protocol
        try:
            data = file.file.read(20 * 1024 * 1024 + 1)
            if len(data) > 20 * 1024 * 1024 or Path(file.filename or '').suffix.lower() != '.docx':
                raise WorkflowError('invalid_document', '请选择不超过20MB的Word方案。')
            result = inspect_protocol(data)
            draft_id = hashlib.sha256(data).hexdigest()
            with store.connect() as conn:
                conn.execute(
                    'INSERT INTO protocol_drafts(id,filename,source,preview_json) VALUES (?,?,?,?) '
                    'ON CONFLICT(id) DO UPDATE SET preview_json=excluded.preview_json',
                    (draft_id, Path(file.filename).name, data, json.dumps(result, ensure_ascii=False)),
                )
            return {'draft_id': draft_id, **result}
        except (BadZipFile, KeyError, ValueError, XMLSyntaxError) as exc:
            raise WorkflowError('invalid_document', '无法读取Word方案。') from exc
        finally:
            file.file.close()

    @app.get('/api/v1/projects/{project}/worksheet-drafts')
    def worksheet_draft_list(project: ProjectDependency, operator: ActorDependency):
        require(operator, Capability.VIEW)
        with store.read() as conn:
            return [dict(row) for row in conn.execute(
                """SELECT id,name,created_at,json_extract(draft_json,'$.protocol_draft') AS protocol_draft,
                          json_extract(draft_json,'$.protocol_table') AS protocol_table,
                          json_extract(draft_json,'$.copy_source') AS copy_source
                   FROM worksheet_drafts d WHERE d.project=? AND NOT EXISTS (
                       SELECT 1 FROM workflow_events e WHERE e.project=d.project AND e.subject_id=d.id
                       AND e.action='worksheet_draft_converted'
                   ) ORDER BY d.created_at DESC,d.rowid DESC LIMIT 100""",
                (project,),
            )]

    @app.post('/api/v1/projects/{project}/worksheet-drafts', status_code=201)
    def save_worksheet_draft(project: ProjectDependency, command: WorksheetDraftInput, operator: ActorDependency):
        require(operator, Capability.CONFIRM_FACT)
        from .workflow_common import project_row
        payload = json.dumps(command.model_dump(mode='json'), ensure_ascii=False, sort_keys=True)
        if len(payload.encode('utf-8')) > 256 * 1024:
            raise WorkflowError('worksheet_draft_size', '待补全计划超过可保存大小。')
        identifier = hashlib.sha256((project + '\n' + payload).encode('utf-8')).hexdigest()
        with store.connect() as conn:
            project_row(conn, project)
            conn.execute('INSERT OR IGNORE INTO worksheet_drafts(id,project,name,draft_json) VALUES (?,?,?,?)',
                         (identifier, project, command.fields.get('name', '').strip() or '未命名研究表', payload))
        return {'id': identifier}

    @app.get('/api/v1/projects/{project}/worksheet-drafts/{draft_id}')
    def worksheet_draft(project: ProjectDependency, draft_id: str, operator: ActorDependency):
        require(operator, Capability.VIEW)
        with store.read() as conn:
            row = conn.execute('SELECT draft_json FROM worksheet_drafts WHERE project=? AND id=?', (project, draft_id)).fetchone()
        if row is None:
            raise WorkflowError('not_found', '找不到此项目的待补全计划。', 404)
        return json.loads(row['draft_json'])

    @app.get('/api/v1/plan-drafts')
    def protocol_drafts(operator: ActorDependency):
        require(operator, Capability.VIEW)
        with store.read() as conn:
            return [dict(row) for row in conn.execute(
                'SELECT id,filename,created_at FROM protocol_drafts ORDER BY created_at DESC,id LIMIT 100'
            )]

    def protocol_draft_row(draft_id):
        with store.read() as conn:
            row = conn.execute('SELECT * FROM protocol_drafts WHERE id=?', (draft_id,)).fetchone()
        if row is None:
            raise WorkflowError('not_found', '方案草稿不存在。', 404)
        return row

    @app.get('/api/v1/plan-drafts/{draft_id}')
    def protocol_draft(draft_id: str, operator: ActorDependency):
        require(operator, Capability.VIEW)
        row = protocol_draft_row(draft_id)
        return {'draft_id': row['id'], 'filename': row['filename'], **json.loads(row['preview_json'])}

    @app.get('/api/v1/projects/{project}/plan-drafts/{draft_id}/standards')
    def protocol_standard_links(project: ProjectDependency, draft_id: str, operator: ActorDependency):
        require(operator, Capability.VIEW)
        from .specification_preview import matching_standards, protocol_numbers
        numbers = protocol_numbers(protocol_draft_row(draft_id))
        with store.read() as conn:
            found = matching_standards(conn, project, numbers)
        return {'missing': [number for number in numbers if not any(item['number'] == number for item in found)], 'found': found}

    @app.get('/api/v1/plan-drafts/{draft_id}/file')
    def protocol_draft_file(draft_id: str, operator: ActorDependency):
        require(operator, Capability.VIEW)
        from urllib.parse import quote
        row = protocol_draft_row(draft_id)
        if hashlib.sha256(row['source']).hexdigest() != draft_id:
            raise WorkflowError('source_integrity', '方案原件校验失败。', 409)
        return Response(row['source'], media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                        headers={'Content-Disposition': "attachment; filename*=UTF-8''" + quote(row['filename'], safe='')})

    @app.post('/api/v1/specifications')
    def upload_specification(operator: ActorDependency, file: Annotated[UploadFile, File()]):
        require(operator, Capability.PREVIEW)
        from .specification_preview import inspect_specification
        try:
            data = file.file.read(20 * 1024 * 1024 + 1)
            suffix = Path(file.filename or '').suffix.lower()
            if len(data) > 20 * 1024 * 1024 or suffix not in {'.docx', '.xlsx'}:
                raise WorkflowError('invalid_document', '请选择不超过20MB的Word或Excel质量标准（.docx / .xlsx）。')
            preview = inspect_specification(data, file.filename or "")
            if not preview['rows']:
                raise WorkflowError('specification_table_missing', '未识别到质量标准表。')
            identifier = hashlib.sha256(data).hexdigest()
            with store.connect() as conn:
                conn.execute('''
                    INSERT INTO specifications(id,filename,source,preview_json) VALUES (?,?,?,?)
                    ON CONFLICT(id) DO NOTHING
                ''', (identifier, Path(file.filename).name, data, json.dumps(preview, ensure_ascii=False)))
            return get_specification(identifier, operator)
        except WorkflowError:
            raise
        except (BadZipFile, KeyError, ValueError, XMLSyntaxError) as exc:
            raise WorkflowError('invalid_document', '无法读取质量标准文件，请核对文件格式。') from exc
        finally:
            file.file.close()

    @app.get("/api/v1/projects/{project}/current-specifications")
    def current_specifications(project: ProjectDependency, operator: ActorDependency):
        require(operator, Capability.VIEW)
        with store.read() as conn:
            return [dict(row) for row in conn.execute("""SELECT c.material_scope,c.specification_id,s.filename
                FROM current_specifications c JOIN specifications s ON s.id=c.specification_id
                WHERE c.project=? ORDER BY c.material_scope""", (project,))]

    @app.post("/api/v1/projects/{project}/current-specifications")
    def set_current_specification(project: ProjectDependency, command: CurrentSpecification, operator: ActorDependency):
        require(operator, Capability.CONFIRM_FACT)
        from .specification_preview import specification_material_scope
        from .workflow_common import bump, check_revision, event, remember, replay
        with store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            payload = {"current_specification": command.model_dump()}
            prior = replay(conn, project, operator, command.idempotency_key, payload)
            if prior is not None:
                return prior
            check_revision(conn, project, command.revision)
            source = conn.execute("""SELECT filename,preview_json FROM specifications s WHERE s.id=? AND EXISTS (
                SELECT 1 FROM specification_assay_components a WHERE a.specification_id=s.id AND a.project=?)""",
                (command.specification_id, project)).fetchone()
            if source is None:
                raise WorkflowError("specification_scope", "请先核对并登记此项目的标准条款。", 409)
            scope = specification_material_scope(source["filename"], json.loads(source["preview_json"]))
            if scope not in {"DS", "DP", "placebo", "reference"}:
                raise WorkflowError("specification_scope", "标准原件的适用材料尚未明确。", 409)
            previous = conn.execute("SELECT specification_id FROM current_specifications WHERE project=? AND material_scope=?",
                                    (project, scope)).fetchone()
            conn.execute("""INSERT INTO current_specifications VALUES (?,?,?) ON CONFLICT(project,material_scope)
                DO UPDATE SET specification_id=excluded.specification_id""", (project, scope, command.specification_id))
            event(conn, project, command.specification_id, "current_specification", operator, "确认当前适用标准",
                  {"material_scope": scope, "previous": previous[0] if previous else None})
            result = {"material_scope": scope, "specification_id": command.specification_id, "revision": bump(conn, project)}
            remember(conn, project, operator, command.idempotency_key, payload, result)
            return result

    @app.get('/api/v1/specifications')
    def specifications(operator: ActorDependency):
        require(operator, Capability.VIEW)
        from .specification_preview import specification_material_scope
        with store.read() as conn:
            projects = {}
            for row in conn.execute('SELECT DISTINCT specification_id,project FROM specification_assay_components ORDER BY project'):
                projects.setdefault(row["specification_id"], []).append(row["project"])
            return [{"id": row["id"], "filename": row["filename"], "created_at": row["created_at"],
                     "material_scope": specification_material_scope(row["filename"], json.loads(row["preview_json"])),
                     "registered_projects": projects.get(row["id"], [])} for row in conn.execute(
                'SELECT id,filename,created_at,preview_json FROM specifications ORDER BY created_at DESC,id LIMIT 100'
            )]

    def specification_row(specification_id, *, source=False):
        columns = 'id,filename,source,preview_json' if source else 'id,filename,preview_json'
        with store.read() as conn:
            row = conn.execute(f'SELECT {columns} FROM specifications WHERE id=?', (specification_id,)).fetchone()
        if row is None:
            raise WorkflowError('not_found', '质量标准不存在。', 404)
        return row

    @app.get('/api/v1/specifications/{specification_id}')
    def get_specification(specification_id: str, operator: ActorDependency):
        require(operator, Capability.VIEW)
        row = specification_row(specification_id, source=True)
        preview = json.loads(row['preview_json'])
        # 兼容旧版本预览或未补充完整字段的规格：自动利用原件重新解析并持久化
        needs_refresh = (
            not preview.get('rows')
            or any('result_type' not in r or not r.get('component') or ('item_zh' not in r) for r in preview['rows'])
        )
        if row['source'] and needs_refresh and str(row['filename']).lower().endswith('.docx'):
            try:
                from .specification_preview import inspect_specification
                fresh = inspect_specification(row['source'], row['filename'] or "")
                if fresh.get('rows'):
                    preview = fresh
                    with store.connect() as conn:
                        conn.execute("UPDATE specifications SET preview_json=? WHERE id=?",
                                     (json.dumps(fresh, ensure_ascii=False), specification_id))
            except Exception:
                pass
        with store.read() as conn:
            linked_projects = [dict(entry) for entry in conn.execute(
                """SELECT a.project,COUNT(*) AS count, EXISTS (
                       SELECT 1 FROM current_specifications c
                       WHERE c.project=a.project AND c.specification_id=a.specification_id) AS is_current
                   FROM specification_assay_components a WHERE a.specification_id=? GROUP BY a.project""",
                (specification_id,)) if active_project_code(conn, entry['project'])]
        from .specification_preview import specification_material_scope
        return {'id': row['id'], 'filename': row['filename'], **preview, 'linked_projects': linked_projects,
                'material_scope': specification_material_scope(row['filename'], preview)}

    @app.get('/api/v1/specifications/{specification_id}/file')
    def specification_file(specification_id: str, operator: ActorDependency):
        require(operator, Capability.VIEW)
        from urllib.parse import quote
        row = specification_row(specification_id, source=True)
        source_hash = json.loads(row['preview_json']).get('scan_id', specification_id)
        if hashlib.sha256(row['source']).hexdigest() != source_hash:
            raise WorkflowError('source_integrity', '质量标准原件校验失败。', 409)
        if str(row['filename']).lower().endswith('.xlsx'):
            media_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        else:
            media_type = 'application/pdf' if source_hash != specification_id else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        return Response(row['source'], media_type=media_type,
                        headers={'Content-Disposition': "attachment; filename*=UTF-8''" + quote(row['filename'], safe='')})

    @app.get('/api/v1/pdf-ocr')
    def list_ocr(operator: ActorDependency):
        require(operator, Capability.VIEW)
        return ocr.list()

    @app.post('/api/v1/pdf-ocr', status_code=201)
    def archive_scan(operator: ActorDependency, file: Annotated[UploadFile, File()]):
        require(operator, Capability.PREVIEW)
        try:
            return {'id': ocr.archive(file.file.read(20 * 1024 * 1024 + 1), file.filename or 'scan.pdf')}
        finally:
            file.file.close()

    @app.get('/api/v1/pdf-ocr/{identifier}')
    def read_ocr(identifier: str, operator: ActorDependency):
        require(operator, Capability.VIEW)
        return ocr.read(identifier)

    @app.post('/api/v1/pdf-ocr/{identifier}/start')
    def start_ocr(identifier: str, operator: ActorDependency):
        require(operator, Capability.PREVIEW)
        return ocr.start(identifier)

    @app.post('/api/v1/pdf-ocr/{identifier}/cancel')
    def cancel_ocr(identifier: str, operator: ActorDependency):
        require(operator, Capability.PREVIEW)
        return ocr.cancel(identifier)

    @app.put('/api/v1/pdf-ocr/{identifier}/pages/{number}/review')
    def save_ocr_page_review(identifier: str, number: int, command: OcrPageReview, operator: ActorDependency):
        require(operator, Capability.PREVIEW)
        return ocr.save_review(identifier, number, command.revision, command.lines)

    @app.post('/api/v1/pdf-ocr/{identifier}/specification', status_code=201)
    def save_scan_specification(identifier: str, command: ScanSpecification, operator: ActorDependency):
        require(operator, Capability.PREVIEW)
        from .specification_preview import reviewed_scan_criteria
        scan = ocr.read(identifier)
        page = next((page for page in scan['pages'] if page['page'] == command.page), None)
        if command.manual_page:
            if any(row.table or row.column for row in command.rows):
                raise WorkflowError('ocr_cell_missing', '手工核对只能登记手工条款，不能引用 OCR 单元格。')
            page = {'page': command.page, 'tables': []}
        if page is None:
            raise WorkflowError('ocr_page_missing', '找不到对应的识别页。', 404)
        rows = reviewed_scan_criteria(page, command.rows)
        preview = {'scan_id': identifier, 'scope': command.scope, 'material_scope': command.material_scope,
                   'rows': rows}
        if command.manual_page:
            preview['manual_page_review'] = True
        encoded_preview = json.dumps(preview, ensure_ascii=False, sort_keys=True)
        version = hashlib.sha256(encoded_preview.encode('utf-8')).hexdigest()
        original = ocr.original(identifier).read_bytes()
        filename = scan['filename']
        with store.connect() as conn:
            conn.execute('INSERT OR IGNORE INTO specifications(id,filename,source,preview_json) VALUES (?,?,?,?)',
                         (version, filename, original, encoded_preview))
        return get_specification(version, operator)

    @app.get('/api/v1/pdf-ocr/{identifier}/file')
    def scan_file(identifier: str, operator: ActorDependency):
        require(operator, Capability.VIEW)
        return FileResponse(ocr.original(identifier), media_type='application/pdf')

    @app.post('/api/v1/intake/inspect')
    def inspect_upload(operator: ActorDependency, file: Annotated[UploadFile, File()]):
        require(operator, Capability.PREVIEW)
        try:
            data = file.file.read(20 * 1024 * 1024 + 1)
            suffix = Path(file.filename or '').suffix.lower()
            if len(data) > 20 * 1024 * 1024 or suffix not in {'.xlsx', '.docx'}:
                raise WorkflowError('invalid_document', '请选择不超过20MB的Excel或Word文件。')
            with TemporaryDirectory() as folder:
                source = Path(folder) / ('input' + suffix)
                source.write_bytes(data)
                parsed = parse_source(source, allow_partial=True)
            content_projects = sorted({r.fields.get('project', parsed.metadata.get('project', '')).strip()
                                       for r in parsed.observations} - {''})
            file_project = None
            stem = Path(file.filename or '').stem
            # Do not extract project from hex hashes (e.g. sha256/md5) or arbitrary substrings within hex
            if stem and not (len(stem) >= 16 and all(c in '0123456789abcdefABCDEF' for c in stem)):
                m = re.search(r'(?:^|[\s_—\-\.\(\)\[\]])([A-Za-z]{1,5}\d{2,5}[A-Za-z]?)(?:$|[\s_—\-\.\(\)\[\]])', stem)
                if m:
                    file_project = m.group(1).upper()
            sha = hashlib.sha256(data).hexdigest()
            with workflow.store.connect() as conn:
                existing = conn.execute(
                    'SELECT id, project FROM workflow_uploads WHERE source_hash=? ORDER BY sequence LIMIT 1',
                    (sha,)
                ).fetchone()
                existing_project = existing['project'] if existing else None
                source_candidates = (
                    conn.execute('SELECT count(*) FROM workflow_candidates WHERE upload_id=?', (existing['id'],)).fetchone()[0]
                    if existing else len(parsed.observations)
                )

            # Strongest evidence is parsed document content; fallback to non-hash filename token, then existing record
            if content_projects:
                projects_found = content_projects
            elif file_project:
                projects_found = [file_project]
            elif existing_project:
                projects_found = [existing_project]
            else:
                projects_found = []

            resp = {
                'projects': projects_found,
                'parsing_summary': parsed.coverage if not existing else [],
                'source_candidates': source_candidates,
            }
            if existing:
                resp['duplicate_source'] = True
                resp['existing_project'] = existing_project
                if projects_found and existing_project not in projects_found:
                    resp['project_conflict'] = {
                        'existing_project': existing_project,
                        'detected_projects': projects_found,
                        'notice': f'该文件内容与已入库项目【{existing_project}】存在归属冲突，请人工核对并确认项目。'
                    }
            return resp
        finally:
            file.file.close()

    @app.post('/api/v1/projects/{project}/items', response_model=MutationResult)
    def add_items(project: ProjectDependency, command: NewItems, operator: ActorDependency):
        return workflow.add_items(project, command, actor=operator)

    from .v5_contracts import ItemDisplayName
    from .item_identity import rename as rename_item

    @app.post('/api/v1/projects/{project}/items/{item_id}/display-name',response_model=MutationResult)
    def rename_item_display(project: ProjectDependency, item_id: str, command: ItemDisplayName, operator: ActorDependency):
        return rename_item(workflow,project,item_id,command,actor=operator)

    @app.get("/api/v1/projects/{project}/configuration", response_model=ConfigurationView)
    def configuration(project: ProjectDependency, operator: ActorDependency):
        return workflow.configuration(project, actor=operator)

    @app.post("/api/v1/projects/{project}/configuration", response_model=MutationResult)
    def change_configuration(project: ProjectDependency, command: ConfigurationChange, operator: ActorDependency):
        return workflow.change_configuration(project, command, actor=operator)

    @app.get("/api/v1/projects/{project}/previews", response_model=list[PreviewSummary])
    def previews(project: ProjectDependency, operator: ActorDependency, offset: int = Query(0, ge=0),
                 limit: int = Query(20, ge=1, le=100)):
        return workflow.list_previews(project, offset, limit, actor=operator)

    from threading import Lock
    upload_progress = {}
    upload_progress_lock = Lock()

    @app.get("/api/v1/projects/{project}/preview-progress/{progress_id}")
    def read_upload_progress(project: ProjectDependency, progress_id: str, operator: ActorDependency):
        require(operator, Capability.PREVIEW)
        with upload_progress_lock:
            return upload_progress.get((project, operator.id, progress_id), {"phase": "waiting"})

    @app.post("/api/v1/projects/{project}/previews", response_model=UploadPreview)
    def upload(project: ProjectDependency, operator: ActorDependency, file: Annotated[UploadFile, File()],
               idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
               context: Annotated[str, Form()] = "{}",
               progress_id: Annotated[str | None, Header(alias="Upload-Progress-Id", max_length=80)] = None,
               compact: bool = False):
        require(operator, Capability.PREVIEW)
        key = (project, operator.id, progress_id)

        def update(phase, completed=None, total=None, candidates=None, section=None):
            with upload_progress_lock:
                upload_progress[key] = {"phase": phase, "completed": completed, "total": total,
                                        "candidates": candidates, "section": section}

        if progress_id:
            with upload_progress_lock:
                if key in upload_progress:
                    raise WorkflowError("upload_in_progress", "此上传仍在处理，请等待。", 409)
                upload_progress[key] = {"phase": "reading"}
        try:
            ctx = ImportContext.model_validate_json(context)
            data = file.file.read(20 * 1024 * 1024 + 1)
            result = workflow.upload(project, file.filename or "input", data, ctx, idempotency_key,
                                     actor=operator, deduplicate=True, progress=update if progress_id else None)
            return result.model_copy(update={"candidates": ()}) if compact else result
        except (BadZipFile, KeyError, ValidationError) as exc:
            raise WorkflowError("invalid_document", "文件结构或上传上下文不符合支持格式。") from exc
        finally:
            file.file.close()
            if progress_id:
                with upload_progress_lock:
                    upload_progress.pop(key, None)

    @app.get("/api/v1/previews/{preview_id}", response_model=UploadPreview)
    def preview(preview_id: str, operator: ActorDependency):
        return workflow.preview(preview_id, actor=operator)

    @app.get("/api/v1/previews/{preview_id}/workspace", response_model=WorkspacePreview)
    def preview_workspace(preview_id: str, operator: ActorDependency, section: str | None = None):
        require(operator, Capability.VIEW)
        with store.read() as conn:
            upload = workflow._upload(conn, preview_id)
            result = workflow._preview(conn, upload, operator, fresh=True)
            if result.sheet_confirmation_required:
                from .review_tasks import current_review, enrich_sheet_preflight
                preview, physical_sheets, flights, tasks, _ = current_review(workflow, conn, upload, operator, _shared=True)
                result = preview
                physical = physical_sheets
                sections = [sheet["section"] for sheet in physical]
                if section is not None and section not in sections:
                    raise WorkflowError("unknown_sheet", "链接中的工作表已无法定位，请重新选择工作表。", 404)
                selected = section if section is not None else next(iter(sections), None)
                flight = next((f for f in flights if f.section == selected), None)
                view = enrich_sheet_preflight(conn, upload, result, flight, physical_sheets=physical) if flight else None
                all_candidates = result.candidates
                result = result.model_copy(update={"candidates": tuple(
                    candidate for candidate in result.candidates if candidate.location.section == selected)})

                section_statuses = []
                flight_by_sec = {f.section: f for f in flights}
                from .review_tasks import blocker_index, blocker_summary
                for s in sections:
                    f = flight_by_sec.get(s)
                    c_count = sum(1 for c in all_candidates if c.location.section == s)
                    if f is None:
                        sec_state = "empty"
                        unresolved = 0
                    elif f.state == "committed":
                        sec_state = "committed"
                        unresolved = 0
                    else:
                        # Real decisions only: a not_received receipt placeholder is
                        # a save step, not a source question awaiting review.
                        unresolved = blocker_summary(blocker_index(f, tasks))["tasks"]
                        sec_state = ("not_received" if any(u == "not_received" or u.startswith("not_received:")
                                                           for u in f.unresolved) else f.state)
                    section_statuses.append({
                        "section": s,
                        "state": sec_state,
                        "unresolved_count": unresolved,
                        "candidate_count": c_count,
                    })
            else:
                sections = []
                selected = None
                view = preflight(workflow, conn, upload, None, operator, preview=result)[0] if result.candidates else None
                section_statuses = []

            receipt = None
            if view and view.state == "committed":
                row = conn.execute("""SELECT detail_json FROM workflow_events WHERE subject_id=? AND action='confirm_sheet'
                    AND json_extract(detail_json,'$.section')=? ORDER BY sequence DESC LIMIT 1""",
                    (preview_id, selected)).fetchone()
                receipt = json.loads(row[0]) if row else None

            return {"preview": result, "sections": sections, "section": selected, "commit_receipt": receipt,
                    "preflight": view.model_dump(mode="json") if view else None,
                    "section_statuses": section_statuses}

    @app.post('/api/v1/previews/{preview_id}/sync-worksheets')
    def sync_confirmed_worksheets(preview_id: str, operator: ActorDependency, section: str | None = None):
        require(operator, Capability.CONFIRM_FACT)
        from .worksheet_seed import provision_from_results
        with store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            upload = workflow._upload(conn, preview_id)
            rows = conn.execute("""SELECT detail_json FROM workflow_events
                WHERE project=? AND subject_id=? AND action='confirm_sheet'
                  AND (? IS NULL OR json_extract(detail_json,'$.section')=?)""",
                (upload["project"], preview_id, section, section))
            ids = [version_id for row in rows for version_id in
                   json.loads(row["detail_json"]).get("result_version_ids", [])]
            return {"worksheet_sync": provision_from_results(conn, worksheets, upload["project"], ids, operator)}

    @app.get('/api/v1/previews/{preview_id}/worksheets')
    def worksheets_for_import(preview_id: str, operator: ActorDependency, section: str | None = None):
        require(operator, Capability.VIEW)
        with store.read() as conn:
            workflow._upload(conn, preview_id)
            return [dict(row) for row in conn.execute(
                """SELECT DISTINCT sheet.id, sheet.name
                   FROM workflow_candidates candidate
                   LEFT JOIN workflow_events receipt ON receipt.subject_id = candidate.id
                       AND receipt.project = candidate.project AND receipt.action = 'repeat_source_reference'
                   JOIN workflow_versions version ON version.candidate_id = candidate.id
                       OR version.id = json_extract(receipt.detail_json, '$.previous_version_id')
                   JOIN business_worksheet_slots slot ON slot.slot_id = version.slot_id
                   JOIN business_worksheets sheet ON sheet.id = slot.sheet_id AND sheet.project = version.project
                     AND sheet.deleted_at IS NULL
                   WHERE candidate.upload_id = ? AND candidate.lifecycle = 'submitted'
                     AND (? IS NULL OR json_extract(candidate.raw_json, '$.location.section') = ?)
                   ORDER BY sheet.name, sheet.id""", (preview_id, section, section),
            )]

    @app.patch("/api/v1/previews/{preview_id}/mapping", response_model=MutationResult)
    def map_items(preview_id: str, command: CandidateMapping, operator: ActorDependency):
        return workflow.map_items(preview_id, command, actor=operator)

    @app.patch('/api/v1/previews/{preview_id}/shared', response_model=MutationResult)
    def shared_edit(preview_id: str, command: SharedEdit, operator: ActorDependency):
        return workflow.edit_shared(preview_id, command, actor=operator)

    @app.patch("/api/v1/candidates/{candidate_id}", response_model=MutationResult)
    def edit(candidate_id: str, command: CandidateEdit, operator: ActorDependency):
        return workflow.edit(candidate_id, command, actor=operator)

    @app.post("/api/v1/candidates/{candidate_id}/edit-preview")
    def edit_candidate_preview(candidate_id: str, command: CandidateEdit, operator: ActorDependency):
        return workflow.preview_edit(candidate_id, command, actor=operator)

    @app.post("/api/v1/candidates/{candidate_id}/withdraw", response_model=MutationResult)
    def withdraw(candidate_id: str, command: WithdrawRequest, operator: ActorDependency):
        return versions.withdraw_candidate(candidate_id, command, actor=operator)

    @app.post("/api/v1/previews/{preview_id}/confirm", response_model=MutationResult)
    def confirm(preview_id: str, command: ConfirmRequest, operator: ActorDependency):
        return versions.confirm(preview_id, command, actor=operator)

    @app.post("/api/v1/results/{version_id}/review", response_model=MutationResult)
    def review(version_id: str, command: ReviewRequest, operator: ActorDependency):
        return versions.review(version_id, command, actor=operator)

    @app.post('/api/v1/results/{version_id}/change', response_model=MutationResult)
    def result_change(version_id: str, command: ResultChange, operator: ActorDependency):
        return versions.change(version_id, command, actor=operator)

    @app.post('/api/v1/results/{version_id}/invalidate', response_model=MutationResult)
    def invalidate_result(version_id: str, command: InvalidateRequest, operator: ActorDependency):
        """P0-3: 正式结果留痕作废接口"""
        return versions.invalidate(version_id, command, actor=operator)

    @app.get('/api/v1/results/{version_id}/invalidate')
    def preview_invalidation(version_id: str, operator: ActorDependency):
        return versions.preview_invalidation(version_id, actor=operator)

    @app.get("/api/v1/projects/{project}/result-cells", response_model=ResultPage)
    def results(project: ProjectDependency, operator: ActorDependency, query: Annotated[ResultQuery, Query()]):
        filters = ResultFilter.model_validate(query.model_dump(exclude={'offset', 'limit'}))
        return workflow.results(project, filters, query.offset, query.limit, actor=operator)

    @app.get('/api/v1/projects/{project}/worksheet-seed')
    def worksheet_seed(project: ProjectDependency, operator: ActorDependency, batch: str = "", slot_id: str = "",
                       source: Literal["blank", "standard", "protocol", "results", "copy"] | None = None,
                       source_id: str = "", table: int | None = None):
        require(operator, Capability.VIEW)
        from .worksheet_seed import plan_seed
        with store.read() as conn:
            return plan_seed(conn, worksheets, project, operator, source=source or ("results" if slot_id else "blank"),
                             batch=batch, slot_id=slot_id, source_id=source_id, table=table)

    @app.get('/api/v1/projects/{project}/result-display')
    def result_display(project: ProjectDependency, operator: ActorDependency, filters: Annotated[ResultFilter, Query()]):
        require(operator, Capability.VIEW)
        from .result_display import display_results
        with store.read() as conn:
            return display_results(conn, project, filters)

    from .workflow_contracts import ResultTrendQuery

    @app.get('/api/v1/projects/{project}/result-trend')
    def result_trend(project: ProjectDependency, operator: ActorDependency, query: Annotated[ResultTrendQuery, Query()]):
        require(operator, Capability.VIEW)
        from .result_display import trend_results
        with store.read() as conn:
            filters = ResultFilter.model_validate(query.model_dump(exclude={'group_id','base_revision','batches','axis_mode','axis_min','axis_max'}))
            return trend_results(conn, project, filters, group_id=query.group_id,
                                 revision=query.base_revision, batches=query.batches,
                                 axis={k:getattr(query,k) for k in ('axis_mode','axis_min','axis_max')})

    @app.get("/api/v1/sources/{source_id}/file")
    def candidate_original(source_id: str, operator: ActorDependency):
        path, filename = versions.original(source_id, actor=operator)
        return FileResponse(path, filename=filename, media_type='application/octet-stream')

    @app.get("/api/v1/sources/{source_id}")
    def source(source_id: str, operator: ActorDependency):
        return versions.source(source_id, actor=operator)

    @app.get("/api/v1/projects/{project}/history")
    def history(project: ProjectDependency, operator: ActorDependency):
        return versions.history(project, actor=operator)

    @app.get("/api/v1/projects/{project}/detections")
    def detections(project: ProjectDependency, operator: ActorDependency):
        require(operator, Capability.VIEW)
        with store.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT id,source_number,created_at FROM workflow_detections WHERE project=? ORDER BY rowid", (project,))]

    @app.get("/api/v1/projects/{project}/pending-results")
    def pending(project: ProjectDependency, operator: ActorDependency):
        require(operator, Capability.VIEW)
        with store.connect() as conn:
            from .identity_associations import versions as logical_versions
            return [{"result_version_id": row["id"], "candidate_id": row["candidate_id"], "slot_id": row["slot_id"],
                     "physical_slot_id":row.get('physical_slot_id',row['slot_id']),
                     "record": json.loads(row["record_json"]), "intent": row["intent"], "reason": row["reason"],
                     "submitted_by": row["submitted_by"], "references": json.loads(row["references_json"])}
                    for row in logical_versions(conn,project) if row['state'] == 'pending']

    @app.post("/api/v1/exports", response_model=ExportResult)
    def export(command: ExportRequest, operator: ActorDependency):
        require(operator, Capability.EXPORT)
        with store.read() as conn:
            project = unique_project_code(conn, command.project)
        return exports.create(command.model_copy(update={"project": project}), actor=operator)

    @app.get("/api/v1/exports/{export_id}/file")
    def download(export_id: str, operator: ActorDependency):
        path = exports.download(export_id, actor=operator)
        return FileResponse(path, filename=path.name,
                            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    @app.get('/api/v1/exports/{export_id}/image')
    def download_image(export_id: str,operator: ActorDependency):
        return FileResponse(exports.download_image(export_id,actor=operator),filename='trend.png',media_type='image/png')

    @app.get('/api/v1/exports/{export_id}/attachment')
    def download_export_attachment(export_id: str, operator: ActorDependency):
        require(operator, Capability.EXPORT)
        with store.read() as conn:
            row = conn.execute('SELECT snapshot_json FROM workflow_exports WHERE id=?', (export_id,)).fetchone()
        attachment = json.loads(row['snapshot_json']).get('attachment') if row else None
        if not attachment:
            raise WorkflowError('export_attachment_missing', '此交付没有扫描附件。', 404)
        return FileResponse(ocr.original(attachment['id']), filename=attachment['filename'], media_type='application/pdf')

    from .comparability import StudySave, StudyQuery, QuickCompare
    from .study_reports import ReportRequest

    @app.get('/api/v1/projects/{project}/comparison-catalog')
    def comparison_catalog(project: ProjectDependency, operator: ActorDependency):
        require(operator, Capability.VIEW)
        from .quick_comparison import catalog
        with store.read() as conn:
            return catalog(conn,project)

    @app.post('/api/v1/projects/{project}/comparison-preview')
    def comparison_preview(project: ProjectDependency, command: QuickCompare, operator: ActorDependency):
        require(operator, Capability.VIEW)
        from .quick_comparison import quick_compare
        with store.read() as conn:
            return quick_compare(conn,project,command)

    @app.get('/api/v1/projects/{project}/comparison-studies')
    def comparison_studies(project: ProjectDependency, operator: ActorDependency):
        require(operator, Capability.VIEW)
        from .comparability import studies, inventory
        from .workflow_common import project_row
        with store.read() as conn:
            return {'project':project,'base_revision':project_row(conn,project)['revision'],
                    'studies':studies(conn,project),'samples':inventory(conn,project)}

    @app.post('/api/v1/projects/{project}/comparison-studies/{study_id}')
    def save_comparison(project: ProjectDependency, study_id: str, command: StudySave, operator: ActorDependency):
        from .comparability import save
        return save(workflow,project,study_id,command,operator)

    @app.get('/api/v1/projects/{project}/comparison-studies/{study_id}/results')
    def comparison_results(project: ProjectDependency, study_id: str, operator: ActorDependency, query: Annotated[StudyQuery, Query()]):
        require(operator, Capability.VIEW)
        from .comparability import compare
        with store.read() as conn:
            return compare(conn,project,study_id,query)

    @app.post('/api/v1/reports')
    def report_export(command: ReportRequest, operator: ActorDependency):
        from .study_reports import create_report
        require(operator, Capability.EXPORT)
        with store.read() as conn:
            project = unique_project_code(conn, command.project)
        return create_report(workflow, command.model_copy(update={"project": project}), operator)

    @app.get('/api/v1/reports/{export_id}/file')
    def report_download(export_id: str, operator: ActorDependency):
        path = exports.download(export_id,actor=operator)
        if path.name != 'report.zip':
            raise WorkflowError('report_not_found','不是报告导出。',404)
        return FileResponse(path,filename='report.zip',media_type='application/zip')

    @app.get('/api/v1/reports/{export_id}/word')
    def report_word(export_id: str, operator: ActorDependency):
        path = exports.download(export_id,actor=operator)
        if path.name != 'report.zip':
            raise WorkflowError('report_not_found','不是报告导出。',404)
        from zipfile import ZipFile
        with ZipFile(path) as package:
            content = package.read('report.docx')
        return Response(content,media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                        headers={'Content-Disposition':'attachment; filename="report.docx"'})

    @app.get('/api/v1/catalog/platform')
    def get_platform_catalog(
        operator: ActorDependency,
        category_code: str = "",
        subcategory_code: str = "",
        query: str = "",
    ):
        require(operator, Capability.VIEW)
        from .platform_catalog import list_platform_catalog
        with store.read() as conn:
            return list_platform_catalog(
                conn,
                category_code=category_code.strip(),
                subcategory_code=subcategory_code.strip(),
                query=query.strip(),
            )

    @app.post('/api/v1/catalog/platform/assays')
    def post_platform_assay(payload: dict, operator: ActorDependency):
        require(operator, Capability.CONFIRM_FACT)
        from .platform_catalog import create_platform_assay
        try:
            with store.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                return create_platform_assay(
                    conn,
                    category_code=str(payload.get("category_code") or "").strip(),
                    subcategory_code=str(payload.get("subcategory_code") or "").strip(),
                    name_zh=str(payload.get("name_zh") or "").strip(),
                    name_en=str(payload.get("name_en") or "").strip(),
                    default_unit=str(payload.get("default_unit") or "").strip(),
                    pharmacopoeia_scope=str(payload.get("pharmacopoeia_scope") or "ChP/USP/EP").strip(),
                    modality_tags=list(payload.get("modality_tags") or ["mAb"]),
                    methods=list(payload.get("methods") or []),
                    components=list(payload.get("components") or []),
                    aliases=list(payload.get("aliases") or []),
                )
        except ValueError as exc:
            raise WorkflowError("invalid_catalog_entry", str(exc)) from exc

    @app.post('/api/v1/catalog/platform/aliases')
    def post_platform_alias(payload: dict, operator: ActorDependency):
        require(operator, Capability.CONFIRM_FACT)
        from .platform_catalog import add_platform_alias
        try:
            with store.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                return add_platform_alias(
                    conn,
                    entity_type=str(payload.get("entity_type") or "assay").strip(),
                    entity_id=str(payload.get("entity_id") or "").strip(),
                    raw_alias=str(payload.get("raw_alias") or "").strip(),
                    source="user",
                )
        except ValueError as exc:
            raise WorkflowError("invalid_catalog_alias", str(exc)) from exc

    @app.post('/api/v1/projects/{project}/catalog/promote')
    def promote_catalog_item(project: ProjectDependency, payload: dict, operator: ActorDependency):
        from .assay_catalog import promote_to_platform_catalog
        name_zh = str(payload.get("name_zh") or "").strip()
        if not name_zh:
            raise WorkflowError("invalid_catalog_entry", "请提供标准中文检项名称。")
        return promote_to_platform_catalog(
            store,
            project,
            name_zh=name_zh,
            name_en=str(payload.get("name_en") or "").strip(),
            method=str(payload.get("method") or "").strip(),
            component_zh=str(payload.get("component_zh") or "").strip(),
            component_en=str(payload.get("component_en") or "").strip(),
            unit=str(payload.get("unit") or "").strip(),
            result_type=str(payload.get("result_type") or "numeric").strip(),
            category_code=str(payload.get("category_code") or "").strip(),
            subcategory_code=str(payload.get("subcategory_code") or "").strip(),
            aliases=list(payload.get("aliases") or []),
            also_add_to_project=bool(payload.get("also_add_to_project", True)),
            actor=operator,
        )

    @app.get('/api/v1/templates')
    def get_templates(operator: ActorDependency):
        require(operator, Capability.VIEW)
        from .template_library import list_templates
        return {"templates": list_templates()}

    @app.get('/api/v1/templates/guide')
    def get_templates_guide(operator: ActorDependency):
        require(operator, Capability.VIEW)
        guide_path = Path(__file__).resolve().parents[2] / "docs" / "标准模板最小改动填表说明.md"
        content = guide_path.read_text(encoding="utf-8") if guide_path.is_file() else ""
        return {"filename": "标准模板最小改动填表说明.md", "markdown": content}

    @app.get('/api/v1/templates/{template_id}/download')
    def download_template(template_id: str, operator: ActorDependency):
        require(operator, Capability.VIEW)
        from urllib.parse import quote
        from .template_library import render_template
        try:
            filename, media_type, content = render_template(template_id)
        except KeyError as exc:
            raise WorkflowError("template_missing", "找不到所选标准模板。", 404) from exc
        return Response(
            content,
            media_type=media_type,
            headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename, safe="")},
        )

    @app.get('/api/v1/previews/{preview_id}/cleaning-audit')
    def preview_cleaning_audit(preview_id: str, operator: ActorDependency, section: str | None = None):
        require(operator, Capability.VIEW)
        from .cleaning import summarize_cleaning_audit
        from .models import RawObservation
        with store.read() as conn:
            workflow._upload(conn, preview_id)
            rows = conn.execute("SELECT raw_json FROM workflow_candidates WHERE upload_id=?", (preview_id,)).fetchall()
        raws = []
        for row in rows:
            obs = RawObservation.model_validate_json(row["raw_json"])
            if section and obs.location.section != section:
                continue
            raws.append(obs)
        return summarize_cleaning_audit(raws)

    @app.get('/api/v1/deliveries')
    def deliveries(operator: ActorDependency, limit: Annotated[int, Query(ge=1, le=200)] = 50):
        require(operator, Capability.EXPORT)
        with store.read() as conn:
            rows = conn.execute(
                "SELECT id, project, created_at, path, "
                "json_extract(snapshot_json, '$.attachment.filename') AS attachment_filename, "
                "json_extract(snapshot_json, '$.stability_document.version') AS report_version, "
                "json_extract(snapshot_json, '$.stability_document.change_history') AS report_changes "
                'FROM workflow_exports ORDER BY created_at DESC, id DESC LIMIT ?',
                (limit,),
            ).fetchall()
        return [{'id': row['id'], 'project': row['project'], 'created_at': row['created_at'],
                 'filename': Path(row['path']).name, 'attachment_filename': row['attachment_filename'],
                 'report_version': row['report_version'], 'report_changes': row['report_changes']} for row in rows]

    @app.get('/api/v1/deliveries/{export_id}/compare')
    def compare_delivery(export_id: str, baseline: str, operator: ActorDependency):
        require(operator, Capability.EXPORT)
        from .study_reports import compare_result_snapshots
        with store.read() as conn:
            rows = {row['id']: row for row in conn.execute(
                'SELECT id, project, path, snapshot_json FROM workflow_exports WHERE id IN (?, ?)',
                (export_id, baseline),
            )}
        if len(rows) != 2 or any(Path(row['path']).name != 'report.zip' for row in rows.values()):
            raise WorkflowError('report_comparison_missing', '请选择两个不同的 Word 报告版本。')
        if rows[export_id]['project'] != rows[baseline]['project']:
            raise WorkflowError('report_comparison_scope', '请选择同一项目的报告。')
        return compare_result_snapshots(json.loads(rows[baseline]['snapshot_json']),
                                        json.loads(rows[export_id]['snapshot_json']))

    if workbench:
        app.mount('/workbench', StaticFiles(directory=workbench, html=True), name='workbench')

    if frontend or workbench:
        @app.get("/assets/worksheet/{filename}", include_in_schema=False)
        def worksheet_asset(filename: str):
            directory = code_root / "frontend" / "v3" / "univer" / "dist"
            if filename not in {"editor.js", "editor.css"} or not (directory / filename).is_file():
                raise WorkflowError("worksheet_editor_unavailable", "研究工作表组件尚未构建。", 404)
            return FileResponse(directory / filename)

    if frontend:
        frontend_path = Path(frontend).resolve()

        @app.get("/legacy" if workbench else "/", include_in_schema=False)
        def index():
            return FileResponse(frontend_path, media_type="text/html")

    if workbench:
        @app.get('/', include_in_schema=False)
        def workbench_home():
            return RedirectResponse('/workbench/')
    return app
