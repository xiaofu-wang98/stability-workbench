"""Platform-level 4-tier standard assay catalog (6 categories + subcategories + assays/methods/components/aliases)."""
from __future__ import annotations

import json
import re
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any

from .models import digest

SEED_PATH = Path(__file__).resolve().parent / "data" / "platform_catalog_seed.json"

DEFAULT_CATEGORY_CODE = "physicochemical_other"
CATEGORY_ORDER_FALLBACK = 99


@lru_cache(maxsize=1)
def load_seed_catalog() -> dict[str, Any]:
    return json.loads(SEED_PATH.read_text(encoding="utf-8"))


def normalize_catalog_token(text: str | None) -> str:
    """Deterministic text normalization for catalog alias & signature matching."""
    if not text:
        return ""
    s = str(text)
    replacements = {
        "（": "(",
        "）": ")",
        "：": ":",
        "％": "%",
        "＋": "+",
        "－": "-",
        "–": "-",
        "—": "-",
        "µ": "μ",
        "　": " ",
    }
    for src, dst in replacements.items():
        s = s.replace(src, dst)
    s = re.sub(r"^\s*\d+[.、．]\s*", "", s)
    s = re.sub(r"\s+", " ", s).strip().casefold()
    return s


def canonical_unit(unit: str | None) -> str:
    """Normalize unit spelling to SOP(G)-RQA-0-001 canonical representation without converting physical dimensions."""
    if not unit:
        return ""
    raw = str(unit).strip()
    if not raw:
        return ""
    seed = load_seed_catalog()
    unit_map = seed.get("unit_canonical_map", {})
    if raw in unit_map:
        return unit_map[raw]
    compact = re.sub(r"\s+", " ", raw).replace("％", "%").replace("µ", "μ").strip()
    if compact in unit_map:
        return unit_map[compact]
    lowered = compact.casefold()
    for key, val in unit_map.items():
        if key.casefold() == lowered:
            return val
    # Handle cfu / 10 ml spacing variants cleanly
    cfu = re.fullmatch(r"cfu\s*/\s*(\d+)\s*ml", lowered, re.I)
    if cfu:
        return f"cfu/{cfu.group(1)} mL"
    if re.fullmatch(r"cfu\s*/\s*ml", lowered, re.I):
        return "cfu/mL"
    return compact


def units_equivalent(unit_a: str | None, unit_b: str | None) -> bool:
    """Two units are equivalent only if their SOP(G)-RQA-0-001 canonical spellings match."""
    return canonical_unit(unit_a).casefold() == canonical_unit(unit_b).casefold()


