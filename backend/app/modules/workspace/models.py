"""工作区插件 ORM（v3.63）：受控项目根 + 文件变更台账。

口径：
- 项目根必须是绝对路径，且位于允许根（默认用户主目录）之内；
- 每次修改先落 ``preview`` 记录，应用/回滚都基于同一份前后快照；
- 只允许文本文件，路径解析后必须仍在项目根内，敏感文件一律拒绝。
"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

PROJECT_STATUS_ACTIVE = "active"
PROJECT_STATUS_ARCHIVED = "archived"
PROJECT_STATUSES = (PROJECT_STATUS_ACTIVE, PROJECT_STATUS_ARCHIVED)

CHANGE_STATUS_PREVIEW = "preview"
CHANGE_STATUS_APPLIED = "applied"
CHANGE_STATUS_ROLLED_BACK = "rolled_back"
CHANGE_STATUSES = (CHANGE_STATUS_PREVIEW, CHANGE_STATUS_APPLIED, CHANGE_STATUS_ROLLED_BACK)


class WorkspaceProject(Base, TimestampMixin):
    __tablename__ = "workspace_projects"
    __table_args__ = (
        UniqueConstraint("yard_id", "name", name="uq_workspace_projects_yard_name"),
        Index("ix_workspace_projects_yard_status", "yard_id", "status"),
        # 同一宿主目录只允许一个 active 工作区（跨小院独占的数据库兜底）；
        # 部分索引放行 archived 行，归档后目录可重新注册。
        Index(
            "uq_workspace_projects_root_active",
            "root_path",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    root_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    allowed_paths: Mapped[list | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=PROJECT_STATUS_ACTIVE)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class WorkspaceChange(Base, TimestampMixin):
    __tablename__ = "workspace_changes"
    __table_args__ = (
        Index("ix_workspace_changes_yard_project", "yard_id", "project_id"),
        Index("ix_workspace_changes_yard_status", "yard_id", "status"),
    )

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    plugin_id: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=CHANGE_STATUS_PREVIEW)
    before_existed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    before_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    before_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    after_content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    after_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    diff_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    diff_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    added_lines: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    removed_lines: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    git_head_before: Mapped[str | None] = mapped_column(String(64), nullable=True)
    git_head_after: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    applied_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rolled_back_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
