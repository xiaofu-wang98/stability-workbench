from uuid import uuid4

from stability_hub.models import ProjectConfig, RecordKey, digest
from stability_hub.permissions import Role, actor_for_role
from stability_hub.result_versions import ResultVersions
from stability_hub.storage import Store
from stability_hub.workflow import Workflow
from stability_hub.workflow_contracts import ConfirmItem, ConfirmRequest, ExportRequest
from stability_hub.workflow_excel import ExcelExports


def test_source_facts_need_no_dictionary_and_repeat_is_not_initial(tmp_path, xlsx, context):
    flow = Workflow(Store(tmp_path / 'facts'))
    flow.configure(ProjectConfig(project='PROJECT-P'))
    tech = actor_for_role('tech', 'tech', Role.TECHNICIAN)
    source = xlsx([['New assay', '0M', '1.2', '%']])
    preview = flow.upload('PROJECT-P', source.name, source.read_bytes(), context, uuid4().hex, actor=tech)
    c = preview.candidates[0]
    assert c.status == 'new' and c.record.key.method == '未注明'
    from test_v5_regressions import confirm_initial
    confirm_initial(flow,preview)
    again = flow.upload('PROJECT-P', source.name, source.read_bytes(), context, uuid4().hex, actor=tech)
    assert again.candidates[0].status == 'same_value'
    assert 'confirm_initial' not in again.candidates[0].allowed_actions
    assert flow.configuration('PROJECT-P', actor=tech)['config']['items'] == []
    lead = actor_for_role('lead', 'lead', Role.PROJECT_LEAD)
    exported = ExcelExports(flow).create(ExportRequest(project='PROJECT-P',
        base_revision=flow.results('PROJECT-P', actor=tech).base_revision), actor=lead)
    assert exported.state == 'verified'


def test_day_identity_and_legacy_month_hash_are_distinct():
    fields = dict(project='P', sample_type='placebo', batch='B', presentation='未注明',
                  study='long_term', study_id='S', condition='5C', item='pH', method='未注明')
    month = RecordKey(**fields, month=0)
    day = RecordKey(**fields, day=10)
    old = month.model_dump(mode='json')
    old.pop('day')
    old.pop('purpose')
    old.pop('reporting_basis')
    assert month.slot_id == digest(old)
    assert month.slot_id != day.slot_id
    assert day.time_label == '10D'


def test_receipt_keeps_conflicts_visible_without_adoption(tmp_path, xlsx, context):
    from stability_hub.ledger_review import LedgerReview
    from stability_hub.workflow_contracts import ReceiveRequest, CandidateEdit
    flow = Workflow(Store(tmp_path / 'receipt'))
    flow.configure(ProjectConfig(project='PROJECT-P'))
    tech = actor_for_role('tech', 'tech', Role.TECHNICIAN)
    path = xlsx([['Assay', '0M', '1', '%'], ['Assay', '0M', '2', '%'], ['Other', '0M', '3', '%']])
    preview = flow.upload('PROJECT-P', path.name, path.read_bytes(), context, 'upload', actor=tech)
    service = LedgerReview(flow)
    command = ReceiveRequest(base_revision=preview.base_revision,idempotency_key='receive',
                             candidate_ids=tuple(c.id for c in preview.candidates))
    result = service.receive(preview.preview_id, command, actor=tech)
    assert service.receive(preview.preview_id, command, actor=tech) == result
    # B02 supersedes V4 partial normal-row adoption: whole sheet stays draft.
    assert len(result.result_version_ids) == 0
    assert flow.results('PROJECT-P', actor=tech).total == 0
    matrix = service.matrix(preview.preview_id, actor=tech)
    assert len(matrix['candidate_ids']) == 3 and matrix['sheets']
    assert len(service.uploads('PROJECT-P', actor=tech)) == 1
    first = flow.preview(preview.preview_id, actor=tech).candidates[0]
    flow.edit(first.id, CandidateEdit(base_revision=result.base_revision,
        candidate_revision=first.candidate_revision,idempotency_key='resolve',reason='different subresult',
        fields={'test_item':'Assay child A'}), actor=tech)
    updated = flow.preview(preview.preview_id, actor=tech)
    ready = [c for c in updated.candidates if c.lifecycle=='draft']
    assert all(c.status=='new' for c in ready)
    from stability_hub.workflow_contracts import SheetConfirmRequest
    service.confirm_sheet(preview.preview_id,SheetConfirmRequest(base_revision=updated.base_revision,
        idempotency_key='confirm-resolved',section='Results'),actor=tech)
    assert flow.results('PROJECT-P', actor=tech).total == 3
    assert all(c['lifecycle']=='submitted' for c in service.matrix(preview.preview_id,actor=tech)['preview']['candidates'])


