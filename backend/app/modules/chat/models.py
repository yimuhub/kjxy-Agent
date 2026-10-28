"""对话模式 ORM 模型。

建表与扩展：0017 会话/消息、0021 附件与卡片、0025 群聊成员、0027 Token、
0029 生成文档、0043 会话管理（置顶/归档/未读）与本会话授权模式。
"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

# 消息角色
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
VALID_ROLES = (ROLE_USER, ROLE_ASSISTANT)

# 消息状态（assistant 执行失败也入库，status=failed + error_code；
# 用户主动停止为 aborted）
MSG_OK = "ok"
MSG_FAILED = "failed"
MSG_ABORTED = "aborted"
VALID_MSG_STATUSES = (MSG_OK, MSG_FAILED, MSG_ABORTED)

# 附件类型
ATT_IMAGE = "image"
ATT_DOCUMENT = "document"
VALID_ATT_KINDS = (ATT_IMAGE, ATT_DOCUMENT)

# 附件状态（ready=可读；failed=解析失败，文件仍在但无可用文本）
ATT_READY = "ready"
ATT_FAILED = "failed"
VALID_ATT_STATUSES = (ATT_READY, ATT_FAILED)

# 群聊本轮应答策略（前后端稳定契约，只追加不改含义）：
# - all   ：全体成员都答（N 位 = N 条助手消息）
# - named ：只由被点名的那位答（请求带 target）
# - auto  ：每位先自判能否胜任，过门槛者答（门槛见 group.py，判不上的记步骤说明）
POLICY_ALL = "all"
POLICY_NAMED = "named"
POLICY_AUTO = "auto"
VALID_GROUP_POLICIES = (POLICY_ALL, POLICY_NAMED, POLICY_AUTO)

# 本会话的工具授权模式（0043；前后端稳定契约，只追加不改含义）：
# - ask  ：默认，与历史行为一致——高风险工具挂审批单，人工批准后才执行
# - auto ：同一工具+同一参数指纹在本会话内已被人工批准过，则自动放行（首次仍询问）
# - full ：本会话高风险工具直接执行、不再产生审批单（仅小院/平台管理员可开，全程留痕）
APPROVAL_ASK = "ask"
APPROVAL_AUTO = "auto"
APPROVAL_FULL = "full"
VALID_APPROVAL_MODES = (APPROVAL_ASK, APPROVAL_AUTO, APPROVAL_FULL)

# 能开 auto/full 的角色（与工具审批人同一口径：能批准的人才能替自己免批准）
APPROVAL_ESCALATION_ROLES = ("yard_admin", "yard_owner", "expert", "platform_admin")


class ChatSession(Base, TimestampMixin):
    __tablename__ = "chat_sessions"

    yard_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    # 创建者：会话私有边界（同小院他人也访问不到）
    created_by: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    # 缺省由首条消息自动继承；改名放开
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # 执行对象（会话列表标识）：auto=自动路由，agent=锁定某助手，team=班组；
    # 群聊不占取值——按 chat_participants 是否有行实时判定
    target: Mapped[str] = mapped_column(String(10), nullable=False, default="auto")
    target_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # ---- 会话列表管理（0043）----
    # 置顶时间：非空即置顶，列表里恒排在最前（按置顶时间倒序）
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 归档时间：非空即已归档，默认列表隐藏，切「已归档」筛选才看得到
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 未读标记：用户手动「标记未读」，打开会话即清除（会话是私有的，不引入已读回执表）
    unread: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 本会话的工具授权模式（见 VALID_APPROVAL_MODES）
    approval_mode: Mapped[str] = mapped_column(String(12), nullable=False, default=APPROVAL_ASK)
    # 分支来源：从哪个会话的哪条回答拉出来的（原会话不受影响）
    parent_session_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    parent_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class ChatMessage(Base, TimestampMixin):
    __tablename__ = "chat_messages"
    __table_args__ = (UniqueConstraint("session_id", "seq", name="uq_chat_messages_session_seq"),)

    session_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    # 冗余小院列：全部业务查询带小院边界，不依赖 join
    yard_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    # 会话内序号（1 递增）：时间戳同秒并发时排序仍稳定
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=MSG_OK)
    # ---- 以下仅 assistant 行使用：supervisor 路由结果随行存证 ----
    routed_by: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # {kind, id, code, name}（team 路由时附 run_id）
    choice_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # ---- 0021：结构化载荷与过程痕迹（assistant 行为主，user 行只带附件引用）----
    # 能力卡片等可操作载荷（{type:"capability", …}）
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 过程步骤（[{phase,title,detail,status,ts}]）
    steps_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 本条消息引用的附件 id 列表
    attachments_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # ---- 0027：单条消息 Token 消耗统计（input/output/total/model/latency_ms）----
    token_usage_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # ---- 0037：本轮回答引用的知识/网页来源（可点开溯源）----
    citations_json: Mapped[list | None] = mapped_column(JSON, nullable=True)


class ChatAttachment(Base, TimestampMixin):
    """会话附件（0021）：照片与文档的元数据 + 抽取文本。"""

    __tablename__ = "chat_attachments"

    session_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    yard_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    created_by: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    mime: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=ATT_READY)
    extracted_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # ---- 0029：对话内生成的文档（上传附件三列为空/False）----
    # md/docx/xlsx/pptx/pdf；空表示用户上传的附件
    doc_format: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # DocModel 快照：在线预览与"预览即下载"的唯一来源
    doc_model_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 是否由对话生成（前端据此允许预览；上传附件走原有下载能力）
    generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ChatParticipant(Base, TimestampMixin):
    """群聊成员表（0025）：一条会话的固定助手名单，一行一位。"""

    __tablename__ = "chat_participants"
    __table_args__ = (
        UniqueConstraint("session_id", "position", name="uq_chat_participants_session_position"),
        UniqueConstraint("session_id", "agent_id", name="uq_chat_participants_session_agent"),
        Index("ix_chat_participants_session_position", "session_id", "position"),
    )

    session_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    yard_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    created_by: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    agent_id: Mapped[str] = mapped_column(String(36), nullable=False)
    agent_code: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
