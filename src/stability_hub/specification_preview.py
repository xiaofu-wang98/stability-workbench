"""Read bilingual specification tables while preserving individual criterion rows."""
import json
import re
from io import BytesIO

from docx import Document
from lxml import html

from .protocol_preview import _cell_text
from .workflow_common import WorkflowError

DOCUMENT_NUMBER = re.compile(
    r"(?<![A-Z0-9])STP(?:\([A-Z]+\))?[-–]QC[-–][A-Z0-9]+[-–]\d+[-–]\d+(?:[-–]\d+\.\d+)?(?![A-Z0-9])",
    re.I,
)

# 稳定性与质量标准行业领域字典（涵盖单抗、ADC、双抗、融合蛋白等生物药常见检项）
DOMAIN_ASSAY_DEFAULTS = [
    # 纯度与杂质
    (r"SEC[-_ ]?HPLC|体积排阻", {"unit": "%", "type": "numeric"}),
    (r"CE[-_ ]?SDS|毛细管凝胶电泳", {"unit": "%", "type": "numeric"}),
    (r"CEX[-_ ]?HPLC|阳离子交换|电荷变异体", {"unit": "%", "type": "numeric"}),
    (r"单体|聚合体|高分子量|低分子量|片段|主峰|酸性|碱性|非糖基化|HMW|LMW|NGHC", {"unit": "%", "type": "numeric"}),

    # 活性
    (r"结合活性|相对结合活性|Binding Activity", {"unit": "%", "type": "numeric", "comp": "相对结合活性"}),
    (r"生物学活性|细胞杀伤|杀伤法|相对效价|Biological Activity", {"unit": "%", "type": "numeric", "comp": "相对效价"}),

    # 鉴别
    (r"肽图|Peptide Mapping", {"unit": "", "type": "qualitative", "comp": "图谱一致"}),
    (r"等电点|icIEF|Isoelectric", {"unit": "", "type": "numeric", "comp": "主峰pI"}),

    # 含量与浓度
    (r"蛋白浓度|蛋白质含量|Protein (?:Concentration|Content)", {"unit": "mg/ml", "type": "numeric", "comp": "蛋白浓度"}),

    # 理化
    (r"^pH$|^pH值", {"unit": "", "type": "numeric", "comp": "pH"}),
    (r"渗透压|Osmolality", {"unit": "mOsmol/kg", "type": "numeric", "comp": "渗透压摩尔浓度"}),
    (r"颜色|Color", {"unit": "", "type": "qualitative", "comp": "颜色"}),
    (r"澄清度|Clarification|Turbidity", {"unit": "", "type": "qualitative", "comp": "澄清度"}),
    (r"外观|Appearance", {"unit": "", "type": "qualitative", "comp": "外观"}),
    (r"可见异物|不溶性微粒|Subvisible", {"unit": "粒/支", "type": "numeric", "comp": "不溶性微粒"}),

    # 残留与安全性
    (r"外源性DNA|宿主细胞DNA|DNA残留|Residual Exogenous DNA|Host Cell DNA", {"unit": "ppm", "type": "numeric", "comp": "宿主细胞DNA残留"}),
    (r"宿主细胞蛋白|HCP|Residual Host Cell", {"unit": "ppm", "type": "numeric", "comp": "宿主细胞蛋白残留"}),
    (r"蛋白A残留|Protein A", {"unit": "ppm", "type": "numeric", "comp": "蛋白A残留"}),
    (r"细菌内毒素|Bacterial Endotoxins", {"unit": "EU/ml", "type": "numeric", "comp": "细菌内毒素"}),
    (r"微生物限度|Bioburden", {"unit": "cfu/10ml", "type": "numeric", "comp": "微生物限度"}),
    (r"无菌|Sterility", {"unit": "", "type": "qualitative", "comp": "无菌"}),

    # ADC 特有
    (r"DAR|平均偶联率", {"unit": "", "type": "numeric", "comp": "DAR值"}),
    (r"游离小分子|游离毒素|Free Drug", {"unit": "μg/ml", "type": "numeric", "comp": "游离毒素残留"}),
]


def split_bilingual(text):
    if not text:
        return "", ""
    cleaned = re.sub(r'\s+', ' ', text.replace('\n', ' ')).strip()
    last_zh = [m.end() for m in re.finditer(r'[\u4e00-\u9fa5]', cleaned)]
    if last_zh:
        pos = last_zh[-1]
        zh = cleaned[:pos].strip()
        en = cleaned[pos:].strip()
        return zh, en
    return text.strip(), text.strip()