def test_incomplete_attribution_receipt_is_draft_only(tmp_path, xlsx, context):
    import pytest
    from stability_hub.ledger_review import LedgerReview
    from stability_hub.workflow_contracts import ReceiveRequest
    flow = Workflow(Store(tmp_path / 'blocked'))
    flow.configure(ProjectConfig(project='PROJECT-P'))
    tech = actor_for_role('tech', 'tech', Role.TECHNICIAN)
    path = xlsx([['Assay', '0M', '1', '%'], ['Other', 'unknown', '2', '%']])
    preview = flow.upload('PROJECT-P', path.name, path.read_bytes(), context, 'upload', actor=tech)
    service = LedgerReview(flow)
    assert preview.candidates[0].can_receive and not preview.candidates[1].can_receive
    result = service.receive(preview.preview_id, ReceiveRequest(base_revision=preview.base_revision,
        idempotency_key='all', candidate_ids=tuple(c.id for c in preview.candidates)),actor=tech)
    assert not result.result_version_ids
    assert len(service.uploads('PROJECT-P',actor=tech))==1
    matrix = service.matrix(preview.preview_id,actor=tech)
    assert matrix['sheet_progress'][0]['state']=='needs_review'
    assert flow.results('PROJECT-P',actor=tech).total == 0
    with pytest.raises(PermissionError):
        service.receive(preview.preview_id, ReceiveRequest(base_revision=preview.base_revision,
            idempotency_key='view-only', candidate_ids=(preview.candidates[0].id,)),
            actor=actor_for_role('lead','lead',Role.PROJECT_LEAD))


def test_legacy_confirmed_upload_is_visible_without_reimport_or_database_write(tmp_path, xlsx, context):
    from stability_hub.ledger_review import LedgerReview
    flow = Workflow(Store(tmp_path / 'legacy-ledger'))
    flow.configure(ProjectConfig(project='PROJECT-P'))
    tech = actor_for_role('tech','tech',Role.TECHNICIAN)
    path = xlsx([['Assay','0M','1','%'],['Assay','0M','2','%'],['Other','0M','3','%']])
    preview = flow.upload('PROJECT-P',path.name,path.read_bytes(),context,'legacy-upload',actor=tech,_legacy_compatibility=True)
    c = preview.candidates[2]
    ResultVersions(flow).confirm(preview.preview_id,ConfirmRequest(base_revision=preview.base_revision,
        idempotency_key='legacy-confirm',items=(ConfirmItem(candidate_id=c.id,candidate_revision=c.candidate_revision,
        detection_id=c.detection_id,identity_confirmed=True,intent='initial',reason='previous version confirmation'),)),actor=tech)
    review = LedgerReview(flow)
    assert len(review.uploads('PROJECT-P',actor=tech)) == 1
    flow.configure(ProjectConfig(project='OTHER'))
    assert review.uploads('OTHER',actor=tech) == []
    before = flow.results('PROJECT-P',actor=tech)
    matrix = review.matrix(preview.preview_id,actor=tech)
    assert len(matrix['candidate_ids']) == 3
    assert flow.results('PROJECT-P',actor=tech) == before
    with flow.store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM workflow_events WHERE action='receive_source'").fetchone()[0] == 0
