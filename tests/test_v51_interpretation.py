"""5.6: draft edits stay independent; current facts determine all gates."""

from uuid import uuid4

import pytest

from fastapi.testclient import TestClient
from openpyxl import load_workbook
from openpyxl.comments import Comment

from stability_hub.api import create_app
from stability_hub.bulk_drafts import preview as bulk_preview, apply
from stability_hub.ledger_review import LedgerReview
from stability_hub.storage import Store
from stability_hub.workflow import Workflow
from stability_hub.workflow_contracts import CandidateEdit, ReceiveRequest
from stability_hub.v5_contracts import BulkPreview, BulkApply, IssueDecision
from stability_hub.review_issues import resolve_issue
from test_v5_regressions import TECH, preview
from test_v5_commit import inspect, send


def test_auxiliary_source_is_explicit_atomic_and_keeps_original(tmp_path, xlsx, context):
    from stability_hub.v5_contracts import SheetCommit

    path = xlsx([["Comparison note", "source only"]], headers=["Note", "Explanation"])
    flow, p = preview(tmp_path, path, context)
    assert not p.candidates
    client = TestClient(create_app(flow.store, [], TECH))
    route = f"/api/v1/previews/{p.preview_id}/v5/auxiliary-source"

    def command(key):
        v = inspect(flow, p)
        return SheetCommit(
            base_revision=v.base_revision,
            idempotency_key=key,
            section=v.section,
            fingerprint=v.fingerprint,
            reason="全表为来源比较说明，无检测结果",
        ).model_dump(mode="json")

    assert client.post(route, json=command("before-receipt")).status_code == 422
    p = receive(flow, p)
    stale = command("stale")
    request = command("archive")
    response = client.post(route, json=request)
    assert response.status_code == 200, response.text
    assert not response.json()["result_version_ids"]
    assert client.post(route, json=request).json() == response.json()
    assert client.post(route, json=stale).status_code == 409
    assert inspect(flow, p).state == "committed"
    assert not inspect(flow, p).unresolved
    with flow.store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM workflow_versions").fetchone()[0] == 0
    matrix = LedgerReview(flow).matrix(p.preview_id, actor=TECH)
    assert any(c["text"] == "Comparison note" for c in matrix["sheets"][0]["cells"])
    assert path.read_bytes() == client.get(f"/api/v1/previews/{p.preview_id}/file").content


@pytest.mark.parametrize("withdrawn", [False, True])
def test_auxiliary_source_rejects_real_candidates_even_withdrawn(tmp_path, xlsx, context, withdrawn):
    from stability_hub.v5_contracts import DraftDecision, SheetCommit
    from stability_hub.worksheet_commit import save_decision

    flow, p = preview(tmp_path, xlsx([["Purity", "0M", 99, "%"]]), context)
    p = receive(flow, p)
    if withdrawn:
        c = p.candidates[0]
        save_decision(
            flow,
            p.preview_id,
            DraftDecision(
                base_revision=p.base_revision,
                idempotency_key="exclude",
                reason="测试不能把排除当作无结果来源",
                candidate_id=c.id,
                candidate_revision=c.candidate_revision,
                action="exclude",
            ),
            actor=TECH,
        )
    v = inspect(flow, p)
    request = SheetCommit(
        base_revision=v.base_revision,
        idempotency_key="archive",
        section=v.section,
        fingerprint=v.fingerprint,
        reason="不允许掩盖结果",
    )
    client = TestClient(create_app(flow.store, [], TECH))
    response = client.post(
        f"/api/v1/previews/{p.preview_id}/v5/auxiliary-source", json=request.model_dump(mode="json")
    )
    assert response.status_code == 422, response.text
    assert "contains_candidates" in response.text


def edit(flow, p, c, fields):
    flow.edit(
        c.id,
        CandidateEdit(
            base_revision=p.base_revision,
            idempotency_key=uuid4().hex,
            candidate_revision=c.candidate_revision,
            fields=fields,
            reason="来源明确修正",
        ),
        actor=TECH,
    )
    return flow.preview(p.preview_id, actor=TECH)


def receive(flow, p):
    LedgerReview(flow).receive(
        p.preview_id,
        ReceiveRequest(
            base_revision=p.base_revision,
            idempotency_key=uuid4().hex,
            candidate_ids=tuple(c.id for c in p.candidates),
        ),
        actor=TECH,
    )
    return flow.preview(p.preview_id, actor=TECH)