def normalize_method_signature(method: str | None) -> str:
    """Deterministic canonical method family signature."""
    m = normalize_catalog_token(method)
    if not m:
        return ""
    # Strip trailing Chinese/English method suffixes
    m_stripped = re.sub(r"(?:法|\s+method|\s+assay)$", "", m).strip()
    is_ce = bool(re.search(r"ce[\s-]*sds|毛细管(?:凝胶)?电泳", m))
    if is_ce and ("非还原" in m or re.search(r"(?<![a-z])nr(?![a-z])|non[\s-]*reduced", m)):
        return "nr-ce-sds"
    if is_ce and ("还原" in m or re.search(r"(?<![a-z])r(?![a-z])|\breduced\b", m)):
        return "r-ce-sds"
    if re.search(r"(?<![a-z])nrce(?![a-z])", m):
        return "nr-ce-sds"
    if re.search(r"(?<![a-z])rce(?![a-z])", m):
        return "r-ce-sds"
    if re.search(r"sec[\s-]*uplc", m):
        return "sec-hplc"
    if re.search(r"sec[\s-]*hplc|\bsec\b|分子排阻|体积排阻", m):
        return "sec-hplc"
    if re.search(r"icief|全柱成像", m):
        return "icief"
    if re.search(r"\bcief\b|毛细管等电聚焦", m):
        return "cief"
    if re.search(r"cex[\s-]*hplc|\bcex\b|阳离子交换|iec[\s-]*hplc", m):
        return "cex-hplc"
    if re.search(r"aex[\s-]*hplc|\baex\b|阴离子交换", m):
        return "aex-hplc"
    if re.search(r"\bcze\b|毛细管区带", m):
        return "cze"
    if re.search(r"rp[\s-]*uplc|反相超高效", m):
        return "rp-uplc"
    if re.search(r"rp[\s-]*hplc|反相高效", m):
        return "rp-hplc"
    if re.search(r"hic[\s-]*hplc|\bhic\b|疏水色谱", m):
        return "hic-hplc"
    if re.search(r"hilic[\s-]*uplc|2-ab|寡糖|糖谱", m):
        return "hilic-uplc"
    if re.search(r"lc[\s-]*ms|液质", m):
        return "lc-ms"
    if re.search(r"elisa|酶联免疫", m):
        return "elisa"
    if re.search(r"报告基因|reporter\s*gene", m):
        return "reporter-gene"
    if re.search(r"电化学发光|\becl\b|\bmsd\b", m):
        return "ecl"
    if re.search(r"细胞杀伤|细胞增殖|cytotoxicity|bioassay", m):
        return "cell-cytotoxicity"
    if re.search(r"camp", m):
        return "cell-camp"
    if re.search(r"\buv(?:-vis)?\b|紫外|a280|solovpe", m):
        return "uv"
    if re.search(r"hplc[\s-]*cad|\bcad\b", m):
        return "hplc-cad"
    if re.search(r"hplc[\s-]*elsd|\belsd\b", m):
        return "hplc-elsd"
    if re.search(r"薄膜过滤|membrane\s*filtration", m):
        return "sterility-membrane"
    if re.search(r"直接接种|direct\s*inoculation", m):
        return "sterility-direct"
    if re.search(r"动态显色|显色基质|kinetic[\s-]*chromogenic", m):
        return "endotoxin-kinetic-chromogenic"
    if re.search(r"动态浊度|kinetic[\s-]*turbidimetric", m):
        return "endotoxin-kinetic-turbidimetric"
    if re.search(r"凝胶|gel[\s-]*clot", m):
        return "endotoxin-gel-clot"
    if re.search(r"光阻|light\s*obscuration", m):
        return "light-obscuration"
    if re.search(r"显微计数|microscopic", m):
        return "microscopic-count"
    if re.search(r"可见异物|灯检|visible\s*particle", m):
        return "visible-particles"
    if re.search(r"目视比色|目视|观察|visual|observation", m):
        return "visual"
    if re.search(r"冰点下降|渗透压|freezing\s*point|osmomet", m):
        return "freezing-point"
    if re.search(r"ph值测定|电位|ph\s*electrode|potentiomet", m):
        return "ph-electrode"
    if re.search(r"卡尔费休|费休|karl\s*fis?cher|\bkf\b", m):
        return "karl-fischer"
    if re.search(r"可抽提体积|extractable\s*volume", m):
        return "extractable-volume"
    if re.search(r"高压放电|\bhvld\b", m):
        return "ccit-hvld"
    if re.search(r"真空衰减|vacuum\s*decay", m):
        return "ccit-vacuum"
    if re.search(r"色水|dye\s*ingress", m):
        return "ccit-dye"
    if re.search(r"qpcr|荧光定量pcr", m):
        return "qpcr"
    return m_stripped


def normalize_component_signature(component: str | None) -> str:
    """Deterministic sub-component canonical key."""
    c = normalize_catalog_token(component)
    if not c:
        return ""
    if re.search(r"重链\s*%?\s*\+\s*轻链|hc\s*%?\s*\+\s*lc", c):
        return "hc_lc"
    if re.search(r"非糖基化重链|\bnghc\b", c):
        return "nghc"
    if re.search(r"单体|\bmonomer\b|主峰|main\s*peak|intact\s*igg", c):
        return "monomer"
    if re.search(r"高分子量|聚合体|聚体|\bhmws?\b|aggregate", c):
        return "hmw"
    if re.search(r"低分子量|片段|碎片|\blmws?\b|fragment", c):
        return "lmw"
    if re.search(r"酸性|酸峰|acidic", c):
        return "acidic"
    if re.search(r"碱性|碱峰|basic", c):
        return "basic"
    if re.search(r"其他峰|\bothers?\b", c):
        return "others"
    if re.search(r"≥\s*10\s*μ?m|>=\s*10\s*μ?m", c):
        return "ge_10um"
    if re.search(r"≥\s*25\s*μ?m|>=\s*25\s*μ?m", c):
        return "ge_25um"
    if re.search(r"图谱一致|profile\s*consisten", c):
        return "profile_consistency"
    if re.search(r"量效曲线|曲线相似|dose[\s-]*response", c):
        return "curve_consistency"
    if re.search(r"主峰.*pi.*差值|pi.*diff", c):
        return "main_peak_pi_diff"
    if re.search(r"主峰.*(?:pi|等电点)|main\s*peak\s*pi", c):
        return "main_peak_pi"
    return re.sub(r"[()%]", "", c).strip()


