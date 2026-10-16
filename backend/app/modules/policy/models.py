"""政策模块模型（第三期：采集 / 匹配 / 申报的地基）。

本文件只做数据结构定义：Policy 补 tenant_id 多租户隔离字段、
新增 PolicyApplication 申报申请单模型。业务逻辑（采集、匹配、材料核对）
在 service.py / router.py 中实现，不在此文件。
"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Policy(Base, TimestampMixin):
    """惠农/农业政策条目。"""

    __tablename__ = "policies"

    # 多租户隔离。nullable=True 兼容迁移前的老数据；新写入必须带 tenant_id（业务层强制）。
    tenant_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    # 适用范围
    # national|province|city
    scope: Mapped[str] = mapped_column(String(50), nullable=False, default="national")
    province: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    city: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    department: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # 基本信息
    policy_name: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    publish_department: Mapped[str | None] = mapped_column(String(200), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    apply_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    apply_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    policy_type: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    support_direction: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # 结构化内容（P0 统一用通用 JSON；PG 下可按需迁移为 JSONB）
    apply_conditions_json: Mapped[dict | None] = mapped_column(
        "apply_conditions", JSON, nullable=True
    )
    material_requirements_json: Mapped[dict | None] = mapped_column(
        "material_requirements", JSON, nullable=True
    )

    # 原文与来源
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # 采集与核验（P1 采集能力待实现；字段先占位）
    first_crawled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # 状态
    # active|expired|archived
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", index=True)
    # pending|verified|rejected
    review_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")


class PolicyApplication(Base, TimestampMixin):
    """政策申报申请单：一份针对某条政策的申报材料。

    第三期地基模型。申报流程（草稿填报 → 材料核对 → 提交）与
    材料缺失计算等业务逻辑在 service.py 中实现，不在此文件。
    """

    __tablename__ = "policy_applications"

    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    policy_id: Mapped[str] = mapped_column(
        String(36), sa.ForeignKey("policies.id"), nullable=False, index=True
    )

    applicant_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # 个人 | 家庭农场 | 合作社 | 企业
    applicant_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    contact_info: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # 填充的申请表字段（键值对）
    form_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 材料清单（条目数组：每项含名称 / 是否必填 / 上传状态等）
    material_checklist: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 缺失材料（材料名称数组）
    missing_materials: Mapped[list | None] = mapped_column(JSON, nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # draft|submitted
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", index=True)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
