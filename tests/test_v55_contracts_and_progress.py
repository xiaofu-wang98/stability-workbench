from openpyxl import Workbook
from stability_hub.v5_contracts import ReviewBlock, ActionGroup, ReviewProgress, SheetPreflight, TaskBatchScope
from stability_hub.review_tasks import count_resolved_decisions, worksheet_states
from stability_hub.permissions import Role, actor_for_role
from stability_hub.storage import Store
from stability_hub.workflow import Workflow

TECH = actor_for_role('tech', 'technician', Role.TECHNICIAN)


def test_contracts_initialization():
    block = ReviewBlock(
        block_id='blk_1',
        section='Sheet1',
        range_refs=('C154:C171',),
        role='auxiliary',
        summary='绘图辅助区域 · 原行154—171',
        requires_human=True,
    )
    assert block.role == 'auxiliary'
    assert block.range_refs == ('C154:C171',)

    ag = ActionGroup(
        group_id='grp_1',
        title='标准删改15处',
        reason='保留未划线内容',
        scope=TaskBatchScope(section='Sheet1', operation='keep_active'),
        decision_count=15,
        affected_result_count=45,
        base_revision=1,
    )
    assert ag.decision_count == 15
    assert ag.title == '标准删改15处'

    prog = ReviewProgress(
        section='Sheet1',
        pending_decision_count=5,
        resolved_decision_count=3,
        affected_result_count=12,
        can_commit=False,
        state='needs_review',
        blocker_reasons=('有 1 处绘图辅助区域待明确用途',),
    )
    assert prog.pending_decision_count == 5
    assert prog.resolved_decision_count == 3


def test_progress_counting_single_origin_and_region_classification(tmp_path):
    store = Store(tmp_path / 'store')
    flow = Workflow(store)

    # Simulate upload
    upload = {'id': 'up_test', 'project': 'P1', 'source_hash': 'sha123'}

    # Simulate changes: 2 field changes on SAME origin (row 12, col 4)
    changes = [
        {'section': 'Sheet1', 'row': 12, 'column': 4, 'field': 'criterion', 'before': 'old', 'after': 'new'},
        {'section': 'Sheet1', 'row': 12, 'column': 4, 'field': 'subresult', 'before': 'old2', 'after': 'new2'},
    ]
    sheets = [{'section': 'Sheet1', 'regions': [{'id': 'reg_1', 'coordinates': ['C154'], 'role': 'auxiliary', 'revision': 2, 'basis': 'classify_region'}]}]

    with store.connect() as conn:
        conn.execute("INSERT OR IGNORE INTO workflow_projects(project, revision, config_json) VALUES ('P1', 1, '{}')")
        conn.execute("INSERT OR IGNORE INTO documents(sha256, filename, stored_path) VALUES ('sha123', 'test.xlsx', 'test.xlsx')")
        conn.execute(
            "INSERT INTO workflow_uploads(id, project, source_hash, filename, context_json, uploaded_by, client_key, request_hash) "
            "VALUES ('up_test', 'P1', 'sha123', 'test.xlsx', '{}', 'tech', 'k1', 'r1')"
        )
        flow_event = __import__('stability_hub.workflow_common', fromlist=['event']).event
        flow_event(conn, 'P1', 'up_test', 'classify_region', TECH, '核对辅助数据', {'region_id': 'reg_1', 'role': 'auxiliary'})

        resolved = count_resolved_decisions(conn, upload, None, sheets, changes)

    # 1 decision for row 12 col 4 (even though 2 fields were changed) + 1 for classify_region = 2
    assert resolved['Sheet1'] == 2
