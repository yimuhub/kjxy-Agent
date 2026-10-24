"""入站编排管线（gateway）：一条平台消息的完整旅程。

幂等 → 会话建档/批准闸门 → @剥离 → /use 会话内切换 → 上下文滑窗 →
supervisor 三级路由（或按配置绑定指定助手/班组）→ 出站回复（注入发送函数）→
双向账本（只存 sha12）+ 上下文缓存更新 + 审计。

发送抽象：本模块不触碰任何平台协议——出站是注入的 ``SendFn(conversation_id, text)``，
由 manager 按平台绑定具体实现（企微流式 finish / 钉钉 sessionWebhook / 飞书 API）。
"""

from __future__ import annotations

import hashlib
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import TenantContext
from app.modules.audit.service import log_audit
from app.modules.bots.models import (
    BINDING_TEAM,
    CONV_DM,
    DIRECTION_IN,
    DIRECTION_OUT,
    BotConfig,
    BotConversation,
    BotMessageLog,
)
from app.modules.bots.protocol import InboundMessage

log = logging.getLogger(__name__)

SendFn = Callable[[str, str], Awaitable[None]]

# 陌生会话（需批准）的固定回复：不透露任何业务内容
APPROVAL_PENDING_REPLY = "已收到你的消息，等待管理员批准后即可对话。"

# /use 命令：会话内临时切换指定助手（不改机器人配置）
_USE_PREFIX = ("/use", "/使用", "/助手")


