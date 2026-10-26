"""手机配对 ORM 模型（迁移 0018）。

两张表构成配对账本：

- ``PairCode``：一次性配对码。手机扫码后凭码明文交换设备令牌；库内**只存
  sha256 哈希**（码本身等同短期凭证，泄露即等同于小院被配对），TTL 短
  （默认 300s），用过即废（``used_at`` 打标），绝不可复用。
- ``PairDevice``：已配对设备。``token_hash`` 存签发令牌的 sha256（服务端
  可核对/吊销，但拿不到令牌本身）；``expires_at`` 与令牌 exp 同口径，过期
  需重新扫码；``revoked_at`` 打标即吊销（历史保留，设备列表可核对）；
  ``last_seen_at`` 预留给后续设备心跳回写。扫码交换无登录主体，
  ``created_by`` 诚实置空（区别于 Web 端创建的资产）。

小院边界：两表全部查询带 yard_id；跨小院与不存在统一 404（防枚举）。
"""

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class PairDevice(Base, TimestampMixin):
    __tablename__ = "pair_devices"

    yard_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    # 扫码交换无登录主体：诚实置空（区别于 Web 端创建的资产）
    created_by: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    # 设备自报名称（手机型号之类，仅展示用，不做唯一约束）
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # 签发令牌的 sha256：服务端可核对/吊销，但拿不到令牌明文
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # 与令牌 exp 同口径：过期即需重新扫码配对
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # 预留：设备心跳/最近一次携令牌访问时回写
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 打标即吊销（历史保留，设备列表可核对）
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PairCode(Base, TimestampMixin):
    __tablename__ = "pair_codes"

    yard_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    # 配对码只存哈希：库库审计/备份泄露都不等于配对能力泄露
    code_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # 交换成功打标；未打标且未过期才算"未用码"（配额统计口径）
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
