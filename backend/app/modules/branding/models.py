"""品牌与门户 ORM（v3.67，迁移 0074）。

白标不 fork：平台维护**一份品牌包**（logo / 名称 / 主色 / 对外域名），
前端启动时读一次并注入 CSS 变量与文案；将来要「每枢纽一品牌」时，
再加 ``branding_scopes`` 关联表即可，读接口形状不变。

``platform_grants`` 承载平台级逐人授权（目前只有 ``branding:manage``）：
小院负责人拿不到、也不能经小院授权下放，只能由平台超管授予。
"""

from __future__ import annotations

from sqlalchemy import JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

#: 品牌包单行的固定主键（平台只有一份默认品牌）
DEFAULT_BRANDING_ID = "default"


class PlatformBranding(Base, TimestampMixin):
    __tablename__ = "platform_branding"

    app_name: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    short_name: Mapped[str] = mapped_column(String(8), nullable=False, default="")
    login_tagline: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    footer_text: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    #: 主色 #RRGGBB（空串表示沿用前端默认色）
    primary_color: Mapped[str] = mapped_column(String(7), nullable=False, default="")
    #: logo 存储键（LocalStorageBackend）；空表示用文字 logo
    logo_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    logo_mime: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: 门户对外域名（规范化后的 host 列表，用于生成链接与「非官方入口」提示）
    domains_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    updated_by: Mapped[str] = mapped_column(String(36), nullable=False, default="")


class PlatformGrant(Base, TimestampMixin):
    """平台级逐人授权（与 yard_member_grants 不同：不绑小院，只有超管能写）。"""

    __tablename__ = "platform_grants"
    __table_args__ = (UniqueConstraint("user_id", name="uq_platform_grants_user"),)

    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    grants_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


__all__ = ["DEFAULT_BRANDING_ID", "PlatformBranding", "PlatformGrant"]
