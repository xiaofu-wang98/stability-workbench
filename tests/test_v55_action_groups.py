import pytest
from openpyxl import Workbook
from stability_hub.models import ImportContext, ProjectConfig
from stability_hub.storage import Store
from stability_hub.workflow import Workflow
from stability_hub.permissions import Role, actor_for_role
from stability_hub.review_tasks import build_action_groups, build_review_blocks

TECH = actor_for_role('tech', 'technician', Role.TECHNICIAN)

def test_action_groups_and_review_blocks_generation(tmp_path):
    book = Workbook()
    s = book.active
    s.title = "DP"
    for coord, value in {
        "A1": "DP稳定性实验数据（5℃）",
        "A2": "批号: B1",
        "A3": "检测项目",
        "B3": "质量标准",
        "C3": "考察时间（月）",
        "C4": 0,
        "D4": 3,
        "A5": "纯度",
        "B5": "≥95%",
        "C5": 99,
        "D5": 98,
        "A6": "外观",
        "B6": "澄明液体",
        "C6": "合格",
        "D6": "合格",
    }.items():
        s[coord] = value
    s.merge_cells("C3:D3")

    for r in range(8, 15):
        s[f"B{r}"] = f"Tail-Note-{r}"

    path = tmp_path / "action_groups.xlsx"
    book.save(path)

    store = Store(tmp_path / 'store')
    flow = Workflow(store)
    flow.configure(ProjectConfig(project='P1'))
    ctx = ImportContext(role='stability_source', defaults={'sample_type': 'DP', 'batch': 'B1', 'presentation': 'pen', 'study': 'long_term', 'condition': '5C'})
    p = flow.upload('P1', 'action_groups.xlsx', path.read_bytes(), ctx, 'k1', actor=TECH)

    # Fetch matrix
    from uuid import uuid4
    from stability_hub.workflow_contracts import ReceiveRequest
    from stability_hub.ledger_review import LedgerReview
    lr = LedgerReview(flow)
    lr.receive(
        p.preview_id,
        ReceiveRequest(
            base_revision=p.base_revision,
            idempotency_key=uuid4().hex,
            candidate_ids=tuple(c.id for c in p.candidates),
        ),
        actor=TECH,
    )
    matrix = lr.matrix(p.preview_id, actor=TECH)
    wr = matrix.get('worksheet_review', {})
    assert 'action_groups' in wr
    assert 'review_blocks' in wr
    assert 'DP' in wr['action_groups']
    assert 'DP' in wr['review_blocks']

    # Tail block B8..B14 should form a review block in DP
    dp_blocks = wr['review_blocks']['DP']
    assert any('B8' in str(b.get('range_refs')) for b in dp_blocks)

    dp_actions = wr['action_groups']['DP']
    assert any(ag['group_id'].startswith('ag_reg_DP') for ag in dp_actions)
    reg_action = next(ag for ag in dp_actions if ag['group_id'].startswith('ag_reg_DP'))
    assert reg_action['decision_count'] == 1
    assert reg_action['affected_result_count'] == 0
