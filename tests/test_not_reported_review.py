"""Shared non-reporting statements are facts for every covered logical row."""
from io import BytesIO

import pytest
from openpyxl import Workbook

from stability_hub.ledger_review import LedgerReview
from stability_hub.models import ImportContext, ProjectConfig
from stability_hub.parsing import parse_source
from stability_hub.permissions import Role, actor_for_role
from stability_hub.storage import Store
from stability_hub.workflow import Workflow
from stability_hub.workflow_common import WorkflowError
from stability_hub.workflow_contracts import LedgerDecision, ReceiveRequest, SheetConfirmRequest, ExportRequest
from stability_hub.workflow_excel import ExcelExports
from test_xlsx_matrix import add_block


def workbook(value):
    book = Workbook()
    sheet = book.active
    add_block(sheet)
    for r in range(6, 9):
        for c in range(3, 7):
            sheet.cell(r, c).value = None
    sheet['B6'] = sheet['B7'] = '报告数据'
    sheet['B8'] = 'Group 3 报告数据'
    sheet['C6'] = value
    sheet.merge_cells('C6:C8')
    sheet['F10'] = '暂停检测，详见AR-C-P-2026003'
    out = BytesIO()
    book.save(out)
    return out.getvalue()


def test_shared_statement_expands_all_rows_and_exports_without_numeric_value(tmp_path):
    data = workbook('不进行汇报结果，详见：AR-C-P-2026003')
    path = tmp_path / 'source.xlsx'
    path.write_bytes(data)
    parsed = parse_source(path)
    assert len(parsed.observations) == 3
    assert parsed.coverage[0]['result_cells'] == 1
    assert parsed.coverage[0]['logical_results'] == 3
    assert [o.logical_row for o in parsed.observations] == [6, 7, 8]
    assert all(o.source_span == (6, 3, 8, 3) and not o.hazards for o in parsed.observations)
    flow = Workflow(Store(tmp_path / 'store'))
    flow.configure(ProjectConfig(project='P'))
    tech = actor_for_role('tech', '实验员', Role.TECHNICIAN)
    preview = flow.upload('P', path.name, data, ImportContext(), 'upload', actor=tech)
    # Name questions need an explicit identity, not a generic keep-body decision.
    # Each logical row still keeps the source's combined non-reporting statement.
    from stability_hub.review_issues import resolve_issue
    from stability_hub.v5_contracts import IssueDecision,RegionDecision
    from stability_hub.worksheet_regions import coverage,classify
    for c in preview.candidates:
        for issue in c.issues:
            current = flow.preview(preview.preview_id,actor=tech)
            row = next(x for x in current.candidates if x.id == c.id)
            naming = bool(issue.proposal.get('guidance'))
            resolve_issue(flow,preview.preview_id,IssueDecision(base_revision=current.base_revision,idempotency_key='review-'+issue.id,
                reason='synthetic human retains source combined non-reporting statement',candidate_id=c.id,
                candidate_revision=row.candidate_revision,issue_id=issue.id,action='manual' if naming else 'keep_body',
                fields={'test_item': f'Electrophoresis · fraction {row.logical_row}'} if naming else {}),actor=tech)
    preview = flow.preview(preview.preview_id,actor=tech)
    assert all(c.status == 'new' for c in preview.candidates)
    assert len({c.record.key.slot_id for c in preview.candidates}) == 3
    assert all(c.record.result.status == 'not_reported' and c.record.result.value is None
               and c.record.result.report_references == ('AR-C-P-2026003',) for c in preview.candidates)
    service = LedgerReview(flow)
    service.receive(preview.preview_id, ReceiveRequest(base_revision=preview.base_revision,
        idempotency_key='receive', candidate_ids=tuple(c.id for c in preview.candidates), defer_confirmation=True), actor=tech)
    matrix = service.matrix(preview.preview_id, actor=tech)
    assert matrix['sheet_progress'][0]['state'] == 'ready', 'This source pause was explicitly reviewed above; note classification must not demand a duplicate decision'
    assert not any(c.issues for c in flow.preview(preview.preview_id,actor=tech).candidates)
    assert any('暂停检测' in raw.comment for raw in parsed.observations), 'Pause must remain a source question before the explicit review above'
    assert any('暂停检测' in n['text'] for n in matrix['sheets'][0]['notes'])
    with flow.store.connect() as conn:
        regions = coverage(conn,flow._upload(conn,preview.preview_id))
    for sheet in regions:
        for region in sheet['regions']:
            if region['role'] == 'unclassified':
                current = flow.preview(preview.preview_id,actor=tech)
                classify(flow,preview.preview_id,RegionDecision(base_revision=current.base_revision,idempotency_key='note-'+region['id'],
                    reason='synthetic human classifies remaining source notes',region_id=region['id'],role='note'),actor=tech)
    confirmed = service.confirm_sheet(preview.preview_id, SheetConfirmRequest(base_revision=flow.preview(preview.preview_id,actor=tech).base_revision,
        idempotency_key='confirm', section='Sheet'), actor=tech)
    assert len(confirmed.result_version_ids) == 3 and not confirmed.pending_confirmation_ids
    assert service.matrix(preview.preview_id, actor=tech)['sheet_progress'][0]['state'] == 'confirmed'
    exported = ExcelExports(flow).create(ExportRequest(project='P', base_revision=confirmed.base_revision), actor=actor_for_role('lead', '负责人', Role.PROJECT_LEAD))
    assert exported.state == 'verified'