def are_parent_child_methods(line_a: str, line_b: str) -> str | None:
    """If two method lines form an explicit pharmacopoeial parent-child pair (e.g. 无菌检查法 + 薄膜过滤法),
    return the more specific child line. Otherwise return None (preserving genuine dual-method ambiguity)."""
    a = normalize_catalog_token(line_a)
    b = normalize_catalog_token(line_b)
    if not a or not b:
        return None
    # Strip trailing '法' / 'method' for matching
    a_core = re.sub(r"(?:检查)?法$|\s+method$", "", a).strip()
    b_core = re.sub(r"(?:检查)?法$|\s+method$", "", b).strip()
    if a_core == b_core:
        return line_a.strip()
    seed = load_seed_catalog()
    for parent, child in seed.get("parent_child_method_pairs", []):
        p_norm = normalize_catalog_token(parent)
        c_norm = normalize_catalog_token(child)
        if p_norm in a and c_norm in b:
            return line_b.strip()
        if p_norm in b and c_norm in a:
            return line_a.strip()
    return None


def seed_platform_catalog(conn: sqlite3.Connection) -> None:
    """Idempotently populate platform 4-tier catalog tables from versioned seed JSON."""
    seed = load_seed_catalog()
    for cat in seed.get("categories", []):
        conn.execute(
            """INSERT OR IGNORE INTO platform_categories(code, display_order, name_zh, name_en, description)
               VALUES (?,?,?,?,?)""",
            (cat["code"], cat["display_order"], cat["name_zh"], cat["name_en"], cat.get("description", "")),
        )
        for alias in cat.get("aliases", []):
            _insert_alias(conn, "category", cat["code"], alias, source="seed")

    for sub in seed.get("subcategories", []):
        conn.execute(
            """INSERT OR IGNORE INTO platform_subcategories(code, category_code, display_order, name_zh, name_en)
               VALUES (?,?,?,?,?)""",
            (sub["code"], sub["category_code"], sub["display_order"], sub["name_zh"], sub["name_en"]),
        )

    for assay in seed.get("assays", []):
        assay_id = f"plat_assay_{assay['code']}"
        conn.execute(
            """INSERT OR IGNORE INTO platform_assays(
                   id, code, category_code, subcategory_code, display_order,
                   name_zh, name_en, default_unit, pharmacopoeia_scope, modality_tags_json, is_builtin, status
               ) VALUES (?,?,?,?,?,?,?,?,?,?,1,'active')""",
            (
                assay_id,
                assay["code"],
                assay["category_code"],
                assay.get("subcategory_code", ""),
                assay.get("display_order", 999),
                assay["name_zh"],
                assay.get("name_en", ""),
                canonical_unit(assay.get("default_unit", "")),
                assay.get("pharmacopoeia_scope", ""),
                json.dumps(assay.get("modality_tags", []), ensure_ascii=False),
            ),
        )
        for alias in (assay["name_zh"], assay.get("name_en", ""), *assay.get("aliases", [])):
            if alias:
                _insert_alias(conn, "assay", assay_id, alias, source="seed")

        for method in assay.get("methods", []):
            method_id = f"plat_method_{assay['code']}_{method['code']}"
            conn.execute(
                """INSERT OR IGNORE INTO platform_methods(
                       id, assay_id, code, name_zh, name_en, method_family, default_unit, is_builtin
                   ) VALUES (?,?,?,?,?,?,?,1)""",
                (
                    method_id,
                    assay_id,
                    method["code"],
                    method["name_zh"],
                    method.get("name_en", ""),
                    method.get("method_family", method["code"]),
                    canonical_unit(method.get("default_unit", "")),
                ),
            )
            for alias in (method["name_zh"], method.get("name_en", ""), *method.get("aliases", [])):
                if alias:
                    _insert_alias(conn, "method", method_id, alias, source="seed")

        for comp in assay.get("components", []):
            comp_id = f"plat_comp_{assay['code']}_{comp['code']}"
            conn.execute(
                """INSERT OR IGNORE INTO platform_components(
                       id, assay_id, method_id, code, name_zh, name_en, pharmacopoeia,
                       default_unit, value_kind, display_order, is_builtin
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,1)""",
                (
                    comp_id,
                    assay_id,
                    "",
                    comp["code"],
                    comp["name_zh"],
                    comp.get("name_en", ""),
                    comp.get("pharmacopoeia", ""),
                    canonical_unit(comp.get("unit", "")),
                    comp.get("value_kind", "numeric"),
                    comp.get("display_order", 99),
                ),
            )
            for alias in (comp["name_zh"], comp.get("name_en", ""), *comp.get("aliases", [])):
                if alias:
                    _insert_alias(conn, "component", comp_id, alias, source="seed")