def standard_numbers(text):
    return list(dict.fromkeys(match.upper().replace("–", "-") for match in DOCUMENT_NUMBER.findall(text)))


def split_number(value):
    return re.sub(r"-\d+\.\d+$", "", value)


def protocol_numbers(draft):
    parsed = json.loads(draft["preview_json"])
    if "standard_numbers" in parsed:
        return parsed["standard_numbers"]
    from .protocol_preview import inspect_protocol
    return inspect_protocol(draft["source"])["standard_numbers"]


def specification_material_scope(filename, preview):
    declared = preview.get("material_scope", "unspecified")
    if declared != "unspecified":
        return declared
    title = filename + " " + preview.get("scope", "")
    if re.search(r"参考品|对照品|\breference standard\b", title, re.I):
        return "reference"
    categories = {
        category for category, pattern in {
            "DS": r"原液|\bdrug substance\b|(?<![A-Za-z0-9])DS(?![A-Za-z0-9])",
            "DP": r"制剂|注射用|\bdrug product\b|\binjection\b|(?<![A-Za-z0-9])DP(?![A-Za-z0-9])",
            "placebo": r"安慰剂|\bplacebo\b",
        }.items() if re.search(pattern, title, re.I)
    }
    return next(iter(categories)) if len(categories) == 1 else "unspecified"


def matching_standards(conn, project, numbers, required_scope="unspecified"):
    found = []
    current = {row["specification_id"] for row in conn.execute(
        "SELECT specification_id FROM current_specifications WHERE project=?", (project,))}
    for source in conn.execute("SELECT id,filename,preview_json FROM specifications ORDER BY id"):
        preview = json.loads(source["preview_json"])
        if preview.get("project") and preview["project"] != project:
            continue
        # 严格防误配：在匹配标准时，校验标准的 material_scope（严禁把 reference 参考品标准 2-004 自动套用到 DS/DP 方案上）
        mat_scope = specification_material_scope(source["filename"], preview)
        if required_scope != "unspecified" and mat_scope not in (required_scope, "unspecified"):
            continue

        candidates = standard_numbers(source["filename"])
        for number in numbers:
            if not any(candidate == number or split_number(candidate) == number for candidate in candidates):
                continue
            links = [dict(row) for row in conn.execute("""
                SELECT table_number AS 'table',row_number AS 'row',item_id
                FROM specification_assay_components WHERE project=? AND specification_id=?
                ORDER BY table_number,row_number
            """, (project, source["id"]))]
            criteria = {(row["table"], row["row"]): row for row in preview.get("rows", [])}
            for link in links:
                row = criteria.get((link["table"], link["row"]), {})
                link["assay_names"] = [row[key] for key in ("item_zh", "item_en", "item") if row.get(key)]
                link["method"] = row.get("method", "")
            pages = {row["page"] for row in preview.get("rows", []) if row.get("page")}
            found.append({"number": number, "id": source["id"], "filename": source["filename"], "links": links,
                          "current": source["id"] in current, "exact_version": number in candidates and split_number(number) != number,
                          "material_scope": mat_scope, "scan_id": preview.get("scan_id"),
                          "page": next(iter(pages)) if len(pages) == 1 else None})
    return found


def unambiguous_standard_sources(sources):
    if len(sources) < 2:
        return sources
    selected = [source for source in sources if source.get("exact_version")]
    if not selected:
        selected = [source for source in sources if source.get("current")]
    if len(selected) == 1:
        source = selected[0]
        if not source.get("scan_id"):
            return selected
        sources = [entry for entry in sources if entry.get("scan_id") == source["scan_id"]
                   and entry["material_scope"] == source["material_scope"]]
        if len(sources) == 1:
            return sources
    scan_ids = {source["scan_id"] for source in sources}
    scopes = {source["material_scope"] for source in sources}
    pages = {source["page"] for source in sources}
    if len(scan_ids) == len(scopes) == 1 and None not in scan_ids and None not in pages and len(pages) == len(sources):
        return sources
    return []


