import pytest

from stability_hub.bulk_drafts import preview as bulk_preview,apply
from stability_hub.structural_review import restore
from stability_hub.v5_contracts import BulkPreview,BulkApply,DraftDecision
from stability_hub.result_versions import ResultVersions
from stability_hub.workflow_common import WorkflowError
from stability_hub.workflow_contracts import CandidateEdit,WithdrawRequest
from test_v5_regressions import TECH,preview


def test_a12_stale_bulk_never_saves_half_batch(tmp_path,xlsx,context):
    flow,p = preview(tmp_path,xlsx([['Purity','0M',99,'%'],['Purity','3M',98,'%']]),context)
    scope = BulkPreview(candidate_ids=tuple(c.id for c in p.candidates),fields={'method':'SEC'})
    with flow.store.connect() as conn:
        info = bulk_preview(flow,conn,p.preview_id,scope)
    changed = flow.edit(p.candidates[1].id,CandidateEdit(base_revision=p.base_revision,idempotency_key='other',reason='concurrent edit',
        candidate_revision=1,fields={'method':'UV'}),actor=TECH)
    with pytest.raises(WorkflowError,match='整批未执行'):
        apply(flow,p.preview_id,BulkApply(base_revision=changed.base_revision,idempotency_key='stale',reason='batch edit',
            fingerprint=info['fingerprint'],scope=scope),actor=TECH)
    after = flow.preview(p.preview_id,actor=TECH)
    assert after.candidates[0].corrections == {}
    assert after.candidates[1].corrections == {'method':'UV'}


def test_a13_exclusion_can_be_restored_after_reopen(tmp_path,xlsx,context):
    flow,p = preview(tmp_path,xlsx([['Purity','0M',99,'%']]),context)
    c = p.candidates[0]
    out = ResultVersions(flow).withdraw_candidate(c.id,WithdrawRequest(base_revision=p.base_revision,
        idempotency_key='exclude',reason='wrong exclusion'),actor=TECH)
    after = flow.preview(p.preview_id,actor=TECH).candidates[0]
    assert after.lifecycle == 'withdrawn'
    restore(flow,p.preview_id,DraftDecision(base_revision=out.base_revision,idempotency_key='restore',reason='undo exclusion',
        action='restore',candidate_id=c.id,candidate_revision=after.candidate_revision),actor=TECH)
    assert flow.preview(p.preview_id,actor=TECH).candidates[0].lifecycle == 'draft'