def _sha12(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


class IdempotencyGuard:
    """平台消息 id 去重（进程内 LRU，每机器人 512 条）。

    长连接断线重连后平台可能重投最新消息；重复投递直接丢弃。
    """

    def __init__(self, maxsize: int = 512) -> None:
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._max = maxsize

    def seen(self, key: str) -> bool:
        if key in self._seen:
            self._seen.move_to_end(key)
            return True
        self._seen[key] = None
        while len(self._seen) > self._max:
            self._seen.popitem(last=False)
        return False


def _get_or_create_conversation(
    db: Session, bot: BotConfig, msg: InboundMessage
) -> BotConversation:
    conv = db.scalars(
        select(BotConversation).where(
            BotConversation.tenant_id == bot.tenant_id,
            BotConversation.platform == msg.platform,
            BotConversation.external_conv_id == msg.external_conv_id,
        )
    ).first()
    if conv is None:
        conv = BotConversation(
            tenant_id=bot.tenant_id,
            bot_config_id=bot.id,
            platform=msg.platform,
            external_conv_id=msg.external_conv_id[:191],
            external_conv_type=msg.conv_type or CONV_DM,
            display_name=(msg.display_name or "")[:200] or None,
        )
        db.add(conv)
        db.flush()
        log_audit(
            db,
            tenant_id=bot.tenant_id,
            actor_id=None,
            action="bot_conversation_create",
            resource_type="bot_conversation",
            resource_id=conv.id,
            result="success",
            request_id=msg.external_msg_id[:64],
            metadata={"platform": msg.platform, "type": conv.external_conv_type},
        )
    elif msg.display_name and conv.display_name != msg.display_name:
        conv.display_name = msg.display_name[:200]
    return conv


def _is_approved(bot: BotConfig, conv: BotConversation) -> bool:
    if not bot.require_approval:
        return True
    return conv.approved_at is not None and (
        conv.revoked_at is None or conv.revoked_at < conv.approved_at
    )


def _load_context(conv: BotConversation) -> list[dict[str, str]]:
    # JSON 列直接存 list（无需字符串往返）；防御式取值兼容坏数据
    data = conv.context_cache_json
    if not isinstance(data, list):
        return []
    return [t for t in data if isinstance(t, dict) and "role" in t and "content" in t]


def _save_context(conv: BotConversation, turns: list[dict[str, str]]) -> None:
    limit = max(settings.bot_context_window_turns, 0) * 2  # user+assistant 成对
    conv.context_cache_json = list(turns[-limit:] if limit else [])


def _log_msg(
    db: Session,
    *,
    bot: BotConfig,
    conv_id: str,
    direction: str,
    text: str,
    routed_by: str | None = None,
    reason: str | None = None,
    ok: bool = True,
) -> None:
    db.add(
        BotMessageLog(
            tenant_id=bot.tenant_id,
            conversation_id=conv_id,
            direction=direction,
            content_sha12=_sha12(text),
            length=len(text),
            routed_by=routed_by,
            reason_summary=(reason or "")[:200] or None,
            ok=ok,
        )
    )


def _strip_mentions(text: str) -> str:
    # 群里 @机器人 的文本变体（企微/钉钉普遍带 @ 前缀）
    while text.startswith("@"):
        parts = text.split(" ", 1)
        text = parts[1].strip() if len(parts) > 1 else ""
    return text.strip()


def _ctx_for_bot(bot: BotConfig, conv: BotConversation) -> TenantContext:
    """构造总控语境：虚拟执行者（bot:<id>），角色按系统内部调用口径。"""
    return TenantContext(
        tenant_id=bot.tenant_id,
        user_id=f"bot:{bot.id}",
        roles=["operator"],
        request_id=conv.id,
    )


def _use_override(db: Session, bot: BotConfig, text: str) -> tuple[str | None, str]:
    """/use 命令解析：命中则返回 (agent 覆盖 id 或 None 表示列表, 剩余文本)。"""
    for prefix in _USE_PREFIX:
        if text.startswith(prefix):
            arg = text[len(prefix) :].strip()
            if not arg:
                return ("__list__", "")
            # 「/use 名称 后续问题」：首段是目标，其余是问题本体
            name, _, rest = arg.partition(" ")
            return (name, rest.strip())
    return (None, text)


async def handle_inbound(
    db: Session,
    *,
    bot: BotConfig,
    msg: InboundMessage,
    guard: IdempotencyGuard,
    send: SendFn,
    list_candidates: Callable[[str], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """处理一条入站消息，返回处理摘要（status/routed_by，供测试与看板）。

    list_candidates：注入「候选助手列表」函数（manager 传 agents.supervisor 的
    包装），/use 无参数时回复可选清单。测试注入 fake。
    """
    if guard.seen(f"{bot.id}:{msg.external_msg_id}"):
        return {"status": "duplicated"}

    conv = _get_or_create_conversation(db, bot, msg)
    text = _strip_mentions(msg.text)
    _log_msg(db, bot=bot, conv_id=conv.id, direction=DIRECTION_IN, text=text)
    conv.last_msg_at = datetime.now(UTC)
    conv.msg_count += 1

    if not text:
        db.commit()
        return {"status": "empty"}

    # 批准闸门：陌生会话只回固定欢迎语
    if not _is_approved(bot, conv):
        await send(conv.external_conv_id, APPROVAL_PENDING_REPLY)
        _log_msg(
            db,
            bot=bot,
            conv_id=conv.id,
            direction=DIRECTION_OUT,
            text=APPROVAL_PENDING_REPLY,
            routed_by="approval",
        )
        db.commit()
        return {"status": "pending_approval", "conversation_id": conv.id}

    # /use 会话内切换
    override, rest = _use_override(db, bot, text)
    if override == "__list__":
        names = ""
        if list_candidates:
            names = "、".join(c["name"] for c in list_candidates(bot.tenant_id)[:10]) or "无"
        reply = f"可选助手：{names}。用法：/use 名称"
        await send(conv.external_conv_id, reply)
        _log_msg(
            db, bot=bot, conv_id=conv.id, direction=DIRECTION_OUT, text=reply, routed_by="command"
        )
        db.commit()
        return {"status": "use_list"}
    bound_override = override
    if bound_override:
        assert rest is not None
        text = rest
        if not text:
            await send(conv.external_conv_id, "请在 /use 名称 后跟上你的问题。")
            db.commit()
            return {"status": "use_need_question"}

    # 上下文滑窗（问题先入窗，回答后入窗）
    turns = _load_context(conv)
    turns.append({"role": "user", "content": text})

    # 路由执行
    from app.modules.agents import supervisor

    ctx = _ctx_for_bot(bot, conv)
    routed_by = "bound" if (bot.binding_mode != "auto" or bound_override) else None
    reason = None
    try:
        if bot.binding_mode == "auto" and not bound_override:
            out = await supervisor.ask(db, ctx=ctx, question=text)
            answer = str((out.get("result") or {}).get("answer", ""))
            routed_by = str(out.get("routed_by", ""))
            reason = str(out.get("reason", ""))[:200]
        else:
            # 指定助手/班组或 /use 覆盖：走 supervisor 的定向执行入口
            target = bound_override or bot.bound_agent_id
            out = await supervisor.ask_directed(
                db,
                ctx=ctx,
                question=text,
                target=target,
                prefer_team=(bot.binding_mode == BINDING_TEAM),
            )
            answer = str((out.get("result") or {}).get("answer", ""))
            routed_by = str(out.get("routed_by", "bound"))
            reason = str(out.get("reason", ""))[:200]
        ok = True
    except Exception as e:  # noqa: BLE001 失败也要诚实记账并回固定话术
        answer = "抱歉，我这边处理出了问题，请稍后再试或换个问法。"
        routed_by, reason, ok = "error", str(e)[:180], False

    if not ok:
        await send(conv.external_conv_id, answer)
        _log_msg(
            db,
            bot=bot,
            conv_id=conv.id,
            direction=DIRECTION_OUT,
            text=answer,
            routed_by=routed_by,
            reason=reason,
            ok=False,
        )
        _save_context(conv, turns)
        db.commit()
        return {"status": "error", "routed_by": routed_by, "conversation_id": conv.id}

    await send(conv.external_conv_id, answer)
    _log_msg(
        db,
        bot=bot,
        conv_id=conv.id,
        direction=DIRECTION_OUT,
        text=answer,
        routed_by=routed_by,
        reason=reason,
        ok=True,
    )
    turns.append({"role": "assistant", "content": answer})
    _save_context(conv, turns)
    db.commit()
    return {"status": "ok", "routed_by": routed_by, "conversation_id": conv.id}


def prune_stale_context(db: Session, *, days: int = 30) -> int:
    """清理长期无活动会话的上下文明文缓存（隐私承诺的代码兑现）。返回清理条数。"""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    rows = db.scalars(
        select(BotConversation).where(
            BotConversation.context_cache_json.isnot(None),
            BotConversation.last_msg_at.isnot(None),
            BotConversation.last_msg_at < cutoff,
        )
    ).all()
    for c in rows:
        c.context_cache_json = None
    db.commit()
    return len(rows)
