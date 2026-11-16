from fastapi.testclient import TestClient

from stability_hub.api import create_app
from stability_hub.ledger_review import LedgerReview
from stability_hub.v5_contracts import DraftDecision
from stability_hub.workflow_contracts import CandidateEdit,ReceiveRequest
from stability_hub.worksheet_commit import save_decision
from test_v5_regressions import TECH,preview


def test_draft_state_never_pairs_old_decisions_with_new_revision(tmp_path,xlsx,context,monkeypatch):
    flow,p=preview(tmp_path,xlsx([['Assay','0M',99,'%']]),context)
    c=p.candidates[0]
    app=create_app(flow.store,[],TECH)
    import stability_hub.worksheet_commit as commands
    original=commands.decisions
    def concurrent_write(conn,upload_id):
        result=original(conn,upload_id)
        save_decision(flow,p.preview_id,DraftDecision(base_revision=p.base_revision,idempotency_key='concurrent',
            reason='Synthetic concurrent choice',candidate_id=c.id,candidate_revision=c.candidate_revision,action='exclude'),actor=TECH)
        return result
    monkeypatch.setattr(commands,'decisions',concurrent_write)
    client=TestClient(app)
    state=client.get(f'/api/v1/previews/{p.preview_id}/v5/draft-state').json()
    assert state=={'decisions':{},'base_revision':p.base_revision}
    later=client.get(f'/api/v1/previews/{p.preview_id}/v5/draft-state').json()
    assert later['base_revision']==p.base_revision+1 and later['decisions'][c.id]['action']=='exclude'


def test_source_matrix_never_pairs_old_value_with_new_revision(tmp_path,xlsx,context,monkeypatch):
    flow,p=preview(tmp_path,xlsx([['Assay','0M',99,'%']]),context)
    c=p.candidates[0]
    received=LedgerReview(flow).receive(p.preview_id,ReceiveRequest(base_revision=p.base_revision,idempotency_key='receive',
        candidate_ids=(c.id,),defer_confirmation=True),actor=TECH)
    app=create_app(flow.store,[],TECH)
    original=app.state.workflow._mapped
    def concurrent_write(conn,candidate,upload,**kwargs):
        flow.edit(c.id,CandidateEdit(base_revision=received.base_revision,idempotency_key='concurrent-edit',
            reason='Synthetic concurrent value correction',candidate_revision=c.candidate_revision,fields={'result':'97'}),actor=TECH)
        return original(conn,candidate,upload,**kwargs)
    monkeypatch.setattr(app.state.workflow,'_mapped',concurrent_write)
    client=TestClient(app)
    data=client.get(f'/api/v1/previews/{p.preview_id}/ledger-matrix').json()['preview']
    assert data['candidates'][0]['record']['result']['value']=='99'
    assert data['base_revision']==received.base_revision
    later=client.get(f'/api/v1/previews/{p.preview_id}/ledger-matrix').json()['preview']
    assert later['base_revision']==received.base_revision+1
    assert later['candidates'][0]['record']['result']['value']=='97'
