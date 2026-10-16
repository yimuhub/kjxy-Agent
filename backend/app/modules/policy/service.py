"""政策服务：采集入库（去重）+ 过期标记 + 规则匹配。

第三期实现，不再是 P1 桩。
- 新写入强制带 tenant_id（从 TenantContext 取）；list/get/match 全部按
  tenant_id 隔离。
- 去重：同一 tenant 下 (policy_name + publish_department) 已存在 → 409。
- 过期：GET list / POST match 前先对本租户执行 mark_expired。
- 匹配：只做规则比对，不编造成功率预测。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.orm import Session

from app.core.deps import TenantContext
from app.core.errors import AppError
from app.modules.policy.models import Policy, PolicyApplication
from app.modules.policy.schemas import (
    ApplicationDraftCreate,
    ChecklistItem,
    PolicyIngestRequest,
    PolicyMatchResult,
    PolicyProfile,
)

_MATCH_LEVEL_RANK = {"eligible": 0, "partial": 1, "insufficient": 2, "ineligible": 3}


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ------------------------------------------------------------------ 采集


def ingest_policy(db: Session, ctx: TenantContext, req: PolicyIngestRequest) -> Policy:
    """政策采集入库。

    同一 tenant 下 (policy_name + publish_department) 已存在 → 409
    POLICY_DUPLICATE。自动计算 source_hash 并写入 first_crawled_at。
    """
    duplicate = (
        db.query(Policy)
        .filter(
            Policy.tenant_id == ctx.tenant_id,
            Policy.policy_name == req.policy_name,
            Policy.publish_department == req.publish_department,
        )
        .first()
    )
    if duplicate is not None:
        raise AppError(
            "POLICY_DUPLICATE",
            "该租户下已存在同名同发布机构的政策，不重复入库。",
            http_status=409,
        )
    source_hash = hashlib.sha256(
        ((req.content or "") + "\n" + (req.source_url or "")).encode("utf-8")
    ).hexdigest()
    policy = Policy(
        tenant_id=ctx.tenant_id,
        scope=req.scope,
        province=req.province,
        city=req.city,
        department=req.department,
        policy_name=req.policy_name,
        publish_department=req.publish_department,
        published_at=req.published_at,
        apply_start_at=req.apply_start_at,
        apply_end_at=req.apply_end_at,
        policy_type=req.policy_type,
        support_direction=req.support_direction,
        apply_conditions_json=req.apply_conditions_json,
        material_requirements_json=req.material_requirements_json,
        content=req.content,
        source_url=req.source_url,
        source_hash=source_hash,
        first_crawled_at=_utcnow(),
        status="active",
        review_status="pending",
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return policy


# ------------------------------------------------------------------ 过期标记


def mark_expired(db: Session, tenant_id: str) -> int:
    """把本租户下 apply_end_at 已过且仍为 active 的政策标记为 expired。

    返回被标记的条数。由 list/match 调用前自动执行，无需管理接口。
    """
    now = _utcnow()
    overdue = (
        db.query(Policy)
        .filter(
            Policy.tenant_id == tenant_id,
            Policy.status == "active",
            Policy.apply_end_at.isnot(None),
            Policy.apply_end_at < now,
        )
        .all()
    )
    for policy in overdue:
        policy.status = "expired"
        policy.last_checked_at = now
    if overdue:
        db.commit()
    return len(overdue)


# ------------------------------------------------------------------ 查询


def list_policies(
    db: Session,
    ctx: TenantContext,
    page: int,
    page_size: int,
    keyword: str | None = None,
    province: str | None = None,
    policy_type: str | None = None,
    status: str | None = None,
) -> tuple[list[Policy], int]:
    mark_expired(db, ctx.tenant_id)
    q = db.query(Policy).filter(Policy.tenant_id == ctx.tenant_id)
    if keyword:
        q = q.filter(Policy.policy_name.ilike(f"%{keyword}%"))
    if province:
        q = q.filter(Policy.province == province)
    if policy_type:
        q = q.filter(Policy.policy_type == policy_type)
    if status:
        q = q.filter(Policy.status == status)
    total = q.count()
    items = (
        q.order_by(Policy.published_at.desc().nullslast(), Policy.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return items, total


def get_policy(db: Session, ctx: TenantContext, policy_id: str) -> Policy:
    policy = (
        db.query(Policy).filter(Policy.tenant_id == ctx.tenant_id, Policy.id == policy_id).first()
    )
    if policy is None:
        raise AppError("POLICY_NOT_FOUND", "政策不存在。", http_status=404)
    return policy


# ------------------------------------------------------------------ 规则匹配


def _evaluate_conditions(
    policy: Policy, profile: PolicyProfile
) -> tuple[list[str], list[str], list[str]]:
    """逐条比对申报条件，返回 (命中, 未命中, 无法判断) 三组条件名。

    只评估政策实际声明的条件（apply_conditions_json + 行级 province/city）；
    政策未声明的条件不列入任何一组。
    """
    matched: list[str] = []
    unmet: list[str] = []
    unknown: list[str] = []
    conds = policy.apply_conditions_json or {}

    # 作物：取交集
    crops = conds.get("crops")
    if crops:
        if profile.crops is None:
            unknown.append("crops")
        elif set(profile.crops) & set(crops):
            matched.append("crops")
        else:
            unmet.append("crops")

    # 规模：area_mu >= min_area_mu
    min_area = conds.get("min_area_mu")
    if min_area is not None:
        if profile.area_mu is None:
            unknown.append("min_area_mu")
        elif float(profile.area_mu) >= float(min_area):
            matched.append("min_area_mu")
        else:
            unmet.append("min_area_mu")

    # 申报主体类型
    applicant_types = conds.get("applicant_types")
    if applicant_types:
        if profile.applicant_type is None:
            unknown.append("applicant_types")
        elif profile.applicant_type in applicant_types:
            matched.append("applicant_types")
        else:
            unmet.append("applicant_types")

    # 地域：conditions 中的 provinces/cities 列表与行级 province/city 合并
    allowed_provinces = set(conds.get("provinces") or [])
    if policy.province:
        allowed_provinces.add(policy.province)
    allowed_cities = set(conds.get("cities") or [])
    if policy.city:
        allowed_cities.add(policy.city)
    if allowed_provinces:
        if profile.province is None:
            unknown.append("province")
        elif profile.province in allowed_provinces:
            matched.append("province")
        else:
            unmet.append("province")
    if allowed_cities:
        if profile.city is None:
            unknown.append("city")
        elif profile.city in allowed_cities:
            matched.append("city")
        else:
            unmet.append("city")

    return matched, unmet, unknown


def _match_level(
    matched: list[str], unmet: list[str], unknown: list[str]
) -> Literal["eligible", "partial", "insufficient", "ineligible"]:
    if unmet:
        return "ineligible"  # 明确不命中
    if unknown:
        return "insufficient"  # 画像缺关键字段，无法判断
    if matched:
        return "eligible"  # 全部命中
    # 政策未声明任何规则条件：只能部分参考
    return "partial"


def match_policies(
    db: Session, ctx: TenantContext, profile: PolicyProfile
) -> list[PolicyMatchResult]:
    """按画像对本租户的 active 政策做规则匹配。

    只做规则比对：eligible（全部命中）/ partial（部分命中）/
    insufficient（画像缺关键字段）/ ineligible（明确不命中）。
    不做成功率预测。按匹配度从高到低排序。
    """
    mark_expired(db, ctx.tenant_id)
    policies = (
        db.query(Policy)
        .filter(Policy.tenant_id == ctx.tenant_id, Policy.status == "active")
        .order_by(Policy.published_at.desc().nullslast(), Policy.created_at.desc())
        .all()
    )
    results = []
    for policy in policies:
        matched, unmet, unknown = _evaluate_conditions(policy, profile)
        results.append(
            PolicyMatchResult(
                policy_id=policy.id,
                policy_name=policy.policy_name,
                match_level=_match_level(matched, unmet, unknown),
                matched_conditions=matched,
                unmet_conditions=unmet,
                unknown_conditions=unknown,
                source_url=policy.source_url,
                verification_status=policy.review_status,
            )
        )
    results.sort(key=lambda r: _MATCH_LEVEL_RANK[r.match_level])
    return results


# ---------------------------------------------------------------------------
# 申报材料草案（第三期 Worker B）
# ---------------------------------------------------------------------------

VERIFICATION_REMINDER = "本材料为系统生成的草案，需人工核对后提交"


def _visible_policy(db: Session, tenant_id: str, policy_id: str) -> Policy:
    """租户可见的政策：本租户的或全局的（tenant_id 为空）。其他租户的按 404 处理。"""
    policy = db.query(Policy).filter(Policy.id == policy_id).first()
    if policy is None or (policy.tenant_id and policy.tenant_id != tenant_id):
        raise AppError("POLICY_NOT_FOUND", "政策不存在。", http_status=404)
    return policy


def _normalize_materials(raw: dict | list | None) -> list[dict]:
    """把 material_requirements_json 归一化为 [{name, required}]。

    兼容多种入库形状：[{name, required}]、{"materials": [...]}、
    {name: required}、纯字符串列表。缺失时返回空列表。
    """
    if not raw:
        return []
    items: list = []
    if isinstance(raw, dict):
        inner = raw.get("materials", raw.get("items", raw))
        if isinstance(inner, dict):
            items = [
                {"name": k, "required": v} for k, v in inner.items() if not isinstance(v, dict)
            ] + [{"name": k, **v} for k, v in inner.items() if isinstance(v, dict)]
        else:
            items = inner
    else:
        items = raw
    if not isinstance(items, list):
        return []

    normalized: list[dict] = []
    for it in items:
        if isinstance(it, str):
            normalized.append({"name": it, "required": True})
            continue
        if not isinstance(it, dict):
            continue
        name = it.get("name") or it.get("名称")
        if not name:
            continue
        required = it.get("required", it.get("是否必填", True))
        if isinstance(required, str):
            required = required.strip().lower() not in ("false", "0", "否", "no", "n")
        normalized.append({"name": str(name), "required": bool(required)})
    return normalized


def _is_blank(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list | tuple | set | dict):
        return len(value) == 0
    return False


def build_material_checklist(materials: list[dict]) -> list[ChecklistItem]:
    return [
        ChecklistItem(name=m["name"], required=m["required"], source="政策要求") for m in materials
    ]


def find_missing_materials(checklist: list[ChecklistItem], form_data: dict) -> list[str]:
    """必填材料在 form_data 中无对应值的 → 缺失。"""
    form = form_data or {}
    return [item.name for item in checklist if item.required and _is_blank(form.get(item.name))]


def build_notes(missing: list[str]) -> str:
    """固定模板：草案声明 + 需人工核对项 + 不承诺申报成功。"""
    lines = [
        "【草案声明】本申报材料为系统根据政策要求自动生成的草案，仅供参考，不代表已提交。",
        "【需人工核对项】请逐项核对材料清单中的每份材料（原件/复印件份数、有效期、盖章签字）；"
        "申请人姓名、身份类型、联系方式等基本信息请再次确认无误。",
    ]
    if missing:
        lines.append(
            "【需人工核对项】以下必填材料尚未在申请表中填写，请先行补齐："
            + "、".join(missing)
            + "。"
        )
    lines.append("【重要提示】本草案不承诺申报成功，申报结果以审批部门最终审核为准。")
    return "\n".join(lines)


def create_application_draft(
    db: Session,
    tenant_id: str,
    user_id: str,
    policy_id: str,
    payload: ApplicationDraftCreate,
) -> PolicyApplication:
    policy = _visible_policy(db, tenant_id, policy_id)

    materials = _normalize_materials(policy.material_requirements_json)
    checklist = build_material_checklist(materials)
    missing = find_missing_materials(checklist, payload.form_data or {})
    notes = build_notes(missing)

    draft = PolicyApplication(
        tenant_id=tenant_id,
        policy_id=policy.id,
        applicant_name=payload.applicant_name.strip(),
        applicant_type=payload.applicant_type,
        contact_info=payload.contact_info,
        form_data=payload.form_data or {},
        material_checklist=[c.model_dump() for c in checklist],
        missing_materials=missing,
        notes=notes,
        status="draft",  # 规范 13.5：默认草稿，不自动提交
        created_by=user_id,
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return draft


def get_application_draft(db: Session, tenant_id: str, draft_id: str) -> PolicyApplication:
    draft = (
        db.query(PolicyApplication)
        .filter(PolicyApplication.id == draft_id, PolicyApplication.tenant_id == tenant_id)
        .first()
    )
    if draft is None:
        raise AppError("DRAFT_NOT_FOUND", "申报草案不存在。", http_status=404)
    return draft
