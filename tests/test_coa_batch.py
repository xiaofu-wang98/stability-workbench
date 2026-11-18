import json
from io import BytesIO

from docx import Document
from fastapi.testclient import TestClient

from stability_hub.api import create_app
from stability_hub.models import ProjectConfig
from stability_hub.permissions import Role, actor_for_role
from stability_hub.result_versions import ResultVersions
from stability_hub.storage import Store
from stability_hub.workflow_common import WorkflowError


def ok(response):
    assert response.is_success, response.text
    return response.json()


def post(client, path, **command):
    return ok(client.post(path, json=command))


def make_app(store, role=Role.ADMIN):
    return create_app(store, [ProjectConfig(project="P")], actor_for_role("researcher", "Researcher", role))


def add_sample(client, batch, *, project="P", stage="ADC", sample_type="DS"):
    return post(client, f"/api/v1/projects/{project}/samples", batch=batch, material_stage=stage,
                sample_type=sample_type, presentation="1mg", purpose="放行")


def make_study(client, sample, items, standards=None, *, study="long_term"):
    identity = {"batch": sample["batch"], "presentation": sample["presentation"], "sample_type": sample["sample_type"],
                "study": study, "study_id": study, "condition": "5C"}
    result = post(client, f"/api/v1/projects/{sample['project']}/worksheets", name=study,
                  sample_id=sample["id"], idempotency_key="study-" + sample["id"] + study,
                  identity=identity, start_date="2026-01-31", items=items, item_standards=standards or {},
                  points=[{"unit": "M", "amount": 0, "required_items": items}])
    return "/api/v1/worksheets/" + result["id"]


def test_project_sample_directory_and_study_grouping(tmp_path, config):
    store = Store(tmp_path)
    app = create_app(store, [config, ProjectConfig(project="OTHER")], actor_for_role("owner", "Owner", Role.ADMIN),
                     api_token="isolated-test-token")
    with TestClient(app, headers={"Authorization": "Bearer isolated-test-token"}) as client:
        project = config.project
        base = f"/api/v1/projects/{project}"
        first = add_sample(client, "02", project=project)
        assert add_sample(client, "02", project=project)["id"] == first["id"]
        second = add_sample(client, "02", project=project, stage="裸抗")
        third = add_sample(client, "09", project=project)
        removed = add_sample(client, "01", project=project)
        other = add_sample(client, "02", project="OTHER")
        sandbox = add_sample(client, "02", project="TEST")
        archive = "/api/v1/samples/" + removed["id"]
        impact = ok(client.get(archive + "/archive-impact"))
        ok(client.request("DELETE", archive, json={"basis": "Duplicate", "fingerprint": impact["fingerprint"],
                                                   "idempotency_key": "archive-sample"}))
        directory = base + "/samples"
        all_samples = ok(client.get(directory))
        assert {entry["id"] for entry in all_samples} == {first["id"], second["id"], third["id"]}
        assert [entry["batch"] for entry in all_samples] == ["02", "02", "09"]
        for offset in range(4):
            assert ok(client.get(directory, params={"limit": 1, "offset": offset})) == all_samples[offset:offset + 1]
        assert ok(client.get(directory, params={"batch": " 02 "})) == all_samples[:2]
        assert ok(client.get(directory, params={"batch": "0"})) == []
        assert ok(client.get(directory, params={"batch": ""})) == []
        assert ok(client.get("/api/v1/projects/TEST/samples")) == [sandbox]
        assert ok(client.get("/api/v1/projects/OTHER/samples")) == [other]
        assert client.get(directory, params={"limit": 501}).status_code == 422
        assert client.get(directory, headers={"Authorization": "Bearer wrong"}).status_code == 401
        item = config.items[0].id
        first_study = make_study(client, first, [item])
        make_study(client, first, [item], study="accelerated")
        make_study(client, second, [item])
        studies = base + "/worksheets"
        assert len(ok(client.get(studies))) == 3
        grouped = ok(client.get(studies, params={"sample_id": first["id"]}))
        assert {row["study"] for row in grouped} == {"long_term", "accelerated"}
        assert {row["sample_id"] for row in grouped} == {first["id"]}
        assert [row["id"] for row in ok(client.get(studies, params={"sample_id": first["id"], "study": "long_term"}))] == [
            first_study.rsplit("/", 1)[1]
        ]
        assert ok(client.get(studies, params={"sample_id": sandbox["id"]})) == []


def upload_standard(client, indices, name):
    document = Document()
    document.add_paragraph(name)
    table = document.add_table(rows=len(indices) + 1, cols=3)
    for cell, value in zip(table.rows[0].cells, ("Test Item", "Method", "Specification")):
        cell.text = value
    for row, index in enumerate(indices, 2):
        for cell, value in zip(table.rows[row - 1].cells, (f"Assay {index}", "SEC", "≥90%")):
            cell.text = value
    data = BytesIO()
    document.save(data)
    return ok(client.post("/api/v1/specifications", files={"file": (name + ".docx", data.getvalue())}))["id"]


