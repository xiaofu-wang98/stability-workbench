"""
Phase 4 端到端真实业务场景验收测试集：
覆盖 场景 A、场景 B、场景 C、场景 D 并生成自动化验收报告。
"""
import io
import json
import pytest
from io import BytesIO
from pathlib import Path
from docx import Document
import openpyxl
from starlette.testclient import TestClient

from stability_hub.api import create_app
from stability_hub.models import ProjectConfig
from stability_hub.permissions import Role, actor_for_role
from stability_hub.storage import Store

ACTOR = actor_for_role('tester', 'tech', Role.ADMIN)


def create_mock_docx(title, rows_data):
    doc = Document()
    doc.add_paragraph(title)
    table = doc.add_table(rows=0, cols=3)
    r0 = table.add_row()
    r0.cells[0].text = "检测项目"
    r0.cells[1].text = "方法"
    r0.cells[2].text = "质量标准"
    for item, method, crit in rows_data:
        r = table.add_row()
        r.cells[0].text = item
        r.cells[1].text = method
        r.cells[2].text = crit
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_scenario_a_ip281_multi_components_and_activity(tmp_path):
    """
    场景 A（IP281 复杂多子结果与同名定性/定量闭环）：
    验证 SEC-HPLC（3子结果）、R CE-SDS（3子结果）、NR CE-SDS（2子结果）、
    两行 生物学活性（细胞杀伤法）（定性+定量）全部准确入库且无身份覆盖。
    """
    store = Store(tmp_path / "store_a")
    client = TestClient(create_app(store, [], ACTOR))

    client.post("/api/v1/projects", json={"project": "IP281", "name": "IP281 Project"})

    spec_rows = [
        # SEC-HPLC (3 子结果)
        ("纯度", "SEC-HPLC法", "单体：≥ 95.00%"),
        ("纯度", "SEC-HPLC法", "高分子量组分：≤ 3.50%"),
        ("纯度", "SEC-HPLC法", "低分子量组分：≤ 1.50%"),
        # R CE-SDS (3 子结果)
        ("纯度", "还原型CE-SDS法", "主峰（重链+轻链）%：≥ 90.0%"),
        ("纯度", "还原型CE-SDS法", "非糖基化重链%：报告结果"),
        ("纯度", "还原型CE-SDS法", "其它组分%：报告结果"),
        # NR CE-SDS (2 子结果)
        ("纯度", "非还原型CE-SDS法", "主峰（单抗完整分子）%：≥ 85.0%"),
        ("纯度", "非还原型CE-SDS法", "杂质总量%：≤ 15.0%"),
        # 生物学活性 (两行：定性鉴别 + 定量相对效价)
        ("生物学活性", "细胞杀伤法", "与阶段性标准品量效关系曲线相似"),
        ("生物学活性", "细胞杀伤法", "70% ~ 130%"),
    ]

    docx_bytes = create_mock_docx("STP(C)-QC-IP281-001 质量标准", spec_rows)
    spec_resp = client.post("/api/v1/specifications", files={"file": ("STP(C)-QC-IP281-001.docx", docx_bytes)})
    assert spec_resp.status_code == 200
    spec_id = spec_resp.json()["id"]

    # 验证 preview 中的 rows 自动识别定性与定量，且区分 component
    spec_detail = spec_resp.json()
    bio_rows = [r for r in spec_detail["rows"] if "生物学活性" in r["item"]]
    assert len(bio_rows) == 2
    qual_row = next(r for r in bio_rows if r["result_type"] == "qualitative")
    num_row = next(r for r in bio_rows if r["result_type"] == "numeric")
    assert qual_row["component"] == "量效曲线"
    assert num_row["component"] == "相对效价"

    # 注册全部检项
    rev = client.get("/api/v1/projects/IP281/configuration").json()["base_revision"]
    components_to_reg = [
        {"table": r["table"], "row": r["row"], "assay_zh": r["item"], "method": r["method"],
         "component_zh": r.get("component") or r["item"], "result_type": r.get("result_type", "numeric"),
         "unit": r.get("unit", "")}
        for r in spec_detail["rows"]
    ]

    reg_resp = client.post(f"/api/v1/projects/IP281/specifications/{spec_id}/components", json={
        "revision": rev,
        "idempotency_key": "reg-ip281-all",
        "components": components_to_reg
    })
    assert reg_resp.status_code == 200, reg_resp.text
    links = reg_resp.json()["links"]
    assert len(links) == len(spec_rows)

    # 验证生成的 item_id 各自独立，无覆盖
    item_ids = [l["item_id"] for l in links]
    assert len(set(item_ids)) == len(spec_rows)