def test_56_1_missing_item_can_receive_repair_and_commit(tmp_path, xlsx, context):
    flow, p = preview(tmp_path, xlsx([["", "0M", 99, "%"]]), context)
    p = receive(flow, p)
    assert LedgerReview(flow).matrix(p.preview_id, actor=TECH)["sheets"]
    assert not inspect(flow, p).can_commit
    p = edit(flow, p, p.candidates[0], {"test_item": "Purity"})
    assert inspect(flow, p).can_commit
    assert len(send(flow, p, "complete").result_version_ids) == 1


def test_56_2_two_problems_can_be_repaired_independently(tmp_path, xlsx, context):
    path = xlsx(
        [["Purity", "0M", 99, "%", "1mg\n2mg"]],
        headers=["Test item", "Time point", "Result", "Unit", "Presentation"],
    )
    book = load_workbook(path)
    book.active["C2"].comment = Comment("核对原结果是否须更正", "source")
    book.save(path)
    flow, p = preview(tmp_path, path, context)
    assert {i.code for i in p.candidates[0].issues} == {"source_comment", "identity_ambiguity"}
    p = edit(flow, p, p.candidates[0], {"presentation": "1mg"})
    assert {i.code for i in p.candidates[0].issues} == {"source_comment"}
    c = p.candidates[0]
    resolve_issue(
        flow,
        p.preview_id,
        IssueDecision(
            base_revision=p.base_revision,
            idempotency_key="comment",
            candidate_id=c.id,
            candidate_revision=c.candidate_revision,
            issue_id=c.issues[0].id,
            action="manual",
            fields={"result": "98.5"},
            reason="原文核对",
        ),
        actor=TECH,
    )
    p = receive(flow, flow.preview(p.preview_id, actor=TECH))
    assert inspect(flow, p).can_commit and str(p.candidates[0].record.result.value) == "98.5"


def test_56_3_range_only_repairs_selected_field_members(tmp_path, xlsx, context):
    path = xlsx(
        [["Purity", f"{m}M", v, "%", "1mg\n2mg", "UV\nSEC"] for m, v in [(0, 99), (3, 98), (6, 97)]],
        headers=["Test item", "Time point", "Result", "Unit", "Presentation", "Method"],
    )
    flow, p = preview(tmp_path, path, context)
    scope = BulkPreview(candidate_ids=tuple(c.id for c in p.candidates[:2]), fields={"presentation": "1mg"})
    with flow.store.connect() as conn:
        info = bulk_preview(flow, conn, p.preview_id, scope)
    apply(
        flow,
        p.preview_id,
        BulkApply(
            base_revision=p.base_revision,
            idempotency_key="bulk",
            scope=scope,
            fingerprint=info["fingerprint"],
            reason="明确成员规格",
        ),
        actor=TECH,
    )
    p = flow.preview(p.preview_id, actor=TECH)
    assert [len(c.issues) for c in p.candidates] == [1, 1, 2]
    assert [c.raw_fields["result"] for c in p.candidates] == ["99", "98", "97"]
    reopened = Workflow(Store(flow.store.home)).preview(p.preview_id, actor=TECH)
    assert reopened == p


def test_56_5_new_identity_collision_is_not_cleared_with_old_problem(tmp_path, xlsx, context):
    context = context.model_copy(
        update={"defaults": {k: v for k, v in context.defaults.items() if k != "presentation"}}
    )
    path = xlsx(
        [["A", "0M", 99, "%", "1mg\n2mg"], ["B", "0M", 98, "%", "1mg"]],
        headers=["Test item", "Time point", "Result", "Unit", "Presentation"],
    )
    flow, p = preview(tmp_path, path, context)
    p = receive(flow, p)
    p = edit(flow, p, p.candidates[0], {"presentation": "1mg", "test_item": "B"})
    assert not p.candidates[0].issues
    assert all(c.issue_code == "ambiguous_file_slot" for c in p.candidates)
    assert not inspect(flow, p).can_commit


