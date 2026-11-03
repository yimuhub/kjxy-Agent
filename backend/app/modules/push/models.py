"""推送通道 ORM（迁移 0015）。

安全三件套完整复刻 devices 连接器模式：
1. URL 内含机器人 key → **视同密钥**：``url_enc`` 存 Fernet 密文
   （复用 llm/supplier_crypto），任何接口响应只回掩码，永不回显明文；
2. 出网前后双重 ``check_egress_url``（net_guard）：保存时校验 + 发送前复检
   （防 DNS 重绑定）；
3. 连续失败 5 次自动 ``enabled=false`` 熔断 + 审计（见 service.dispatch）。

``events_json`` 为订阅事件数组（device_alert|workorder_created|team_run_failed|
skill_force_published|self_improve_run|farm_alert|farm_agent_run）；
``url_enc`` 可空：允许先建通道后补 URL，无 URL 的通道 dispatch 直接跳过。
"""

from sqlalchemy import JSON, Boolean, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

# 通道类型与事件字典（服务层白名单校验，宁严勿松）
KIND_WECOM = "wecom_robot"
KIND_DINGTALK = "dingtalk"
KIND_GENERIC = "generic_webhook"
VALID_KINDS = (KIND_WECOM, KIND_DINGTALK, KIND_GENERIC)

EVENT_DEVICE_ALERT = "device_alert"
EVENT_WORKORDER_CREATED = "workorder_created"
EVENT_TEAM_RUN_FAILED = "team_run_failed"
EVENT_SKILL_FORCE_PUBLISHED = "skill_force_published"
EVENT_SELF_IMPROVE_RUN = "self_improve_run"
#: v3.28：Agent 自动执行产出的告警与运行摘要
EVENT_FARM_ALERT = "farm_alert"
EVENT_FARM_AGENT_RUN = "farm_agent_run"
#: v3.56：指挥官周报落地推送给负责人（本周幂等，见 reports.weekly_optimization）
EVENT_COMMANDER_WEEKLY = "commander_weekly_report"
VALID_EVENTS = (
    EVENT_COMMANDER_WEEKLY,
    EVENT_DEVICE_ALERT,
    EVENT_WORKORDER_CREATED,
    EVENT_TEAM_RUN_FAILED,
    EVENT_SKILL_FORCE_PUBLISHED,
    EVENT_SELF_IMPROVE_RUN,
    EVENT_FARM_ALERT,
    EVENT_FARM_AGENT_RUN,
)

#: 事件的人类中文标签（markdown 正文与审计展示共用口径）
EVENT_LABELS = {
    EVENT_DEVICE_ALERT: "设备告警",
    EVENT_WORKORDER_CREATED: "工单已创建",
    EVENT_TEAM_RUN_FAILED: "班组运行失败",
    EVENT_SKILL_FORCE_PUBLISHED: "私有技能被转为公共",
    EVENT_SELF_IMPROVE_RUN: "自我总结运行完成",
    EVENT_FARM_ALERT: "农事预警",
    EVENT_FARM_AGENT_RUN: "Agent 自动执行",
    EVENT_COMMANDER_WEEKLY: "指挥官周报已生成",
}


class PushChannel(Base, TimestampMixin):
    __tablename__ = "push_channels"
    __table_args__ = (UniqueConstraint("yard_id", "name", name="uq_push_channels_yard_name"),)

    yard_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # wecom_robot|dingtalk|generic_webhook
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    # 机器人 URL 的 Fernet 密文（URL 含 key 视同密钥；明文永不出库/出接口）
    url_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 订阅事件数组：["device_alert", ...]
    events_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