def _insert_alias(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: str,
    raw_alias: str,
    *,
    locale: str = "",
    priority: int = 10,
    source: str = "seed",
) -> None:
    norm = normalize_catalog_token(raw_alias)
    if not norm:
        return
    if not locale:
        locale = "zh" if any("\u4e00" <= ch <= "\u9fff" for ch in raw_alias) else "en"
    alias_id = "alias_" + digest([entity_type, entity_id, norm])[:24]
    conn.execute(
        """INSERT OR IGNORE INTO platform_aliases(
               id, entity_type, entity_id, raw_alias, norm_signature, locale, priority, source
           ) VALUES (?,?,?,?,?,?,?,?)""",
        (alias_id, entity_type, entity_id, raw_alias.strip(), norm, locale, priority, source),
    )


def _has_platform_tables(conn: sqlite3.Connection | None) -> bool:
    if conn is None:
        return False
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='platform_assays'"
    ).fetchone()
    return row is not None


def _seed_lookup_index() -> dict[str, Any]:
    """In-memory fast index from seed JSON for read-only / connectionless matching."""
    seed = load_seed_catalog()
    categories = {c["code"]: c for c in seed.get("categories", [])}
    subcategories = {s["code"]: s for s in seed.get("subcategories", [])}
    cat_aliases: dict[str, str] = {}
    for c in seed.get("categories", []):
        for a in (c["code"], c["name_zh"], c.get("name_en", ""), *c.get("aliases", [])):
            tok = normalize_catalog_token(a)
            if tok:
                cat_aliases[tok] = c["code"]

    assays = []
    for a in seed.get("assays", []):
        assay_id = f"plat_assay_{a['code']}"
        alias_tokens = {
            normalize_catalog_token(x)
            for x in (a["name_zh"], a.get("name_en", ""), *a.get("aliases", []))
            if normalize_catalog_token(x)
        }
        method_families = {
            m.get("method_family", m["code"])
            for m in a.get("methods", [])
        }
        method_aliases = {
            normalize_catalog_token(x)
            for m in a.get("methods", [])
            for x in (m["name_zh"], m.get("name_en", ""), *m.get("aliases", []))
            if normalize_catalog_token(x)
        }
        assays.append({
            "id": assay_id,
            **a,
            "alias_tokens": alias_tokens,
            "method_families": method_families,
            "method_aliases": method_aliases,
        })
    return {
        "categories": categories,
        "subcategories": subcategories,
        "cat_aliases": cat_aliases,
        "assays": assays,
    }


@lru_cache(maxsize=1)
def _cached_seed_index() -> dict[str, Any]:
    return _seed_lookup_index()


def resolve_category_code(raw_category: str | None) -> str:
    """Resolve an explicit category string (e.g. '2.纯度和杂质 / Purity and Impurity') to one of the 6 category codes."""
    if not raw_category:
        return ""
    idx = _cached_seed_index()
    for part in re.split(r"[\r\n/|]+", str(raw_category)):
        tok = normalize_catalog_token(part)
        if tok in idx["cat_aliases"]:
            return idx["cat_aliases"][tok]
    full = normalize_catalog_token(raw_category)
    for alias, code in idx["cat_aliases"].items():
        if len(alias) >= 2 and alias in full:
            return code
    return ""


