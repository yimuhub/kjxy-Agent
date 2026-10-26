"""能力市场装载服务（HTTP 路由与对话链路共用同一份实现）。

口径：
- 逐码 创建 → 发布 → 审计 → 提交；已存在同码 skipped 不覆盖（幂等）；
- 单码失败回滚该码（此前已提交的码不受影响），继续处理其余编码；
- 权限：yard_admin（platform_admin 直通）。非管理员在对话里只能拿到
  **建议卡片**，不落库（与能力中心的权限闸门一致）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.agents import declarative
from app.modules.agents.models import CREATED_FROM_TEMPLATE, YardAgent
from app.modules.audit.service import log_audit
from app.modules.market.seeds import SEED_AGENTS, SEED_CODE_PREFIX, find_seed

log = get_logger(__name__)

#: 单次装载编码数量上限
MAX_INSTALL_CODES = 20

#: 对话内市场卡片类型
CARD_TYPE_MARKET = "market"


def is_admin(ctx: YardContext) -> bool:
    return bool({"yard_admin", "yard_owner", "platform_admin"} & set(ctx.roles))


def installed_codes(db: Session, yard_id: str) -> set[str]:
    """本小院已存在的种子相关编码（含小院自建同码——一律视为已占用）。"""
    candidates: list[str] = []
    for seed in SEED_AGENTS:
        code = str(seed["agent_code"])
        candidates.extend([code, SEED_CODE_PREFIX + code])
    rows = (
        db.query(YardAgent.agent_code)
        .filter(YardAgent.yard_id == yard_id, YardAgent.agent_code.in_(candidates))
        .all()
    )
    return {str(r[0]) for r in rows}


def install_seed_codes(
    db: Session,
    *,
    yard_id: str,
    actor_id: str,
    roles: list[str],
    codes: list[str],
    request_id: str = "",
) -> dict[str, Any]:
    """装载种子包（核心实现）：返回 {installed, skipped, failed}。"""
    installed: list[str] = []
    skipped: list[str] = []
    failed: list[dict[str, str]] = []
    taken = installed_codes(db, yard_id)

    for code in codes:
        seed = find_seed(code)
        if seed is None:
            failed.append(
                {"code": code, "error": "种子包不存在，请从 /market/catalog 获取可用编码。"}
            )
            continue
        if code in taken or SEED_CODE_PREFIX + code in taken:
            skipped.append(code)
            continue
        payload: dict[str, Any] = {
            "agent_code": SEED_CODE_PREFIX + code,
            "name": seed["name"],
            "description": seed["description"],
            "emoji": seed["emoji"],
            "persona_prompt": seed["persona_prompt"],
            "task_prompt_template": seed["task_prompt_template"],
            "allowed_tools": list(seed["allowed_tools"]),
            "knowledge_scopes": list(seed["knowledge_scopes"]),
            "created_from": CREATED_FROM_TEMPLATE,
        }
        try:
            req = declarative.parse_create_request(payload)
            agent = declarative.create_agent(
                db,
                yard_id=yard_id,
                actor_id=actor_id,
                req=req,
                request_id=request_id,
            )
            declarative.publish_agent(
                db,
                yard_id=yard_id,
                actor_id=actor_id,
                agent_id=agent.id,
                roles=list(roles),
                request_id=request_id,
            )
            log_audit(
                db,
                yard_id=yard_id,
                actor_id=actor_id,
                action="market_install",
                resource_type="yard_agent",
                resource_id=agent.id,
                result="success",
                request_id=request_id,
                metadata={
                    "seed_code": code,
                    "installed_as": agent.agent_code,
                    "domain": seed["domain"],
                },
            )
            db.commit()
            installed.append(code)
            taken.add(agent.agent_code)
        except AppError as e:
            db.rollback()
            failed.append({"code": code, "error": e.message})
    return {"installed": installed, "skipped": skipped, "failed": failed}


def _match_seeds(text: str) -> list[dict[str, Any]]:
    """按名称/编码匹配种子包（用户说"装个苗情测报助手"）。"""
    lowered = (text or "").lower()
    hits: list[dict[str, Any]] = []
    for seed in SEED_AGENTS:
        code = str(seed["agent_code"])
        name = str(seed["name"])
        matched = code in lowered or name in text
        if not matched:
            # 名称中长度 ≥3 的连续片段命中也算（如"病虫害""棚室环"）
            for size in (4, 3):
                if any(name[i : i + size] in text for i in range(len(name) - size + 1)):
                    matched = True
                    break
        if matched:
            hits.append(seed)
    return hits


def install_from_chat(db: Session, *, ctx: YardContext, request_text: str) -> dict[str, Any]:
    """对话内装载市场能力：返回 {summary, name, card}。

    匹配不到 → 给出目录建议卡片（不猜、不乱装）；非管理员 → 只出建议卡片。
    """
    seeds = _match_seeds(request_text)
    if not seeds:
        names = "、".join(str(s["name"]) for s in SEED_AGENTS[:8])
        card = {
            "type": CARD_TYPE_MARKET,
            "state": "suggested",
            "name": "能力市场",
            "description": f"没有从你的话里匹配到确定的能力包。可用能力包括：{names} 等。",
            "items": [
                {"code": str(s["agent_code"]), "name": str(s["name"])} for s in SEED_AGENTS[:8]
            ],
            "note": "在「能力市场」页面可直接装载，装载后即可在对话中被路由到。",
            "actions": [],
        }
        return {
            "summary": "没有从你的话里匹配到确定的能力包，请到「能力市场」查看可用能力。",
            "name": "能力市场",
            "card": card,
        }

    seed = seeds[0]
    code = str(seed["agent_code"])
    name = str(seed["name"])
    if not is_admin(ctx):
        log_audit(
            db,
            yard_id=ctx.yard_id,
            actor_id=ctx.user_id,
            action="chat_capability_requested",
            resource_type="yard_agent",
            resource_id="",
            result="success",
            request_id=ctx.request_id,
            metadata={"seed_code": code, "name": name},
        )
        card = {
            "type": CARD_TYPE_MARKET,
            "state": "requested",
            "name": name,
            "description": str(seed["description"]),
            "items": [{"code": code, "name": name}],
            "note": "装载市场能力需要小院管理员权限，请联系管理员在能力市场确认。",
            "actions": [],
        }
        return {
            "summary": f"已匹配到能力包「{name}」，装载需要小院管理员权限，请联系管理员确认。",
            "name": name,
            "card": card,
        }

    result = install_seed_codes(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        roles=list(ctx.roles),
        codes=[code],
        request_id=ctx.request_id,
    )
    if result["installed"]:
        summary = f"已装载能力包「{name}」，现在可以直接用它提问了。"
        state = "installed"
    elif result["skipped"]:
        summary = f"能力包「{name}」此前已装载，无需重复安装。"
        state = "installed"
    else:
        reason = result["failed"][0]["error"] if result["failed"] else "未知原因"
        summary = f"装载「{name}」失败：{reason}"
        state = "failed"
    card = {
        "type": CARD_TYPE_MARKET,
        "state": state,
        "name": name,
        "description": str(seed["description"]),
        "items": [{"code": code, "name": name}],
        "note": summary,
        "actions": [],
    }
    return {"summary": summary, "name": name, "card": card}


__all__ = [
    "CARD_TYPE_MARKET",
    "MAX_INSTALL_CODES",
    "install_from_chat",
    "install_seed_codes",
    "installed_codes",
    "is_admin",
]