def test_56_6_7_independent_sheets_shared_snapshot_and_restart(tmp_path, xlsx, context):
    path = xlsx([["A", "0M", 99, "%"]])
    book = load_workbook(path)
    book.active.title = "A"
    second = book.copy_worksheet(book.active)
    second.title = "B"
    book["A"]["C2"].comment = Comment("结果需要核对", "source")
    book.save(path)
    flow, p = preview(tmp_path, path, context)
    p = receive(flow, p)
    assert not inspect(flow, p, "A").can_commit and inspect(flow, p, "B").can_commit
    from stability_hub.worksheet_commit import commit
    from stability_hub.v5_contracts import SheetCommit

    v = inspect(flow, p, "B")
    commit(
        flow,
        p.preview_id,
        SheetCommit(
            base_revision=v.base_revision,
            idempotency_key="b",
            reason="核对B表",
            section="B",
            fingerprint=v.fingerprint,
        ),
        actor=TECH,
    )
    p = flow.preview(p.preview_id, actor=TECH)
    assert next(c for c in p.candidates if c.location.section == "A").lifecycle == "draft"
    assert flow.results("PROJECT-P", actor=TECH).total == 1
    reopened = Workflow(Store(flow.store.home))
    client = TestClient(create_app(reopened.store, [], TECH))
    matrix = client.get(f"/api/v1/previews/{p.preview_id}/ledger-matrix").json()
    for flight in matrix["worksheet_review"]["preflights"]:
        actual = client.get(
            f"/api/v1/previews/{p.preview_id}/v5/preflight", params={"section": flight["section"]}
        ).json()
        assert actual == flight and actual["base_revision"] == matrix["preview"]["base_revision"]


def test_bilingual_equivalence_is_narrow():
    from stability_hub.matrix_fields import presentation_lines

    assert presentation_lines("3ml/瓶\n3ml/bottle") == ["3ml/瓶"]
    assert len(presentation_lines("3mg/瓶\n6mg/bottle")) == 2


def test_real_excel_percent_serialization_round_trip(tmp_path, xlsx, context):
    from zipfile import ZipFile
    from decimal import Decimal
    from stability_hub.permissions import Role, actor_for_role
    from stability_hub.workflow_excel import ExcelExports
    from stability_hub.workflow_contracts import ExportRequest
    from test_v5_regressions import confirm_initial

    path = xlsx([["Water", "0M", 0.017, "%"]])
    book = load_workbook(path)
    book.active["C2"].number_format = "0.00%"
    book.save(path)
    with ZipFile(path) as source:
        parts = {name: source.read(name) for name in source.namelist()}
    assert b"<v>0.017</v>" in parts["xl/worksheets/sheet1.xml"]
    parts["xl/worksheets/sheet1.xml"] = parts["xl/worksheets/sheet1.xml"].replace(
        b"<v>0.017</v>", b"<v>1.7000000000000001E-2</v>"
    )
    with ZipFile(path, "w") as target:
        for name, data in parts.items():
            target.writestr(name, data)
    flow, p = preview(tmp_path, path, context)
    assert p.candidates[0].record.result.value == Decimal("1.700")
    result = confirm_initial(flow, p)
    lead = actor_for_role("lead", "lead", Role.PROJECT_LEAD)
    exports = ExcelExports(flow)
    exported = exports.create(
        ExportRequest(project="PROJECT-P", base_revision=result.base_revision), actor=lead
    )
    book = load_workbook(exports.download(exported.export_id, actor=lead))
    assert book["Records"]["M2"].value == "1.700%"
    assert Decimal(book["Records"]["S2"].value) == Decimal("1.7")
    book.close()


def test_percent_serialization_check_rejects_different_values_and_rounded_integers():
    from stability_hub.workflow_excel_verify import same_excel_number_literal

    assert same_excel_number_literal("1.7000000000000001E-2", "0.017")
    assert not same_excel_number_literal("0.018", "0.017")
    assert not same_excel_number_literal("0.017000000000000005", "0.017")
    assert not same_excel_number_literal("9007199254740993", "9007199254740992")


