"""Register reviewed assay components in the existing project item dictionary."""

import json
import re
from typing import Literal

from pydantic import Field

from .models import ItemDefinition, Model, ProjectConfig, Text, digest
from .permissions import Capability, require
from .workflow_common import WorkflowError, bump, check_revision, event, project_row, remember, replay


def compute_norm_signature(assay_name, method, component_name, result_type="numeric", reporting_basis=""):
    """
    归一化签名：
    - 将全角括号（）转半角 ()，去除多余空格
    - 统一方法核心词（SEC-HPLC法 / SEC-HPLC Method -> sec-hplc；还原型CE-SDS法 / R CE-SDS -> r-ce-sds；非还原型CE-SDS法 / NR CE-SDS -> nr-ce-sds）
    - 子结果同义词归一：单体/Monomer -> monomer；高分子量组分/聚合体/HMW -> hmw；低分子量组分/片段/LMW -> lmw
    """
    def clean(s):
        if not s:
            return ""
        s = s.replace("（", "(").replace("）", ")").replace("：", ":").replace("％", "%")
        s = re.sub(r"\s+", " ", s).strip().lower()
        return s

    m = clean(method)
    is_ce = bool(re.search(r"ce[\s-]*sds", m))
    if is_ce and ("非还原" in m or re.search(r"\bnr\b|non[ -]?reduced", m)):
        m_core = "nr-ce-sds"
    elif is_ce and ("还原" in m or re.search(r"\br\b|\breduced\b", m)):
        m_core = "r-ce-sds"
    elif "sec-hplc" in m or "sec" in m or "体积排阻" in m:
        m_core = "sec-hplc"
    elif "icief" in m or "全柱" in m or "毛细管等电" in m:
        m_core = "icief"
    elif "cief" in m:
        m_core = "cief"
    elif "hplc" in m:
        m_core = re.sub(r"(法|method)$", "", m).strip()
    elif "cell" in m or "杀伤" in m or "活性" in m or "bioassay" in m:
        m_core = "bioassay"
    else:
        # 去掉结尾的 "法" 或 " method"
        m_core = re.sub(r"(法|method)$", "", m).strip()

    c = clean(component_name)
    # 子结果同义词归一
    if any(k in c for k in ("单体", "monomer", "main peak", "主峰")):
        c_core = "monomer"
    elif any(k in c for k in ("高分子量", "聚合体", "hmw", "aggregate")):
        c_core = "hmw"
    elif any(k in c for k in ("低分子量", "片段", "lmw", "fragment")):
        c_core = "lmw"
    elif any(k in c for k in ("酸性", "acidic")):
        c_core = "acidic"
    elif any(k in c for k in ("碱性", "basic")):
        c_core = "basic"
    elif any(k in c for k in ("中性", "neutral")):
        c_core = "neutral"
    else:
        c_core = c

    a = clean(assay_name)
    # 统一 assay_name 中的核心词
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


class RegisterComponents(Model):
    revision: int = Field(ge=0)
    idempotency_key: Text
    components: list[ReviewedComponent] = Field(min_length=1, max_length=150)


def _registered_components(conn, project, specification_id):
    return [dict(row) for row in conn.execute("""SELECT l.table_number AS "table",l.row_number AS "row",l.item_id,
               d.name_zh AS assay_zh,d.name_en AS assay_en,d.method,d.method_version,
               c.name_zh AS component_zh,c.name_en AS component_en,c.unit,c.result_type,
               c.reporting_basis,c.norm_signature
        FROM specification_assay_components l JOIN assay_components c ON c.project=l.project AND c.item_id=l.item_id
        JOIN assay_definitions d ON d.id=c.assay_id
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

            item = ItemDefinition(id=item_id, label=label, method=component.method, method_version=component.method_version,
                                  unit=component.unit, result_type=component.result_type)
            if item_id in items and items[item_id] != item:
                if any(getattr(items[item_id], field) != getattr(item, field)
                       for field in ("method", "method_version", "unit", "result_type")):
                    raise WorkflowError("assay_component_conflict", "已有子结果的方法、版本、单位或类型不同，请先核对。", 409)
                item = items[item_id]

            params = (project, specification_id, component.table, component.row)
            conn.execute("INSERT OR IGNORE INTO assay_definitions VALUES (?,?,?,?,?,?)", (assay_id, project, *assay_fields))
            conn.execute("""
                INSERT INTO assay_components (project, item_id, assay_id, name_zh, name_en, unit, result_type, reporting_basis, norm_signature, aliases_json)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(project, item_id) DO UPDATE SET
                    unit=COALESCE(NULLIF(excluded.unit, ''), assay_components.unit),
                    norm_signature=COALESCE(NULLIF(excluded.norm_signature, ''), assay_components.norm_signature)
            """, (project, item_id, assay_id, component.component_zh, component.component_en,
                  component.unit, component.result_type, reporting_basis, norm_sig, json.dumps([component.component_zh], ensure_ascii=False)))
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
                   c.name_zh AS component_zh,c.name_en AS component_en,
                   c.unit,c.result_type,c.reporting_basis,c.norm_signature,c.aliases_json
            FROM assay_components c JOIN assay_definitions d ON d.id=c.assay_id WHERE c.project=?
        """, (project,))}
        return [{**item.model_dump(), **metadata.get(item.id, {})} for item in config.items]
