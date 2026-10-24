"""机器人配置服务：CRUD + 凭据加密掩码 + 连接状态与熔断。

安全口径（与 push 通道同构）：
- credentials 明文只存在于创建/更新的入参与 test_connection 内存瞬时——
  库内是 Fernet 密文，任何接口回显一律掩码；
- 状态机由 manager 回填（connecting/online/offline/error + status_reason）；
- 连续失败达 bot_consecutive_failure_limit 自动 enabled=False + 审计；
  人工重新启用（PATCH enabled=true）时清零计数。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.audit.service import log_audit
from app.modules.bots.models import (
    STATUS_ERROR,
    STATUS_OFFLINE,
    VALID_BINDING_MODES,
    VALID_PLATFORMS,
    BotConfig,
    BotConversation,
)
from app.modules.llm.supplier_crypto import decrypt_api_key, encrypt_api_key

log = logging.getLogger(__name__)


def _mask_value(v: str) -> str:
    s = str(v)
    return (s[:3] + "***" + s[-2:]) if len(s) > 7 else "***"


def mask_credentials(creds: dict[str, Any]) -> dict[str, str]:
    """凭据字典逐字段掩码（只回结构不回值；键名本身不含敏感資訊）。"""
    return {k: _mask_value(str(v)) for k, v in creds.items()}


def _dump_credentials(creds: dict[str, Any]) -> str:
    return encrypt_api_key(json.dumps(creds, ensure_ascii=False))


def load_credentials(bot: BotConfig) -> dict[str, Any]:
    try:
        data = json.loads(decrypt_api_key(bot.credentials_enc))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 密文损坏按未配置处理（manager 会置 error）
        return {}


def bot_to_out(b: BotConfig) -> dict[str, Any]:
    return {
        "id": b.id,
        "platform": b.platform,
        "name": b.name,
        "enabled": b.enabled,
        "status": b.status,
        "status_reason": b.status_reason,
        "credentials_masked": mask_credentials(load_credentials(b)),
        "binding_mode": b.binding_mode,
        "bound_agent_id": b.bound_agent_id,
        "require_approval": b.require_approval,
        "consecutive_failures": b.consecutive_failures,
        "created_at": b.created_at.isoformat() if b.created_at else "",
    }


def validate_create(
    *,
    platform: str,
    name: str,
    credentials: dict[str, Any],
    binding_mode: str,
    bound_agent_id: str | None,
) -> None:
    from app.core.errors import AppError

    if platform not in VALID_PLATFORMS:
        raise AppError("INVALID_REQUEST", f"不支持的平台：{platform}", http_status=400)
    if binding_mode not in VALID_BINDING_MODES:
        raise AppError("INVALID_REQUEST", f"不支持的绑定方式：{binding_mode}", http_status=400)
    if binding_mode != "auto" and bound_agent_id is not None and not str(bound_agent_id).strip():
        raise AppError("INVALID_REQUEST", "绑定方式需要指定助手/班组。", http_status=400)
    if not name.strip():
        raise AppError("INVALID_REQUEST", "名称不能为空。", http_status=400)
    if not credentials:
        raise AppError("INVALID_REQUEST", "凭据不能为空。", http_status=400)
    _PLATFORM_REQUIRED[platform](credentials)


# 平台 → 必填凭据字段（企微长连接 BotID+Secret；钉钉/飞书 ClientID(App)+Secret）
_PLATFORM_REQUIRED = {
    "wecom_aibot": lambda c: _need(c, ("bot_id", "secret")),
    "dingtalk": lambda c: _need(c, ("client_id", "client_secret")),
    "feishu": lambda c: _need(c, ("app_id", "app_secret")),
}


def _need(creds: dict[str, Any], keys: tuple[str, ...]) -> None:
    from app.core.errors import AppError

    missing = [k for k in keys if not str(creds.get(k, "")).strip()]
    if missing:
        raise AppError(
            "INVALID_REQUEST",
            f"凭据缺少必填字段：{'、'.join(missing)}",
            http_status=400,
        )


def create_bot(
    db: Session,
    *,
    tenant_id: str,
    actor_id: str,
    platform: str,
    name: str,
    credentials: dict[str, Any],
    binding_mode: str = "auto",
    bound_agent_id: str | None = None,
    require_approval: bool = True,
) -> BotConfig:
    validate_create(
        platform=platform,
        name=name.strip(),
        credentials=credentials,
        binding_mode=binding_mode,
        bound_agent_id=bound_agent_id,
    )
    bot = BotConfig(
        tenant_id=tenant_id,
        platform=platform,
        name=name.strip()[:120],
        credentials_enc=_dump_credentials(credentials),
        binding_mode=binding_mode,
        bound_agent_id=(bound_agent_id or None),
        require_approval=require_approval,
        created_by=actor_id,
    )
    db.add(bot)
    db.flush()
    log_audit(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="bot_create",
        resource_type="bot_config",
        resource_id=bot.id,
        result="success",
        request_id="",
        metadata={"platform": platform, "name": bot.name},
    )
    db.commit()
    return bot


def list_bots(db: Session, tenant_id: str) -> list[BotConfig]:
    return list(
        db.scalars(
            select(BotConfig)
            .where(BotConfig.tenant_id == tenant_id)
            .order_by(BotConfig.created_at.desc())
        )
    )


def get_bot_or_404(db: Session, tenant_id: str, bot_id: str) -> BotConfig:
    from app.core.errors import AppError

    row = db.scalars(
        select(BotConfig).where(BotConfig.id == bot_id, BotConfig.tenant_id == tenant_id)
    ).first()
    if row is None:
        raise AppError("NOT_FOUND", "机器人不存在或无权访问。", http_status=404)
    return row


def update_bot(
    db: Session,
    *,
    tenant_id: str,
    actor_id: str,
    bot: BotConfig,
    name: str | None = None,
    credentials: dict[str, Any] | None = None,
    binding_mode: str | None = None,
    bound_agent_id: str | None = None,
    require_approval: bool | None = None,
    enabled: bool | None = None,
) -> BotConfig:
    from app.core.errors import AppError

    if name is not None:
        if not name.strip():
            raise AppError("INVALID_REQUEST", "名称不能为空。", http_status=400)
        bot.name = name.strip()[:120]
    if credentials:
        validate_create(
            platform=bot.platform,
            name=bot.name,
            credentials=credentials,
            binding_mode=binding_mode or bot.binding_mode,
            bound_agent_id=bound_agent_id if bound_agent_id is not None else bot.bound_agent_id,
        )
        bot.credentials_enc = _dump_credentials(credentials)
    if binding_mode is not None:
        if binding_mode not in VALID_BINDING_MODES:
            raise AppError("INVALID_REQUEST", "不支持的绑定方式。", http_status=400)
        bot.binding_mode = binding_mode
    if bound_agent_id is not None:
        bot.bound_agent_id = bound_agent_id or None
    if require_approval is not None:
        bot.require_approval = require_approval
    if enabled is not None:
        if enabled and not bot.enabled:
            # 人工重新启用：清熔断计数与错误状态（保留 status_reason 供回看）
            bot.consecutive_failures = 0
            if bot.status == STATUS_ERROR:
                bot.status = STATUS_OFFLINE
        bot.enabled = enabled
    log_audit(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="bot_update",
        resource_type="bot_config",
        resource_id=bot.id,
        result="success",
        request_id="",
        metadata={
            "fields": sorted(
                k
                for k, v in {
                    "name": name,
                    "credentials": credentials,
                    "binding_mode": binding_mode,
                    "bound_agent_id": bound_agent_id,
                    "require_approval": require_approval,
                    "enabled": enabled,
                }.items()
                if v is not None
            )
        },
    )
    db.commit()
    return bot


def delete_bot(db: Session, *, tenant_id: str, actor_id: str, bot: BotConfig) -> None:
    """删除机器人：会话与账本保留（审计链），仅断配置。manager 侧由调用方先停连接。"""
    db.delete(bot)
    log_audit(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="bot_delete",
        resource_type="bot_config",
        resource_id=bot.id,
        result="success",
        request_id="",
        metadata={"name": bot.name},
    )
    db.commit()


def record_failure(db: Session, bot: BotConfig, reason: str) -> None:
    """连接失败记账：达阈值自动停用（熔断），并清零等待人工重新启用。"""
    bot.consecutive_failures += 1
    bot.status = (
        STATUS_ERROR
        if bot.consecutive_failures >= settings.bot_consecutive_failure_limit
        else STATUS_OFFLINE
    )
    bot.status_reason = reason[:300]
    if bot.consecutive_failures >= settings.bot_consecutive_failure_limit and bot.enabled:
        bot.enabled = False
        log_audit(
            db,
            tenant_id=bot.tenant_id,
            actor_id=None,
            action="bot_circuit_open",
            resource_type="bot_config",
            resource_id=bot.id,
            result="failed",
            request_id="",
            metadata={"failures": bot.consecutive_failures, "reason": reason[:200]},
        )
    db.commit()


def record_state(db: Session, bot_id: str, status: str, reason: str) -> None:
    """manager 回填连接状态（online 清计数）。bot 可能已被删除：静默忽略。"""
    bot = db.get(BotConfig, bot_id)
    if bot is None:
        return
    bot.status = status
    bot.status_reason = (reason or "")[:300] or None
    if status == "online":
        bot.consecutive_failures = 0
    db.commit()


def list_conversations(db: Session, tenant_id: str, *, limit: int = 50) -> list[BotConversation]:
    return list(
        db.scalars(
            select(BotConversation)
            .where(BotConversation.tenant_id == tenant_id)
            .order_by(BotConversation.last_msg_at.desc())
            .limit(min(limit, 200))
        )
    )


def conv_to_out(c: BotConversation) -> dict[str, Any]:
    def _iso(dt: Any) -> str:
        return dt.isoformat() if dt else ""

    return {
        "id": c.id,
        "bot_config_id": c.bot_config_id,
        "platform": c.platform,
        "external_conv_type": c.external_conv_type,
        "display_name": c.display_name,
        "approved_at": _iso(c.approved_at),
        "revoked_at": _iso(c.revoked_at),
        "last_msg_at": _iso(c.last_msg_at),
        "msg_count": c.msg_count,
    }


def review_conversation(
    db: Session, *, tenant_id: str, actor_id: str, conv_id: str, approve: bool
) -> BotConversation:
    from datetime import UTC, datetime

    from app.core.errors import AppError

    conv = db.scalars(
        select(BotConversation).where(
            BotConversation.id == conv_id, BotConversation.tenant_id == tenant_id
        )
    ).first()
    if conv is None:
        raise AppError("NOT_FOUND", "会话不存在或无权访问。", http_status=404)
    now = datetime.now(UTC)
    if approve:
        conv.approved_at = now
        conv.revoked_at = None
    else:
        conv.revoked_at = now
        conv.approved_at = None
        conv.context_cache_json = None  # 吊销即清上下文明文（隐私承诺）
    log_audit(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="bot_conversation_approve" if approve else "bot_conversation_revoke",
        resource_type="bot_conversation",
        resource_id=conv.id,
        result="success",
        request_id="",
        metadata={"platform": conv.platform},
    )
    db.commit()
    return conv


def list_public_names(db: Session, tenant_id: str) -> list[dict[str, Any]]:
    """/use 命令的候选清单（复用 supervisor 候选集，只取 name/id/kind）。"""
    from app.modules.agents.supervisor import list_candidates

    return [
        {"id": c["id"], "name": c["name"], "kind": c["kind"]}
        for c in list_candidates(db, tenant_id)
    ]