def test_scenario_b_ip501_protocol_scope_and_tbd(tmp_path):
    """
    场景 B（IP501 方案 TBD 批号与防误配参考品标准）：
    解析参考品标准与方案，验证参考品标准不会误绑到 DS/DP 研究表上，且 0M 引用放行准确识别。
    """
    store = Store(tmp_path / "store_b")
    client = TestClient(create_app(store, [], ACTOR))

    client.post("/api/v1/projects", json={"project": "IP501", "name": "IP501 Project"})

    # 1. 上传参考品标准
    doc_ref = Document()
    doc_ref.add_paragraph("范围 SCOPE: 本标准适用于 IP501 参考品及对照品检验。")
    t_ref = doc_ref.add_table(rows=0, cols=3)
    r0 = t_ref.add_row()
    r0.cells[0].text = "检测项目"
    r0.cells[1].text = "方法"
    r0.cells[2].text = "质量标准"
    r1 = t_ref.add_row()
    r1.cells[0].text = "等电点"
    r1.cells[1].text = "icIEF法"
    r1.cells[2].text = "主峰pI: 8.2 ~ 8.6"
    buf_ref = BytesIO()
    doc_ref.save(buf_ref)
    spec_ref_resp = client.post("/api/v1/specifications", files={"file": ("STP(C)-QC-IP501-2-004.docx", buf_ref.getvalue())})
    assert spec_ref_resp.status_code == 200
    assert spec_ref_resp.json()["material_scope"] == "reference"

    # 2. 上传 DS 方案 (含 TBD 批号与 0M 引用放行数据)
    doc_proto = Document()
    doc_proto.add_paragraph("IP501 原液长期与加速稳定性方案 STB(C)-QC-501-P-2-001")
    doc_proto.add_paragraph("适用标准：STP(C)-QC-IP501-2-001, STP(C)-QC-IP501-2-004")
    # 批号表
    t_batch = doc_proto.add_table(rows=2, cols=2)
    t_batch.rows[0].cells[0].text = "序号"
    t_batch.rows[0].cells[1].text = "批号 Batch No."
    t_batch.rows[1].cells[0].text = "1"
    t_batch.rows[1].cells[1].text = "TBD"

    # 矩阵表
    t_mat = doc_proto.add_table(rows=3, cols=4)
    t_mat.rows[0].cells[0].text = "考察项目 Study Items"
    t_mat.rows[0].cells[1].text = "0月"
    t_mat.rows[0].cells[2].text = "3月"
    t_mat.rows[0].cells[3].text = "6月"

    t_mat.rows[1].cells[0].text = "储存条件: 5±3℃"
    t_mat.rows[1].cells[1].text = ""
    t_mat.rows[1].cells[2].text = ""
    t_mat.rows[1].cells[3].text = ""

    t_mat.rows[2].cells[0].text = "纯度（SEC-HPLC法）\nPurity (SEC-HPLC Method)"
    t_mat.rows[2].cells[1].text = "引用放行数据"
    t_mat.rows[2].cells[2].text = "√"
    t_mat.rows[2].cells[3].text = "√"

    buf_proto = BytesIO()
    doc_proto.save(buf_proto)

    draft_resp = client.post("/api/v1/intake/protocol", files={"file": ("STB(C)-QC-501-P-2-001.docx", buf_proto.getvalue())})
    assert draft_resp.status_code == 200
    draft_id = draft_resp.json()["draft_id"]

    # 3. 使用 worksheet-seed 从 protocol 生成研究表种子
    seed_resp = client.get("/api/v1/projects/IP501/worksheet-seed", params={
        "source": "protocol",
        "source_id": draft_id,
        "table": 2
    })
    assert seed_resp.status_code == 200
    seed_data = seed_resp.json()
    assert seed_data["matrix"]["items"][-1]["label"] == "纯度（SEC-HPLC法）\nPurity (SEC-HPLC Method)"

    # 验证 TBD 批号被置空
    assert seed_data["identity"]["batch"] == ""
    # 验证参考品标准 STP(C)-QC-IP501-2-004 不会误绑定至 DS 方案上
    found_numbers = [s["number"] for s in seed_data["protocol_standards"]["found"]]
    assert "STP(C)-QC-IP501-2-004" not in found_numbers
    # 缺失的 DS 标准 STP(C)-QC-IP501-2-001 标记在 missing 中
    assert "STP(C)-QC-IP501-2-001" in seed_data["protocol_standards"]["missing"]


