"""Read-only intake preview. No implicit confirmation or result adoption."""

import hashlib
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

from .contracts import Candidate, Preview
from .mapping import map_observation, prepare_observation
from .models import ImportContext, ProjectConfig, StabilityRecord, digest
from .normalize import NeedsReview
from .parsing import parse_source
from .permissions import Actor, Capability, require
from .storage import Store


def preview_file(
    store: Store, path: Path | str, config: ProjectConfig, context: ImportContext, *, actor: Actor
) -> Preview:
    require(actor, Capability.PREVIEW)
    path = Path(path)
    if path.suffix.lower() not in {".xlsx", ".docx"}:
        raise ValueError("Only .xlsx and .docx are supported")
    data = path.read_bytes()
    source_hash = hashlib.sha256(data).hexdigest()
    # Parse the exact bytes whose hash is returned, even if the source file changes during preview.
    with TemporaryDirectory(prefix="stability-preview-") as temporary:
        frozen = Path(temporary) / ("source" + path.suffix.lower())
        frozen.write_bytes(data)
        parsed = parse_source(frozen)
    snapshot = store.snapshot(config.project)
    revision = digest(snapshot)
    existing = {entry["slot_id"]: StabilityRecord.model_validate(entry["record"]) for entry in snapshot}
    preview_id = digest({
        "source": source_hash, "config": config.model_dump(mode="json"),
        "context": context.model_dump(mode="json"), "base_revision": revision,
        "parser": "long-table-v1", "preview": 1,
    })
    candidates = []
    seen: dict[str, list[int]] = {}
    for observation in parsed.observations:
        raw = prepare_observation(observation, parsed.metadata)
        record = old = None
        code = None
        try:
            record = map_observation(raw, context, config)
            old = existing.get(record.key.slot_id)
            if record.result.status in {"blank", "placeholder"}:
                status, detail = "empty", "空白或占位，本次不会清除已确认结果。"
            elif old is None:
                status, detail = "new", "新增候选；检测身份和结果尚未确认，未入库。"
            elif record.result.equivalent(old.result):
                status, detail = "same_value", "与已有值相同；仍需核对是否为同一次检测。"
            else:
                status, detail = "different_value", "与已有值不同；需区分复测、更正或解析错误。"
            if status != "empty":
                seen.setdefault(record.key.slot_id, []).append(len(candidates))
        except NeedsReview as exc:
            status, code, detail = "needs_review", exc.code, str(exc)
        candidates.append(Candidate(
            id=digest({"preview": preview_id, "location": raw.location.model_dump()}),
            location=raw.location, raw_fields=raw.fields, status=status, record=record,
            existing_record=old, issue_code=code, detail=detail, comment=raw.comment,
        ))
    # Multiple rows can represent separate tests; never silently pick the last one.
    for indices in seen.values():
        if len(indices) > 1:
            for index in indices:
                candidates[index] = candidates[index].model_copy(update={
                    "status": "needs_review", "issue_code": "multiple_detection_candidates",
                    "detail": "本文件同槽位有多条结果，请确认是重复记录还是独立检测。",
                })
    counts = Counter(candidate.status for candidate in candidates)
    return Preview(
        preview_id=preview_id, project=config.project, filename=path.name, source_hash=source_hash,
        base_revision=revision, candidates=tuple(candidates),
        counts={status: counts[status] for status in (
            "new", "same_value", "empty", "different_value", "needs_review"
        )},
    )