def test_numeric_vertical_merge_never_copied(tmp_path):
    path = tmp_path / 'numeric.xlsx'
    path.write_bytes(workbook(99))
    parsed = parse_source(path)
    assert len(parsed.observations) == 1
    assert parsed.observations[0].hazards and not parsed.observations[0].source_span


def test_batch_exclusion_atomic_and_receipt_progress(tmp_path, context):
    from test_sheet_revision_review import setup
    flow, service, tech, preview = setup(tmp_path, context)
    received = service.receive(preview.preview_id, ReceiveRequest(base_revision=preview.base_revision,
        idempotency_key='receive', candidate_ids=tuple(c.id for c in preview.candidates), defer_confirmation=True), actor=tech)
    ids = tuple(c.id for c in preview.candidates[:2])
    # An invalid member rolls back the entire selection.
    with pytest.raises(WorkflowError):
        service.decide(preview.preview_id, LedgerDecision(base_revision=received.base_revision,
            idempotency_key='invalid', candidate_ids=(*ids, 'missing'), action='exclude'), actor=tech)
    assert all(c.lifecycle == 'draft' for c in flow.preview(preview.preview_id, actor=tech).candidates)
    command = LedgerDecision(base_revision=received.base_revision, idempotency_key='exclude', candidate_ids=ids, action='exclude')
    result = service.decide(preview.preview_id, command, actor=tech)
    assert service.decide(preview.preview_id, command, actor=tech) == result
    assert [c.lifecycle for c in flow.preview(preview.preview_id, actor=tech).candidates[:2]] == ['withdrawn'] * 2
    last = preview.candidates[-1]
    result = service.decide(preview.preview_id, LedgerDecision(base_revision=result.base_revision,
        idempotency_key='review', candidate_ids=(last.id,), action='review'), actor=tech)
    assert flow.preview(preview.preview_id, actor=tech).candidates[-1].reviewed
    assert flow.results('PROJECT-P', actor=tech).total == 0, 'Reviewing is not adoption'
    result = service.confirm_sheet(preview.preview_id, SheetConfirmRequest(base_revision=result.base_revision,
        idempotency_key='sheet', section='Second'), actor=tech)
    with pytest.raises(WorkflowError, match='只能处理'):
        service.decide(preview.preview_id, LedgerDecision(base_revision=result.base_revision,
            idempotency_key='no-rewrite', candidate_ids=(last.id,), action='exclude'), actor=tech)
    progress = service.matrix(preview.preview_id, actor=tech)['sheet_progress']
    assert [p['state'] for p in progress] == ['needs_review', 'confirmed']