def test_scenario_c_coa_roundtrip_and_0m_binding(tmp_path):
    """
    场景 C（CoA Word + Excel 双格式往返与 0M 引用）：
    对登记的样品生成 Word CoA 和 Excel CoA，填入放行数据后回传，
    再新建该批次长期稳定性表，验证 0M 自动发现并成功引用 CoA 放行结果。
    """
    store = Store(tmp_path / "store_c")
    client = TestClient(create_app(store, [], ACTOR))

    client.post("/api/v1/projects", json={"project": "IP318", "name": "IP318 Project"})

    # 上传质量标准
    spec_bytes = create_mock_docx("STP(C)-QC-IP318-001", [
        ("纯度", "SEC-HPLC法", "单体：≥ 95.00%"),
        ("电荷异质性", "icIEF法", "主峰：≥ 60.0%")
    ])
    spec_resp = client.post("/api/v1/specifications", files={"file": ("STP(C)-QC-IP318-001.docx", spec_bytes)})
    spec_id = spec_resp.json()["id"]

    # 注册检项
    rev = client.get("/api/v1/projects/IP318/configuration").json()["base_revision"]
    reg_resp = client.post(f"/api/v1/projects/IP318/specifications/{spec_id}/components", json={
        "revision": rev,
        "idempotency_key": "reg-318",
        "components": [
            {"table": 1, "row": 2, "assay_zh": "纯度", "method": "SEC-HPLC法", "component_zh": "单体", "result_type": "numeric", "unit": "%"},
            {"table": 1, "row": 3, "assay_zh": "电荷异质性", "method": "icIEF法", "component_zh": "主峰", "result_type": "numeric", "unit": "%"}
        ]
    })
    assert reg_resp.status_code == 200
    monomer_item_id = reg_resp.json()["links"][0]["item_id"]

    # 登记样品
    sample_resp = client.post("/api/v1/projects/IP318/samples", json={
        "batch": "B20261101", "material_stage": "原液", "sample_type": "DS", "purpose": "放行与稳定性", "presentation": "100mg"
    })
    assert sample_resp.status_code == 201
    sample_id = sample_resp.json()["id"]

    coa_path = f"/api/v1/samples/{sample_id}/coa/{spec_id}"

    # 1. 导出 Excel CoA
    excel_download = client.get(coa_path + "/file?format=xlsx")
    assert excel_download.status_code == 200
    wb = openpyxl.load_workbook(BytesIO(excel_download.content))
    ws = wb["质量标准与放行(COA)"]
    ws.cell(row=2, column=7).value = "96.80%"  # 填入单体放行值
    excel_buf = BytesIO()
    wb.save(excel_buf)

    # 2. 回传 Excel CoA
    preview_resp = client.post(coa_path + "/preview", files={"file": ("coa.xlsx", excel_buf.getvalue())})
    assert preview_resp.status_code == 200
    p_data = preview_resp.json()
    assert p_data["values"].get("1:2") == "96.80%"

    save_resp = client.post(coa_path, json={
        "upload_id": p_data["upload_id"],
        "values": p_data["values"],
        "revision": p_data["revision"],
        "version": p_data["version"],
        "idempotency_key": "save-c-coa"
    })
    assert save_resp.status_code == 200

    # 3. 创建长期稳定性表
    sheet_create = client.post("/api/v1/projects/IP318/worksheets", json={
        "name": "IP318 长期稳定性 5C",
        "idempotency_key": "create-sheet-318",
        "start_date": "2026-11-01",
        "sample_id": sample_id,
        "identity": {
            "batch": "B20261101",
            "presentation": "100mg",
            "sample_type": "DS",
            "study": "long_term",
            "study_id": "STUDY-318-LT",
            "condition": "5C"
        },
        "items": [monomer_item_id],
        "points": [
            {"amount": 0, "unit": "M", "required_items": [monomer_item_id]},
            {"amount": 3, "unit": "M", "required_items": [monomer_item_id]}
        ]
    })
    assert sheet_create.status_code == 201, sheet_create.text
    sheet_id = sheet_create.json()["id"]

    # 4. 打开研究表，验证 0M 处自动发现 CoA 放行值
    sheet_view = client.get(f"/api/v1/worksheets/{sheet_id}").json()
    row_id = sheet_view["rows"][0]["id"]
    assert "available_coa_references" in sheet_view
    assert row_id in sheet_view["available_coa_references"]
    coa_opts = sheet_view["available_coa_references"][row_id]
    assert any(opt["value"] == "96.80%" for opt in coa_opts)