def component_suggestion(criterion, category="", item_name=""):
    """Read explicit leading result labels, units, pharmacopoeia basis, and qualitative/numeric distinctions."""
    text = criterion.strip()
    if not text:
        return {}

    lines = [line.strip() for line in text.split('\n') if line.strip()]
    first_line = lines[0]

    reporting_basis = ""
    # 2. 药典体系（reporting_basis）自动剥离（支持前缀 EP: 及后缀/括号 (ChP)、（中国药典）等）
    basis_match = re.match(r"^(EP|USP|ChP|CN|US)\s*[：:]\s*(.+)$", first_line, re.I)
    if basis_match:
        reporting_basis = basis_match.group(1).upper()
        first_line = basis_match.group(2).strip()
    elif re.search(r"[（(](?:中国药典|ChP)[）)]", text, re.I):
        reporting_basis = "ChP"
    elif re.search(r"[（(](?:欧洲药典|EP)[）)]", text, re.I):
        reporting_basis = "EP"
    elif re.search(r"[（(](?:美国药典|USP)[）)]", text, re.I):
        reporting_basis = "USP"

    # 1. 带冒号的前缀提取（不仅限百分比，支持多行中英对照及报告数据/报告结果）
    prefix_match = re.match(r"^([^\n：:]{1,40}?)\s*[：:]\s*(.+)$", first_line)
    if prefix_match:
        prefix = prefix_match.group(1).strip()
        body = prefix_match.group(2).strip()

        # 尝试从第二行提取英文子结果名（如果有）
        comp_en = ""
        if len(lines) > 1:
            en_match = re.match(r"^([^\n：:]{1,40}?)\s*[：:]\s*(.+)$", lines[1])
            if en_match:
                comp_en = en_match.group(1).strip()

        unit = ""
        # 提取括号中的单位，如 (%) 或 (mg/ml) 或 ppm
        unit_match = re.search(r"[（\(](%|％|mg/ml|mg/mL|μg/ml|ug/ml|ppm|ppb|EU/mg|EU/ml|cfu/(?:10\s*ml|100\s*ml|ml)|mOsmol/kg|NTU)[）\)]", prefix, re.I)
        if unit_match:
            unit = "%" if unit_match.group(1) in ("%", "％") else unit_match.group(1)
            prefix = re.sub(r"[（\(](%|％|mg/ml|mg/mL|μg/ml|ug/ml|ppm|ppb|EU/mg|EU/ml|cfu/(?:10\s*ml|100\s*ml|ml)|mOsmol/kg|NTU)[）\)]", "", prefix).strip()
        elif "%" in prefix or "％" in prefix:
            unit = "%"
            prefix = prefix.replace("%", "").replace("％", "").strip()

        if comp_en:
            comp_en = comp_en.replace("%", "").replace("％", "").strip()
            # 剥离英文中括号单位
            comp_en = re.sub(r"[（\(].*?[）\)]", "", comp_en).strip()

        # 分离单行混排的中英文，例如 "主峰 Main Peak" 或 "高分子量组分 HMW"
        comp_zh = prefix
        if not comp_en:
            en_part = re.search(r"([A-Za-z][A-Za-z0-9 /()_-]*)$", prefix)
            if en_part and en_part.group(1).strip() != prefix:
                comp_en = en_part.group(1).strip()
                comp_zh = prefix[:en_part.start()].strip()

        all_text = " ".join(lines)
        is_qual = bool(re.search(r"图谱一致|图谱|曲线相似|比色液|黄色\s*\d+\s*号|标准比色液|浊度标准液|浊度液|无色|澄清|微乳光|澄清至|乳光|无菌|不得深于|不得大于[0-9IVX]+号|深于|浅于", all_text, re.I))
        has_num = bool(re.search(r"\d|[%％~～≤≥<>]|报告(结果|数据)|Report (?:result|data)", all_text, re.I))
        if is_qual and not re.search(r"[%％~～]|(?:≤|≥|<|>)\s*\d+(?:\.\d+)?\s*(?:mg|ppm|EU|%|cfu|NTU)", all_text, re.I):
            res_type = "qualitative"
        elif has_num:
            res_type = "numeric"
        else:
            res_type = "qualitative"

        if not unit:
            u_body = re.search(r"(ppm|ppb|mg/ml|mg/mL|μg/ml|ug/ml|%|％|EU/mg|EU/ml|cfu/(?:10\s*ml|100\s*ml|ml)|mOsmol/kg|NTU)", all_text, re.I)
            if u_body:
                u_raw = u_body.group(1)
                unit = "%" if u_raw in ("%", "％") else ("cfu/10ml" if "cfu" in u_raw.lower() else u_raw)

        res = {
            "component": comp_zh,
            "component_en": comp_en,
            "unit": unit,
            "result_type": res_type,
        }
        if reporting_basis:
            res["reporting_basis"] = reporting_basis
        return res

    # 无前缀冒号的情况（例如：<1 ppm，不得检出，与参考品图谱一致）
    unit_match = re.search(r"(ppm|ppb|mg/ml|mg/mL|μg/ml|ug/ml|%|％|EU/mg|EU/ml|cfu/(?:10\s*ml|100\s*ml|ml)|mOsmol/kg|NTU)", text, re.I)
    if unit_match:
        u_raw = unit_match.group(1)
        unit = "%" if u_raw in ("%", "％") else ("cfu/10ml" if "cfu" in u_raw.lower() else u_raw)
    else:
        unit = ""

    is_qual = bool(re.search(r"图谱一致|图谱|曲线相似|比色液|黄色\s*\d+\s*号|标准比色液|浊度标准液|浊度液|无色|澄清|微乳光|澄清至|乳光|无菌|不得深于|不得大于[0-9IVX]+号|深于|浅于", text, re.I))
    has_num = bool(re.search(r"\d|[%％~～≤≥<>]|报告(结果|数据)|Report (?:result|data)|不少于|不高于|不得超过", text, re.I))
    if is_qual and not re.search(r"[%％~～]|(?:≤|≥|<|>)\s*\d+(?:\.\d+)?\s*(?:mg|ppm|EU|%|cfu|NTU)", text, re.I):
        res_type = "qualitative"
    elif has_num:
        res_type = "numeric"
    else:
        res_type = "qualitative"

    res = {
        "result_type": res_type,
        "unit": unit,
    }
    if reporting_basis:
        res["reporting_basis"] = reporting_basis
    return res


