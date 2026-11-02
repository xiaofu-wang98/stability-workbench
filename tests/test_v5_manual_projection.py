from io import BytesIO

from openpyxl import Workbook

from stability_hub.models import ProjectConfig
from stability_hub.permissions import Role,actor_for_role
from stability_hub.storage import Store
from stability_hub.structural_review import resolve_structure
from stability_hub.v5_contracts import StructuralDecision,RegionDecision
from stability_hub.workflow import Workflow
from stability_hub.workflow_contracts import ExportRequest
from stability_hub.workflow_excel import ExcelExports
from stability_hub.worksheet_regions import coverage,classify
from test_v5_regressions import TECH,confirm_initial


def test_a05_a06_manual_unknown_region_can_commit_and_export(tmp_path,context):
    book = Workbook()
    book.active.title = 'Odd layout'
    book.active['A1'] = 'Unrecognized assay note'
    book.active['C13'] = 'old notation 98.5'
    data = BytesIO()
    book.save(data)
    flow = Workflow(Store(tmp_path/'manual-source'))
    flow.configure(ProjectConfig(project='PROJECT-P'))
    p = flow.upload('PROJECT-P','unknown.xlsx',data.getvalue(),context,'unknown',actor=TECH)
    assert not p.candidates
    request = StructuralDecision(base_revision=p.base_revision,idempotency_key='manual',reason='synthetic human reads the bound original value',
        section='Odd layout',coordinate='C13',results=({'test_item':'Odd assay','method':'UV','time_point':'0M','result':'98.5','unit':'%'},))
    resolve_structure(flow,p.preview_id,request,actor=TECH)
    p = flow.preview(p.preview_id,actor=TECH)
    assert p.candidates[0].record.result.value is not None
    with flow.store.connect() as conn:
        sheets = coverage(conn,flow._upload(conn,p.preview_id))
    roles = {c:r['role'] for r in sheets[0]['regions'] for c in r['coordinates']}
    assert roles['C13'] == 'result' and roles['A1'] == 'unclassified'
    for region in sheets[0]['regions']:
        if region['role'] == 'unclassified':
            current = flow.preview(p.preview_id,actor=TECH)
            classify(flow,p.preview_id,RegionDecision(base_revision=current.base_revision,idempotency_key='note',
                reason='synthetic note classification',region_id=region['id'],role='note'),actor=TECH)
    confirm_initial(flow,flow.preview(p.preview_id,actor=TECH))
    out = ExcelExports(flow).create(ExportRequest(project='PROJECT-P',base_revision=flow.results('PROJECT-P',actor=TECH).base_revision),
        actor=actor_for_role('lead','Lead',Role.PROJECT_LEAD))
    assert out.state == 'verified'
