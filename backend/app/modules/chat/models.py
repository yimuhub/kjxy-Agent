"""对话模式 ORM 模型（迁移 0017，附件/卡片扩展见 0021）。

两张表构成会话账本：

- ``ChatSession``：会话。``title`` 缺省由首条用户消息自动继承（可改名）；
  会话对**创建者私有**（tenant_id + created_by 双重边界）——对话内容含个人
  表述，同租户成员之间互不可见，跨租户与无权访问统一 404（防枚举）。
- ``ChatMessage``：消息。``seq`` 为会话内序号（从 1 递增，(session_id, seq)
  唯一约束兜底），历史按 seq 升序输出；``role`` ∈ user|assistant；
  ``status`` ∈ ok|failed|aborted——assistant 的 failed 表示已路由但执行失败
  （error_code 记错误码），aborted 表示用户主动停止，失败与中止同样诚实
  入库，历史可核对当时发生了什么。路由信息（routed_by/choice_json/reason）
  随行存证，与 supervisor 审计口径一致；会话正文在此持久化是产品要求
  （supervisor 只存问题摘要）。

0021 新增：
- ``ChatAttachment``：会话附件（照片/文档）。原始文件存于文件存储后端
  （随机键，不落原始文件名），本表只存元数据与抽取出的文本摘要。
  ``kind`` ∈ image|document；``status`` ∈ ready|failed。
- ``ChatMessage.payload_json``：assistant 消息的结构化载荷（能力卡片等），
  与纯文本 ``content`` 并存——正文给人看，卡片给前端渲染可操作控件。
- ``ChatMessage.steps_json``：本条消息的过程步骤（正在思考/正在阅读…），
  历史回放时能复现"当时怎么走过来的"。
- ``ChatMessage.attachments_json``：本条消息引用的附件 id 列表。
"""

from sqlalchemy import JSON, Integer, String, Text, UniqueConstraint
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


class ChatSession(Base, TimestampMixin):
    __tablename__ = "chat_sessions"

    tenant_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    # 创建者：会话私有边界（同租户他人也访问不到）
    created_by: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    # 缺省由首条消息自动继承；改名放开
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)


class ChatMessage(Base, TimestampMixin):
    __tablename__ = "chat_messages"
    __table_args__ = (UniqueConstraint("session_id", "seq", name="uq_chat_messages_session_seq"),)

    session_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    # 冗余租户列：全部业务查询带租户边界，不依赖 join
    tenant_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
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


class ChatAttachment(Base, TimestampMixin):
    """会话附件（0021）：照片与文档的元数据 + 抽取文本。

    文件本体存于文件存储后端（``LocalStorageBackend``，随机存储键 + 防路径
    穿越），本表不存二进制；``extracted_text`` 是给模型读的文本（文档抽取
    结果），图片为空串——图片走知识库索引检索路径读取，不做 OCR。
    """

    __tablename__ = "chat_attachments"

    session_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    # 冗余租户列：全部查询带租户边界，不依赖 join
    tenant_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    # 冗余创建者列：附件与会话同边界（会话私有，同租户他人不可见）
    created_by: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    # 原始文件名（仅展示用，永不作为服务器路径的一部分）
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # 存储键（随机，不含原始文件名）
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    mime: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    # image | document
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # ready | failed（解析失败）
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=ATT_READY)
    # 抽取出的文本（文档用；已按上限截断）
    extracted_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 解析失败原因（status=failed 时有值）
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)
