"""对话模式 ORM 模型（迁移 0017）。

两张表构成会话账本：

- ``ChatSession``：会话。``title`` 缺省由首条用户消息自动继承（可改名）；
  会话对**创建者私有**（tenant_id + created_by 双重边界）——对话内容含个人
  表述，同租户成员之间互不可见，跨租户与无权访问统一 404（防枚举）。
- ``ChatMessage``：消息。``seq`` 为会话内序号（从 1 递增，(session_id, seq)
  唯一约束兜底），历史按 seq 升序输出；``role`` ∈ user|assistant；
  ``status`` ∈ ok|failed——assistant 的 failed 表示已路由但执行失败
  （error_code 记错误码），失败同样诚实入库，历史可核对当时发生了什么。
  路由信息（routed_by/choice_json/reason）随行存证，与 supervisor 审计
  口径一致；会话正文在此持久化是产品要求（supervisor 只存问题摘要）。
"""

from sqlalchemy import JSON, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

# 消息角色
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
VALID_ROLES = (ROLE_USER, ROLE_ASSISTANT)

# 消息状态（assistant 执行失败也入库，status=failed + error_code）
MSG_OK = "ok"
MSG_FAILED = "failed"
VALID_MSG_STATUSES = (MSG_OK, MSG_FAILED)


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
