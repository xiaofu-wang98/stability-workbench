"""Thin HTTP adapter. IT can replace get_actor without touching business services."""

import json
import re
import hashlib
import secrets
from tempfile import TemporaryDirectory
from pathlib import Path
from typing import Annotated
from zipfile import BadZipFile

from fastapi import Depends, FastAPI, File, Form, Header, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import ValidationError
from lxml.etree import XMLSyntaxError

from .source_tables import SourceTables
from .source_contracts import ArchiveInfo, ArchiveView, ArchiveTablePage, SourcePackage, SourcePreviewRequest
from .models import ImportContext
from .parsing import parse_source
from .permissions import Actor, Capability, require
from .result_versions import ResultVersions
from .workflow import Workflow
from .business_worksheets import BusinessWorksheets, CreateWorksheet, ExtendPlan, LeaseToken, SaveWorksheet
from .workflow_common import WorkflowError
from .workflow_contracts import (
    ConfigurationChange, ConfigurationView, CandidateEdit, CandidateMapping, ConfirmRequest, ExportRequest, ExportResult, MutationResult, ResultFilter,
    ProjectCreation, NewItems, SharedEdit, ResultChange,
    PreviewSummary, ResultPage, ResultQuery, ReviewRequest, UploadPreview, WithdrawRequest,
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


def create_app(store, configs, actor: Actor, *, api_token=None, frontend=None, origins=()):
    from . import __version__
    app = FastAPI(title="Stability Data Hub", version=__version__)
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
    worksheets = BusinessWorksheets(workflow, versions)
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

    @app.get("/api/v1/projects/{project}/worksheets")
    def list_worksheets(project: str, operator: ActorDependency):
        return worksheets.list(project, operator)

    @app.post("/api/v1/projects/{project}/worksheets", status_code=201)
    def create_worksheet(project: str, command: CreateWorksheet, operator: ActorDependency):
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

    @app.post("/api/v1/worksheets/{sheet_id}/plan")
    def extend_worksheet(sheet_id: str, command: ExtendPlan, operator: ActorDependency):
        return worksheets.extend(sheet_id, command, operator)

    @app.post("/api/v1/worksheets/{sheet_id}/confirmation")
    def export_worksheet_confirmation(
        sheet_id: str, operator: ActorDependency, revision: Annotated[int, Query(ge=1)],
        selection_id: Annotated[str, Query(min_length=64, max_length=64)],
    ):
        from .worksheet_confirmation import create_confirmation
        return create_confirmation(worksheets, sheet_id, revision, selection_id, operator)

    @app.get('/api/v1/previews/{preview_id}/file')
    def upload_original(preview_id: str,operator: ActorDependency):
        path,filename = versions.original_upload(preview_id,actor=operator)
        return FileResponse(path,filename=filename)

    @app.get('/api/v1/projects/{project}/v5/rules')
    def project_rules(project: str,operator: ActorDependency):
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
    def identity_association_preview(project: str, command: IdentityAssociationScope, operator: ActorDependency):
        require(operator,Capability.VIEW)
        with store.connect() as conn:
            return preview_association(conn,project,command)

    @app.post('/api/v1/projects/{project}/v5/identity-associations/apply',response_model=MutationResult)
    def identity_association_apply(project: str, command: IdentityAssociationApply, operator: ActorDependency):
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
    def retire_project_rule(project: str,command: RuleRetirement,operator: ActorDependency):
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
    def project_rule_preview(project: str, command: ProjectRule, operator: ActorDependency):
        require(operator,Capability.VIEW)
        with store.connect() as conn:
            return rule_impact(conn,project,command)

    @app.post('/api/v1/projects/{project}/v5/rules', response_model=MutationResult)
    def project_rule_save(project: str, command: ProjectRule, operator: ActorDependency):
        return save_rule(workflow,project,command,actor=operator)
    from .v5_contracts import IdentityLink
    from .identity_links import link_preview, apply_link

    @app.get('/api/v1/projects/{project}/v5/identity-link')
    def identity_link_preview(project: str, item_id: str, historical_item: str, operator: ActorDependency):
        require(operator, Capability.VIEW)
        with store.connect() as conn:
            return link_preview(conn,project,item_id,historical_item)

    @app.post('/api/v1/projects/{project}/v5/identity-link', response_model=MutationResult)
    def identity_link(project: str, command: IdentityLink, operator: ActorDependency):
        return apply_link(workflow,project,command,actor=operator)

    @app.post('/api/v1/projects/{project}/source-imports', response_model=ArchiveInfo)
    def archive_source(project: str, operator: ActorDependency, file: Annotated[UploadFile, File()],
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
    def archives(project: str, operator: ActorDependency):
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
    def ledger_sources(project: str, operator: ActorDependency):
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
            return [{'project': row['project'], 'revision': row['revision'],
                     'name': json.loads(row['config_json']).get('name', '')} for row in
                    conn.execute("SELECT project,revision,config_json FROM workflow_projects ORDER BY project")]

    @app.post('/api/v1/projects', response_model=ConfigurationView)
    def create_project(command: ProjectCreation, operator: ActorDependency):
        return workflow.create_project(command, actor=operator)

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
    def add_items(project: str, command: NewItems, operator: ActorDependency):
        return workflow.add_items(project, command, actor=operator)

    from .v5_contracts import ItemDisplayName
    from .item_identity import rename as rename_item

    @app.post('/api/v1/projects/{project}/items/{item_id}/display-name',response_model=MutationResult)
    def rename_item_display(project: str, item_id: str, command: ItemDisplayName, operator: ActorDependency):
        return rename_item(workflow,project,item_id,command,actor=operator)

    @app.get("/api/v1/projects/{project}/configuration", response_model=ConfigurationView)
    def configuration(project: str, operator: ActorDependency):
        return workflow.configuration(project, actor=operator)

    @app.post("/api/v1/projects/{project}/configuration", response_model=MutationResult)
    def change_configuration(project: str, command: ConfigurationChange, operator: ActorDependency):
        return workflow.change_configuration(project, command, actor=operator)

    @app.get("/api/v1/projects/{project}/previews", response_model=list[PreviewSummary])
    def previews(project: str, operator: ActorDependency, offset: int = Query(0, ge=0),
                 limit: int = Query(20, ge=1, le=100)):
        return workflow.list_previews(project, offset, limit, actor=operator)

    @app.post("/api/v1/projects/{project}/previews", response_model=UploadPreview)
    def upload(project: str, operator: ActorDependency, file: Annotated[UploadFile, File()],
               idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
               context: Annotated[str, Form()] = "{}"):
        require(operator, Capability.PREVIEW)
        try:
            ctx = ImportContext.model_validate_json(context)
            data = file.file.read(20 * 1024 * 1024 + 1)
            return workflow.upload(project, file.filename or "input", data, ctx, idempotency_key, actor=operator, deduplicate=True)
        except (BadZipFile, KeyError, ValidationError) as exc:
            raise WorkflowError("invalid_document", "文件结构或上传上下文不符合支持格式。") from exc
        finally:
            file.file.close()

    @app.get("/api/v1/previews/{preview_id}", response_model=UploadPreview)
    def preview(preview_id: str, operator: ActorDependency):
        return workflow.preview(preview_id, actor=operator)

    @app.patch("/api/v1/previews/{preview_id}/mapping", response_model=MutationResult)
    def map_items(preview_id: str, command: CandidateMapping, operator: ActorDependency):
        return workflow.map_items(preview_id, command, actor=operator)

    @app.patch('/api/v1/previews/{preview_id}/shared', response_model=MutationResult)
    def shared_edit(preview_id: str, command: SharedEdit, operator: ActorDependency):
        return workflow.edit_shared(preview_id, command, actor=operator)

    @app.patch("/api/v1/candidates/{candidate_id}", response_model=MutationResult)
    def edit(candidate_id: str, command: CandidateEdit, operator: ActorDependency):
        return workflow.edit(candidate_id, command, actor=operator)

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

    @app.get("/api/v1/projects/{project}/result-cells", response_model=ResultPage)
    def results(project: str, operator: ActorDependency, query: Annotated[ResultQuery, Query()]):
        filters = ResultFilter.model_validate(query.model_dump(exclude={'offset', 'limit'}))
        return workflow.results(project, filters, query.offset, query.limit, actor=operator)

    @app.get('/api/v1/projects/{project}/result-display')
    def result_display(project: str, operator: ActorDependency, filters: Annotated[ResultFilter, Query()]):
        require(operator, Capability.VIEW)
        from .result_display import display_results
        with store.read() as conn:
            return display_results(conn, project, filters)

    from .workflow_contracts import ResultTrendQuery

    @app.get('/api/v1/projects/{project}/result-trend')
    def result_trend(project: str, operator: ActorDependency, query: Annotated[ResultTrendQuery, Query()]):
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
    def history(project: str, operator: ActorDependency):
        return versions.history(project, actor=operator)

    @app.get("/api/v1/projects/{project}/detections")
    def detections(project: str, operator: ActorDependency):
        require(operator, Capability.VIEW)
        with store.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT id,source_number,created_at FROM workflow_detections WHERE project=? ORDER BY rowid", (project,))]

    @app.get("/api/v1/projects/{project}/pending-results")
    def pending(project: str, operator: ActorDependency):
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
        return exports.create(command, actor=operator)

    @app.get("/api/v1/exports/{export_id}/file")
    def download(export_id: str, operator: ActorDependency):
        path = exports.download(export_id, actor=operator)
        return FileResponse(path, filename=path.name,
                            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    @app.get('/api/v1/exports/{export_id}/image')
    def download_image(export_id: str,operator: ActorDependency):
        return FileResponse(exports.download_image(export_id,actor=operator),filename='trend.png',media_type='image/png')

    from .comparability import StudySave, StudyQuery, QuickCompare
    from .study_reports import ReportRequest

    @app.get('/api/v1/projects/{project}/comparison-catalog')
    def comparison_catalog(project: str, operator: ActorDependency):
        require(operator, Capability.VIEW)
        from .quick_comparison import catalog
        with store.read() as conn:
            return catalog(conn,project)

    @app.post('/api/v1/projects/{project}/comparison-preview')
    def comparison_preview(project: str, command: QuickCompare, operator: ActorDependency):
        require(operator, Capability.VIEW)
        from .quick_comparison import quick_compare
        with store.read() as conn:
            return quick_compare(conn,project,command)

    @app.get('/api/v1/projects/{project}/comparison-studies')
    def comparison_studies(project: str, operator: ActorDependency):
        require(operator, Capability.VIEW)
        from .comparability import studies, inventory
        from .workflow_common import project_row
        with store.read() as conn:
            return {'project':project,'base_revision':project_row(conn,project)['revision'],
                    'studies':studies(conn,project),'samples':inventory(conn,project)}

    @app.post('/api/v1/projects/{project}/comparison-studies/{study_id}')
    def save_comparison(project: str, study_id: str, command: StudySave, operator: ActorDependency):
        from .comparability import save
        return save(workflow,project,study_id,command,operator)

    @app.get('/api/v1/projects/{project}/comparison-studies/{study_id}/results')
    def comparison_results(project: str, study_id: str, operator: ActorDependency, query: Annotated[StudyQuery, Query()]):
        require(operator, Capability.VIEW)
        from .comparability import compare
        with store.read() as conn:
            return compare(conn,project,study_id,query)

    @app.post('/api/v1/reports')
    def report_export(command: ReportRequest, operator: ActorDependency):
        from .study_reports import create_report
        return create_report(workflow,command,operator)

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

    if frontend:
        frontend_path = Path(frontend).resolve()

        @app.get("/assets/worksheet/{filename}", include_in_schema=False)
        def worksheet_asset(filename: str):
            directory = code_root / "frontend" / "v3" / "univer" / "dist"
            if filename not in {"editor.js", "editor.css"} or not (directory / filename).is_file():
                raise WorkflowError("worksheet_editor_unavailable", "研究工作表组件尚未构建。", 404)
            return FileResponse(directory / filename)

        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(frontend_path, media_type="text/html")
    return app
