"""对话内媒体生成：media_assets 资产与任务表（v3.73）。

对话里直接出图/出片需要三样东西落库：产物位置、上游任务状态、按人计量。
上传附件表（chat_attachments）语义是「用户给的输入文件」，与生成产物混用会
把解析、预览、下载权限搅在一起；这里单独建表，只服务生成产物与视频异步任务。

只新增一张表与三个索引，SQLite/Postgres 均可直接执行；dev 环境 create_all
已按新模型建表，存在即跳过。
"""

import sqlalchemy as sa
from alembic import op

revision = "0081_media_assets"
down_revision = "0080_yard_quota_reset_period"
branch_labels = None
depends_on = None

_TABLE = "media_assets"


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, _TABLE):
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("yard_id", sa.String(36), nullable=False),
        sa.Column("session_id", sa.String(36), nullable=True),
        sa.Column("message_id", sa.String(36), nullable=True),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("storage_key", sa.String(255), nullable=True),
        sa.Column("poster_key", sa.String(255), nullable=True),
        sa.Column("url", sa.String(500), nullable=True),
        sa.Column("mime", sa.String(120), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(80), nullable=True),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("upstream_task_id", sa.String(200), nullable=True),
        sa.Column("error_code", sa.String(50), nullable=True),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("meta_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_media_assets_yard_id", _TABLE, ["yard_id"])
    op.create_index("ix_media_assets_session_id", _TABLE, ["session_id"])
    op.create_index("ix_media_assets_message_id", _TABLE, ["message_id"])
    op.create_index("ix_media_assets_created_by", _TABLE, ["created_by"])
    op.create_index("ix_media_assets_upstream_task_id", _TABLE, ["upstream_task_id"])
    op.create_index("ix_media_assets_yard_created", _TABLE, ["yard_id", "created_at"])
    op.create_index("ix_media_assets_session_created", _TABLE, ["session_id", "created_at"])
    op.create_index("ix_media_assets_status_updated", _TABLE, ["status", "updated_at"])


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, _TABLE):
        return
    op.drop_table(_TABLE)