def test_scenario_d_worksheet_template_roundtrip_and_conflict(tmp_path):
    """
    场景 D（研究表 Excel 模板导出、单格填写、冲突检测与原样往返）：
    验证空表导出 -> 填 1 格回传仅增 1 格 -> 再次原样回传 0 新增 -> 网页修改后旧表回传触发 conflict 拦截。
    """
    store = Store(tmp_path / "store_d")
    from stability_hub.models import ItemDefinition, ProjectConfig, ImportContext
    cfg = ProjectConfig(
        project="PROJECT-P",
        items=[
            ItemDefinition(id="purity", label="纯度", method="SEC-HPLC", unit="%", result_type="numeric"),
            ItemDefinition(id="appearance", label="外观", method="目测", unit="", result_type="qualitative")
        ]
    )
    client = TestClient(create_app(store, [cfg], ACTOR))

    from test_business_worksheets import create_sheet

    ctx = ImportContext(
        role="stability_source",
        defaults={
            "sample_type": "DP",
            "batch": "B001A1",
            "presentation": "pen-1mg",
            "study": "long_term",
            "study_id": "STUDY-P-01",
            "condition": "5C",
        }
    )
    sheet_path = create_sheet(client, ctx)

    def export(filled=False):
        response = client.post(sheet_path + '/study-workbook', params={'filled': filled})
        assert response.status_code == 200, response.text
        file = client.get(response.json()['download_url'])
        assert file.status_code == 200, file.text
        return file.content

    def preview(data):
        return client.post('/api/v1/study-workbooks/preview', files={'file': ('test.xlsx', data)})

    def commit(view, key, decisions=None):
        token = client.post(sheet_path + '/lease').json()['token']
        try:
            response = client.post(sheet_path + '/study-workbooks/' + view['source_id'], json={
                'token': token, 'revision': view['revision'], 'fingerprint': view['fingerprint'],
                'idempotency_key': key, 'reason': 'Reviewed uploaded result', 'decisions': decisions or {},
            })
            assert response.status_code == 200, response.text
            return response.json()
        finally:
            assert client.post(sheet_path + '/lease/release', json={'token': token}).status_code == 200

    # 1. 导出空表模板
    blank = export(filled=False)
    assert preview(blank).json()['changes'] == []

    # 2. 在导出模板中填写 1 格 (D6: 2.10)
    wb = openpyxl.load_workbook(BytesIO(blank))
    wb.active['D6'] = '2.10'
    filled_buf = BytesIO()
    wb.save(filled_buf)
    filled = filled_buf.getvalue()

    # 3. 回传填写的 Excel (仅增 1 格)
    first = preview(filled).json()
    assert [cell['status'] for cell in first['changes']] == ['new']
    initial = commit(first, 'initial')['result_version_ids'][0]

    # 4. 再次原样回传，验证 0 新增
    repeat = preview(filled).json()
    assert commit(repeat, 'repeat')['result_version_ids'] == []

    # 5. 网页修改后旧表回传触发 conflict 拦截
    baseline = export(filled=True)
    updated = openpyxl.load_workbook(BytesIO(baseline))
    updated.active['D6'] = '2.20'
    up_buf = BytesIO()
    updated.save(up_buf)
    corrected = commit(preview(up_buf.getvalue()).json(), 'updated')['result_version_ids'][0]
    assert corrected != initial

    competing_wb = openpyxl.load_workbook(BytesIO(baseline))
    competing_wb.active['D6'] = '2.30'
    comp_buf = BytesIO()
    competing_wb.save(comp_buf)
    competing = preview(comp_buf.getvalue()).json()
    assert competing['changes'][0]['status'] == 'conflict'


