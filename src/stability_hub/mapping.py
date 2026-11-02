import re
from typing import Protocol

from pydantic import ValidationError

from .matrix_fields import interpret_raw
from .models import ImportContext, ItemDefinition, ProjectConfig, RawObservation, RecordKey, StabilityRecord
from .normalize import NeedsReview, normalize_month
from .numeric_formats import interpret_result


class MappingAdvisor(Protocol):
    """Optional future LLM/rule advisor. Suggestions never become write authority."""

    def suggest(self, observation: RawObservation, config: ProjectConfig) -> list[dict]: ...


ALLOWED_CONTEXT = {
    "project",
    "sample_type",
    "purpose",
    "batch",
    "presentation",
    "study",
    "study_id",
    "condition",
    "time_point",
    "method",
    "method_version",
}


def prepare_observation(raw: RawObservation, metadata: dict[str, str]) -> RawObservation:
    """Share row/document merging between preview and the legacy importer."""
    if raw.adapter == "stability_matrix" or raw.evidence and not metadata:
        return raw
    fields = dict(metadata)
    contradictions = []
    for key, value in raw.fields.items():
        if value.strip():
            if fields.get(key, "").strip() and fields[key].strip() != value.strip():
                contradictions.append(f"Row/document metadata differ for {key}")
            fields[key] = value
    fields["result"] = raw.fields.get("result", "")
    return RawObservation(
        fields=fields, revisions=raw.revisions, logical_row=raw.logical_row,
        source_span=raw.source_span, subresult_hint=raw.subresult_hint,
        system_hints=raw.system_hints, reporting_basis=raw.reporting_basis, result_anchor=raw.result_anchor,
        location=raw.location,
        evidence=raw.evidence,
        comment=raw.comment,
        formula=raw.formula,
        number_format=raw.number_format,
        source_value=raw.source_value,
        hazards=raw.hazards + tuple(contradictions),
    )


def map_observation(raw: RawObservation, context: ImportContext, config: ProjectConfig, *, source_facts=False, source_scope='') -> StabilityRecord:
    if raw.hazards:
        raise NeedsReview("unsupported_structure", "; ".join(raw.hazards))
    unknown = set(context.defaults) - ALLOWED_CONTEXT
    if unknown:
        raise NeedsReview("invalid_context", f"Unsupported context fields: {sorted(unknown)}")
    fields = interpret_raw(raw, raw.fields) if raw.adapter == "stability_matrix" else dict(raw.fields)
    for key, value in context.defaults.items():
        if fields.get(key, "").strip() and fields[key].strip() != value.strip():
            raise NeedsReview(
                "context_conflict", f"File {key}={fields[key]!r} differs from context {value!r}"
            )
        fields[key] = value
    sample_type = fields.get("sample_type", "")
    if not fields.get("presentation", "").strip() and sample_type in config.presentation_defaults:
        fields["presentation"] = config.presentation_defaults[sample_type]
    if fields.get("project") and fields["project"].strip() != config.project:
        raise NeedsReview("project_conflict", "File/context project differs from selected configuration")
    fields["project"] = config.project
    name = " ".join(fields.get("test_item", "").split()).casefold()
    matches = [
        item
        for item in config.items
        if name in {" ".join(a.split()).casefold() for a in (item.id, item.label, *item.aliases)}
    ]
    if not matches and source_facts and name:
        # A source-local identity is not a canonical dictionary entry. No fuzzy matching.
        label = " ".join(fields['test_item'].split())
        matches = [ItemDefinition(id=label, label=label, method=fields.get('method') or '未注明',
                                  unit=fields.get('unit', ''), result_type='qualitative')]
    if len(matches) != 1:
        raise NeedsReview("unknown_item", f"检测项目 {fields.get('test_item')!r} 尚未匹配，请选择已有项目或确认新增。")
    item = matches[0]
    unit = fields.get("unit", "").strip() or item.unit
    if unit != item.unit and item.unit:
        raise NeedsReview(
            "unit_conflict", f"Expected unit {item.unit!r}, received {unit!r}; no implicit conversion"
        )
    method = fields.get("method", "").strip() or item.method
    version = fields.get("method_version", "").strip() or item.method_version
    if context.role == "release_coa":
        if fields.get("study", "release") != "release" or fields.get("time_point", "").strip():
            raise NeedsReview("release_context", "COA is release evidence, not a stability time point")
        fields["study"] = "release"
        fields.setdefault("condition", "release")
    elif fields.get("study") == "release":
        raise NeedsReview("source_role", "Use release_coa role for a release observation")
    if raw.formula:
        raise NeedsReview(
            "formula_result", "源格为公式，请核对原件并明确填写经核对的结果，系统不自行计算采用。"
        )
    from .review_issues import comment_requires_review
    if raw.comment and (not source_facts or comment_requires_review(raw.comment)):
        raise NeedsReview("source_comment", "源格含批注或相关更正/调查说明，请核对原文并明确确认结果。")
    day_match = re.fullmatch(r"(\d+)D", fields.get("time_point", ""), re.I)
    day = int(day_match[1]) if day_match else None
    month = None if fields.get("study") == "release" or day is not None else normalize_month(fields.get("time_point", ""))
    if source_facts and not fields.get("presentation", "").strip():
        fields["presentation"] = "原文未注明" + ('@' + source_scope if source_scope else '')
    if source_facts and not fields.get("study_id", "").strip():
        fields["study_id"] = "原文未注明" + ('@' + source_scope if source_scope else '')
    try:
        key = RecordKey(
            **{
                k: fields.get(k, "")
                for k in ("project", "sample_type", "batch", "presentation", "study", "study_id", "condition")
            },
            purpose=fields.get("purpose") or "unknown",
            reporting_basis=fields.get("reporting_basis", ""),
            month=month, day=day,
            item=item.id,
            method=method,
            method_version=version,
        )
    except ValidationError as exc:
        labels = {"sample_type": "样品类型", "batch": "批号", "presentation": "规格", "study": "研究类型", "purpose": "用途",
                  "study_id": "研究编号", "condition": "条件", "month": "时间点", "method": "方法"}
        missing = list(dict.fromkeys(labels.get(str(e["loc"][0]), str(e["loc"][0])) for e in exc.errors() if e["loc"]))
        raise NeedsReview("missing_identity", "请核对或补充：" + "、".join(missing or ["检测身份与时间点"])) from exc
    from .standards import associations
    return StabilityRecord(key=key, result=interpret_result(raw, fields.get("result", ""), unit, item.result_type),
                           standards=associations(raw, fields))


