"""Register reviewed assay components in the existing project item dictionary."""

import json
import re
from typing import Literal

from pydantic import Field

from .models import ItemDefinition, Model, ProjectConfig, Text, digest
from .permissions import Capability, require
from .platform_catalog import (
    add_platform_alias,
    canonical_unit,
    create_platform_assay,
    normalize_component_signature,
    normalize_method_signature,
    resolve_category_metadata,
)
from .workflow_common import WorkflowError, bump, check_revision, event, project_row, remember, replay


def compute_norm_signature(assay_name, method, component_name, result_type="numeric", reporting_basis=""):
    """
    归一化签名：统一复用 platform_catalog 确定性方法/子结果签名规则，兼容既有签名接口。
    """
    def clean(s):
        if not s:
            return ""
        s = s.replace("（", "(").replace("）", ")").replace("：", ":").replace("％", "%")
        s = re.sub(r"\s+", " ", s).strip().lower()
        return s

    m_core = normalize_method_signature(method) or clean(method)
    c_core = normalize_component_signature(component_name) or clean(component_name)

    a = clean(assay_name)
    if "sec" in a or "纯度" in a:
        a_core = "purity"
    elif "ce-sds" in a:
        a_core = "ce-sds"
    elif "活性" in a or "效价" in a or "杀伤" in a:
        a_core = "potency"
    elif "电荷" in a or "icief" in a or "等电点" in a:
        a_core = "charge"
    else:
        a_core = a

    rt = clean(result_type) or "numeric"
    rb = clean(reporting_basis)

    parts = [a_core, m_core, c_core, rt]
    if rb:
        parts.append(rb)
    return "/".join(parts)


class ReviewedComponent(Model):
    table: int = Field(ge=0)
    row: int = Field(ge=1)
    assay_zh: Text
    assay_en: str = ""
    component_zh: Text
    component_en: str = ""
    method: Text
    method_version: Text = "not_reported"
    unit: str = ""
    result_type: Literal["numeric", "qualitative"]
    reporting_basis: str | None = Field(default=None, max_length=200)
    category_code: str = ""
    subcategory_code: str = ""


class RegisterComponents(Model):
    revision: int = Field(ge=0)
    idempotency_key: Text
    components: list[ReviewedComponent] = Field(min_length=1, max_length=150)


def _registered_components(conn, project, specification_id):
    return [dict(row) for row in conn.execute("""SELECT l.table_number AS "table",l.row_number AS "row",l.item_id,
               d.name_zh AS assay_zh,d.name_en AS assay_en,d.method,d.method_version,
               d.category_code,d.subcategory_code,d.platform_assay_id,d.display_order,
               COALESCE(pc.name_zh, '') AS category_name_zh,
               COALESCE(pc.name_en, '') AS category_name_en,
               COALESCE(pc.display_order, 99) AS category_order,
               COALESCE(ps.name_zh, '') AS subcategory_name_zh,
               c.name_zh AS component_zh,c.name_en AS component_en,c.unit,c.result_type,
               c.reporting_basis,c.norm_signature
        FROM specification_assay_components l JOIN assay_components c ON c.project=l.project AND c.item_id=l.item_id
        JOIN assay_definitions d ON d.id=c.assay_id
        LEFT JOIN platform_categories pc ON pc.code=d.category_code
        LEFT JOIN platform_subcategories ps ON ps.code=d.subcategory_code
        WHERE l.project=? AND l.specification_id=? ORDER BY l.table_number,l.row_number""", (project, specification_id))]


def registered_components(store, project, specification_id, actor):
    require(actor, Capability.VIEW)
    with store.read() as conn:
        project_row(conn, project)
        return _registered_components(conn, project, specification_id)


