from io import BytesIO

from openpyxl import Workbook

from stability_hub.models import ProjectConfig
from stability_hub.storage import Store
from stability_hub.workflow import Workflow
from stability_hub.worksheet_regions import coverage,classify
from stability_hub.v5_contracts import RegionDecision
from test_v5_regressions import TECH


def test_a11_zero_candidate_hidden_sheet_and_sparse_unknown(tmp_path,context):
    book = Workbook()
    book.active.title = 'Instructions'
    book.active['A1'] = 'unrecognized material needs classification'
    hidden = book.create_sheet('Hidden measurements')
    hidden.sheet_state = 'hidden'
    hidden['C13'] = 123
    hidden.cell(10000,10000).number_format = '0.00'
    data = BytesIO()
    book.save(data)
    flow = Workflow(Store(tmp_path / 'isolated'))
    flow.configure(ProjectConfig(project='PROJECT-P'))
    p = flow.upload('PROJECT-P','unknown.xlsx',data.getvalue(),context,'upload',actor=TECH)
    assert not p.candidates
    with flow.store.connect() as conn:
        sheets = coverage(conn,flow._upload(conn,p.preview_id))
    assert [s['section'] for s in sheets] == ['Instructions','Hidden measurements']
    assert sheets[1]['hidden'] and sheets[1]['physical_nonempty'] == 1
    region = sheets[1]['regions'][0]
    assert region['role'] == 'unclassified' and region['coordinates'] == ['C13']
    classify(flow,p.preview_id,RegionDecision(base_revision=p.base_revision,idempotency_key='note',
        reason='human identifies auxiliary material',region_id=region['id'],role='auxiliary'),actor=TECH)
    with flow.store.connect() as conn:
        updated = coverage(conn,flow._upload(conn,p.preview_id))
    assert updated[1]['regions'][0]['role'] == 'auxiliary'


def test_a11_partial_bad_matrix_does_not_reject_other_sheet(tmp_path,context):
    book = Workbook()
    book.active.title = 'Bad matrix'
    book.active.append(['检验项目','可接受标准','考察时间（月）'])
    book.active.append(['','','unrecognized time'])
    book.active.append(['Purity','>95%',99])
    second = book.create_sheet('Good')
    second.append(['Test item','Time point','Result','Unit'])
    second.append(['Purity','0M',98,'%'])
    data = BytesIO()
    book.save(data)
    flow = Workflow(Store(tmp_path/'partial'))
    flow.configure(ProjectConfig(project='PROJECT-P'))
    p = flow.upload('PROJECT-P','mixed.xlsx',data.getvalue(),context,'upload',actor=TECH)
    assert len(p.candidates)==1 and p.candidates[0].location.section=='Good'
    bad = next(s for s in p.parsing_summary if s['section']=='Bad matrix')
    assert bad['adapter']=='unrecognized'
    assert any(r['role']=='unclassified' for r in bad['regions'])


def test_a11_native_drawing_line_is_visible_without_claiming_font_revision(tmp_path):
    from zipfile import ZipFile
    from lxml import etree
    from stability_hub.worksheet_regions import unsupported_drawings
    book = Workbook()
    book.active.title = 'Native line'
    book.active['A1'] = 'Source with a drawn line'
    data = BytesIO()
    book.save(data)
    with ZipFile(BytesIO(data.getvalue())) as original:
        parts = {name:original.read(name) for name in original.namelist()}
    ns = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    sheet = etree.fromstring(parts['xl/worksheets/sheet1.xml'])
    drawing = etree.SubElement(sheet,'{http://schemas.openxmlformats.org/spreadsheetml/2006/main}drawing')
    drawing.set('{'+ns+'}id','rIdShape')
    parts['xl/worksheets/sheet1.xml'] = etree.tostring(sheet)
    parts['xl/worksheets/_rels/sheet1.xml.rels'] = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rIdShape" Type="'+ns+'/drawing" Target="../drawings/drawing1.xml"/></Relationships>').encode()
    parts['xl/drawings/drawing1.xml'] = b'<wsDr xmlns="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"><twoCellAnchor><sp/></twoCellAnchor></wsDr>'
    path = tmp_path/'native-line.xlsx'
    with ZipFile(path,'w') as target:
        for name,payload in parts.items():
            target.writestr(name,payload)
    region = unsupported_drawings(path)['Native line'][0]
    assert region['role']=='unclassified'
    assert region['coordinates']==['drawing:xl/drawings/drawing1.xml']
    assert 'not font strikethrough' in region['basis']
