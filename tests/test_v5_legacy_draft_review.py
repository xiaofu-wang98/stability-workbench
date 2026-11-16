from openpyxl import load_workbook
from openpyxl.comments import Comment

from stability_hub.permissions import Role,actor_for_role
from stability_hub.review_issues import resolve_issue
from stability_hub.v5_contracts import IssueDecision
from stability_hub.workflow_contracts import ExportRequest
from stability_hub.workflow_excel import ExcelExports
from test_v5_regressions import TECH,preview,confirm_initial


def test_migrated_v4_draft_can_complete_scoped_formula_and_comment_review(tmp_path,xlsx,context):
    source=xlsx([['Legacy draft','0M','=99','%']])
    book=load_workbook(source)
    book.active['C2'].comment=Comment('Synthetic legacy source correction requires review','test')
    book.save(source)
    flow,p=preview(tmp_path,source,context)
    # Schema-5/7 upload rows receive marker 4 during migration. No facts are rewritten.
    with flow.store.connect() as conn:
        conn.execute('UPDATE workflow_uploads SET workflow_version=4 WHERE id=?',(p.preview_id,))
    p=flow.preview(p.preview_id,actor=TECH)
    assert p.contract_revision=='5'
    for code,action,fields in [('source_comment','keep_body',{}),('formula_result','manual',{'result':'98.5'})]:
        c=p.candidates[0]
        issue=next(i for i in c.issues if i.code==code)
        resolve_issue(flow,p.preview_id,IssueDecision(base_revision=p.base_revision,idempotency_key=code,
            reason='Synthetic explicit legacy draft decision',candidate_id=c.id,candidate_revision=c.candidate_revision,
            issue_id=issue.id,action=action,fields=fields),actor=TECH)
        p=flow.preview(p.preview_id,actor=TECH)
        if code=='source_comment':
            assert any(i.code=='formula_result' for i in p.candidates[0].issues)
    assert p.candidates[0].record.result.value is not None
    assert str(p.candidates[0].record.result.value)=='98.5' and not p.candidates[0].issues
    r=confirm_initial(flow,p)
    lead=actor_for_role('lead','Lead',Role.PROJECT_LEAD)
    out=ExcelExports(flow).create(ExportRequest(project='PROJECT-P',base_revision=r.base_revision),actor=lead)
    assert out.state=='verified'