def match_platform_assay(
    *,
    conn: sqlite3.Connection | None = None,
    raw_category: str = "",
    item_text: str = "",
    method_text: str = "",
    component_text: str = "",
) -> dict[str, Any] | None:
    """Deterministically match raw assay/method/component metadata to a platform standard assay."""
    idx = _cached_seed_index()
    item_norm = normalize_catalog_token(item_text)
    method_sig = normalize_method_signature(method_text)
    method_norm = normalize_catalog_token(method_text)
    cat_code = resolve_category_code(raw_category)

    # First check dynamic SQLite aliases if a connection is available (supports user-added aliases/assays)
    if _has_platform_tables(conn):
        assert conn is not None
        raw_parts = [normalize_catalog_token(p) for p in re.split(r"[\r\n·|/]+", item_text or "") if normalize_catalog_token(p)]
        candidates_to_try = list(dict.fromkeys([t for t in (item_norm, normalize_catalog_token(item_text.split("\n")[0] if item_text else ""), *raw_parts) if t]))
        for token in candidates_to_try:
            rows = conn.execute(
                """SELECT a.id, a.code, a.category_code, a.subcategory_code, a.display_order,
                          a.name_zh, a.name_en, a.default_unit, a.pharmacopoeia_scope,
                          c.name_zh AS category_name_zh, c.name_en AS category_name_en, c.display_order AS category_order,
                          COALESCE(s.name_zh, '') AS subcategory_name_zh, COALESCE(s.name_en, '') AS subcategory_name_en
                   FROM platform_aliases al
                   JOIN platform_assays a ON a.id = al.entity_id
                   JOIN platform_categories c ON c.code = a.category_code
                   LEFT JOIN platform_subcategories s ON s.code = a.subcategory_code
                   WHERE al.entity_type = 'assay' AND al.norm_signature = ? AND a.status = 'active'""",
                (token,),
            ).fetchall()
            if len(rows) == 1:
                row = dict(rows[0])
                row["match_source"] = "platform_db_alias"
                return row
            if len(rows) > 1 and method_sig:
                narrowed = []
                for r in rows:
                    m_rows = conn.execute(
                        "SELECT method_family FROM platform_methods WHERE assay_id=?", (r["id"],)
                    ).fetchall()
                    if any(mr["method_family"] == method_sig for mr in m_rows):
                        narrowed.append(r)
                if len(narrowed) == 1:
                    row = dict(narrowed[0])
                    row["match_source"] = "platform_db_alias_method"
                    return row

    # Deterministic in-memory seed matching (strict, no fuzzy guessing)
    assays = idx["assays"]

    def _build_hit(a: dict[str, Any], source: str) -> dict[str, Any]:
        cat = idx["categories"][a["category_code"]]
        sub = idx["subcategories"].get(a.get("subcategory_code", ""), {})
        # Find best matching component if provided
        matched_comp = None
        comp_sig = normalize_component_signature(component_text)
        if comp_sig:
            for c in a.get("components", []):
                c_sigs = {
                    normalize_component_signature(x)
                    for x in (c["code"], c["name_zh"], c.get("name_en", ""), *c.get("aliases", []))
                }
                if comp_sig in c_sigs:
                    matched_comp = c
                    break
        return {
            "id": a["id"],
            "code": a["code"],
            "category_code": a["category_code"],
            "category_name_zh": cat["name_zh"],
            "category_name_en": cat["name_en"],
            "category_order": cat["display_order"],
            "subcategory_code": a.get("subcategory_code", ""),
            "subcategory_name_zh": sub.get("name_zh", ""),
            "subcategory_name_en": sub.get("name_en", ""),
            "display_order": a.get("display_order", 999),
            "name_zh": a["name_zh"],
            "name_en": a.get("name_en", ""),
            "default_unit": (matched_comp.get("unit") if matched_comp else "") or a.get("default_unit", ""),
            "value_kind": matched_comp.get("value_kind", "") if matched_comp else "",
            "matched_component": matched_comp,
            "match_source": source,
        }

    # 1. Exact alias token match on item (split bilingual lines/slashes)
    item_parts = [normalize_catalog_token(p) for p in re.split(r"[\r\n·|/]+", item_text or "") if normalize_catalog_token(p)]
    if item_norm and item_norm not in item_parts:
        item_parts.insert(0, item_norm)

    exact_hits = []
    for a in assays:
        if any(p in a["alias_tokens"] for p in item_parts):
            exact_hits.append(a)
    if len(exact_hits) == 1:
        return _build_hit(exact_hits[0], "seed_exact_item")
    if len(exact_hits) > 1:
        if method_sig:
            by_method = [a for a in exact_hits if method_sig in a["method_families"]]
            if len(by_method) == 1:
                return _build_hit(by_method[0], "seed_item_method")
        if cat_code:
            by_cat = [a for a in exact_hits if a["category_code"] == cat_code]
            if len(by_cat) == 1:
                return _build_hit(by_cat[0], "seed_item_category")

    # 2. Pure "纯度 / Purity" + specific method family disambiguation (very common in QC specs & matrices)
    if any(k in item_norm for k in ("纯度", "purity")) and method_sig:
        purity_map = {
            "sec-hplc": "sec_purity",
            "nr-ce-sds": "nr_ce_sds",
            "r-ce-sds": "r_ce_sds",
            "rp-hplc": "rp_purity",
            "rp-uplc": "rp_purity",
            "icief": "charge_variants",
            "cex-hplc": "charge_variants",
            "aex-hplc": "charge_variants",
            "cze": "charge_variants",
        }
        target_code = purity_map.get(method_sig)
        if target_code:
            for a in assays:
                if a["code"] == target_code:
                    return _build_hit(a, "seed_purity_method")

    # 3. Substring keyword match against unique assay alias combined with method/category
    combined = f"{item_norm} {method_norm}".strip()
    if combined:
        scored: list[tuple[int, dict[str, Any]]] = []
        for a in assays:
            score = 0
            if any(len(tok) >= 2 and tok in item_norm for tok in a["alias_tokens"]):
                score += 4
            if method_sig and method_sig in a["method_families"]:
                score += 3
            if cat_code and a["category_code"] == cat_code:
                score += 1
            if score >= 4:
                scored.append((score, a))
        if scored:
            scored.sort(key=lambda pair: (-pair[0], pair[1].get("display_order", 999)))
            top_score = scored[0][0]
            top_items = [a for sc, a in scored if sc == top_score]
            if len(top_items) == 1:
                return _build_hit(top_items[0], "seed_scored_match")

    # 4. Fallback: method-only deterministic identification when method uniquely maps to one assay
    if method_sig:
        method_hits = [a for a in assays if method_sig in a["method_families"]]
        if len(method_hits) == 1:
            return _build_hit(method_hits[0], "seed_unique_method")

    return None


