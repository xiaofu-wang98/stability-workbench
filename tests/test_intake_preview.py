import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from stability_hub.contracts import ContractSchema, Preview, ResultCell
from stability_hub.intake import preview_file
from stability_hub.permissions import Actor, Capability, require
from stability_hub.service import Hub


@pytest.fixture
def actor():
    return Actor(id="operator-1", display_name="Operator", capabilities={Capability.PREVIEW, Capability.CONFIRM_FACT})


def persisted_counts(hub):
    with hub.store.connect() as conn:
        return {table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in ("documents", "imports", "observations", "effective", "exports")}


def test_preview_reports_delta_without_writing(tmp_path, config, context, xlsx, actor):
    hub = Hub(tmp_path / "hub")
    hub.import_file(xlsx([["Purity", "12M", "98.1", "%"], ["Protein content", "12M", "2.1", "mg/ml"]]), config, context, legacy_compatibility=True)
    before = hub.store.snapshot(config.project)
    counts = persisted_counts(hub)
    files = list((hub.store.home / "sources").iterdir())
    source = xlsx([
        ["Purity", "12M", "98.8", "%"], ["Protein content", "12M", "2.10", "mg/ml"],
        ["Large Fragment", "12M", "0.1", "%"], ["N-terminal", "12M", None, "%"],
        ["Unknown", "12M", "2", "%"],
    ], "new.xlsx")
    outcome = preview_file(hub.store, source, config, context, actor=actor)
    assert outcome.counts == {"new": 1, "same_value": 1, "empty": 1, "different_value": 1, "needs_review": 1}
    assert not outcome.can_commit
    assert outcome.source_hash == hashlib.sha256(source.read_bytes()).hexdigest()
    assert outcome.candidates[0].existing_record.result.value != outcome.candidates[0].record.result.value
    assert preview_file(hub.store, source, config, context, actor=actor) == outcome
    assert hub.store.snapshot(config.project) == before
    assert persisted_counts(hub) == counts
    assert list((hub.store.home / "sources").iterdir()) == files


def test_preview_revision_changes_with_confirmed_data(tmp_path, config, context, xlsx, actor):
    hub = Hub(tmp_path / "hub")
    source = xlsx([["Purity", "12M", "98.1", "%"]])
    before = preview_file(hub.store, source, config, context, actor=actor)
    hub.import_file(source, config, context, legacy_compatibility=True)
    after = preview_file(hub.store, source, config, context, actor=actor)
    assert before.base_revision != after.base_revision
    assert before.preview_id != after.preview_id
    assert after.candidates[0].status == "same_value"


@pytest.mark.parametrize("second", ["98.1", "98.8"])
def test_multiple_rows_need_detection_identity(tmp_path, config, context, xlsx, actor, second):
    hub = Hub(tmp_path / "hub")
    source = xlsx([["Purity", "12M", "98.1", "%"], ["Purity", "12M", second, "%"]])
    outcome = preview_file(hub.store, source, config, context, actor=actor)
    assert outcome.counts["needs_review"] == 2
    assert {c.issue_code for c in outcome.candidates} == {"multiple_detection_candidates"}
    assert hub.store.snapshot(config.project) == []


def test_permission_checked_before_file_access(tmp_path, config, context):
    hub = Hub(tmp_path / "hub")
    actor = Actor(id="no-rights", display_name="No rights", capabilities=set())
    with pytest.raises(PermissionError):
        preview_file(hub.store, tmp_path / "does-not-exist.xlsx", config, context, actor=actor)


def test_ordinary_actor_cannot_adopt_or_correct(actor):
    require(actor, Capability.CONFIRM_FACT)
    for permission in (Capability.ADOPT_RESULT, Capability.CORRECT_RESULT):
        with pytest.raises(PermissionError):
            require(actor, permission)


def test_contract_schema_and_examples_are_current():
    root = Path(__file__).resolve().parents[1]
    schema = json.loads((root / "contracts/v1/schema.json").read_text(encoding="utf-8"))
    assert schema == ContractSchema.model_json_schema()
    example = json.loads((root / "contracts/v1/examples/pending-result.json").read_text(encoding="utf-8"))
    cell = ResultCell.model_validate(example)
    assert cell.display.pending_confirmation and not cell.adopted.pending_confirmation
    example["adopted"]["pending_confirmation"] = True
    with pytest.raises(ValidationError):
        ResultCell.model_validate(example)
    Actor.model_validate_json((root / "contracts/v1/examples/ordinary-actor.json").read_text(encoding="utf-8"))
    Preview.model_validate_json((root / "contracts/v1/examples/preview.json").read_text(encoding="utf-8"))