def test_scenario_e_ip501_full_domain_dictionary_and_auto_confirm(tmp_path):
    """
    场景 E（IP501 真实27条全检项标准解析与检项字典一键入库）：
    涵盖肽图（定性图谱）、等电点（定量pI+定性图谱）、SEC纯度（3子结果）、
    CE-SDS（NR 3子结果+R 2子结果）、CEX电荷变异体（酸性/主峰/碱性%）、
    外源性DNA/HCP/蛋白A（ppm单位自动填充）、相对结合活性（%）、蛋白浓度（mg/ml）、
    双药典颜色与澄清度（ChP与EP独立区分）、pH、内毒素（EU/ml）、微生物限度（cfu/10ml）。
    验证零人工手工修改即可 100% 成功一键确认并注册检项字典。
    """
    store = Store(tmp_path / "store_e")
    client = TestClient(create_app(store, [], ACTOR))

    client.post("/api/v1/projects", json={"project": "IP501", "name": "IP501 Project"})

    spec_rows = [
        ("肽图 Peptide Mapping", "RP-UPLC法 RP-UPLC Method", "与阶段性标准品或参考品图谱一致\nThe profile is consistent with interim reference standard or reference standard"),
        ("等电点 Isoelectric Point", "icIEF法 icIEF Method", "主峰pI与阶段性标准品或参考品主峰pI差值不大于0.2\nThe absolute difference of main peak pI value ≤0.2"),
        ("等电点 Isoelectric Point", "icIEF法 icIEF Method", "与阶段性标准品或参考品图谱一致\nThe profile is consistent with interim reference standard or reference standard"),
        ("纯度 Purity", "SEC-HPLC法 SEC-HPLC Method", "主峰%：≥95.0%\nMain Peak%: ≥95.0%"),
        ("纯度 Purity", "SEC-HPLC法 SEC-HPLC Method", "聚合体%：≤3.0%\nHMW%: ≤3.0%"),
        ("纯度 Purity", "SEC-HPLC法 SEC-HPLC Method", "片段%：报告数据\nLMW%: Report Result"),
        ("纯度 Purity", "非还原型CE-SDS法 NR CE-SDS Method", "主峰%：≥90.0%\nMain Peak%: ≥90.0%"),
        ("纯度 Purity", "非还原型CE-SDS法 NR CE-SDS Method", "低分子量组分%：报告数据\nLMW%: Report Result"),
        ("纯度 Purity", "非还原型CE-SDS法 NR CE-SDS Method", "高分子量组分%：报告数据\nHMW%: Report Result"),
        ("纯度 Purity", "还原型CE-SDS法 R CE-SDS Method", "主峰(LC+HC+NGHC)%：≥95.0%\nMain Peak (LC+HC+NGHC) %: ≥95.0%"),
        ("纯度 Purity", "还原型CE-SDS法 R CE-SDS Method", "低分子量组分%：报告数据\nLMW%: Report Result"),
        ("电荷变异体 Charge Variants", "CEX-HPLC法 CEX-HPLC Method", "酸性组分%：报告数据\nAcidic Peak%: Report result"),
        ("电荷变异体 Charge Variants", "CEX-HPLC法 CEX-HPLC Method", "主峰%：≥45.0%\nMain Peak%: ≥45.0%"),
        ("电荷变异体 Charge Variants", "CEX-HPLC法 CEX-HPLC Method", "碱性组分%：报告数据\nBasic Peak%: Report result"),
        ("外源性DNA残留 Residual Exogenous DNA", "qPCR法 qPCR Method", "<1 ppm"),
        ("宿主细胞蛋白残留 Residual Host Cell Proteins", "ELISA法 ELISA Method", "≤100 ppm"),
        ("蛋白A残留 Residual Protein A", "ELISA法 ELISA Method", "≤100 ppm"),
        ("相对结合活性 Relative Binding Activity", "ELISA法 ELISA Method", "应为阶段性标准品或参考品的70%~130%"),
        ("蛋白浓度 Protein Concentration", "UV法 UV Method", "9.0~11.0 mg/ml"),
        ("颜色 Color", "目视法 Visual Inspection", "无色至淡黄色液体，不得深于黄色 3 号标准比色液（中国药典）"),
        ("颜色 Color", "目视法 Visual Inspection", "无色至淡黄色液体，不得深于Y5标准比色液（欧洲药典）"),
        ("澄清度 Clarification", "目视法 Visual Inspection", "澄清至微乳光液体，不得大于4号浊度标准液（中国药典）"),
        ("澄清度 Clarification", "目视法 Visual Inspection", "澄清至微乳光液体，不得大于Ⅳ号浊度标准液（欧洲药典）"),
        ("pH", "pH值测定法 pH Electrode Method", "4.5~5.5"),
        ("细菌内毒素 Bacterial Endotoxins", "动态显色法 Kinetic Chromogenic Method", "<2 EU/ml"),
        ("微生物限度 Bioburden", "薄膜过滤法 Membrane Filtration Method", "≤1 cfu/10 ml"),
    ]

    docx_bytes = create_mock_docx("STP(C)-QC-IP501-2-001 质量标准", spec_rows)
    spec_resp = client.post("/api/v1/specifications", files={"file": ("STP(C)-QC-IP501-2-001.docx", docx_bytes)})
    assert spec_resp.status_code == 200
    spec_data = spec_resp.json()
    spec_id = spec_data["id"]
    rows = spec_data["rows"]
    assert len(rows) == len(spec_rows)

    # 1. 验证关键字段解析
    peptide_row = next(r for r in rows if "肽图" in r["item"])
    assert peptide_row["result_type"] == "qualitative"
    assert peptide_row["component"] == "图谱一致"

    pi_num = next(r for r in rows if "等电点" in r["item"] and r["result_type"] == "numeric")
    pi_qual = next(r for r in rows if "等电点" in r["item"] and r["result_type"] == "qualitative")
    assert pi_num["component"] == "主峰pI"
    assert pi_qual["component"] == "图谱一致"

    dna_row = next(r for r in rows if "外源性DNA" in r["item"])
    assert dna_row["unit"] == "ppm"
    assert dna_row["result_type"] == "numeric"

    hcp_row = next(r for r in rows if "宿主细胞蛋白" in r["item"])
    assert hcp_row["unit"] == "ppm"
    assert hcp_row["result_type"] == "numeric"

    conc_row = next(r for r in rows if "蛋白浓度" in r["item"])
    assert conc_row["unit"] == "mg/ml"
    assert conc_row["result_type"] == "numeric"

    color_chp = next(r for r in rows if "颜色" in r["item"] and "ChP" in r["component"])
    color_ep = next(r for r in rows if "颜色" in r["item"] and "EP" in r["component"])
    assert color_chp["result_type"] == "qualitative"
    assert color_ep["result_type"] == "qualitative"

    endo_row = next(r for r in rows if "细菌内毒素" in r["item"])
    assert endo_row["unit"] == "EU/ml"

    biob_row = next(r for r in rows if "微生物限度" in r["item"])
    assert biob_row["unit"] == "cfu/10ml"

    # 2. 模拟前端一键确认入库
    components_payload = [
        {
            "table": r["table"],
            "row": r["row"],
            "assay_zh": r.get("item_zh") or r["item"],
            "assay_en": r.get("item_en", ""),
            "component_zh": r["component"],
            "component_en": r.get("component_en", ""),
            "method": r.get("method_zh") or r["method"],
            "method_version": "not_reported",
            "unit": r.get("unit", ""),
            "result_type": r.get("result_type", "numeric"),
        }
        for r in rows
    ]

    conf_resp = client.get("/api/v1/projects/IP501/configuration")
    base_rev = conf_resp.json()["base_revision"]

    reg_resp = client.post(
        f"/api/v1/projects/IP501/specifications/{spec_id}/components",
        json={"revision": base_rev, "components": components_payload, "idempotency_key": "key-scenario-e"}
    )
    if reg_resp.status_code != 200:
        print("REG RESP FAILED:", reg_resp.status_code, reg_resp.json())
    assert reg_resp.status_code == 200
    assert len(reg_resp.json()["links"]) == len(rows)