def resolve_category_metadata(
    *,
    conn: sqlite3.Connection | None = None,
    raw_category: str = "",
    item_text: str = "",
    method_text: str = "",
    component_text: str = "",
) -> dict[str, Any]:
    """Always return structured 6-category metadata (never raises; falls back cleanly)."""
    idx = _cached_seed_index()
    hit = match_platform_assay(
        conn=conn,
        raw_category=raw_category,
        item_text=item_text,
        method_text=method_text,
        component_text=component_text,
    )
    if hit:
        return {
            "category_code": hit["category_code"],
            "category_name_zh": hit["category_name_zh"],
            "category_name_en": hit["category_name_en"],
            "category_order": hit["category_order"],
            "subcategory_code": hit.get("subcategory_code", ""),
            "subcategory_name_zh": hit.get("subcategory_name_zh", ""),
            "subcategory_name_en": hit.get("subcategory_name_en", ""),
            "platform_assay_id": hit["id"],
            "display_order": hit.get("display_order", 999),
            "default_unit": hit.get("default_unit", ""),
            "matched_assay_zh": hit.get("name_zh", ""),
            "matched_assay_en": hit.get("name_en", ""),
        }
    explicit_code = resolve_category_code(raw_category)
    if explicit_code and explicit_code in idx["categories"]:
        cat = idx["categories"][explicit_code]
        return {
            "category_code": explicit_code,
            "category_name_zh": cat["name_zh"],
            "category_name_en": cat["name_en"],
            "category_order": cat["display_order"],
            "subcategory_code": "",
            "subcategory_name_zh": "",
            "subcategory_name_en": "",
            "platform_assay_id": "",
            "display_order": cat["display_order"] * 100 + 99,
            "default_unit": "",
            "matched_assay_zh": "",
            "matched_assay_en": "",
        }
    fallback = idx["categories"][DEFAULT_CATEGORY_CODE]
    return {
        "category_code": DEFAULT_CATEGORY_CODE,
        "category_name_zh": fallback["name_zh"],
        "category_name_en": fallback["name_en"],
        "category_order": fallback["display_order"],
        "subcategory_code": "",
        "subcategory_name_zh": "",
        "subcategory_name_en": "",
        "platform_assay_id": "",
        "display_order": 999,
        "default_unit": "",
        "matched_assay_zh": "",
        "matched_assay_en": "",
    }