def map_corrected(raw, context, config, changes, acknowledge_warning=False, *, source_facts=False, source_scope='',interpreted_fields=None):
    """Explicit human extraction corrections; original evidence remains untouched."""
    from .xlsx_revisions import is_deleted, retained_fields
    remembered = None
    if raw.revisions:
        if is_deleted(raw):
            raise NeedsReview('deleted_source', '原项目/结果已全划线或标注删除；请在分表核对后排除，不作为有效结果。')
        identity_only = set(raw.revisions) <= {'test_item', 'method'}
        source_fields = interpret_raw(raw, raw.fields) if raw.adapter == 'stability_matrix' else raw.fields
        remembered = next((i for i in config.items if " ".join(source_fields.get('test_item', '').split()).casefold() in
                    {" ".join(a.split()).casefold() for a in (i.id, i.label, *i.aliases)}), None) if identity_only else None
        if not acknowledge_warning:
            raise NeedsReview('source_revision', '原格含修订删除线；请确认保留未划线内容，可一次应用同名项目并记住映射。')
    hazards = tuple(h for h in raw.hazards if not any(
        h == f"Row/document metadata differ for {key}" for key in changes
    ))
    acknowledged = acknowledge_warning and "result" in changes
    effective = retained_fields(raw) if raw.revisions else raw.fields
    interpreted = interpreted_fields if interpreted_fields is not None else interpret_raw(raw, effective) if raw.adapter == "stability_matrix" else effective
    if raw.subresult_hint:
        interpreted = {**interpreted, 'test_item': interpreted.get('test_item', '') + ' · ' + raw.subresult_hint}
    if source_facts:
        from .matrix_fields import presentation_lines
        lines = presentation_lines(interpreted.get('presentation',''))
        if len(lines)==1:
            interpreted = {**interpreted,'presentation':lines[0]}
    if remembered is not None:
        interpreted = {**interpreted, 'test_item': remembered.id, 'method': remembered.method}
    from .standards import select
    effective_fields = select(raw, {**interpreted, **changes})
    corrected = raw.model_copy(update={
        'number_format': 'General' if 'result' in changes else raw.number_format,
        'source_value': {k:v for k,v in raw.source_value.items() if k.endswith('_rule_version')} | {'raw_type': 's'} if 'result' in changes else raw.source_value,
        "adapter": "long_table", "fields": effective_fields, "hazards": hazards,
        "comment": "" if acknowledged else raw.comment, "formula": "" if acknowledged else raw.formula,
    })
    context = context.model_copy(update={"defaults": {k: v for k, v in context.defaults.items() if k not in changes}})
    return map_observation(corrected, context, config, source_facts=source_facts, source_scope=source_scope)
