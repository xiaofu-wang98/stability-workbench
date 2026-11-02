from io import BytesIO
from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference

from stability_hub.models import ImportContext, ProjectConfig
from stability_hub.storage import Store
from stability_hub.workflow import Workflow
from stability_hub.worksheet_regions import coverage, classify
from stability_hub.v5_contracts import RegionDecision
from stability_hub.review_tasks import current_review
from stability_hub.permissions import Role, actor_for_role

TECH = actor_for_role('tech', 'technician', Role.TECHNICIAN)


def test_tail_labels_clustered_into_single_unclassified_block(tmp_path):
    book = Workbook()
    s = book.active
    s.title = "DP长期"
    for coord, value in {
        "A1": "DP长期稳定性实验数据（5℃）",
        "A2": "B1",
        "A3": "检测项目",
        "B3": "质量标准",
        "C3": "考察时间（月）",
        "C4": 0,
        "D4": 3,
        "A5": "纯度",
        "B5": "≥95%",
        "C5": 99,
        "D5": 98,
        "A6": "pH",
        "B6": "报告结果",
        "C6": 5.5,
        "D6": 5.4,
    }.items():
        s[coord] = value
    s.merge_cells("C3:D3")

    # Row 7: chart axis
    s["C7"] = 0
    s["D7"] = 3

    # Rows 8-25: 18 tail labels in column B (B8..B25)
    for r in range(8, 26):
        s[f"B{r}"] = f"Chart-Label-{r}"

    path = tmp_path / "tail_labels.xlsx"
    book.save(path)

    store = Store(tmp_path / 'store')
    flow = Workflow(store)
    flow.configure(ProjectConfig(project='P1'))
    ctx = ImportContext(role='stability_source', defaults={'sample_type': 'DP', 'batch': 'B1', 'presentation': 'pen', 'study': 'long_term', 'condition': '5C'})
    p = flow.upload('P1', 'tail_labels.xlsx', path.read_bytes(), ctx, 'k1', actor=TECH)

    with store.connect() as conn:
        sheets = coverage(conn, flow._upload(conn, p.preview_id))

    test_sheet = next(s for s in sheets if s['section'] == 'DP长期')
    unclassified = [r for r in test_sheet['regions'] if r['role'] == 'unclassified']

    # The 18 labels in rows 8-25 must NOT be split into 18 regions; they must be clustered into 1 block!
    tail_block = next((r for r in unclassified if len(r['coordinates']) == 18), None)
    assert tail_block is not None
    assert tail_block['coordinates'][0] == 'B8'
    assert tail_block['coordinates'][-1] == 'B25'

    # Now verify review task: there should be ONE task for this block, not 18!
    with store.connect() as conn:
        _, _, _, tasks, _ = current_review(flow, conn, flow._upload(conn, p.preview_id), TECH)

    block_tasks = [t for t in tasks if t.code == 'region' and any(m.get('region_id') == tail_block['id'] for m in t.members)]
    assert len(block_tasks) == 1
    assert len(block_tasks[0].evidence) == 18

    # Classifying this 1 region as auxiliary resolves the entire 18-label block
    classify(flow, p.preview_id, RegionDecision(
        base_revision=p.base_revision,
        idempotency_key='cls1',
        reason='确认18个标签均为绘图辅助说明',
        region_id=tail_block['id'],
        role='auxiliary',
    ), actor=TECH)

    with store.connect() as conn:
        updated_sheets = coverage(conn, flow._upload(conn, p.preview_id))
    updated_region = next(r for r in updated_sheets[0]['regions'] if r['id'] == tail_block['id'])
    assert updated_region['role'] == 'auxiliary'


def test_chart_referenced_results_remain_results(tmp_path):
    book = Workbook()
    sheet = book.active
    sheet.title = 'ChartResults'
    sheet.append(['Test item', 'Time point', 'Result', 'Unit'])
    sheet.append(['Purity', '0M', 98.5, '%'])
    sheet.append(['Purity', '1M', 98.2, '%'])
    # Chart referencing rows 2-3
    chart = BarChart()
    chart.add_data(Reference(sheet, min_col=3, min_row=1, max_row=3), titles_from_data=True)
    sheet.add_chart(chart, 'E5')

    data = BytesIO()
    book.save(data)

    store = Store(tmp_path / 'chart_store')
    flow = Workflow(store)
    flow.configure(ProjectConfig(project='P2'))
    ctx = ImportContext(role='stability_source', defaults={'sample_type': 'DP', 'batch': 'B1', 'presentation': 'pen', 'study': 'long_term', 'condition': '5C'})
    p = flow.upload('P2', 'chart_ref.xlsx', data.getvalue(), ctx, 'k2', actor=TECH)

    # Real results must remain candidates
    assert len(p.candidates) == 2
    assert all(c.status != 'empty' for c in p.candidates)
