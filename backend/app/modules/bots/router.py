"""机器人管理接口 ``/bots``（小院管理员口径同 pairing）。

- CRUD + 测试连接 + 状态查询；
- 会话台账与批准/吊销（陌生人安全模型的运营入口）；
- 凭据一律掩码回显；test 端点接受「用已存凭据」或「临时凭据试跑」两种入参。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import YardContext, get_db_session, get_yard_context
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.bots import service
from app.modules.bots.manager import manager
from app.modules.bots.models import STATUS_CONFIGURED, STATUS_ERROR

router = APIRouter(prefix="/bots", tags=["bots"])

_ADMIN_ROLES = {"platform_admin", "admin", "yard_admin"}


def _admin(ctx: YardContext = Depends(get_yard_context)) -> YardContext:
    if not set(ctx.roles) & _ADMIN_ROLES:
        raise AppError("FORBIDDEN", "机器人管理需要小院管理员权限。", http_status=403)
    return ctx


@router.get("", response_model=None)
def list_bots(ctx: YardContext = Depends(_admin), db: Session = Depends(get_db_session)):
    return ok([service.bot_to_out(b) for b in service.list_bots(db, ctx.yard_id)])


@router.post("", response_model=None, status_code=201)
async def create_bot(
    payload: dict,
    ctx: YardContext = Depends(_admin),
    db: Session = Depends(get_db_session),
):
    bot = service.create_bot(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        platform=str(payload.get("platform", "")),
        name=str(payload.get("name", "")),
        credentials=dict(payload.get("credentials") or {}),
        binding_mode=str(payload.get("binding_mode", "auto")),
        bound_agent_id=payload.get("bound_agent_id"),
        require_approval=bool(payload.get("require_approval", True)),
    )
    if bot.enabled:
        manager.spawn(bot.id)
    return ok(service.bot_to_out(bot))


@router.patch("/{bot_id}", response_model=None)
async def update_bot(
    bot_id: str,
    payload: dict,
    ctx: YardContext = Depends(_admin),
    db: Session = Depends(get_db_session),
):
    bot = service.get_bot_or_404(db, ctx.yard_id, bot_id)
    was_enabled = bot.enabled
    bot = service.update_bot(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        bot=bot,
        name=payload.get("name"),
        credentials=payload.get("credentials"),
        binding_mode=payload.get("binding_mode"),
        bound_agent_id=payload.get("bound_agent_id"),
        require_approval=payload.get("require_approval"),
        enabled=payload.get("enabled"),
    )
    # 生命周期联动：启用→拉起；停用→断连
    if bot.enabled and not was_enabled:
        manager.spawn(bot.id)
    elif not bot.enabled and was_enabled:
        await manager.stop(bot.id)
    return ok(service.bot_to_out(bot))


@router.delete("/{bot_id}", response_model=None)
async def delete_bot(
    bot_id: str,
    ctx: YardContext = Depends(_admin),
    db: Session = Depends(get_db_session),
):
    bot = service.get_bot_or_404(db, ctx.yard_id, bot_id)
    await manager.stop(bot.id)
    service.delete_bot(db, yard_id=ctx.yard_id, actor_id=ctx.user_id, bot=bot)
    return ok({"deleted": bot_id})


@router.post("/{bot_id}/test", response_model=None)
async def test_bot(
    bot_id: str,
    payload: dict | None = None,
    ctx: YardContext = Depends(_admin),
    db: Session = Depends(get_db_session),
):
    """连通性自检（离线可测部分）：飞书查 SDK；企微/钉钉做 TLS HTTP 握手探测。

    完整的长连接建立由 manager 负责，本端点只做「凭据格式 + 平台可达」快检，
    结果回写给 status_reason 供前端展示，不改变 enabled。
    """
    from app.modules.bots.feishu_adapter import MISSING_SDK_REASON, feishu_available

    bot = service.get_bot_or_404(db, ctx.yard_id, bot_id)
    raw_creds = payload.get("credentials") if isinstance(payload, dict) else None
    creds = dict(raw_creds) if raw_creds else service.load_credentials(bot)
    problems: list[str] = []
    if bot.platform == "wecom_aibot":
        if not (creds.get("bot_id") and creds.get("secret")):
            problems.append("凭据缺少 bot_id/secret")
    elif bot.platform == "dingtalk":
        if not (creds.get("client_id") and creds.get("client_secret")):
            problems.append("凭据缺少 client_id/client_secret")
        else:
            problems.extend(await _ding_reachable(creds))
    elif bot.platform == "feishu":
        if not feishu_available():
            problems.append(MISSING_SDK_REASON)
    # 本端点只做凭据/可达性自检：通过也只能标记 configured，
    # online 必须由 manager 完成长连接握手后写入，避免误导用户。
    state = STATUS_ERROR if problems else STATUS_CONFIGURED
    result = {"ok": not problems, "state": state, "problems": problems}
    service.record_state(db, bot.id, state, "；".join(problems)[:300])
    return ok(result)


async def _ding_reachable(creds: dict) -> list[str]:
    """向钉钉 open 接口真实探测一次（403/401=凭据无效；网络失败单独报告）。"""
    import httpx

    from app.core.config import settings

    try:
        async with httpx.AsyncClient(timeout=8) as hc:
            resp = await hc.post(
                settings.bot_dingtalk_open_url,
                json={
                    "clientId": str(creds.get("client_id", "")),
                    "clientSecret": str(creds.get("client_secret", "")),
                    "subscriptions": [],
                    "ua": "kjxy-agent/1.4",
                },
            )
        if resp.status_code in (400, 401, 403):
            return [f"钉钉拒绝了凭据（HTTP {resp.status_code}）"]
        return []
    except httpx.HTTPError:
        return ["无法连接钉钉开放平台（网络出网受限？）"]


@router.get("/{bot_id}/status", response_model=None)
def bot_status(
    bot_id: str,
    ctx: YardContext = Depends(_admin),
    db: Session = Depends(get_db_session),
):
    bot = service.get_bot_or_404(db, ctx.yard_id, bot_id)
    return ok(
        {
            "status": bot.status,
            "status_reason": bot.status_reason,
            "enabled": bot.enabled,
            "consecutive_failures": bot.consecutive_failures,
            "connected_here": bot.id in manager.running(),
        }
    )


@router.get("/conversations", response_model=None)
def list_conversations(
    limit: int = 50,
    ctx: YardContext = Depends(_admin),
    db: Session = Depends(get_db_session),
):
    rows = service.list_conversations(db, ctx.yard_id, limit=limit)
    return ok([service.conv_to_out(c) for c in rows])


@router.get("/conversations/{conv_id}/messages", response_model=None)
def conversation_messages(
    conv_id: str,
    limit: int = 30,
    ctx: YardContext = Depends(_admin),
    db: Session = Depends(get_db_session),
):
    """消息账本（sha12 摘要口径，无正文）。"""
    from sqlalchemy import select

    from app.modules.bots.models import BotMessageLog

    rows = list(
        db.scalars(
            select(BotMessageLog)
            .where(
                BotMessageLog.conversation_id == conv_id,
                BotMessageLog.yard_id == ctx.yard_id,
            )
            .order_by(BotMessageLog.created_at.desc())
            .limit(min(limit, 100))
        )
    )
    return ok(
        [
            {
                "id": m.id,
                "direction": m.direction,
                "content_sha12": m.content_sha12,
                "length": m.length,
                "routed_by": m.routed_by,
                "reason_summary": m.reason_summary,
                "ok": m.ok,
                "created_at": m.created_at.isoformat() if m.created_at else "",
            }
            for m in rows
        ]
    )


@router.post("/conversations/{conv_id}/approve", response_model=None)
def approve_conversation(
    conv_id: str,
    ctx: YardContext = Depends(_admin),
    db: Session = Depends(get_db_session),
):
    conv = service.review_conversation(
        db, yard_id=ctx.yard_id, actor_id=ctx.user_id, conv_id=conv_id, approve=True
    )
    return ok(service.conv_to_out(conv))


@router.post("/conversations/{conv_id}/revoke", response_model=None)
def revoke_conversation(
    conv_id: str,
    ctx: YardContext = Depends(_admin),
    db: Session = Depends(get_db_session),
):
    conv = service.review_conversation(
        db, yard_id=ctx.yard_id, actor_id=ctx.user_id, conv_id=conv_id, approve=False
    )
    return ok(service.conv_to_out(conv))