def register_components(store, project, specification_id, command, actor):
    require(actor, Capability.CONFIRM_FACT)
    with store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        payload = {"register_components": specification_id, **command.model_dump(mode="json")}
        if (prior := replay(conn, project, actor, command.idempotency_key, payload)) is not None:
            return prior
        check_revision(conn, project, command.revision)
        spec = conn.execute("SELECT preview_json FROM specifications WHERE id=?", (specification_id,)).fetchone()
        if not spec:
            raise WorkflowError("specification_missing", "找不到所选标准。", 404)
        preview = json.loads(spec["preview_json"])
        if preview.get("project") and preview["project"] != project:
            raise WorkflowError("specification_scope", "此标准适用于其他项目。")
        known = {(row["table"], row["row"]) for row in preview["rows"]}
        selected = [(item.table, item.row) for item in command.components]
        if len(set(selected)) != len(selected) or not set(selected) <= known:
            raise WorkflowError("specification_criterion_missing", "请选择该标准中存在且不重复的条款。")
        config = ProjectConfig.model_validate_json(project_row(conn, project)["config_json"])
        items = {item.id: item for item in config.items}
        previous_links = {(r["table"], r["row"]): r for r in _registered_components(conn, project, specification_id)}
        links, added = [], []
        for component in command.components:
            assay_fields = (component.assay_zh, component.assay_en, component.method, component.method_version)
            assay_id = "assay_" + digest([project, *assay_fields])

            original = next(row for row in preview["rows"] if (row["table"], row["row"]) == (component.table, component.row))
            reporting_basis = component.reporting_basis if component.reporting_basis is not None else original.get("reporting_basis", "")
            norm_sig = compute_norm_signature(
                component.assay_zh,
                component.method,
                component.component_zh,
                component.result_type,
                reporting_basis,
            )
            coords = (component.table, component.row)
            location = f"原件表 {component.table} 行 {component.row}"
            previous = previous_links.get(coords)
            if previous:
                definition = items.get(previous["item_id"])
                labels = {"method": "检测方法", "method_version": "方法版本", "unit": "单位", "result_type": "结果类型"}
                differences = [
                    f"{label}（已登记：{getattr(definition, field, '') or '未注明'}，本次：{getattr(component, field) or '未注明'}）"
                    for field, label in labels.items()
                    if definition is None or getattr(definition, field) != getattr(component, field)
                ]
                identity = previous["norm_signature"] or compute_norm_signature(
                    previous["assay_zh"], previous["method"], previous["component_zh"],
                    previous["result_type"], previous["reporting_basis"])
                if identity != norm_sig:
                    differences.append("检项／子结果／报告体系")
                if differences:
                    raise WorkflowError("assay_component_conflict",
                        f"{location} 已关联 {previous['assay_zh']} · {previous['component_zh']}，"
                        f"冲突字段：{'、'.join(differences)}。请查看已登记定义，取消该行后可继续登记其他条款。", 409)
                links.append({"table": component.table, "row": component.row, "item_id": previous["item_id"]})
                continue

            # 认领机制：按 (project, norm_signature) 查找是否已有 item_id
            candidates = conn.execute("""
                SELECT item_id, aliases_json, unit, result_type FROM assay_components
                WHERE project=? AND norm_signature=? AND norm_signature != ''
            """, (project, norm_sig)).fetchall()
            compatible = [entry for entry in candidates if entry["item_id"] in items and all(
                getattr(items[entry["item_id"]], field) == getattr(component, field)
                for field in ("method", "method_version", "unit", "result_type"))]
            if len(compatible) > 1:
                raise WorkflowError("assay_component_conflict", f"{location} 匹配多个已登记检项，请核对独立身份。", 409)
            existing = compatible[0] if compatible else None

            if existing:
                item_id = existing["item_id"]
                aliases = json.loads(existing["aliases_json"])
                new_alias = component.component_zh
                if new_alias and new_alias not in aliases:
                    aliases.append(new_alias)
                    conn.execute("UPDATE assay_components SET aliases_json=? WHERE project=? AND item_id=?",
                                 (json.dumps(aliases, ensure_ascii=False), project, item_id))
            else:
                item_id = "component_" + digest([assay_id, component.component_zh, component.component_en, component.result_type, reporting_basis])
                if item_id in items and items[item_id].unit != component.unit:
                    raise WorkflowError("assay_component_conflict", f"{location} 的同一子结果已使用其他单位，请核对单位或明确独立子结果。", 409)

            base_label = " · ".join(dict.fromkeys((component.assay_zh, component.component_zh)))
            if reporting_basis:
                base_label += f" · {reporting_basis}"
            # 解决同项目下跨检测方法出现同名子结果（如 SEC-HPLC 的 纯度·主峰 与 CE-SDS 的 纯度·主峰）导致 ProjectConfig 别名歧义校验失败
            needs_method = (
                any(comp.component_zh == component.component_zh and comp.assay_zh == component.assay_zh and comp.method != component.method for comp in command.components)
                or any(it.label.casefold() == base_label.casefold() and it.id != item_id for it in items.values())
            )
            label = f"{base_label}（{component.method}）" if needs_method else base_label

            cat_meta = resolve_category_metadata(
                conn=conn,
                raw_category=original.get("category", ""),
                item_text=component.assay_zh,
                method_text=component.method,
                component_text=component.component_zh,
            )
            category_code = component.category_code or cat_meta["category_code"]
            subcategory_code = component.subcategory_code or cat_meta["subcategory_code"]
            platform_assay_id = cat_meta["platform_assay_id"]
            display_order = cat_meta["display_order"]
            source_origin = "platform_inherited" if platform_assay_id else "project_custom"

            item = ItemDefinition(
                id=item_id,
                label=label,
                method=component.method,
                method_version=component.method_version,
                unit=component.unit,
                result_type=component.result_type,
                category_code=category_code,
                subcategory_code=subcategory_code,
                display_order=display_order,
                platform_assay_id=platform_assay_id,
            )
            if item_id in items and items[item_id] != item:
                if any(getattr(items[item_id], field) != getattr(item, field)
                       for field in ("method", "method_version", "unit", "result_type")):
                    raise WorkflowError("assay_component_conflict", "已有子结果的方法、版本、单位或类型不同，请先核对。", 409)
                item = items[item_id]

            params = (project, specification_id, component.table, component.row)
            conn.execute(
                """INSERT INTO assay_definitions(
                       id, project, name_zh, name_en, method, method_version,
                       category_code, subcategory_code, platform_assay_id, display_order, source_origin
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                       category_code=COALESCE(NULLIF(excluded.category_code, ''), assay_definitions.category_code),
                       subcategory_code=COALESCE(NULLIF(excluded.subcategory_code, ''), assay_definitions.subcategory_code),
                       platform_assay_id=COALESCE(NULLIF(excluded.platform_assay_id, ''), assay_definitions.platform_assay_id),
                       display_order=CASE WHEN excluded.display_order < assay_definitions.display_order THEN excluded.display_order ELSE assay_definitions.display_order END""",
                (
                    assay_id,
                    project,
                    *assay_fields,
                    category_code,
                    subcategory_code,
                    platform_assay_id,
                    display_order,
                    source_origin,
                ),
            )
            conn.execute("""
                INSERT INTO assay_components (project, item_id, assay_id, name_zh, name_en, unit, result_type, reporting_basis, norm_signature, aliases_json, display_order)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(project, item_id) DO UPDATE SET
                    unit=COALESCE(NULLIF(excluded.unit, ''), assay_components.unit),
                    norm_signature=COALESCE(NULLIF(excluded.norm_signature, ''), assay_components.norm_signature),
                    display_order=CASE WHEN excluded.display_order < assay_components.display_order THEN excluded.display_order ELSE assay_components.display_order END
            """, (project, item_id, assay_id, component.component_zh, component.component_en,
                  component.unit, component.result_type, reporting_basis, norm_sig,
                  json.dumps([component.component_zh], ensure_ascii=False), display_order))
            conn.execute("INSERT OR IGNORE INTO specification_assay_components VALUES (?,?,?,?,?)", (*params, item_id))
            items[item_id] = item
            links.append({"table": component.table, "row": component.row, "item_id": item_id})
            added.append(links[-1])
        revision = command.revision
        if added:
            try:
                after = ProjectConfig.model_validate({**config.model_dump(), "items": list(items.values())})
            except ValueError as exc:
                raise WorkflowError("assay_component_conflict", "提交范围存在检项名称或别名冲突。请核对方法版本、名称与子结果后重试。", 409) from exc
            conn.execute("UPDATE workflow_projects SET config_json=? WHERE project=?", (after.model_dump_json(), project))
            event(conn, project, specification_id, "register_assay_components", actor, "确认质量标准检项", {"links": added})
            revision = bump(conn, project)
        result = {"revision": revision, "links": links, "added": len(added), "existing": len(links) - len(added)}
        remember(conn, project, actor, command.idempotency_key, payload, result)
        return result


