"""Controlled minimal-change standard template library (all 6 QC/stability document types)."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from docx import Document
from docx.shared import Inches, Pt
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from .platform_catalog import load_seed_catalog

HEADER_FILL = PatternFill("solid", fgColor="1E3A5F")
SUBHEADER_FILL = PatternFill("solid", fgColor="E8EEF5")
META_LABEL_FILL = PatternFill("solid", fgColor="F1F5F9")
CAT_FILL = PatternFill("solid", fgColor="F8FAFC")
STATUS_FILL = PatternFill("solid", fgColor="FEF9C3")
THIN_SIDE = Side(style="thin", color="CBD5E1")
GRID_BORDER = Border(left=THIN_SIDE, right=THIN_SIDE, top=THIN_SIDE, bottom=THIN_SIDE)
HEADER_FONT = Font(name="Microsoft YaHei", size=10, bold=True, color="FFFFFF")
BOLD_FONT = Font(name="Microsoft YaHei", size=10, bold=True, color="0F172A")
BODY_FONT = Font(name="Microsoft YaHei", size=10, color="1E293B")
TITLE_FONT = Font(name="Microsoft YaHei", size=13, bold=True, color="0F172A")
HINT_FONT = Font(name="Microsoft YaHei", size=9, italic=True, color="475569")

TEMPLATE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "id": "spec_xlsx",
        "name": "质量标准模板（Excel 规范分项版）",
        "category": "质量标准",
        "format": "xlsx",
        "filename": "TPL-01_质量标准模板_6大类规范分项版.xlsx",
        "reference": "对齐 IP501 原液质量标准 5.4 LIMS分项版 与 QC-运营中英文质量标准模板",
        "summary": "采用 8 列标准分项结构（大类/检验项目/子检项/分析方法/SOP编号/药典体系/标准限度/单位），内置 6 大类下拉校验，导入即可零手工拆行入库。",
        "minimal_changes": [
            "固定「大类」列并内置 6 大类下拉校验（纠正原模板理化项误并入安全性的问题）",
            "显式拆分「子检项/细项」「药典体系」「单位」列，避免 ChP/USP 混格与单位大小写漂移",
            "统一按 SOP(G)-RQA-0-001 书写 mg/mL、mOsmol/kg、cfu/10 mL 等规范单位",
        ],
    },
    {
        "id": "spec_docx",
        "name": "质量标准模板（中英文受控 Word 版）",
        "category": "质量标准",
        "format": "docx",
        "filename": "TPL-02_中英文质量标准模板_最小改动版.docx",
        "reference": "基于 QC-运营《中英文质量标准模板.docx》最小改动优化",
        "summary": "完整保留审批页、目的、范围、缩略语与变更历史；修正 6 大类归属与英文拼写细节，确保系统自动解析零歧义。",
        "minimal_changes": [
            "保留原有受控审批页与中英文双语表式，检测/QA 习惯零学习成本",
            "将颜色/澄清度/pH/渗透压/装量/水分从 5.安全性 归位至 6.理化与其它",
            "修正英文模板拼写（radio→ratio、mg/ml→mg/mL）并规范双药典分行",
        ],
    },
    {
        "id": "coa_xlsx",
        "name": "放行检验报告 / CoA 模板（Excel 规范版）",
        "category": "放行与 CoA",
        "format": "xlsx",
        "filename": "TPL-03_放行检验报告CoA模板_最小改动版.xlsx",
        "reference": "基于 QC-运营《中英文检验报告模板.docx》与 CoA(X)-IP166 结构化升级",
        "summary": "顶部独立字段格承载品名/批号/规格/类别/生产日期/效期/贮存条件/检验依据；正文按 6 大类对齐平台词典，支持一键关联稳定性 0M。",
        "minimal_changes": [
            "保留原 CoA 全部受控表头信息与结果判定列",
            "将原复合「检验项目（方法）」拆为 大类/检验项目/子检项/分析方法/药典体系 独立列",
            "结果与单位分列存放，消除 0M 放行引用时的单位冲突",
        ],
    },
    {
        "id": "protocol_docx",
        "name": "稳定性研究方案模板（中英文受控 Word 版）",
        "category": "稳定性方案",
        "format": "docx",
        "filename": "TPL-04_中英文稳定性考察方案模板_最小改动版.docx",
        "reference": "基于 QC-运营《中英文稳定性考察方案模板.docx》最小改动优化",
        "summary": "保留完整受控章节与审批职责表；仅将「考察条件表」拆为独立温湿度/放置/光照列、将「取样计划矩阵」加上 6 大类标识，导入方案即可零歧义建表。",
        "minimal_changes": [
            "考察条件表拆分为 试验类别 / 温度 / 相对湿度 / 放置方式 / 光照 / 考察时间点",
            "取样计划矩阵增加「大类」与标准「方法」列，时间点统一 0M/1M/3M/6M 等标准写法",
            "保留原有取样窗口与变更历史表格，保持受控文件原貌",
        ],
    },
    {
        "id": "worksheet_xlsx",
        "name": "稳定性研究表 / 离线回传模板（T1~T5 规范版）",
        "category": "稳定性研究表",
        "format": "xlsx",
        "filename": "TPL-05_稳定性研究表与回传模板_T1-T5规范版.xlsx",
        "reference": "基于 2026 近期基准《稳定性试验数据.xlsx》落地 Top 5 轻量微调（T1~T5）",
        "summary": "全面落实 T1~T5 轻量微调：独立条件格告别 Wingdings 乱码、显式大类/方法/子检项列、独立签署状态行、表尾 3 列 AR/AE 小表，支持离线填报无缝回传。",
        "minimal_changes": [
            "T1：将 B3 复合条件格拆为 温度 / 相对湿度 / 放置方式 / 光照 独立格（禁用 Wingdings2 R）",
            "T2/T3：拆分 大类 | 检验项目 | 分析方法 | 子检项/药典 | 可接受标准 五列",
            "T4/T5：时间点行纯标签 + 独立签署状态行 + 表尾 3 列 AR/AE 异常小表（消灭备注误广播）",
        ],
    },
    {
        "id": "summary_xlsx",
        "name": "稳定性汇总表模板（多条件多批次 T1~T5 规范版）",
        "category": "稳定性汇总表",
        "format": "xlsx",
        "filename": "TPL-06_稳定性数据汇总表模板_多条件规范版.xlsx",
        "reference": "统一《稳定性试验数据.xlsx》与历史常熟汇总表的全新多 Sheet 标准模板",
        "summary": "内置「项目与批次索引」+ 长期DS / 加速DS / 长期DP / 加速DP 四个标准工作表，每张表沿用统一的 T1~T5 骨架，兼顾人工阅读习惯与 100% 精准入库。",
        "minimal_changes": [
            "新增首页「项目与批次索引」总览批号、材料阶段、0M日期、方案号与考察条件",
            "各条件 Sheet 严格复用 T1~T5 结构，人员只需记住一套填表规范",
            "每个时间点预置 EDATE 计划日期辅助行与独立签署状态行",
        ],
    },
    {
        "id": "confirmation_xlsx",
        "name": "稳定性阶段性确认表模板（受控签署版）",
        "category": "阶段性确认表",
        "format": "xlsx",
        "filename": "TPL-07_稳定性阶段性确认表模板_6大类受控版.xlsx",
        "reference": "基于《IP281DPC20240801 长期24M稳定性确认表-02.pdf》与 QC-运营《稳定性试验数据确认表模板.docx》",
        "summary": "保留常熟工厂受控页眉、开展地点勾选、稳定性总结与三级签署栏位；表体按 6 大类清晰分组呈现，直接对齐系统导出确认表版式。",
        "minimal_changes": [
            "保留原确认表编号、工厂勾选（☑常熟工厂C）、方案编号、备注、总结与三级签字区",
            "表体增设「大类」分组列与规范「分析方法/单位」，与质量标准和 CoA 保持完全一致",
            "条件区改用结构化分项 + 标准 Unicode 复选框 ☑/□，打印与 PDF 导出不乱码",
        ],
    },
)


def _sample_six_category_rows() -> list[dict[str, str]]:
    """Representative CDMO mAb/ADC rows across all 6 standard categories for templates."""
    return [
        {
            "category": "1.鉴别",
            "item": "肽图\nPeptide Mapping",
            "subitem": "图谱一致性",
            "method": "RP-UPLC法\nRP-UPLC Method",
            "sop": "SOP(C)-QC-001",
            "basis": "ChP",
            "criterion": "与参考品图谱一致\nThe profile is consistent with Reference Standard",
            "unit": "",
            "sample_0m": "与参考品图谱一致",
            "sample_3m": "与参考品图谱一致",
        },
        {
            "category": "1.鉴别",
            "item": "等电点\nIsoelectric Point",
            "subitem": "主峰pI差值",
            "method": "icIEF法\nicIEF Method",
            "sop": "SOP(C)-QC-002",
            "basis": "ChP",
            "criterion": "与参考品主峰pI差值 ≤0.2\nThe difference between main peak pI and reference is ≤0.2",
            "unit": "",
            "sample_0m": "0.0",
            "sample_3m": "0.0",
        },
        {
            "category": "2.纯度和杂质",
            "item": "纯度（分子排阻色谱）\nPurity (SEC-HPLC)",
            "subitem": "单体 Monomer",
            "method": "SEC-HPLC法\nSEC-HPLC Method",
            "sop": "SOP(C)-QC-003",
            "basis": "ChP",
            "criterion": "≥95.0%",
            "unit": "%",
            "sample_0m": "99.4",
            "sample_3m": "99.1",
        },
        {
            "category": "2.纯度和杂质",
            "item": "纯度（分子排阻色谱）\nPurity (SEC-HPLC)",
            "subitem": "高分子量组分 HMW",
            "method": "SEC-HPLC法\nSEC-HPLC Method",
            "sop": "SOP(C)-QC-003",
            "basis": "ChP",
            "criterion": "≤5.0%",
            "unit": "%",
            "sample_0m": "0.6",
            "sample_3m": "0.9",
        },
        {
            "category": "2.纯度和杂质",
            "item": "纯度（非还原型CE-SDS）\nPurity (NR CE-SDS)",
            "subitem": "主峰 Main Peak",
            "method": "非还原型CE-SDS法\nNR CE-SDS Method",
            "sop": "SOP(C)-QC-004",
            "basis": "ChP",
            "criterion": "≥90.0%",
            "unit": "%",
            "sample_0m": "98.2",
            "sample_3m": "97.8",
        },
        {
            "category": "2.纯度和杂质",
            "item": "纯度（还原型CE-SDS）\nPurity (R CE-SDS)",
            "subitem": "重链+轻链 HC+LC",
            "method": "还原型CE-SDS法\nR CE-SDS Method",
            "sop": "SOP(C)-QC-005",
            "basis": "ChP",
            "criterion": "≥95.0%",
            "unit": "%",
            "sample_0m": "98.8",
            "sample_3m": "98.5",
        },
        {
            "category": "2.纯度和杂质",
            "item": "电荷变异体\nCharge Variants",
            "subitem": "主峰 Main Peak",
            "method": "icIEF法\nicIEF Method",
            "sop": "SOP(C)-QC-006",
            "basis": "ChP",
            "criterion": "报告数据\nReport Result",
            "unit": "%",
            "sample_0m": "62.5",
            "sample_3m": "60.8",
        },
        {
            "category": "2.纯度和杂质",
            "item": "电荷变异体\nCharge Variants",
            "subitem": "酸性峰 Acidic Peaks",
            "method": "icIEF法\nicIEF Method",
            "sop": "SOP(C)-QC-006",
            "basis": "ChP",
            "criterion": "报告数据\nReport Result",
            "unit": "%",
            "sample_0m": "24.1",
            "sample_3m": "26.0",
        },
        {
            "category": "2.纯度和杂质",
            "item": "N-糖谱\nN-Glycan Profile",
            "subitem": "G0F",
            "method": "HILIC-UPLC法\nHILIC-UPLC Method",
            "sop": "SOP(C)-QC-007",
            "basis": "ChP",
            "criterion": "报告数据\nReport Result",
            "unit": "%",
            "sample_0m": "72.4",
            "sample_3m": "N/A",
        },
        {
            "category": "3.效价",
            "item": "生物学活性\nBiological Activity",
            "subitem": "相对效价 Relative Potency",
            "method": "细胞杀伤法\nCell-based Cytotoxicity Method",
            "sop": "SOP(C)-QC-008",
            "basis": "ChP",
            "criterion": "70%~130%",
            "unit": "%",
            "sample_0m": "102",
            "sample_3m": "98",
        },
        {
            "category": "4.含量",
            "item": "蛋白质含量\nProtein Content",
            "subitem": "蛋白浓度",
            "method": "UV法\nUV Method",
            "sop": "SOP(C)-QC-009",
            "basis": "ChP",
            "criterion": "10.0±1.0 mg/mL",
            "unit": "mg/mL",
            "sample_0m": "10.1",
            "sample_3m": "10.0",
        },
        {
            "category": "5.安全性",
            "item": "细菌内毒素\nBacterial Endotoxins",
            "subitem": "细菌内毒素",
            "method": "动态显色法\nKinetic-chromogenic Method",
            "sop": "ChP 1143",
            "basis": "ChP",
            "criterion": "<0.5 EU/mL",
            "unit": "EU/mL",
            "sample_0m": "<0.05",
            "sample_3m": "N/A",
        },
        {
            "category": "5.安全性",
            "item": "无菌检查\nSterility Test",
            "subitem": "无菌",
            "method": "薄膜过滤法\nMembrane Filtration Method",
            "sop": "ChP 1101",
            "basis": "ChP",
            "criterion": "无菌生长\nNo growth",
            "unit": "",
            "sample_0m": "符合规定",
            "sample_3m": "N/A",
        },
        {
            "category": "5.安全性",
            "item": "不溶性微粒\nSub-visible Particles",
            "subitem": "≥10μm",
            "method": "光阻法\nLight Obscuration Method",
            "sop": "ChP 0903",
            "basis": "ChP",
            "criterion": "≤6000 粒/支",
            "unit": "粒/支",
            "sample_0m": "18",
            "sample_3m": "24",
        },
        {
            "category": "6.理化与其它",
            "item": "外观\nAppearance",
            "subitem": "外观（ChP）",
            "method": "目视法\nVisual Method",
            "sop": "SOP(C)-QC-010",
            "basis": "ChP",
            "criterion": "无色至微黄色澄清至微乳光液体",
            "unit": "",
            "sample_0m": "无色澄清液体",
            "sample_3m": "无色澄清液体",
        },
        {
            "category": "6.理化与其它",
            "item": "pH值\npH",
            "subitem": "pH",
            "method": "pH值测定法\npH Electrode Method",
            "sop": "ChP 0631",
            "basis": "ChP",
            "criterion": "6.0±0.3",
            "unit": "",
            "sample_0m": "6.0",
            "sample_3m": "6.0",
        },
        {
            "category": "6.理化与其它",
            "item": "渗透压摩尔浓度\nOsmolality",
            "subitem": "渗透压摩尔浓度",
            "method": "冰点下降法\nFreezing Point Depression Method",
            "sop": "ChP 0632",
            "basis": "ChP",
            "criterion": "260~340 mOsmol/kg",
            "unit": "mOsmol/kg",
            "sample_0m": "298",
            "sample_3m": "301",
        },
    ]


def _style_cell(cell, *, font=BODY_FONT, fill=None, align="left", wrap=True, border=True):
    cell.font = font
    if fill is not None:
        cell.fill = fill
    cell.alignment = Alignment(horizontal=align, vertical="center", wrap_text=wrap)
    if border:
        cell.border = GRID_BORDER


def _add_category_dropdown(ws, cell_range: str):
    cats = ",".join(c["name_zh"] for c in load_seed_catalog()["categories"])
    dv = DataValidation(type="list", formula1=f'"{cats}"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(cell_range)


def _build_spec_xlsx() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "质量标准_6大类规范分项"
    ws.merge_cells("A1:H1")
    ws["A1"] = "质量标准模板（6大类规范分项版 / Specification Standard Template）"
    _style_cell(ws["A1"], font=TITLE_FONT, fill=SUBHEADER_FILL, align="center")
    ws.row_dimensions[1].height = 28

    meta_rows = [
        ("项目编号 / Project No.", "IPXXX", "产品名称 / Product Name", "IPXXX 原液（DS）", "材料阶段 / Scope", "DS", "标准编号 / Doc No.", "STP(C)-QC-IPXXX-2-001-1.0"),
        ("版本号 / Version", "1.0", "生效日期 / Effective Date", "2026-11-01", "药典体系 / Pharmacopoeia", "ChP / USP / EP", "单位规范 / Unit Std", "SOP(G)-RQA-0-001"),
    ]
    for r_idx, row in enumerate(meta_rows, 2):
        for c_idx, val in enumerate(row, 1):
            cell = ws.cell(r_idx, c_idx, val)
            _style_cell(cell, font=BOLD_FONT if c_idx % 2 == 1 else BODY_FONT, fill=META_LABEL_FILL if c_idx % 2 == 1 else None)
        ws.row_dimensions[r_idx].height = 22

    headers = ["大类\nCategory", "检验项目\nTest Item", "子检项/细项\nSub-item", "分析方法\nMethod", "SOP编号\nSOP No.", "药典体系\nPharmacopoeia", "标准限度\nSpecification", "单位\nUnit"]
    for c_idx, h in enumerate(headers, 1):
        _style_cell(ws.cell(5, c_idx, h), font=HEADER_FONT, fill=HEADER_FILL, align="center")
    ws.row_dimensions[5].height = 28

    rows = _sample_six_category_rows()
    for offset, item in enumerate(rows, 6):
        vals = [item["category"], item["item"], item["subitem"], item["method"], item["sop"], item["basis"], item["criterion"], item["unit"]]
        for c_idx, v in enumerate(vals, 1):
            _style_cell(ws.cell(offset, c_idx, v), fill=CAT_FILL if c_idx == 1 else None, align="center" if c_idx in (1, 5, 6, 8) else "left")
        ws.row_dimensions[offset].height = 28

    _add_category_dropdown(ws, f"A6:A{5 + len(rows) + 30}")
    widths = [16, 26, 22, 24, 16, 14, 34, 14]
    for idx, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(idx)].width = w
    ws.freeze_panes = "A6"

    # Second sheet: filling guide
    guide = wb.create_sheet("填表说明_最小改动规范")
    guide["A1"] = "质量标准模板最小改动填表要点（SOP(G)-RQA-0-001）"
    guide["A1"].font = TITLE_FONT
    notes = [
        "1. 「大类」列必须使用下拉菜单中的 6 个受控标准一级大类：1.鉴别、2.纯度和杂质、3.效价、4.含量、5.安全性、6.理化与其它。",
        "2. 同一检项含多个子结果（如 SEC-HPLC 的单体/HMW/LMW，或不同药典 ChP/USP 的澄清度）时，请逐行拆开并填写「子检项/细项」与「药典体系」。",
        "3. 单位请遵循 SOP(G)-RQA-0-001 规范：mg/mL（非 mg/ml）、mOsmol/kg（非 mOsm/kg）、EU/mL、cfu/10 mL、%。",
        "4. 上传本表至工作台「质量标准与 COA」后，系统可直接按 6 大类自动完成解析与登记，零手工拆行。",
    ]
    for idx, line in enumerate(notes, 3):
        guide.cell(idx, 1, line).font = BODY_FONT
    guide.column_dimensions["A"].width = 105

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _build_spec_docx() -> bytes:
    doc = Document()
    title = doc.add_heading("IPXXX 原液质量标准 / Specification of IPXXX Drug Substance", level=1)
    title.runs[0].font.size = Pt(14)

    doc.add_heading("审批页 / APPROVAL PAGE", level=2)
    app_tbl = doc.add_table(rows=5, cols=4)
    app_tbl.style = "Table Grid"
    app_headers = ["角色 / Role", "姓名 / Name", "部门/职务 / Department/Title", "签名/日期 / Signature/Date"]
    for i, h in enumerate(app_headers):
        app_tbl.rows[0].cells[i].text = h
    for ri, role in enumerate([
        ("起草人 / Author", "", "QC合规研究员 / QC Compliance", ""),
        ("审核人 / Reviewer", "", "QC运营经理 / QC Operation Manager", ""),
        ("审核人 / Reviewer", "", "QA主管 / QA Supervisor", ""),
        ("批准人 / Approver", "", "质量负责人 / Quality Head", ""),
    ], 1):
        for ci, val in enumerate(role):
            app_tbl.rows[ri].cells[ci].text = val

    doc.add_heading("1. 目的 PURPOSE", level=2)
    doc.add_paragraph("建立 IPXXX 原液（DS）的质量标准，确保放行与稳定性检验符合质量控制要求。\nEstablish the specification of IPXXX Drug Substance (DS).")

    doc.add_heading("2. 范围 SCOPE", level=2)
    doc.add_paragraph("本质量标准适用于 IPXXX 原液（DS）的放行检验与稳定性考察检验。\nThis specification applies to IPXXX Drug Substance (DS) release and stability testing.")

    doc.add_heading("3. 质量标准 SPECIFICATION（按 6 大类规范排布）", level=2)
    rows = _sample_six_category_rows()
    tbl = doc.add_table(rows=len(rows) + 1, cols=5)
    tbl.style = "Table Grid"
    spec_headers = ["大类 / Category", "检验项目 / Test Item", "方法 / Method", "方法编号 / SOP No.", "质量标准 / Specification"]
    for i, h in enumerate(spec_headers):
        tbl.rows[0].cells[i].text = h
    for ri, item in enumerate(rows, 1):
        crit = f"{item['subitem']}：{item['criterion']}" if item["subitem"] and item["subitem"] not in item["criterion"] else item["criterion"]
        vals = [item["category"], item["item"], item["method"], item["sop"], crit]
        for ci, v in enumerate(vals):
            tbl.rows[ri].cells[ci].text = v

    doc.add_heading("4. 变更历史 CHANGE HISTORY", level=2)
    ch = doc.add_table(rows=2, cols=2)
    ch.style = "Table Grid"
    ch.rows[0].cells[0].text = "版本号 / Version"
    ch.rows[0].cells[1].text = "变更内容 / Change Description"
    ch.rows[1].cells[0].text = "1.0"
    ch.rows[1].cells[1].text = "新起草（采用 6 大类最小改动规范版式） / New document."

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _build_coa_xlsx() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "放行检验报告_CoA"
    ws.merge_cells("A1:I1")
    ws["A1"] = "IPXXX 放行检验报告 / Certificate of Analysis (Minimal-Change Standard Template)"
    _style_cell(ws["A1"], font=TITLE_FONT, fill=SUBHEADER_FILL, align="center")
    ws.row_dimensions[1].height = 28

    meta = [
        ("品名 / Product Name", "IPXXX 注射液", "批号 / Lot/Batch No.", "IPXXXDPC20260101", "类别 / Category", "成品 / DP"),
        ("规格 / Strength", "10 mg/mL (1 mL/支)", "生产日期 / Mfg Date", "2026-01-15", "有效期至 / Expiry Date", "2028-01-14"),
        ("贮存条件 / Storage", "5±3℃，避光保存", "检验依据 / Specification", "STP(C)-QC-IPXXX-2-003-1.0", "报告编号 / Report No.", "CoA(C)-IPXXX-2601001-00"),
    ]
    for r_idx, row in enumerate(meta, 2):
        pairs = [(1, 2, row[0], row[1]), (4, 5, row[2], row[3]), (7, 8, row[4], row[5])]
        for lc, vc, lval, vval in pairs:
            _style_cell(ws.cell(r_idx, lc, lval), font=BOLD_FONT, fill=META_LABEL_FILL)
            ws.merge_cells(start_row=r_idx, start_column=vc, end_row=r_idx, end_column=vc + 1)
            _style_cell(ws.cell(r_idx, vc, vval))
            _style_cell(ws.cell(r_idx, vc + 1))
        ws.row_dimensions[r_idx].height = 22

    headers = ["大类\nCategory", "检验项目\nTest Item", "子检项/细项\nSub-item", "分析方法\nMethod", "药典体系\nBasis", "质量标准\nAcceptance Criteria", "放行检测结果\nRelease Result", "单位\nUnit", "结果判定\nEvaluation"]
    for c_idx, h in enumerate(headers, 1):
        _style_cell(ws.cell(6, c_idx, h), font=HEADER_FONT, fill=HEADER_FILL, align="center")
    ws.row_dimensions[6].height = 28

    rows = _sample_six_category_rows()
    for offset, item in enumerate(rows, 7):
        vals = [item["category"], item["item"], item["subitem"], item["method"], item["basis"], item["criterion"], item["sample_0m"], item["unit"], "符合规定 / Conform"]
        for c_idx, v in enumerate(vals, 1):
            _style_cell(ws.cell(offset, c_idx, v), fill=CAT_FILL if c_idx == 1 else None, align="center" if c_idx in (1, 5, 7, 8, 9) else "left")
        ws.row_dimensions[offset].height = 26

    _add_category_dropdown(ws, f"A7:A{6 + len(rows) + 25}")
    widths = [15, 24, 20, 22, 10, 30, 18, 12, 18]
    for idx, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(idx)].width = w
    ws.freeze_panes = "A7"

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _build_protocol_docx() -> bytes:
    doc = Document()
    doc.add_heading("IPXXX 稳定性考察方案 / Stability Study Protocol for IPXXX", level=1)
    doc.add_paragraph("文件编号 / Document No.: STB(C)-QC-IPXXX-P-2-001-1.0")

    doc.add_heading("1. 目的与范围 Purpose and Scope", level=2)
    doc.add_paragraph("本方案适用于常熟工厂 QC 实验室对 IPXXX 原液/成品开展的长期与加速稳定性考察，依据质量标准 STP(C)-QC-IPXXX-2-002-1.0 执行。")

    doc.add_heading("2. 样品信息 Samples", level=2)
    s_tbl = doc.add_table(rows=2, cols=5)
    s_tbl.style = "Table Grid"
    for i, h in enumerate(["品名 / Sample Name", "批号 / Batch No.", "工艺规模 / Process Scale", "包装及规格 / Package & Strength", "用途 / Usage"]):
        s_tbl.rows[0].cells[i].text = h
    for i, v in enumerate(["IPXXX 原液（DS）", "IPXXXDSC20260101", "2000L", "10 mg/mL，PC瓶", "正式稳定性考察"]):
        s_tbl.rows[1].cells[i].text = v

    doc.add_heading("3. 考察条件及时间 Study Conditions（结构化最小改动表）", level=2)
    c_tbl = doc.add_table(rows=3, cols=6)
    c_tbl.style = "Table Grid"
    for i, h in enumerate(["试验类别 / Study Type", "温度 / Temperature", "相对湿度 / RH", "放置方式 / Placement", "光照 / Light", "考察时间点 / Timepoints"]):
        c_tbl.rows[0].cells[i].text = h
    for ri, row in enumerate([
        ("长期试验 / Long-term", "-70℃±10℃", "N/A", "正置 / Upright", "避光 / Protect from light", "0M, 3M, 6M, 9M, 12M, 18M, 24M, 36M"),
        ("加速试验 / Accelerated", "5℃±3℃", "N/A", "正置 / Upright", "避光 / Protect from light", "0M, 1M, 3M, 6M"),
    ], 1):
        for ci, v in enumerate(row):
            c_tbl.rows[ri].cells[ci].text = v

    doc.add_heading("4. 长期稳定性取样与检测计划 Sampling Plan Matrix（长期 -70℃±10℃）", level=2)
    tp_headers = ["大类 / Category", "考察项目（方法） / Test Item (Method)", "0M", "3M", "6M", "12M", "18M", "24M", "36M"]
    rows = _sample_six_category_rows()[:10]
    m_tbl = doc.add_table(rows=len(rows) + 1, cols=len(tp_headers))
    m_tbl.style = "Table Grid"
    for i, h in enumerate(tp_headers):
        m_tbl.rows[0].cells[i].text = h
    for ri, item in enumerate(rows, 1):
        item_label = f"{item['item'].splitlines()[0]}（{item['method'].splitlines()[0]}）"
        vals = [item["category"], item_label, "引用放行", "√", "√", "√", "√", "√", "√"]
        for ci, v in enumerate(vals):
            m_tbl.rows[ri].cells[ci].text = v

    doc.add_heading("5. 取样窗口 Sampling Window", level=2)
    w_tbl = doc.add_table(rows=3, cols=2)
    w_tbl.style = "Table Grid"
    w_tbl.rows[0].cells[0].text = "试验类别 / Study Type"
    w_tbl.rows[0].cells[1].text = "取样窗口 / Sampling Window"
    w_tbl.rows[1].cells[0].text = "长期试验 / Long-term"
    w_tbl.rows[1].cells[1].text = "<12M：±7天；≥12M：±14天；最后一个时间点不得提前取样。"
    w_tbl.rows[2].cells[0].text = "加速试验 / Accelerated"
    w_tbl.rows[2].cells[1].text = "不得提前取样，最晚不超过计划日期后 4 天。"

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _populate_t1_t5_matrix_sheet(ws, *, sheet_title: str, study_type: str, sample_name: str, batch: str, temp: str, rh: str, placement: str, light: str, timepoints: list[str]):
    ws.title = sheet_title
    last_col = 5 + len(timepoints)
    last_letter = get_column_letter(last_col)
    ws.merge_cells(f"A1:{last_letter}1")
    ws["A1"] = f"IPXXX {sample_name} {study_type} 稳定性研究表（T1~T5 最小改动规范模板）"
    _style_cell(ws["A1"], font=TITLE_FONT, fill=SUBHEADER_FILL, align="center")
    ws.row_dimensions[1].height = 26

    # Row 2: Project, Product, Batch, Strength
    r2 = [
        ("项目编号", "IPXXX"), ("产品名称", sample_name), ("产品批号", batch), ("规格", "10 mg/mL"),
    ]
    col = 1
    for label, val in r2:
        _style_cell(ws.cell(2, col, label), font=BOLD_FONT, fill=META_LABEL_FILL, align="center")
        _style_cell(ws.cell(2, col + 1, val))
        col += 2
    ws.row_dimensions[2].height = 22

    # Row 3 (T1): Split condition fields (no Wingdings2 R!) + Study Type + Protocol No.
    r3 = [
        ("稳定性类型", f"☑{study_type}"), ("试验方案编号", "STB(C)-QC-IPXXX-P-2-001"),
        ("温度", temp), ("相对湿度", rh), ("放置方式", placement), ("光照", light),
    ]
    col = 1
    for label, val in r3:
        if col + 1 <= max(last_col, 12):
            _style_cell(ws.cell(3, col, label), font=BOLD_FONT, fill=META_LABEL_FILL, align="center")
            _style_cell(ws.cell(3, col + 1, val))
            col += 2
    ws.row_dimensions[3].height = 22

    # Row 4-5 (T2, T3, T4): Table header + clean timepoint row
    fixed_headers = [
        (1, "大类\nCategory"),
        (2, "检验项目\nTest Item"),
        (3, "分析方法\nMethod"),
        (4, "子检项/细项（含药典）\nSub-item / Basis"),
        (5, "可接受标准\nAcceptance Criteria"),
    ]
    for c_idx, h in fixed_headers:
        ws.merge_cells(start_row=4, start_column=c_idx, end_row=5, end_column=c_idx)
        _style_cell(ws.cell(4, c_idx, h), font=HEADER_FONT, fill=HEADER_FILL, align="center")
        _style_cell(ws.cell(5, c_idx), font=HEADER_FONT, fill=HEADER_FILL, align="center")
    ws.merge_cells(start_row=4, start_column=6, end_row=4, end_column=last_col)
    _style_cell(ws.cell(4, 6, "考察时间（月） / Study Timepoints (Month)"), font=HEADER_FONT, fill=HEADER_FILL, align="center")
    for c_idx in range(6, last_col + 1):
        _style_cell(ws.cell(4, c_idx), font=HEADER_FONT, fill=HEADER_FILL, align="center")
        _style_cell(ws.cell(5, c_idx, timepoints[c_idx - 6]), font=HEADER_FONT, fill=HEADER_FILL, align="center")
    ws.row_dimensions[4].height = 24
    ws.row_dimensions[5].height = 22

    # Row 6: Planned dates (EDATE)
    _style_cell(ws.cell(6, 5, "计划日期（辅助行）"), font=HINT_FONT, fill=META_LABEL_FILL, align="right")
    for idx_tp, tp in enumerate(timepoints):
        c_idx = 6 + idx_tp
        if idx_tp == 0:
            ws.cell(6, c_idx, "2026-06-01")
        else:
            months_num = int("".join(ch for ch in tp if ch.isdigit()) or "0")
            ws.cell(6, c_idx, f"=EDATE($F$6,{months_num})" if tp.endswith("M") else "2026-06-15")
        _style_cell(ws.cell(6, c_idx), font=HINT_FONT, fill=META_LABEL_FILL, align="center")
    ws.row_dimensions[6].height = 20

    # Row 7 (T4): Dedicated signing/progress status row
    _style_cell(ws.cell(7, 5, "签署状态（辅助行）"), font=HINT_FONT, fill=STATUS_FILL, align="right")
    for idx_tp in range(len(timepoints)):
        c_idx = 6 + idx_tp
        val = "已签报告" if idx_tp == 0 else ("进行中" if idx_tp == 1 else "待检")
        _style_cell(ws.cell(7, c_idx, val), font=HINT_FONT, fill=STATUS_FILL, align="center")
    ws.row_dimensions[7].height = 20

    # Data rows (Row 8+)
    rows = _sample_six_category_rows()
    for offset, item in enumerate(rows, 8):
        sub_label = f"{item['subitem']} ({item['basis']})" if item["basis"] and item["basis"] != "ChP" else item["subitem"]
        vals = [item["category"], item["item"], item["method"], sub_label, item["criterion"]]
        for c_idx, v in enumerate(vals, 1):
            _style_cell(ws.cell(offset, c_idx, v), fill=CAT_FILL if c_idx == 1 else None, align="center" if c_idx == 1 else "left")
        for idx_tp in range(len(timepoints)):
            c_idx = 6 + idx_tp
            val = item["sample_0m"] if idx_tp == 0 else (item["sample_3m"] if idx_tp == 1 else "")
            _style_cell(ws.cell(offset, c_idx, val), align="center")
        ws.row_dimensions[offset].height = 26

    _add_category_dropdown(ws, f"A8:A{7 + len(rows) + 25}")

    # T5: Structured 3-column AR/AE exception mini-table
    footer_start = 8 + len(rows) + 1
    ws.merge_cells(f"A{footer_start}:E{footer_start}")
    _style_cell(ws.cell(footer_start, 1, "异常与调查记录（AR/AE 结构化小表 — 请勿用自由长段落写表尾备注）"), font=BOLD_FONT, fill=SUBHEADER_FILL)
    ws.row_dimensions[footer_start].height = 22
    ar_headers = [(1, "时间点"), (2, "对应检项"), (3, "AR/AE编号与说明")]
    for c_idx, h in ar_headers:
        _style_cell(ws.cell(footer_start + 1, c_idx, h), font=BOLD_FONT, fill=META_LABEL_FILL, align="center")
    ws.merge_cells(start_row=footer_start + 1, start_column=3, end_row=footer_start + 1, end_column=5)
    _style_cell(ws.cell(footer_start + 2, 1, "示例：3M"), font=HINT_FONT, align="center")
    _style_cell(ws.cell(footer_start + 2, 2, "电荷变异体 · 酸性峰"), font=HINT_FONT)
    ws.merge_cells(start_row=footer_start + 2, start_column=3, end_row=footer_start + 2, end_column=5)
    _style_cell(ws.cell(footer_start + 2, 3, "AR-C-IPXXX-2026001（说明：如无异常请删除本示例行）"), font=HINT_FONT)

    widths = [15, 24, 22, 22, 32] + [14] * len(timepoints)
    for idx, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(idx)].width = w
    ws.freeze_panes = "F8"


def _build_worksheet_xlsx() -> bytes:
    wb = Workbook()
    _populate_t1_t5_matrix_sheet(
        wb.active,
        sheet_title="长期DS(-70℃)",
        study_type="长期试验",
        sample_name="IPXXX 原液（DS）",
        batch="IPXXXDSC20260101",
        temp="-70℃±10℃",
        rh="N/A",
        placement="正置",
        light="避光",
        timepoints=["0M", "3M", "6M", "9M", "12M", "18M", "24M", "36M"],
    )
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _build_summary_xlsx() -> bytes:
    wb = Workbook()
    idx_ws = wb.active
    idx_ws.title = "项目与批次索引"
    idx_ws.merge_cells("A1:H1")
    idx_ws["A1"] = "稳定性数据汇总表 — 项目与批次总览（多条件 T1~T5 规范版）"
    _style_cell(idx_ws["A1"], font=TITLE_FONT, fill=SUBHEADER_FILL, align="center")
    idx_ws.row_dimensions[1].height = 28

    headers = ["工作表名", "项目编号", "样品阶段", "批号", "方案编号", "稳定性类型", "考察条件", "0M开始日期"]
    for c_idx, h in enumerate(headers, 1):
        _style_cell(idx_ws.cell(3, c_idx, h), font=HEADER_FONT, fill=HEADER_FILL, align="center")
    entries = [
        ("长期DS(-70℃)", "IPXXX", "DS", "IPXXXDSC20260101", "STB(C)-QC-IPXXX-P-2-001", "长期试验", "-70℃±10℃ · 正置 · 避光", "2026-06-01"),
        ("加速DS(5℃)", "IPXXX", "DS", "IPXXXDSC20260101", "STB(C)-QC-IPXXX-P-2-001", "加速试验", "5℃±3℃ · 正置 · 避光", "2026-06-01"),
        ("长期DP(5℃)", "IPXXX", "DP", "IPXXXDPC20260101", "STB(C)-QC-IPXXX-P-2-002", "长期试验", "5℃±3℃ · 倒置 · 避光", "2026-06-01"),
        ("加速DP(25℃)", "IPXXX", "DP", "IPXXXDPC20260101", "STB(C)-QC-IPXXX-P-2-002", "加速试验", "25℃±2℃ · 60%RH±5%RH · 倒置 · 避光", "2026-06-01"),
    ]
    for r_idx, row in enumerate(entries, 4):
        for c_idx, v in enumerate(row, 1):
            _style_cell(idx_ws.cell(r_idx, c_idx, v), align="center" if c_idx in (2, 3, 6, 8) else "left")
        idx_ws.row_dimensions[r_idx].height = 22
    for c_idx, w in enumerate([16, 12, 12, 22, 26, 14, 34, 15], 1):
        idx_ws.column_dimensions[get_column_letter(c_idx)].width = w

    _populate_t1_t5_matrix_sheet(wb.create_sheet(), sheet_title="长期DS(-70℃)", study_type="长期试验", sample_name="IPXXX 原液（DS）", batch="IPXXXDSC20260101", temp="-70℃±10℃", rh="N/A", placement="正置", light="避光", timepoints=["0M", "3M", "6M", "12M", "18M", "24M", "36M"])
    _populate_t1_t5_matrix_sheet(wb.create_sheet(), sheet_title="加速DS(5℃)", study_type="加速试验", sample_name="IPXXX 原液（DS）", batch="IPXXXDSC20260101", temp="5℃±3℃", rh="N/A", placement="正置", light="避光", timepoints=["0M", "1M", "3M", "6M"])
    _populate_t1_t5_matrix_sheet(wb.create_sheet(), sheet_title="长期DP(5℃)", study_type="长期试验", sample_name="IPXXX 成品（DP）", batch="IPXXXDPC20260101", temp="5℃±3℃", rh="N/A", placement="倒置", light="避光", timepoints=["0M", "3M", "6M", "12M", "18M", "24M"])
    _populate_t1_t5_matrix_sheet(wb.create_sheet(), sheet_title="加速DP(25℃)", study_type="加速试验", sample_name="IPXXX 成品（DP）", batch="IPXXXDPC20260101", temp="25℃±2℃", rh="60%RH±5%RH", placement="倒置", light="避光", timepoints=["0M", "1M", "3M", "6M"])

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _build_confirmation_xlsx() -> bytes:
    """Build TPL-07 using the exact same renderer as real confirmation exports (100% identical layout)."""
    from .worksheet_confirmation import TEMPLATE_VERSION, render_confirmation_bytes

    cat_order_map = {
        "1.鉴别": (1, "identification"),
        "2.纯度和杂质": (2, "purity_impurity"),
        "3.效价": (3, "potency"),
        "4.含量": (4, "content"),
        "5.安全性": (5, "safety"),
        "6.理化与其它": (6, "physicochemical_other"),
    }
    sample_rows = _sample_six_category_rows()
    tps = [
        ("pt_0m", "0M", "2026-06-01"),
        ("pt_3m", "3M", "2026-09-01"),
        ("pt_6m", "6M", "2026-12-01"),
        ("pt_12m", "12M", "2027-06-01"),
        ("pt_18m", "18M", "2027-12-01"),
        ("pt_24m", "24M", "2028-06-01"),
    ]
    rows = []
    cells = []
    results = {}
    all_row_ids = []
    for idx, item in enumerate(sample_rows, 1):
        rid = f"tpl_r{idx}"
        all_row_ids.append(rid)
        cat_order, cat_code = cat_order_map.get(item["category"], (99, "other"))
        rows.append(
            {
                "id": rid,
                "label": item["item"].splitlines()[0],
                "method": item["method"].splitlines()[0],
                "method_version": "",
                "unit": item["unit"],
                "reporting_basis": item["basis"],
                "criteria_text": item["criterion"].splitlines()[0],
                "category_code": cat_code,
                "category_name_zh": item["category"],
                "subcategory_name_zh": item["subitem"],
                "category_order": cat_order,
                "display_order": idx,
            }
        )
        for pt_id, sample_key in (("pt_0m", "sample_0m"), ("pt_3m", "sample_3m")):
            raw_val = item[sample_key]
            if not raw_val:
                continue
            vid = f"tpl_v_{idx}_{pt_id}"
            cells.append({"row_id": rid, "point_id": pt_id, "adopted": {"result_version_id": vid}})
            results[vid] = {
                "result": {
                    "status": "text",
                    "raw_text": f"{raw_val}%" if item["unit"] == "%" and raw_val not in {"N/A", "ND"} else raw_val,
                    "unit": item["unit"],
                }
            }
    points = [
        {"id": pt_id, "label": label, "planned_date": planned_date, "required_row_ids": list(all_row_ids)}
        for pt_id, label, planned_date in tps
    ]
    snapshot = {
        "snapshot_id": "TEMPLATE-BLANK-TPL07",
        "project_name": "IPXXX 成品（DP）",
        "template_version": TEMPLATE_VERSION,
        "worksheet": {
            "project": "IPXXX",
            "name": "IPXXX 24M 阶段性确认",
            "product_name": "IPXXX 成品（DP）",
            "summary_number": "STB(C)-IPXXX-2026001",
            "document_version": "00",
            "plan_version": 1,
            "start_date": "2026-06-01",
            "location": "常熟工厂C",
            "protocol_number": "STB(C)-QC-IPXXX-P-2-002",
            "specification_text": "10 mg/mL",
            "equipment": "A-8111N",
            "storage_location": "常熟稳定性箱 5℃-01",
            "basis": "24M 阶段性确认模板示例",
            "identity": {
                "batch": "IPXXXDPC20260101",
                "sample_type": "DP",
                "presentation": "10 mg/mL",
                "study": "long_term",
                "study_id": "STB(C)-QC-IPXXX-P-2-002",
                "condition": "5℃±3℃ · 避光",
                "orientation": "倒置",
                "reporting_basis": "",
            },
            "points": points,
            "rows": rows,
            "cells": cells,
            "events": [],
        },
        "results": results,
    }
    return render_confirmation_bytes(snapshot)


_BUILDERS = {
    "spec_xlsx": _build_spec_xlsx,
    "spec_docx": _build_spec_docx,
    "coa_xlsx": _build_coa_xlsx,
    "protocol_docx": _build_protocol_docx,
    "worksheet_xlsx": _build_worksheet_xlsx,
    "summary_xlsx": _build_summary_xlsx,
    "confirmation_xlsx": _build_confirmation_xlsx,
}


def list_templates() -> list[dict[str, Any]]:
    return [dict(item) for item in TEMPLATE_SPECS]


def render_template(template_id: str) -> tuple[str, str, bytes]:
    """Return (filename, media_type, content_bytes) for a standard template."""
    spec = next((item for item in TEMPLATE_SPECS if item["id"] == template_id), None)
    if spec is None or template_id not in _BUILDERS:
        raise KeyError(f"Unknown template_id: {template_id}")
    content = _BUILDERS[template_id]()
    media = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if spec["format"] == "xlsx"
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    return spec["filename"], media, content


def export_template_pack(target_dir: Path) -> list[Path]:
    """Generate the complete minimal-change standard template pack to disk for SME / supervisor review."""
    target_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for spec in TEMPLATE_SPECS:
        filename, _, payload = render_template(spec["id"])
        dest = target_dir / filename
        dest.write_bytes(payload)
        written.append(dest)
    return written
