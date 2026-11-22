"""R3-C API integration tests: platform catalog CRUD, promote, 7 standard templates, Excel spec upload, cleaning audit."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient

from stability_hub.api import create_app
from stability_hub.models import ProjectConfig
from stability_hub.permissions import Role, actor_for_role
from stability_hub.storage import Store
from stability_hub.template_library import render_template

ADMIN = actor_for_role("r3-admin", "R3 Admin", Role.ADMIN)


def test_r3_platform_catalog_and_template_library_api(tmp_path: Path):
    store = Store(tmp_path / "r3c.sqlite")
    app = create_app(store, [ProjectConfig(project="IP501", name="IP501")], ADMIN)
    client = TestClient(app)

    # 1. List platform catalog (all 6 categories + builtin assays)
    res = client.get("/api/v1/catalog/platform")
    assert res.status_code == 200
    data = res.json()
    assert len(data["categories"]) == 6
    assert data["total_assays"] >= 28

    # 2. Filter by category & query
    res_nr = client.get("/api/v1/catalog/platform", params={"category_code": "purity_impurity", "query": "nrCE"})
    assert res_nr.status_code == 200
    assert any(a["code"] == "nr_ce_sds" for a in res_nr.json()["assays"])

    # 3. Create custom platform assay & add alias
    created = client.post("/api/v1/catalog/platform/assays", json={
        "category_code": "purity_impurity",
        "subcategory_code": "adc_conjugation",
        "name_zh": "游离巯基含量",
        "name_en": "Free Thiol Content",
        "default_unit": "mol/mol",
        "methods": [{"name_zh": "Ellman法"}],
        "aliases": ["Free Thiol"],
    })
    assert created.status_code == 200, created.text
    assay_id = created.json()["id"]

    alias_res = client.post("/api/v1/catalog/platform/aliases", json={
        "entity_type": "assay",
        "entity_id": assay_id,
        "raw_alias": "DTNB法游离巯基",
    })
    assert alias_res.status_code == 200

    # 4. Promote from project into platform + project dictionary
    promo = client.post("/api/v1/projects/IP501/catalog/promote", json={
        "name_zh": "渗透压摩尔浓度（自定义复核）",
        "method": "冰点下降法",
        "unit": "mOsm/kg",
        "category_code": "physicochemical_other",
        "aliases": ["Osm复核"],
    })
    assert promo.status_code == 200, promo.text
    assert promo.json()["project_item"]["unit"] == "mOsmol/kg"

    # 5. List & download all 7 minimal-change templates + guide
    tpls = client.get("/api/v1/templates")
    assert tpls.status_code == 200
    tpl_list = tpls.json()["templates"]
    assert len(tpl_list) == 7
    for tpl in tpl_list:
        dl = client.get(f"/api/v1/templates/{tpl['id']}/download")
        assert dl.status_code == 200
        assert len(dl.content) > 2000

    guide = client.get("/api/v1/templates/guide")
    assert guide.status_code == 200
    assert "T1" in guide.json()["markdown"] and "T5" in guide.json()["markdown"]

    # 6. Upload TPL-01 Excel specification directly to /api/v1/specifications
    spec_fn, spec_media, spec_bytes = render_template("spec_xlsx")
    up = client.post(
        "/api/v1/specifications",
        files={"file": (spec_fn, BytesIO(spec_bytes), spec_media)},
    )
    assert up.status_code == 200, up.text
    spec_data = up.json()
    assert len(spec_data["rows"]) >= 15
    assert any(r["category_code"] == "identification" for r in spec_data["rows"])

    # 7. Upload TPL-05 worksheet Excel to preview and fetch cleaning-audit
    ws_fn, ws_media, ws_bytes = render_template("worksheet_xlsx")
    prev = client.post(
        "/api/v1/projects/IP501/previews",
        files={"file": (ws_fn, BytesIO(ws_bytes), ws_media)},
        headers={"Idempotency-Key": "r3-tpl05-preview"},
        data={"context": '{"role":"stability_source","defaults":{}}'},
    )
    assert prev.status_code == 200, prev.text
    preview_id = prev.json()["preview_id"]
    audit = client.get(f"/api/v1/previews/{preview_id}/cleaning-audit")
    assert audit.status_code == 200
    assert "total" in audit.json() and "by_rule" in audit.json()