def list_platform_catalog(
    conn: sqlite3.Connection,
    *,
    category_code: str = "",
    subcategory_code: str = "",
    query: str = "",
) -> dict[str, Any]:
    """Return full platform 4-tier catalog for API / UI browsing and management."""
    categories = [
        dict(r)
        for r in conn.execute(
            "SELECT code, display_order, name_zh, name_en, description FROM platform_categories ORDER BY display_order"
        )
    ]
    subcategories = [
        dict(r)
        for r in conn.execute(
            "SELECT code, category_code, display_order, name_zh, name_en FROM platform_subcategories ORDER BY category_code, display_order"
        )
    ]
    sql = """SELECT a.id, a.code, a.category_code, a.subcategory_code, a.display_order,
                    a.name_zh, a.name_en, a.default_unit, a.pharmacopoeia_scope,
                    a.modality_tags_json, a.is_builtin, a.status,
                    c.name_zh AS category_name_zh, c.name_en AS category_name_en, c.display_order AS category_order,
                    COALESCE(s.name_zh, '') AS subcategory_name_zh, COALESCE(s.name_en, '') AS subcategory_name_en
             FROM platform_assays a
             JOIN platform_categories c ON c.code = a.category_code
             LEFT JOIN platform_subcategories s ON s.code = a.subcategory_code
             WHERE a.status = 'active'"""
    params: list[Any] = []
    if category_code:
        sql += " AND a.category_code = ?"
        params.append(category_code)
    if subcategory_code:
        sql += " AND a.subcategory_code = ?"
        params.append(subcategory_code)
    sql += " ORDER BY c.display_order, a.display_order, a.name_zh"

    assays = [dict(r) for r in conn.execute(sql, params)]
    methods_by_assay: dict[str, list[dict[str, Any]]] = {}
    for m in conn.execute(
        "SELECT id, assay_id, code, name_zh, name_en, method_family, default_unit, is_builtin FROM platform_methods ORDER BY id"
    ):
        methods_by_assay.setdefault(m["assay_id"], []).append(dict(m))

    comps_by_assay: dict[str, list[dict[str, Any]]] = {}
    for c in conn.execute(
        """SELECT id, assay_id, method_id, code, name_zh, name_en, pharmacopoeia,
                  default_unit, value_kind, display_order, is_builtin
           FROM platform_components ORDER BY display_order, id"""
    ):
        comps_by_assay.setdefault(c["assay_id"], []).append(dict(c))

    aliases_by_entity: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for al in conn.execute(
        "SELECT id, entity_type, entity_id, raw_alias, norm_signature, locale, source FROM platform_aliases ORDER BY priority, raw_alias"
    ):
        aliases_by_entity.setdefault((al["entity_type"], al["entity_id"]), []).append(dict(al))

    q_norm = normalize_catalog_token(query)
    enriched = []
    for a in assays:
        aid = a["id"]
        a["modality_tags"] = json.loads(a.pop("modality_tags_json", "[]") or "[]")
        a["methods"] = [
            {**m, "aliases": aliases_by_entity.get(("method", m["id"]), [])}
            for m in methods_by_assay.get(aid, [])
        ]
        a["components"] = [
            {**c, "aliases": aliases_by_entity.get(("component", c["id"]), [])}
            for c in comps_by_assay.get(aid, [])
        ]
        a["aliases"] = aliases_by_entity.get(("assay", aid), [])
        if q_norm:
            haystack = " ".join(
                [
                    a["name_zh"],
                    a["name_en"],
                    a["code"],
                    a["category_name_zh"],
                    a["subcategory_name_zh"],
                    *(al["raw_alias"] for al in a["aliases"]),
                    *(m["name_zh"] + " " + m["name_en"] for m in a["methods"]),
                    *(al["raw_alias"] for m in a["methods"] for al in m["aliases"]),
                    *(c["name_zh"] + " " + c["name_en"] for c in a["components"]),
                ]
            )
            if q_norm not in normalize_catalog_token(haystack):
                continue
        enriched.append(a)

    seed = load_seed_catalog()
    return {
        "catalog_version": seed.get("catalog_version", "2026.11-r3"),
        "unit_standard": seed.get("unit_standard", "SOP(G)-RQA-0-001"),
        "categories": categories,
        "subcategories": subcategories,
        "assays": enriched,
        "total_assays": len(enriched),
    }