def test_d_equal_new_source_uses_existing_fact_without_version_or_approval(tmp_path, xlsx, context):
    from test_v5_regressions import confirm_initial

    flow, p = preview(tmp_path, xlsx([["Purity", "0M", 99, "%"]]), context)
    confirm_initial(flow, p)
    original = flow.results("PROJECT-P", actor=TECH).cells
    path = xlsx([["Purity", "0M", 99, "%"]], "new-source.xlsx")
    book = load_workbook(path)
    book.active["C2"].comment = Comment("Method note: UV", "source")
    book.save(path)
    second = flow.upload("PROJECT-P", path.name, path.read_bytes(), context, "second-source", actor=TECH)
    assert second.source_hash != p.source_hash
    second = receive(flow, second)
    assert inspect(flow, second).can_commit and not inspect(flow, second).review_required
    result = send(flow, second, "repeat-source")
    assert result.counts["repeat_source"] == 1 and not result.result_version_ids
    assert flow.results("PROJECT-P", actor=TECH).cells == original
    assert flow.preview(second.preview_id, actor=TECH).candidates[0].lifecycle == "submitted"
    with flow.store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM workflow_versions").fetchone()[0] == 1
        assert (
            conn.execute(
                "SELECT count(*) FROM workflow_events WHERE action='repeat_source_reference'"
            ).fetchone()[0]
            == 1
        )
        before = conn.execute("SELECT count(*) FROM workflow_events").fetchone()[0]
    LedgerReview(flow).receive(
        second.preview_id,
        ReceiveRequest(
            base_revision=result.base_revision, idempotency_key="repeat-receive", candidate_ids=()
        ),
        actor=TECH,
    )
    with flow.store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM workflow_events").fetchone()[0] == before


def test_e_revision_bound_delta_rechecks_collisions_and_role(tmp_path, xlsx, context):
    flow, p = preview(tmp_path, xlsx([["A", "0M", 99, "%"], ["B", "0M", 98, "%"]]), context)
    p = receive(flow, p)
    app = create_app(flow.store, [], TECH)
    client = TestClient(app)
    url = f"/api/v1/previews/{p.preview_id}/ledger-matrix"
    assert client.get(url).status_code == 200
    c = p.candidates[0]
    response = client.patch(
        f"/api/v1/candidates/{c.id}",
        json=CandidateEdit(
            base_revision=p.base_revision,
            candidate_revision=c.candidate_revision,
            idempotency_key="collision",
            reason="明确身份",
            fields={"test_item": "B"},
        ).model_dump(mode="json"),
    )
    assert response.status_code == 200
    delta = client.get(url, params={"after_revision": p.base_revision}).json()
    assert delta["partial_base_revision"] == p.base_revision
    assert len(delta["candidate_updates"]) == 2
    assert all(c["issue_code"] == "ambiguous_file_slot" for c in delta["candidate_updates"])
    assert delta["preview"]["base_revision"] == response.json()["base_revision"]
    assert not delta["worksheet_review"]["preflights"][0]["can_commit"]
    full = client.get(url, params={"after_revision": 0}).json()
    assert "partial_base_revision" not in full
    assert full["preview"]["candidates"] == delta["candidate_updates"]
    from stability_hub.permissions import Role, actor_for_role

    app.state.actor = actor_for_role(TECH.id, "same ID, different role", Role.PROJECT_LEAD)
    limited = client.get(url, params={"after_revision": full["preview"]["base_revision"]}).json()
    assert "partial_base_revision" not in limited
    assert all(not c["allowed_actions"] for c in limited["preview"]["candidates"])


def test_d_failed_sheet_rolls_back_same_source_receipt_and_new_fact(tmp_path, xlsx, context, monkeypatch):
    import pytest
    from test_v5_regressions import confirm_initial
    from stability_hub.result_versions import ResultVersions
    from stability_hub.workflow_common import WorkflowError

    flow, p = preview(tmp_path, xlsx([["A", "0M", 99, "%"]]), context)
    confirm_initial(flow, p)
    second = flow.upload(
        "PROJECT-P",
        "second.xlsx",
        xlsx([["A", "0M", 99, "%"], ["B", "0M", 98, "%"]]).read_bytes(),
        context,
        "second",
        actor=TECH,
    )
    second = receive(flow, second)
    before = flow.results("PROJECT-P", actor=TECH).cells
    original = ResultVersions.confirm

    def fail(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise WorkflowError("injected_failure", "注入事务失败")

    monkeypatch.setattr(ResultVersions, "confirm", fail)
    with pytest.raises(WorkflowError, match="注入事务失败"):
        send(flow, second, "failed-receipt-and-fact")
    assert flow.results("PROJECT-P", actor=TECH).cells == before
    assert all(c.lifecycle == "draft" for c in flow.preview(second.preview_id, actor=TECH).candidates)
    with flow.store.connect() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM workflow_events WHERE action='repeat_source_reference'"
            ).fetchone()[0]
            == 0
        )