def catalog(store, project, actor):
    require(actor, Capability.VIEW)
    with store.read() as conn:
        config = ProjectConfig.model_validate_json(project_row(conn, project)["config_json"])
        metadata = {row["item_id"]: dict(row) for row in conn.execute("""
            SELECT c.item_id,d.id AS assay_id,d.name_zh AS assay_zh,d.name_en AS assay_en,
                   d.category_code,d.subcategory_code,d.platform_assay_id,d.display_order,d.source_origin,
                   COALESCE(pc.name_zh, '') AS category_name_zh,
                   COALESCE(pc.name_en, '') AS category_name_en,
                   COALESCE(pc.display_order, 99) AS category_order,
                   COALESCE(ps.name_zh, '') AS subcategory_name_zh,
                   c.name_zh AS component_zh,c.name_en AS component_en,
                   c.unit,c.result_type,c.reporting_basis,c.norm_signature,c.aliases_json
            FROM assay_components c JOIN assay_definitions d ON d.id=c.assay_id
            LEFT JOIN platform_categories pc ON pc.code=d.category_code
            LEFT JOIN platform_subcategories ps ON ps.code=d.subcategory_code
            WHERE c.project=?
        """, (project,))}
        rows = []
        for item in config.items:
            meta = metadata.get(item.id, {})
            if not meta.get("category_code"):
                resolved = resolve_category_metadata(
                    conn=conn,
                    item_text=meta.get("assay_zh") or item.label,
                    method_text=item.method,
                    component_text=meta.get("component_zh") or "",
                )
                meta = {
                    **resolved,
                    **{k: v for k, v in meta.items() if v not in ("", None)},
                }
            rows.append({
                **item.model_dump(),
                **meta,
                "canonical_unit": canonical_unit(item.unit),
            })
        rows.sort(key=lambda r: (r.get("category_order", 99), r.get("display_order", 999), r.get("label", "")))
        return rows