def register_standard(client, spec, indices, key):
    revision = ok(client.get("/api/v1/projects/P/configuration"))["base_revision"]
    return post(client, f"/api/v1/projects/P/specifications/{spec}/components", revision=revision, idempotency_key=key,
                components=[{"table": 1, "row": row, "assay_zh": f"Assay {index}", "component_zh": f"Assay {index}",
                             "method": "SEC", "unit": "%", "result_type": "numeric"}
                            for row, index in enumerate(indices, 2)])["links"]


def save_coa(client, sample, spec, key, values):
    path = f"/api/v1/samples/{sample['id']}/coa/{spec}"
    state = ok(client.get(path))
    return post(client, path, idempotency_key=key, revision=state["revision"], version=state["version"], values=values)


def test_coa_batch_20_atomic_replay_and_version_updates(tmp_path, monkeypatch):
    store = Store(tmp_path)
    with TestClient(make_app(store)) as client:
        sample = add_sample(client, "COA-01")
        same_batch = add_sample(client, "COA-01", stage="裸抗")
        indices = list(range(27))
        spec = upload_standard(client, indices, "Acceptance DS")
        alternative = upload_standard(client, [22], "Alternative DS")
        links = register_standard(client, spec, indices, "register")
        alternate_link, = register_standard(client, alternative, [22], "register-alt")
        items = [link["item_id"] for link in links]
        assert alternate_link["item_id"] == items[22]
        criteria = {link["item_id"]: [{"specification_id": spec, "table": 1, "row": link["row"]}]
                    for index, link in enumerate(links) if index != 23}
        criteria[items[22]].append({"specification_id": alternative, "table": 1, "row": 2})
        values = {f"1:{index + 2}": "98.00%" for index in indices if index not in {25, 26}}
        values["1:26"] = "not a numeric result"
        assert save_coa(client, sample, spec, "coa", values)["version"] == 1
        assert save_coa(client, sample, alternative, "alt", {"1:2": "99.00%"})["version"] == 1
        # A different material stage with the same batch must never supply missing results.
        assert save_coa(client, same_batch, spec, "other-coa", {"1:28": "99.00%"})["version"] == 1
        sheet = make_study(client, sample, items, criteria)
        view = ok(client.get(sheet))
        worksheet_rows = {entry["item_id"]: entry["id"] for entry in view["rows"]}
        rows = [worksheet_rows[item] for item in items]
        point = view["points"][0]["id"]
        token = post(client, sheet + "/lease")["token"]
        existing = post(client, sheet + "/save", token=token, revision=view["revision"], idempotency_key="existing",
                        changes=[{"row_id": rows[21], "point_id": point, "intent": "initial", "value": "95%"}])
        reference = post(client, sheet + "/release-reference", token=token, revision=existing["revision"],
                         idempotency_key="reference", row_id=rows[20], basis="Existing reference",
                         coa_specification_id=spec, coa_version=1, release_completed="2026-01-01")
        confirm_path = sheet + "/copy-coa-to-0m/batch"
        preview_path = confirm_path + "/preview"
        date = "2026-01-01"
        for invalid_date in ("2025-12-30", "2026-02-01"):
            assert client.post(preview_path, json={"release_completed": invalid_date}).json()["code"] == "release_reference_date"

        preview = post(client, preview_path, release_completed=date)
        assert post(client, preview_path, release_completed=date) == preview
        assert preview["sample_id"] == sample["id"]
        assert preview["revision"] == reference["revision"]
        reviewed = {row["row_id"]: row for row in preview["rows"]}
        assert [reviewed[row]["status"] for row in rows] == (
            ["matched"] * 20 + ["conflict", "existing", "conflict", "unmatched", "conflict", "unmatched", "unmatched"]
        )
        assert reviewed[rows[20]]["reason"] == "release_reference_exists"
        assert reviewed[rows[21]]["adopted_version_id"] == existing["result_version_ids"][0]
        assert reviewed[rows[22]]["reason"] == "multiple_coa_matches"
        assert reviewed[rows[23]]["candidates"] == []  # A dictionary match alone is insufficient.
        assert reviewed[rows[24]]["reason"] == "invalid_coa_value"
        assert reviewed[rows[26]]["candidates"] == []
        command = {"token": token, "revision": preview["revision"], "release_completed": date,
                   "preview_fingerprint": preview["preview_fingerprint"], "row_ids": rows[:20],
                   "idempotency_key": "copy-20", "basis": "Reviewed exact specification rows and release date"}

        def rejected(payload, code):
            response = client.post(confirm_path, json=payload)
            assert response.status_code == 409 and response.json()["code"] == code, response.text

        rejected({**command, "revision": command["revision"] - 1}, "worksheet_revision_changed")
        rejected({**command, "token": "wrong"}, "worksheet_lease_lost")
        rejected({**command, "row_ids": [rows[0], rows[0]]}, "coa_batch_selection")
        rejected({**command, "row_ids": [rows[0], rows[22]]}, "coa_batch_selection")
        assert save_coa(client, sample, spec, "coa-before", {"1:2": "97.00%"})["version"] == 2
        rejected(command, "coa_batch_preview_changed")
        latest = post(client, preview_path, release_completed=date)
        command["preview_fingerprint"] = latest["preview_fingerprint"]
        original = ResultVersions.record_worksheet_result
        calls = []

        def fail_second(self, conn, record, **kwargs):
            calls.append(record.key.item)
            if len(calls) == 2:
                raise WorkflowError("injected_failure", "Forced transaction failure", 409)
            return original(self, conn, record, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(ResultVersions, "record_worksheet_result", fail_second)
            rejected(command, "injected_failure")
        assert len(calls) == 2
        assert post(client, preview_path, release_completed=date) == latest
        with store.read() as conn:
            assert conn.execute("SELECT count(*) FROM workflow_versions").fetchone()[0] == 1
            assert conn.execute("SELECT count(*) FROM workflow_events WHERE action='worksheet_copy_coa_batch'").fetchone()[0] == 0
        copied = post(client, confirm_path, **command)
        assert copied["revision"] == command["revision"] + 1
        assert set(copied["copied"]) == set(rows[:20])
        assert len(set(copied["result_version_ids"])) == 20
        assert post(client, confirm_path, **command) == copied
        rejected({**command, "basis": "Changed decision"}, "idempotency_conflict")
        assert save_coa(client, sample, spec, "coa-after", {"1:2": "96.00%"})["version"] == 3
        assert post(client, confirm_path, **command) == copied
        current = post(client, preview_path, release_completed=date)
        first = next(row for row in current["rows"] if row["row_id"] == rows[0])
        assert first["status"] == "existing" and first["adopted_value"] == "97.00%"
        assert first["candidates"][0]["value"] == "96.00%"
        rejected({**command, "revision": current["revision"], "preview_fingerprint": current["preview_fingerprint"],
                  "idempotency_key": "overwrite"}, "coa_batch_selection")
        view = ok(client.get(sheet))
        assert set(view["release_references"]) == {rows[20]}
        adopted = {cell["row_id"]: cell["adopted"]["result_version_id"] for cell in view["cells"] if cell["adopted"]}
        assert adopted == {**copied["copied"], rows[21]: existing["result_version_ids"][0]}
        config = ok(client.get("/api/v1/projects/P/configuration"))
        changed = next(item for item in config["config"]["items"] if item["id"] == items[0])
        post(client, "/api/v1/projects/P/configuration", base_revision=config["base_revision"],
             idempotency_key="method-change", reason="Method revision", item={**changed, "method_version": "v2"})
        changed_sheet = make_study(client, sample, items[:2], {item: criteria[item] for item in items[:2]}, study="accelerated")
        changed_preview = post(client, changed_sheet + "/copy-coa-to-0m/batch/preview", release_completed=date)
        assert {row["item_id"]: row["status"] for row in changed_preview["rows"]} == {
            items[0]: "unmatched", items[1]: "matched"
        }

    with TestClient(make_app(store, Role.PROJECT_LEAD)) as readonly:
        assert readonly.post(preview_path, json={"release_completed": date}).is_success
        assert readonly.post(confirm_path, json=command).status_code == 403
    with store.read() as conn:
        assert conn.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 20
        versions = conn.execute("SELECT id,slot_id,source_json,record_json FROM workflow_versions WHERE id<>?",
                                (existing["result_version_ids"][0],)).fetchall()
        assert len(versions) == 20
        for entry in versions:
            source, = json.loads(entry["source_json"])
            row = reviewed[source["row_id"]]
            assert entry["id"] == copied["copied"][row["row_id"]]
            assert entry["slot_id"] == row["slot_id"]
            assert json.loads(entry["record_json"])["key"]["item"] == row["item_id"]
            assert source["kind"] == "coa_snapshot" and source["release_completed"] == date
            assert source["sample_id"] == sample["id"] and source["coa"]["specification_id"] == spec
            assert source["coa"]["version"] == (2 if row["row_id"] == rows[0] else 1)
        action, = conn.execute("SELECT detail_json FROM workflow_events WHERE action='worksheet_copy_coa_batch'").fetchall()
        assert json.loads(action["detail_json"])["copied"] == copied["copied"]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