def test_c_explicit_footer_note_and_shifted_chart_axis_are_not_results(tmp_path):
    from openpyxl import Workbook
    from openpyxl.chart import ScatterChart, Reference, Series
    from stability_hub.parsing import parse_source

    book = Workbook()
    sheet = book.active
    sheet["A1"] = "DP长期稳定性实验数据（2-8℃）"
    sheet["A2"] = "B1"
    for coord, value in {
        "A3": "检测项目",
        "C3": "方法",
        "D3": "质量标准",
        "E3": "时间（月）",
        "E4": 0,
        "F4": 3,
        "G4": 6,
        "B5": "纯度",
        "C5": "SEC-HPLC",
        "D5": "单体≥95%",
        "E5": 99,
        "F5": 98,
        "G5": 97,
        "G6": "6M生物学活性因系统适用性未通过，发AE，详见AE-X-P-001",
        "D9": 0,
        "E9": 3,
        "F9": 6,
        "C11": "Lower Limit:≥95%",
        "D11": 95,
        "E11": 95,
        "F11": 95,
    }.items():
        sheet[coord] = value
    sheet.merge_cells("E3:G3")
    chart = ScatterChart()
    chart.series.append(
        Series(
            Reference(sheet, min_col=5, max_col=7, min_row=5),
            Reference(sheet, min_col=4, max_col=6, min_row=9),
            title="Result",
        )
    )
    sheet.add_chart(chart, "A14")
    path = tmp_path / "shifted-axis.xlsx"
    book.save(path)
    parsed = parse_source(path)
    assert len(parsed.observations) == 3
    assert all(o.location.row == 5 for o in parsed.observations)
    assert not any("AE-X-P-001" in o.comment for o in parsed.observations), (
        "Biological note must not contaminate SEC results"
    )
    assert any(r["role"] == "note" and "G6" in r["coordinates"] for r in parsed.coverage[0]["regions"])


def test_c_blank_identity_with_real_descriptive_result_remains_visible(tmp_path):
    from openpyxl import Workbook
    from stability_hub.parsing import parse_source

    book = Workbook()
    sheet = book.active
    for coord, value in {
        "A1": "DP长期稳定性实验数据（2-8℃）",
        "A2": "B1",
        "A3": "检测项目",
        "B3": "质量标准",
        "C3": "时间（月）",
        "C4": 0,
        "D4": 3,
        "B5": "澄明至微乳光液体",
        "C5": "澄明液体",
        "D5": "澄明液体",
    }.items():
        sheet[coord] = value
    sheet.merge_cells("C3:D3")
    path = tmp_path / "missing-item.xlsx"
    book.save(path)
    parsed = parse_source(path)
    assert len(parsed.observations) == 2
    assert all(not o.fields.get("test_item") for o in parsed.observations)
    assert all(o.fields["result"] == "澄明液体" for o in parsed.observations)


@pytest.mark.parametrize("unknown", [False, True])
def test_empty_chart_sheet_needs_proven_internal_result_references(tmp_path, xlsx, context, unknown):
    from openpyxl.chart import LineChart, Reference
    from stability_hub.parsing import parse_source

    path = xlsx([["Purity", "0M", 99, "%"], ["Purity", "3M", 98, "%"]])
    book = load_workbook(path)
    data = book.active
    data["C4"] = "   "
    chart = LineChart()
    column = 3
    if unknown:
        column = 8
        data["H1"], data["H2"], data["H3"] = "Unknown numbers", 27, 28
    chart.add_data(Reference(data, min_col=column, min_row=1, max_row=4), titles_from_data=True)
    chart.set_categories(Reference(data, min_col=2, min_row=2, max_row=3))
    book.create_sheet("Chart only").add_chart(chart, "A1")
    book.save(path)
    parsed = parse_source(path, allow_partial=True)
    physical = next(s for s in parsed.coverage if s["section"] == "Chart only")
    assert physical["result_cells"] == 0
    assert physical["regions"][0]["role"] == ("unclassified" if unknown else "auxiliary")
