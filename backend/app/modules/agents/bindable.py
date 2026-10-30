"""编辑弹窗「扩展技能 / 扩展插件」的绑定聚合与校验收口（v3.1.0）。

两个职责：
1. ``list_bindable_extensions``：GET /agents/bindable-extensions 的聚合口径——
   系统内已安装、当前小院可用的技能与插件全部可选（不新增权限点，任何小院成员可读）：
   - skills：``evolution_skills`` 中 status=published 且（本小院或平台级 yard_id IS NULL）
     的行，按 skill_code 去重时优先保留本小院；再补 ``market_items`` 中
     item_type=skill 且 status=installed、且该 skill_code 完全没落 Skill 行的孤项
     （source 记 market；待审/草稿不进候选）。
   - plugins：``plugin_manifests`` 中 status=enabled 且（全局 yard_id IS NULL 或本小院）
     的行；再补 ``market_items`` 中 item_type=plugin 且 status=installed、且完全没落
     PluginManifest 行的孤项（可选集必须等于可用集，待审/停用项不进候选）。
2. ``validate_bound_skills``：POST/PATCH /agents/declarative 写入 bound_skills 的
   安全收口——逐项校验必须属于上述可绑定集合，未知 code 一律 422。
   只在写入时校验；读取/展示历史数据（含旧 code 脏数据）不走校验、不报错。

绑定编码契约（与前端对齐，勿改）：``skill:<uuid>`` / ``plugin:<plugin_id>``。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.agents.models import STATUS_DELETED, YardAgent

#: 摘要截断长度（契约：summary 截断 200 字）
_SUMMARY_LIMIT = 200

RISK_LOW = "low"

#: 可绑定技能/插件的编码前缀（bound_skills 存储格式）
SKILL_CODE_PREFIX = "skill:"
PLUGIN_CODE_PREFIX = "plugin:"


# ---------------------------------------------------------------------------
# 工具：definition / manifest 字段容错读取（历史/异构数据一律不抛错）
# ---------------------------------------------------------------------------


def _as_dict(value: Any) -> dict[str, Any]:
    """JSON 列可能是 None / list / 脏数据；只把 dict 当结构化定义用。"""
    return value if isinstance(value, dict) else {}


def _summary(data: dict[str, Any], fallback: str = "") -> str:
    """摘要：summary 优先，缺失用 description，再缺失用调用方兜底；截断 200 字。"""
    text = str(data.get("summary") or data.get("description") or fallback or "")
    return text[:_SUMMARY_LIMIT]


def _risk_level(data: dict[str, Any], fallback: str = RISK_LOW) -> str:
    """风险档：优先 guardrails.risk_level（技能模板契约），缺失回退 risk_level，默认 low。"""
    guardrails = data.get("guardrails")
    if isinstance(guardrails, dict):
        value = str(guardrails.get("risk_level") or "").strip()
        if value:
            return value
    return str(data.get("risk_level") or "").strip() or fallback


def _skill_source(created_from: Any) -> str:
    """Skill 行来源推断（created_from 现网取值见 market/installer.py、agri_skills.py）：
    - market_builtin：小院铺设的内置 12 技能 → builtin；
    - 含 market / github（market、market_import、github 安装）→ market；
    - 含 user_generated / chat（对话/用户生成，v3.2 起落库）→ generated；
    - 其余（手工创建、auto:suggest 等）→ imported。
    """
    value = str(created_from or "").lower()
    if "market_builtin" in value:
        return "builtin"
    if "market" in value or "github" in value:
        return "market"
    if "user_generated" in value or "chat" in value:
        return "generated"
    return "imported"


def _plugin_source(manifest: dict[str, Any]) -> str:
    """PluginManifest 来源推断（manifest_json 无独立来源列，靠溯源标记）：
    - `_auto`：plugins/workflow.py 对话自动挖掘时写入的溯源节；
    - `_market`：市场安装插件溯源标记（installer 目前原样落库 manifest，该标记为
      契约预留；命中任一标记都按 market 展示，不影响可绑定性与 status）；
    - 其余（目录扫描注册、本地导入）→ local。
    """
    if "_market" in manifest or "_auto" in manifest:
        return "market"
    return "local"


def _tags(data: dict[str, Any]) -> list[str]:
    """技能/插件标签（用于一句话建 Agent 的关键词匹配，读不到就返回空列表）。"""
    raw = data.get("tags")
    if not isinstance(raw, list):
        meta = data.get("metadata_json") or data.get("metadata")
        raw = meta.get("tags") if isinstance(meta, dict) else None
    if not isinstance(raw, list):
        return []
    return [str(tag)[:24] for tag in raw if str(tag).strip()][:12]


def _recency(row: Any) -> float:
    """created_at 排序键（None 容错，时区差异不影响相对顺序）。"""
    created = getattr(row, "created_at", None)
    return created.timestamp() if created is not None else 0.0


# ---------------------------------------------------------------------------
# bound_count：一次读出本小院智能体，Python 侧计数（避免逐 code N+1）
# ---------------------------------------------------------------------------


def _bound_counts(db: Session, yard_id: str) -> dict[str, int]:
    """统计本小院各 bound_skills code 被引用的次数（软删除行不计入）。

    bound_skills 是 JSON 列表，历史脏数据（None / 非列表 / 非字符串项）一律容错跳过。
    """
    agents = (
        db.query(YardAgent)
        .filter(YardAgent.yard_id == yard_id, YardAgent.status != STATUS_DELETED)
        .all()
    )
    counts: dict[str, int] = {}
    for agent in agents:
        bound = getattr(agent, "bound_skills", None)
        if not isinstance(bound, list):
            continue
        for raw in bound:
            code = str(raw).strip()
            if code:
                counts[code] = counts.get(code, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# 聚合
# ---------------------------------------------------------------------------


def _skill_entries(db: Session, yard_id: str, counts: dict[str, int]) -> list[dict[str, Any]]:
    from app.modules.evolution.models import Skill
    from app.modules.market.models import MarketItem

    rows = (
        db.query(Skill)
        .filter(
            Skill.status == "published",
            or_(Skill.yard_id == yard_id, Skill.yard_id.is_(None)),
        )
        .all()
    )
    # 去重优先级：本小院行 > 平台级行（yard_id IS NULL）；同级按创建时间倒序取最新版本
    yard_rows = sorted((r for r in rows if r.yard_id == yard_id), key=_recency, reverse=True)
    platform_rows = sorted((r for r in rows if r.yard_id is None), key=_recency, reverse=True)

    entries: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    seen_item_keys: set[str] = set()  # definition.market_item_key：对齐市场条目，防止补重
    for row in yard_rows + platform_rows:
        skill_code = str(row.skill_code or "").strip()
        if not skill_code or skill_code in seen_codes:
            continue
        definition = _as_dict(row.definition_json)
        seen_codes.add(skill_code)
        item_key = str(definition.get("market_item_key") or "").strip()
        if item_key:
            seen_item_keys.add(item_key)
        entries.append(
            {
                "code": f"{SKILL_CODE_PREFIX}{row.id}",
                "name": str(definition.get("name") or skill_code),
                "summary": _summary(definition),
                "tags": _tags(definition),
                "source": _skill_source(row.created_from),
                "visibility": "yard" if row.yard_id == yard_id else "platform",
                "risk_level": _risk_level(definition),
                "bound_count": counts.get(f"{SKILL_CODE_PREFIX}{row.id}", 0),
            }
        )

    # 市场已安装但没有（published）Skill 行的条目：仅补历史数据里完全没落 Skill 行的
    # 孤项（例如安装器尚未回写）。只要该 skill_code 存在任意状态的 Skill 行
    # （draft/pending_review 等），就说明它还不可用——可选集必须等于可用集，
    # 待审核项等审核发布后自然由上面的 published 行接管。
    market_rows = (
        db.query(MarketItem)
        .filter(MarketItem.item_type == "skill", MarketItem.status == "installed")
        .order_by(MarketItem.created_at.desc())
        .all()
    )
    existing_skill_codes: set[str] = {
        str(code)
        for (code,) in db.query(Skill.skill_code)
        .filter(or_(Skill.yard_id == yard_id, Skill.yard_id.is_(None)))
        .all()
        if code
    }
    for item in market_rows:
        item_code = str(item.code or "").strip()
        if item_code and (item_code in seen_codes or item_code in existing_skill_codes):
            continue
        item_key = str(item.item_key or "").strip()
        if item_key and item_key in seen_item_keys:
            continue
        if item_code:
            seen_codes.add(item_code)
        definition = _as_dict(item.definition_json)
        entries.append(
            {
                "code": f"{SKILL_CODE_PREFIX}{item.id}",
                "name": str(item.name or item_code),
                "summary": _summary(definition, str(item.description or "")),
                "tags": _tags(definition),
                "source": "market",
                "visibility": "platform",
                "risk_level": _risk_level(definition, str(item.risk_level or RISK_LOW)),
                "bound_count": counts.get(f"{SKILL_CODE_PREFIX}{item.id}", 0),
            }
        )
    return entries


def _plugin_entries(db: Session, yard_id: str, counts: dict[str, int]) -> list[dict[str, Any]]:
    from app.modules.market.models import MarketItem
    from app.modules.plugins.models import PluginManifest

    rows = (
        db.query(PluginManifest)
        .filter(
            PluginManifest.status == "enabled",
            or_(PluginManifest.yard_id == yard_id, PluginManifest.yard_id.is_(None)),
        )
        .all()
    )
    # 同 plugin_id 同时存在全局行与本小院行时保留更具体的本小院行（与技能口径一致）
    yard_rows = sorted((r for r in rows if r.yard_id == yard_id), key=_recency, reverse=True)
    global_rows = sorted((r for r in rows if r.yard_id is None), key=_recency, reverse=True)

    entries: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for row in yard_rows + global_rows:
        plugin_id = str(row.plugin_id or "").strip()
        if not plugin_id or plugin_id in seen_ids:
            continue
        seen_ids.add(plugin_id)
        manifest = _as_dict(row.manifest_json)
        entries.append(
            {
                "code": f"{PLUGIN_CODE_PREFIX}{plugin_id}",
                "name": str(manifest.get("name") or plugin_id),
                "summary": _summary(manifest),
                "tags": _tags(manifest),
                "source": _plugin_source(manifest),
                "status": str(row.status or "enabled"),
                "risk_level": _risk_level(manifest),
                "bound_count": counts.get(f"{PLUGIN_CODE_PREFIX}{plugin_id}", 0),
            }
        )

    # 市场已安装但没有 enabled PluginManifest 行的插件：仅补完全没落 Manifest 行的
    # 孤项。行存在但 pending_review/disabled 说明还不可用——可选集必须等于可用集，
    # 审核通过/启用后自然由上面的 enabled 行接管。候选 plugin_id 与安装器一致取
    # manifest.plugin_id，缺失回退 item.code（importer/installer 均以此寻址）。
    market_rows = (
        db.query(MarketItem)
        .filter(MarketItem.item_type == "plugin", MarketItem.status == "installed")
        .order_by(MarketItem.created_at.desc())
        .all()
    )
    existing_plugin_ids: set[str] = {
        str(pid)
        for (pid,) in db.query(PluginManifest.plugin_id)
        .filter(or_(PluginManifest.yard_id == yard_id, PluginManifest.yard_id.is_(None)))
        .all()
        if pid
    }
    for item in market_rows:
        manifest = _as_dict(item.manifest_json)
        plugin_id = str(manifest.get("plugin_id") or item.code or "").strip()
        if not plugin_id or plugin_id in seen_ids or plugin_id in existing_plugin_ids:
            continue
        seen_ids.add(plugin_id)
        entries.append(
            {
                "code": f"{PLUGIN_CODE_PREFIX}{plugin_id}",
                "name": str(item.name or plugin_id),
                "summary": _summary(manifest, str(item.description or "")),
                "source": "market",
                "status": "enabled",
                "risk_level": _risk_level(manifest, str(item.risk_level or RISK_LOW)),
                "bound_count": counts.get(f"{PLUGIN_CODE_PREFIX}{plugin_id}", 0),
            }
        )
    return entries


def list_bindable_extensions(db: Session, yard_id: str) -> dict[str, list[dict[str, Any]]]:
    """聚合当前小院可绑定的扩展技能与插件（编辑弹窗两个分区的候选集）。"""
    counts = _bound_counts(db, yard_id)
    return {
        "skills": _skill_entries(db, yard_id, counts),
        "plugins": _plugin_entries(db, yard_id, counts),
    }


def collect_bindable_codes(db: Session, yard_id: str) -> set[str]:
    """可绑定集合的 code 全集（``skill:<uuid>`` / ``plugin:<plugin_id>``），供写入校验。"""
    data = list_bindable_extensions(db, yard_id)
    return {str(e["code"]) for e in data["skills"]} | {str(e["code"]) for e in data["plugins"]}


# ---------------------------------------------------------------------------
# 写入校验（安全收口）：只在 POST/PATCH /agents/declarative 写入时调用
# ---------------------------------------------------------------------------


def validate_bound_skills(db: Session, yard_id: str, bound_skills: Any) -> None:
    """校验请求提交的 bound_skills 每一项都在可绑定集合内。

    - 空 / None / 非列表（历史脏数据形态）直接放行：读取展示不归本函数管，
      写入路径 schema（pydantic list[str]）已保证类型，这里再兜底一次；
    - 未知 code 一律 VALIDATION_ERROR 422，消息携带第一个未知 code，
      details.unknown_codes 带全量，便于前端定位。
    """
    if not isinstance(bound_skills, list) or not bound_skills:
        return
    codes = {str(c).strip() for c in bound_skills if str(c).strip()}
    if not codes:
        return
    unknown = sorted(codes - collect_bindable_codes(db, yard_id))
    if unknown:
        raise AppError(
            "VALIDATION_ERROR",
            f"存在不可绑定的技能或插件：{unknown[0]}",
            http_status=422,
            details={"unknown_codes": unknown},
        )
