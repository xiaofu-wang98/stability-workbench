import pytest
import sqlite3
from io import BytesIO
from docx import Document
from starlette.testclient import TestClient

from stability_hub.migrations import MIGRATIONS, CURRENT_VERSION
from stability_hub.assay_catalog import compute_norm_signature
from stability_hub.specification_preview import component_suggestion
from stability_hub.storage import Store
from stability_hub.api import create_app
from stability_hub.permissions import Role, actor_for_role

ACTOR = actor_for_role('tester', 'tech', Role.TECHNICIAN)


def test_migration_17_schema(tmp_path):
    assert CURRENT_VERSION >= 17
    assert 17 in MIGRATIONS

    # Test migration execution via SQLiteStore
    db_path = tmp_path / "test_mig.db"
    store = Store(tmp_path / "store_mig")
    with store.read() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] >= 17
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(assay_components)")]
        assert "unit" in cols
        assert "result_type" in cols
        assert "reporting_basis" in cols
        assert "norm_signature" in cols
        assert "aliases_json" in cols

        # Check sample_release_results
        assert conn.execute("SELECT 1 FROM sample_release_results LIMIT 0") is not None
        # Check slot_identity_bridges
        assert conn.execute("SELECT 1 FROM slot_identity_bridges LIMIT 0") is not None


def test_norm_signature_equivalence():
    sig1 = compute_norm_signature("纯度（SEC-HPLC法）", "SEC-HPLC法", "高分子量组分", "numeric")
    sig2 = compute_norm_signature("SEC-HPLC Method", "SEC-HPLC", "聚合体(%)", "numeric")
    sig3 = compute_norm_signature("SEC Purity", "SEC-HPLC", "HMW", "numeric")
    assert sig1 == sig2 == sig3
    assert "hmw" in sig1
    assert "sec-hplc" in sig1


def test_component_suggestion_colon_and_basis():
    # 1. 冒号前缀与单位提取
    res1 = component_suggestion("单体：≥ 95.00%")
    assert res1["component"] == "单体"
    assert res1["unit"] == "%"
    assert res1["result_type"] == "numeric"

    res2 = component_suggestion("主峰（重链+轻链）%：≥ 90.0%")
    assert "主峰" in res2["component"]
    assert res2["unit"] == "%"

    # 2. 药典剥离
    res3 = component_suggestion("EP：不得深于Ⅳ号标准液")
    assert res3.get("reporting_basis") == "EP"
    assert res3["result_type"] == "qualitative"

    res4 = component_suggestion("EP: ≤B9或≤Y3")
    assert res4.get("reporting_basis") == "EP"


def test_register_two_biological_activities(tmp_path):
    store = Store(tmp_path / "store")
    client = TestClient(create_app(store, [], ACTOR))

    # Create project
    proj_resp = client.post("/api/v1/projects", json={"project": "IP281", "name": "IP281 Project"})
    assert proj_resp.status_code in (200, 201)

    # Upload mock specification with two Biological Activity rows
    doc = Document()
    table = doc.add_table(rows=0, cols=3)
    # Header
    r0 = table.add_row()
    r0.cells[0].text = "检测项目"
    r0.cells[1].text = "方法"
    r0.cells[2].text = "质量标准"

    # Row 1: qualitative curve similarity
    r1 = table.add_row()
    r1.cells[0].text = "生物学活性"
    r1.cells[1].text = "细胞杀伤法"
    r1.cells[2].text = "与阶段性标准品量效关系曲线相似"

    # Row 2: quantitative potency
    r2 = table.add_row()
    r2.cells[0].text = "生物学活性"
    r2.cells[1].text = "细胞杀伤法"
    r2.cells[2].text = "70% ~ 130%"

    buf = BytesIO()
    doc.save(buf)

    spec_resp = client.post("/api/v1/specifications", files={"file": ("STP(C)-QC-IP281-001.docx", buf.getvalue())})
    assert spec_resp.status_code == 200
    spec_id = spec_resp.json()["id"]

    # Register both components
    rev = client.get("/api/v1/projects/IP281/configuration").json()["base_revision"]
    reg_resp = client.post(f"/api/v1/projects/IP281/specifications/{spec_id}/components", json={
        "revision": rev,
        "idempotency_key": "reg-bio",
        "components": [
            {
                "table": 1, "row": 2, "assay_zh": "生物学活性", "method": "细胞杀伤法",
                "component_zh": "量效曲线", "result_type": "qualitative", "unit": ""
            },
            {
                "table": 1, "row": 3, "assay_zh": "生物学活性", "method": "细胞杀伤法",
                "component_zh": "相对效价", "result_type": "numeric", "unit": "%"
            }
        ]
    })
    assert reg_resp.status_code == 200, reg_resp.text
    links = reg_resp.json()["links"]
    assert len(links) == 2
    assert links[0]["item_id"] != links[1]["item_id"]
