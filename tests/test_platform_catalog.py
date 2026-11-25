"""R3-A verification: platform 4-tier catalog, migration v21, normalized mapping, and 6-category specs."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

from openpyxl import Workbook
import pytest

from stability_hub.assay_catalog import catalog, compute_norm_signature, promote_to_platform_catalog
from stability_hub.mapping import map_observation
from stability_hub.matrix_fields import method_lines
from stability_hub.models import ImportContext, ItemDefinition, Location, ProjectConfig, RawObservation
from stability_hub.normalize import NeedsReview
from stability_hub.platform_catalog import (
    add_platform_alias,
    canonical_unit,
    list_platform_catalog,
    match_platform_assay,
    normalize_method_signature,
    resolve_category_metadata,
    units_equivalent,
)
from stability_hub.specification_preview import inspect_specification
from stability_hub.permissions import Role, actor_for_role
from stability_hub.storage import Store
from stability_hub.workflow import Workflow


SENIOR = actor_for_role("qa-lead", "QA Lead", Role.ADMIN)


def test_v21_seeds_six_categories_and_builtin_assays(tmp_path: Path):
    store = Store(tmp_path / "r3a.sqlite")
    with store.read() as conn:
        data = list_platform_catalog(conn)
    assert [c["code"] for c in data["categories"]] == [
        "identification",
        "purity_impurity",
        "potency",
        "content",
        "safety",
        "physicochemical_other",
    ]
    assert len(data["subcategories"]) >= 15
    assert data["total_assays"] >= 28

    # Query filtering works across Chinese, English, and aliases
    with store.read() as conn:
        nr = list_platform_catalog(conn, query="nrCE")
    assert any(a["code"] == "nr_ce_sds" for a in nr["assays"])


def test_deterministic_signatures_and_units():
    assert normalize_method_signature("NR CE-SDS") == "nr-ce-sds"
    assert normalize_method_signature("非还原型CE-SDS法") == "nr-ce-sds"
    assert normalize_method_signature("nrCE") == "nr-ce-sds"
    assert normalize_method_signature("还原型CE-SDS法") == "r-ce-sds"
    assert normalize_method_signature("分子排阻色谱法(SEC-HPLC Method)") == "sec-hplc"

    assert canonical_unit("mg/ml") == "mg/mL"
    assert canonical_unit("mOsm/kg") == "mOsmol/kg"
    assert canonical_unit("cfu/10ml") == "cfu/10 mL"
    assert canonical_unit("％") == "%"
    assert units_equivalent("mg/ml", "mg/mL")
    assert not units_equivalent("%", "mg/mL")

    sig1 = compute_norm_signature("纯度", "SEC-HPLC法", "单体")
    sig2 = compute_norm_signature("SEC纯度", "分子排阻色谱法(SEC-HPLC Method)", "Monomer")
    assert sig1 == sig2


def test_parent_child_method_vs_genuine_dual_method():
    assert method_lines("无菌检查法\n薄膜过滤法") == ["薄膜过滤法"]
    assert method_lines("渗透压摩尔浓度测定法\n冰点下降法") == ["冰点下降法"]
    # Genuine dual-method switch MUST stay ambiguous (2 lines)
    assert len(method_lines("电化学发光法\n报告基因法")) == 2


def test_normalized_catalog_mapping_and_unit_equivalence():
    config = ProjectConfig(
        project="IP501",
        name="IP501",
        items=(
            ItemDefinition(
                id="sec_monomer",
                label="纯度（分子排阻色谱） · 单体",
                unit="%",
                result_type="numeric",
                method="SEC-HPLC法",
                category_code="purity_impurity",
                subcategory_code="size_variants",
                platform_assay_id="plat_assay_sec_purity",
            ),
            ItemDefinition(
                id="protein_conc",
                label="蛋白质含量",
                unit="mg/mL",
                result_type="numeric",
                method="UV法",
                category_code="content",
                subcategory_code="content_protein",
                platform_assay_id="plat_assay_protein_concentration",
            ),
        ),
    )
    context = ImportContext(
        defaults={
            "sample_type": "DS",
            "batch": "B001",
            "presentation": "10 mg/mL",
            "study": "long_term",
            "study_id": "STB-001",
            "condition": "5±3℃",
        }
    )
    raw_sec = RawObservation(
        fields={"test_item": "SEC纯度 · Monomer", "method": "SEC", "time_point": "3M", "result": "99.1", "unit": "％"},
        location=Location(format="xlsx", section="Sheet1", row=5, column=3),
    )
    rec = map_observation(raw_sec, context, config)
    assert rec.key.item == "sec_monomer"
    assert rec.result.unit == "%"

    raw_uv = RawObservation(
        fields={"test_item": "蛋白浓度", "method": "UV-vis法", "time_point": "3M", "result": "10.2", "unit": "mg/ml"},
        location=Location(format="xlsx", section="Sheet1", row=6, column=3),
    )
    rec_uv = map_observation(raw_uv, context, config)
    assert rec_uv.key.item == "protein_conc"
    assert rec_uv.result.unit == "mg/mL"

    # Real unit conflict still rejected
    raw_bad_unit = RawObservation(
        fields={"test_item": "蛋白浓度", "method": "UV法", "time_point": "3M", "result": "10.2", "unit": "%"},
        location=Location(format="xlsx", section="Sheet1", row=7, column=3),
    )
    with pytest.raises(NeedsReview) as exc:
        map_observation(raw_bad_unit, context, config)
    assert exc.value.code == "unit_conflict"


def test_promote_to_platform_and_custom_alias(tmp_path: Path):
    store = Store(tmp_path / "promote.sqlite")
    flow = Workflow(store)
    flow.create_project(ProjectConfig(project="IP900", name="IP900"), actor=SENIOR)

    promoted = promote_to_platform_catalog(
        store,
        "IP900",
        name_zh="游离巯基含量",
        name_en="Free Thiol Content",
        method="Ellman法",
        unit="mol/mol",
        result_type="numeric",
        category_code="purity_impurity",
        subcategory_code="adc_conjugation",
        aliases=["Free Thiol", "游离巯基"],
        actor=SENIOR,
    )
    assay_id = promoted["platform_assay"]["id"]
    with store.connect() as conn:
        add_platform_alias(conn, entity_type="assay", entity_id=assay_id, raw_alias="DTNB游离巯基")
    with store.read() as conn:
        hit = match_platform_assay(conn=conn, item_text="DTNB游离巯基")
    assert hit is not None
    assert hit["id"] == assay_id
    assert hit["category_code"] == "purity_impurity"

    items = catalog(store, "IP900", SENIOR)
    assert any(it["label"] == "游离巯基含量" and it["category_code"] == "purity_impurity" for it in items)


def test_inspect_specification_xlsx_six_categories():
    wb = Workbook()
    ws = wb.active
    ws.title = "质量标准"
    ws.append(["大类", "检验项目", "子检项/细项", "分析方法", "SOP编号", "药典体系", "标准限度", "单位"])
    ws.append(["1.鉴别", "肽图", "图谱一致性", "RP-UPLC法", "SOP-001", "ChP", "与参考品图谱一致", ""])
    ws.append(["2.纯度和杂质", "纯度", "单体", "SEC-HPLC法", "SOP-002", "ChP", "≥95.0", "%"])
    ws.append(["4.含量", "蛋白质含量", "蛋白浓度", "UV法", "SOP-003", "ChP", "10.0±1.0", "mg/mL"])
    buf = BytesIO()
    wb.save(buf)

    parsed = inspect_specification(buf.getvalue(), filename="STP(C)-QC-IP501-2-002-1.0_原液质量标准.xlsx")
    assert parsed["material_scope"] == "DS"
    assert len(parsed["rows"]) == 3
    assert [r["category_code"] for r in parsed["rows"]] == ["identification", "purity_impurity", "content"]
    assert parsed["rows"][1]["subcategory_code"] == "size_variants"
    assert parsed["rows"][2]["canonical_unit"] == "mg/mL"
