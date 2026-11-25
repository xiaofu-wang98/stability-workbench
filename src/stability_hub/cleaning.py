"""Deterministic Tier-A cleaning & audit trail (never mutates raw_text / frozen originals)."""
from __future__ import annotations

import re
from typing import Any

from .platform_catalog import (
    are_parent_child_methods,
    canonical_unit,
    match_platform_assay,
    normalize_method_signature,
)

EXCEPTION_REF_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:AR|AE|OOS|OOE|OOT|DEV|CC)[-－][A-Za-z0-9]+(?:[-－][A-Za-z0-9]+)*",
    re.I,
)
COMPARATOR_RESULT_RE = re.compile(r"^\s*([<≤>≥＜＞]|<=|>=)\s*\d+(?:\.\d+)?")

_KNOWN_GLUED_REPAIRS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bThe([a-z]{3,})"), r"The \1"),
    (re.compile(r"\b(is|are|was|were|be)(\d)"), r"\1 \2"),
    (re.compile(r"\b(is|are)([≤≥<>])"), r"\1 \2"),
    (re.compile(r"\band([a-z]{4,})"), r"and \1"),
    (re.compile(r"\bmg/via\b", re.I), "mg/vial"),
    (re.compile(r"\bmain\s+peak\s+pl\b"), "main peak pI"),
)


def repair_english_wrap_spaces(text: str | None) -> tuple[str, bool]:
    """Repair deterministic English hard-wrap missing spaces & known typos for matching/display only."""
    if not text:
        return "", False
    original = str(text)
    repaired = re.sub(r"(?<=[A-Za-z,])\r?\n(?=[A-Za-z])", " ", original)
    for pattern, repl in _KNOWN_GLUED_REPAIRS:
        repaired = pattern.sub(repl, repaired)
    repaired = re.sub(r"[ \t]{2,}", " ", repaired).strip()
    return repaired, (repaired != original.strip())


def extract_exception_refs(text: str | None) -> list[str]:
    """Deterministically extract structured AR/AE/OOS/OOE/OOT/DEV/CC reference numbers."""
    if not text:
        return []
    return list(dict.fromkeys(m.group().replace("－", "-").upper() for m in EXCEPTION_REF_RE.finditer(str(text))))


def clean_observation_events(raw: Any) -> list[dict[str, Any]]:
    """Compute non-blocking Tier-A deterministic normalization audit events for one RawObservation.
    Never modifies raw_text or frozen evidence."""
    events: list[dict[str, Any]] = []
    fields = getattr(raw, "fields", None) or {}
    loc = getattr(raw, "location", None)
    section = getattr(loc, "section", "") if loc else ""
    row_idx = getattr(loc, "row", 0) if loc else 0
    col_idx = getattr(loc, "column", 0) if loc else 0

    def add(rule_code: str, field: str, raw_val: str, cleaned_val: str, reason: str) -> None:
        events.append({
            "rule_code": rule_code,
            "field": field,
            "section": section,
            "row": row_idx,
            "column": col_idx,
            "raw_value": raw_val,
            "cleaned_value": cleaned_val,
            "reason": reason,
        })

    # A1: comparator-prefixed numeric result (e.g. '<0.2', '＜0.45')
    result_raw = str(fields.get("result", "") or "").strip()
    if result_raw and COMPARATOR_RESULT_RE.match(result_raw):
        norm_cmp = result_raw.replace("＜", "<").replace("＞", ">").replace("<=", "≤").replace(">=", "≥")
        if norm_cmp != result_raw or result_raw[0] in ("<", ">", "＜", "＞"):
            add("comparator_numeric_fix", "result", result_raw, norm_cmp, "带比较符数值按比较符+数值安全解析")

    # A2: SOP(G)-RQA-0-001 unit & fullwidth normalization
    unit_raw = str(fields.get("unit", "") or "").strip()
    if unit_raw:
        unit_clean = canonical_unit(unit_raw)
        if unit_clean != unit_raw:
            add("unit_and_width_normalize", "unit", unit_raw, unit_clean, "按 SOP(G)-RQA-0-001 规范单位写法")

    # A3: broken English wrap spaces in criterion/result
    for fld in ("criterion", "result"):
        val = str(fields.get(fld, "") or "")
        if val and any("a" <= ch.lower() <= "z" for ch in val):
            repaired, changed = repair_english_wrap_spaces(val)
            if changed:
                add("english_wrap_space_repair", fld, val[:160], repaired[:160], "修复英文断行丢空格用于匹配与规范展示（原值留痕）")

    # A4: parent-child method collapse or platform synonym match
    method_raw = str(fields.get("method", "") or "").strip()
    if method_raw:
        lines = [ln.strip() for ln in method_raw.splitlines() if ln.strip()]
        if len(lines) == 2:
            child = are_parent_child_methods(lines[0], lines[1])
            if child:
                add("catalog_synonym_canonicalize", "method", method_raw, child, "父子同义方法确定性归并")
        else:
            sig = normalize_method_signature(method_raw)
            hit = match_platform_assay(
                raw_category=fields.get("category", ""),
                item_text=fields.get("test_item", ""),
                method_text=method_raw,
            )
            if hit and sig and method_raw.casefold() != hit["name_zh"].casefold():
                # Record only when the raw method is a non-canonical shorthand (e.g. nrCE, SEC)
                if len(method_raw) <= 12 and method_raw.isascii():
                    add("catalog_synonym_canonicalize", "method", method_raw, sig, "缩写方法名经平台总词典归一化匹配")

    # A6: AR/AE/OOS reference extraction inside result cell
    refs = extract_exception_refs(result_raw)
    if refs:
        add("exception_ref_extraction", "result", result_raw[:160], ", ".join(refs), "结构化提取结果格内 AR/AE/OOS 异常编号")

    return events


def summarize_cleaning_audit(observations: list[Any], *, max_samples: int = 80) -> dict[str, Any]:
    """Aggregate Tier-A cleaning audit events for the collapsible review header drawer."""
    rule_labels = {
        "comparator_numeric_fix": "带比较符数值规范（A1）",
        "unit_and_width_normalize": "单位与全半角规范（A2）",
        "english_wrap_space_repair": "英文断行补空格（A3）",
        "catalog_synonym_canonicalize": "词典同义/父子方法归并（A4）",
        "footer_note_targeted_binding": "表尾备注精准定向（A5）",
        "exception_ref_extraction": "AR/AE 编号结构化拆出（A6）",
    }
    counts: dict[str, int] = {}
    samples: list[dict[str, Any]] = []
    total = 0
    for obs in observations:
        for ev in clean_observation_events(obs):
            code = ev["rule_code"]
            counts[code] = counts.get(code, 0) + 1
            total += 1
            if len(samples) < max_samples:
                samples.append({**ev, "rule_label": rule_labels.get(code, code)})
    return {
        "total": total,
        "by_rule": [
            {"rule_code": code, "rule_label": rule_labels.get(code, code), "count": count}
            for code, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        "samples": samples,
    }