def promote_to_platform_catalog(
    store,
    project,
    *,
    name_zh,
    name_en="",
    method="",
    component_zh="",
    component_en="",
    unit="",
    result_type="numeric",
    category_code="",
    subcategory_code="",
    aliases=None,
    also_add_to_project=True,
    actor=None,
):
    """Promote a newly observed or project-local assay/alias into the platform 4-tier catalog (and optionally project config)."""
    require(actor, Capability.CONFIRM_FACT)
    with store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = project_row(conn, project)
        resolved = resolve_category_metadata(
            conn=conn,
            item_text=name_zh,
            method_text=method,
            component_text=component_zh,
        )
        effective_category = category_code or resolved["category_code"]
        effective_sub = subcategory_code or resolved["subcategory_code"]
        created = create_platform_assay(
            conn,
            category_code=effective_category,
            subcategory_code=effective_sub,
            name_zh=name_zh,
            name_en=name_en,
            default_unit=unit,
            methods=[{"name_zh": method}] if method else [],
            components=[{"name_zh": component_zh or name_zh, "name_en": component_en or name_en, "unit": unit, "value_kind": result_type}],
            aliases=list(aliases or []),
        )
        added_project_item = None
        if also_add_to_project:
            config = ProjectConfig.model_validate_json(row["config_json"])
            clean_label = name_zh.strip()
            existing_names = {
                " ".join(a.split()).casefold()
                for it in config.items
                for a in (it.id, it.label, *it.aliases)
            }
            if clean_label.casefold() not in existing_names:
                item_id = "custom_" + digest([project, clean_label, method or "未注明"])[:16]
                new_item = ItemDefinition(
                    id=item_id,
                    label=clean_label,
                    aliases=tuple(a.strip() for a in (aliases or []) if a and a.strip() and a.strip().casefold() not in existing_names and a.strip().casefold() != clean_label.casefold()),
                    unit=canonical_unit(unit),
                    result_type=result_type if result_type in ("numeric", "qualitative") else "numeric",
                    method=method.strip() or "未注明",
                    category_code=effective_category,
                    subcategory_code=effective_sub,
                    platform_assay_id=created["id"],
                )
                after = ProjectConfig.model_validate({**config.model_dump(), "items": [*config.items, new_item]})
                conn.execute("UPDATE workflow_projects SET config_json=? WHERE project=?", (after.model_dump_json(), project))
                bump(conn, project)
                added_project_item = new_item.model_dump()
        event(conn, project, created["id"], "promote_platform_catalog", actor, "沉淀至平台标准总词典", {
            "platform_assay_id": created["id"],
            "category_code": effective_category,
            "name_zh": name_zh,
            "also_add_to_project": also_add_to_project,
        })
        return {"platform_assay": created, "project_item": added_project_item}