def inspect_specification(data, filename=""):
    document = Document(BytesIO(data))
    paragraphs = [p.text.strip() for p in document.paragraphs if p.text.strip()]
    scope = next((paragraphs[index + 1] for index, text in enumerate(paragraphs[:-1])
                  if re.search(r"范围\s*SCOPE", text, re.I)), "")
    if not scope:
        scope = next((text for text in paragraphs if re.search(r"范围\s*SCOPE", text, re.I)), "")
    rows = []
    for table_index, table in enumerate(document.tables, 1):
        if not table.rows:
            continue
        header = [_cell_text(cell) for cell in table.rows[0].cells]
        criterion = next((i for i, value in enumerate(header)
                          if re.search(r"质量标准|specification|acceptance criteria", value, re.I)), None)
        method = next((i for i, value in enumerate(header)
                       if re.fullmatch(r"方法(?:\s*method)?|method", value, re.I)), None)
        sop = next((i for i, value in enumerate(header) if re.search(r"方法编号|SOP No", value, re.I)), None)
        if criterion is None or method is None or method >= criterion:
            continue
        for row_index, row in enumerate(table.rows[1:], 2):
            values = [_cell_text(cell) for cell in row.cells]
            if len(values) <= criterion or not values[criterion]:
                continue
            labels = list(dict.fromkeys(value for value in values[:method] if value))
            rows.append({
                "table": table_index, "row": row_index, "column": criterion + 1,
                "category": labels[0] if len(labels) > 1 else "",
                "item": labels[-1] if labels else "",
                "method": values[method],
                "sop": values[sop] if sop is not None and sop < len(values) else "",
                "criterion": values[criterion],
                **component_suggestion(values[criterion], category=labels[0] if len(labels) > 1 else "", item_name=labels[-1] if labels else ""),
            })

    # 提取检项与方法的中英文
    for r in rows:
        item_zh, item_en = split_bilingual(r.get("item") or r.get("category", ""))
        method_zh, method_en = split_bilingual(r.get("method", ""))
        r["item_zh"] = item_zh
        r["item_en"] = item_en
        r["method_zh"] = method_zh
        r["method_en"] = method_en

    # 3. 同名检项“定性 vs 定量”与药典体系多行冲突消解
    from collections import defaultdict
    groups = defaultdict(list)
    for r in rows:
        key = (r.get("item", "").strip(), r.get("method", "").strip())
        groups[key].append(r)

    for key, item_rows in groups.items():
        if len(item_rows) > 1:
            # 药典体系区分（如 颜色 ChP 与 颜色 EP、澄清度 ChP 与 澄清度 EP）
            bases = [r.get("reporting_basis") for r in item_rows if r.get("reporting_basis")]
            if len(bases) > 1:
                for r in item_rows:
                    b = r.get("reporting_basis")
                    if b and not r.get("component"):
                        r["component"] = f"{r['item_zh']} ({b})"
                        r["component_en"] = f"{r['item_en']} ({b})" if r.get("item_en") else ""

            # 检查是否既有 qualitative 又有 numeric，且没有明确的 component 前缀
            unnamed_rows = [r for r in item_rows if not r.get("component")]
            if len(unnamed_rows) > 1:
                types = {r.get("result_type", "numeric") for r in unnamed_rows}
                if len(types) > 1 or any(r.get("result_type") == "qualitative" for r in unnamed_rows):
                    for r in unnamed_rows:
                        crit = r.get("criterion", "")
                        cat = r.get("category", "")
                        # P0-5 语义修正：如果包含明确的数值限值/差值（如 pI差值≤0.2），即使大类属于鉴别，也属于数值型结果
                        has_numeric_diff = bool(re.search(r"差值.*[≤≥<>]|pI.*[≤≥<>]|[≤≥<>]\s*\d+(?:\.\d+)?", crit, re.I))
                        if not has_numeric_diff and (r.get("result_type") == "qualitative" or "曲线相似" in crit or "图谱一致" in crit or ("鉴别" in cat and "差值" not in crit)):
                            r["result_type"] = "qualitative"
                            if "曲线" in crit or "量效" in crit:
                                r["component"] = "量效曲线"
                            elif "图谱" in crit:
                                r["component"] = "图谱一致"
                            else:
                                r["component"] = "定性鉴别"
                        else:
                            r["result_type"] = "numeric"
                            if "效价" in crit or "效价" in cat or "%" in crit or "～" in crit or "~" in crit or "杀伤" in crit:
                                r["component"] = "相对效价"
                            elif "pI" in crit or "等电点" in crit:
                                r["component"] = "主峰pI"
                            else:
                                r["component"] = "定量测定"

    # 4. 查领域字典补齐 unit、component、result_type
    for r in rows:
        itm = r.get("item_zh", "")
        mth = r.get("method_zh", "")
        for pat, defaults in DOMAIN_ASSAY_DEFAULTS:
            if re.search(pat, itm, re.I) or re.search(pat, mth, re.I):
                if not r.get("unit") and "unit" in defaults:
                    r["unit"] = defaults["unit"]
                if not r.get("component") and "comp" in defaults:
                    r["component"] = defaults["comp"]
                break
        if not r.get("component"):
            r["component"] = r.get("item_zh") or r.get("item", "").split('\n')[0].strip() or r.get("category", "").split('\n')[0].strip()
        if not r.get("component_en"):
            r["component_en"] = r.get("item_en", "")

    numbers = standard_numbers(filename)
    number = numbers[0] if len(numbers) == 1 else ""
    base = split_number(number)
    return {"scope": scope, "material_scope": specification_material_scope(filename, {"scope": scope}), "rows": rows,
            "document_number": base, "document_version": number[len(base):].lstrip("-")}


def reviewed_scan_criteria(page, selections):
    tables = [html.fromstring(table["html"]).xpath(".//tr") if table["html"].strip() else []
              for table in page["tables"]]
    rows = []
    seen = set()
    for selection in selections:
        table, row, column = selection.table, selection.row, selection.column
        if (table, row) in seen:
            raise WorkflowError("duplicate_scan_row", "同一表行只能登记一次，请合并该行条款。")
        seen.add((table, row))
        if table == column == 0:
            recognized = ""
        else:
            if not table or not column:
                raise WorkflowError("ocr_cell_missing", "找不到所选扫描表格位置。")
            try:
                cells = tables[table - 1][row - 1].xpath("./td|./th")
                recognized = cells[column - 1].text_content()
            except IndexError as exc:
                raise WorkflowError("ocr_cell_missing", "找不到所选扫描表格位置。") from exc
        rows.append({**selection.model_dump(), "page": page["page"], "category": "",
                     "recognized_criterion": recognized})
    return sorted(rows, key=lambda row: (row["table"], row["row"]))