def create_platform_assay(
    conn: sqlite3.Connection,
    *,
    category_code: str,
    name_zh: str,
    name_en: str = "",
    subcategory_code: str = "",
    default_unit: str = "",
    pharmacopoeia_scope: str = "ChP/USP/EP",
    modality_tags: list[str] | None = None,
    methods: list[dict[str, str]] | None = None,
    components: list[dict[str, Any]] | None = None,
    aliases: list[str] | None = None,
) -> dict[str, Any]:
    """Create or update a platform-level standard assay entry."""
    cat = conn.execute("SELECT display_order FROM platform_categories WHERE code=?", (category_code,)).fetchone()
    if not cat:
        raise ValueError(f"Unknown platform category_code: {category_code}")
    code = "custom_" + digest([category_code, normalize_catalog_token(name_zh), normalize_catalog_token(name_en)])[:12]
    assay_id = f"plat_assay_{code}"
    display_order = cat["display_order"] * 100 + 80
    conn.execute(
        """INSERT INTO platform_assays(
               id, code, category_code, subcategory_code, display_order,
               name_zh, name_en, default_unit, pharmacopoeia_scope, modality_tags_json, is_builtin, status
           ) VALUES (?,?,?,?,?,?,?,?,?,?,0,'active')
           ON CONFLICT(id) DO UPDATE SET
               category_code=excluded.category_code,
               subcategory_code=excluded.subcategory_code,
               name_zh=excluded.name_zh,
               name_en=excluded.name_en,
               default_unit=excluded.default_unit,
               pharmacopoeia_scope=excluded.pharmacopoeia_scope,
               updated_at=CURRENT_TIMESTAMP""",
        (
            assay_id,
            code,
            category_code,
            subcategory_code or "",
            display_order,
            name_zh.strip(),
            name_en.strip(),
            canonical_unit(default_unit),
            pharmacopoeia_scope.strip(),
            json.dumps(modality_tags or ["mAb"], ensure_ascii=False),
        ),
    )
    for alias in (name_zh, name_en, *(aliases or [])):
        if alias and alias.strip():
            _insert_alias(conn, "assay", assay_id, alias, source="user")
    for m in methods or []:
        m_zh = (m.get("name_zh") or "").strip()
        if not m_zh:
            continue
        m_en = (m.get("name_en") or "").strip()
        m_code = normalize_method_signature(m_zh) or digest([m_zh])[:10]
        m_id = f"plat_method_{code}_{m_code}"
        conn.execute(
            """INSERT OR IGNORE INTO platform_methods(id, assay_id, code, name_zh, name_en, method_family, default_unit, is_builtin)
               VALUES (?,?,?,?,?,?,?,0)""",
            (m_id, assay_id, m_code, m_zh, m_en, m_code, canonical_unit(m.get("default_unit", ""))),
        )
        for alias in (m_zh, m_en):
            if alias:
                _insert_alias(conn, "method", m_id, alias, source="user")
    for idx_c, c in enumerate(components or [], 1):
        c_zh = (c.get("name_zh") or "").strip()
        if not c_zh:
            continue
        c_en = (c.get("name_en") or "").strip()
        c_code = normalize_component_signature(c_zh) or digest([c_zh])[:10]
        c_id = f"plat_comp_{code}_{c_code}"
        conn.execute(
            """INSERT OR IGNORE INTO platform_components(
                   id, assay_id, method_id, code, name_zh, name_en, pharmacopoeia,
                   default_unit, value_kind, display_order, is_builtin
               ) VALUES (?,?,?,?,?,?,?,?,?,?,0)""",
            (
                c_id,
                assay_id,
                "",
                c_code,
                c_zh,
                c_en,
                (c.get("pharmacopoeia") or "").strip(),
                canonical_unit(c.get("unit", "")),
                c.get("value_kind") or "numeric",
                idx_c,
            ),
        )
        for alias in (c_zh, c_en):
            if alias:
                _insert_alias(conn, "component", c_id, alias, source="user")
    return {"id": assay_id, "code": code, "category_code": category_code, "name_zh": name_zh.strip(), "name_en": name_en.strip()}


def add_platform_alias(
    conn: sqlite3.Connection,
    *,
    entity_type: str,
    entity_id: str,
    raw_alias: str,
    source: str = "user",
) -> dict[str, Any]:
    if entity_type not in {"category", "assay", "method", "component"}:
        raise ValueError(f"Unsupported entity_type: {entity_type}")
    norm = normalize_catalog_token(raw_alias)
    if not norm:
        raise ValueError("Alias cannot be blank")
    _insert_alias(conn, entity_type, entity_id, raw_alias, source=source)
    return {"entity_type": entity_type, "entity_id": entity_id, "raw_alias": raw_alias.strip(), "norm_signature": norm}
