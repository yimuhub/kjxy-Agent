"""机器人网关 ORM（迁移 0019）。

三张表构成「平台机器人 ↔ 租户会话 ↔ 消息账本」：

- ``BotConfig``：一个租户的一个平台机器人接入配置。凭据（BotID+Secret /
  ClientID+Secret）**视同密钥**：``credentials_enc`` 存 Fernet 密文（复用
  llm/supplier_crypto），接口只回掩码，明文永不出库；连续失败
  ``consecutive_failures`` 达阈值自动停用（与 push 通道同一熔断口径）。
  ``status`` 是连接器回填的实时状态（connecting/online/offline/error），
  ``status_reason`` 给人看的失败原因（被踢/缺 SDK/握手失败）。
- ``BotConversation``：平台会话映射（群 per-group、私聊 per-user）。同一租户
  + 平台 + 平台会话 id 唯一；``approved_at`` 为空且机器人 ``require_approval``
  时，陌生会话只会收到「等待管理员批准」的固定回复（OpenClaw 同款安全模型）。
  ``context_cache_json`` **存最近若干轮对话明文**，用途仅限多轮上下文拼接：
  它是全模块唯一的正文明文库表面，30 天无活动的会话由清理任务清空
  （见 service.prune_stale_context）。除它以外任何表都不落正文。
- ``BotMessageLog``：消息账本，双向各一条，**只存 sha12 摘要 + 长度 +
  路由元数据**（与 supervisor/llm_traces 隐私口径一致），永不存明文。

租户边界：所有查询带 tenant_id；跨租户与不存在统一 404（防枚举）。
"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

# --- 平台字典（服务层白名单校验，宁严勿松） ---
PLATFORM_WECOM = "wecom_aibot"
PLATFORM_DINGTALK = "dingtalk"
PLATFORM_FEISHU = "feishu"
VALID_PLATFORMS = (PLATFORM_WECOM, PLATFORM_DINGTALK, PLATFORM_FEISHU)

PLATFORM_LABELS = {
    PLATFORM_WECOM: "企业微信机器人",
    PLATFORM_DINGTALK: "钉钉机器人",
    PLATFORM_FEISHU: "飞书机器人",
}

# --- 连接状态字典（由 manager/adapter 回填，前端只做状态灯） ---
STATUS_CONNECTING = "connecting"
STATUS_ONLINE = "online"
STATUS_OFFLINE = "offline"
STATUS_ERROR = "error"
VALID_STATUSES = (STATUS_CONNECTING, STATUS_ONLINE, STATUS_OFFLINE, STATUS_ERROR)

# --- 绑定方式：自动路由 / 指定助手 / 指定班组 ---
BINDING_AUTO = "auto"
BINDING_ASSISTANT = "assistant"
BINDING_TEAM = "team"
VALID_BINDING_MODES = (BINDING_AUTO, BINDING_ASSISTANT, BINDING_TEAM)

# --- 会话类型 ---
CONV_DM = "dm"
CONV_GROUP = "group"
VALID_CONV_TYPES = (CONV_DM, CONV_GROUP)

# --- 消息方向 ---
DIRECTION_IN = "in"
DIRECTION_OUT = "out"


class BotConfig(Base, TimestampMixin):
    __tablename__ = "bot_configs"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_bot_configs_tenant_name"),)

    tenant_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    # wecom_aibot|dingtalk|feishu
    platform: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # connecting|online|offline|error（连接态由 manager 回填）
    status: Mapped[str] = mapped_column(String(20), default=STATUS_OFFLINE, nullable=False)
    # 人类可读状态说明（被踢/缺少 SDK/握手失败……），不含凭据
    status_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # 凭据的 Fernet 密文（BotID+Secret / ClientID+Secret 视同密钥，明文永不出库）
    credentials_enc: Mapped[str] = mapped_column(Text, nullable=False)
    # auto|assistant|team：auto 走 supervisor 三级路由，其余走指定对象
    binding_mode: Mapped[str] = mapped_column(String(20), default=BINDING_AUTO, nullable=False)
    # binding_mode != auto 时指向 tenant_agents.id 或 agent_teams.id
    bound_agent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # 陌生会话是否需要管理员批准（默认 True：陌生群/陌生人只回欢迎语）
    require_approval: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # 连续失败熔断计数（成功一次清零；达阈值 enabled=False）
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class BotConversation(Base, TimestampMixin):
    __tablename__ = "bot_conversations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "platform", "external_conv_id", name="uq_bot_conv_tenant_platform_ext"
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    bot_config_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    platform: Mapped[str] = mapped_column(String(30), nullable=False)
    # 平台侧会话标识（企微 chatid / 钉钉 conversationId / 飞书 chat_id）
    external_conv_id: Mapped[str] = mapped_column(String(191), nullable=False)
    # dm（私聊，per-user）| group（群，per-group）：会话隔离粒度
    external_conv_type: Mapped[str] = mapped_column(String(10), nullable=False)
    # 平台侧展示名（群名/昵称），仅展示用
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # 管理员批准时间；为空且机器人 require_approval 时会话不可用
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 声明者：批准后再次吊销（打标后重新回到「需批准」语义，历史保留）
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_msg_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    msg_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 多轮上下文滑窗（明文，仅限上下文拼接用途；30 天无活动清理，见 service）
    context_cache_json: Mapped[list | None] = mapped_column(JSON, nullable=True)


class BotMessageLog(Base, TimestampMixin):
    __tablename__ = "bot_message_logs"

    tenant_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    # in（用户→平台→我们）| out（我们→平台→用户）
    direction: Mapped[str] = mapped_column(String(5), nullable=False)
    # 正文 sha256 前 12 位：可关联同一句话，但拿不到内容
    content_sha12: Mapped[str] = mapped_column(String(12), nullable=False)
    length: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # supervisor 路由口径如实透传：rules|llm|fallback|bound|approval|error
    routed_by: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 路由理由/错误摘要（截断存，不含正文）
    reason_summary: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # 本次处理/发送是否成功（失败也记账，前端可见）
    ok: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
