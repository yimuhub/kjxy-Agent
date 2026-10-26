"""Skill 灰度发布：双版本并存，按小院名单或百分比切流。

同一 Skill 的 published 版本可配置一条灰度：稳定版（stable_version）继续服务
大部分小院，灰度版（rollout_version）只服务命中策略的小院。
灰度失败时可通过 halt 一键回滚到稳定版（V3.0 §11.4）。

小院模型：
- 灰度配置的写操作（设置/查询/全量/暂停）按配置者小院隔离
  （SkillRollout.yard_id = 配置者小院，鐵律的 yard_id 过滤）；
- 版本解析按 skill_code 查找灰度：allowlist / percentage 的本意就是跨小院分流，
  命中 → rollout_version，未命中 → 灰度行上的 stable_version；
  多个小院对同一 skill_code 配了灰度时，优先用请求小院自己的那条；
- 无灰度时回退到请求小院自己的 published 版本（带 yard_id 过滤）。
"""

import hashlib

from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.modules.audit.service import log_audit
from app.modules.evolution.models import Skill, SkillRollout
from app.modules.evolution.service import (
    _get_skill,
    _require_review_permission,
    _transition,
)

ROLLOUT_STRATEGIES = ("yard_allowlist", "percentage")
ROLLOUT_STATUSES = ("active", "paused", "completed")


def _yard_hash_bucket(skill_code: str, yard_id: str) -> int:
    """按 (skill_code, yard_id) 哈希取模落到 [0, 100) 桶，灰度分流稳定可复现。"""
    digest = hashlib.sha256(f"{skill_code}:{yard_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % 100


def _get_published_skill(db: Session, yard_id: str, skill_code: str, version: str) -> Skill:
    skill = (
        db.query(Skill)
        .filter(
            Skill.yard_id == yard_id,
            Skill.skill_code == skill_code,
            Skill.version == version,
            Skill.status == "published",
        )
        .first()
    )
    if skill is None:
        raise AppError(
            "SKILL_NOT_PUBLISHED",
            f"Skill {skill_code} 版本 {version} 不存在或未发布。",
            http_status=404,
        )
    return skill


def _get_rollout_for_code(db: Session, yard_id: str, skill_code: str) -> SkillRollout | None:
    """配置者视角：只取自己小院未完成的灰度（写操作隔离）。"""
    return (
        db.query(SkillRollout)
        .filter(
            SkillRollout.yard_id == yard_id,
            SkillRollout.skill_code == skill_code,
            SkillRollout.status.in_(("active", "paused")),
        )
        .order_by(SkillRollout.updated_at.desc())
        .first()
    )


def _get_active_rollout_for_resolve(
    db: Session, yard_id: str, skill_code: str
) -> SkillRollout | None:
    """解析视角：按 skill_code 找生效灰度；请求小院自己的优先，其次取最新。

    allowlist / percentage 策略天然跨小院分流，因此解析不按请求小院过滤灰度行；
    多个小院对同一 code 配灰度时，请求小院自己的配置优先。
    """
    rows = (
        db.query(SkillRollout)
        .filter(
            SkillRollout.skill_code == skill_code,
            SkillRollout.status.in_(("active", "paused")),
        )
        .order_by(SkillRollout.updated_at.desc())
        .all()
    )
    if not rows:
        return None
    for row in rows:
        if row.yard_id == yard_id:
            return row
    return rows[0]


def _validate_strategy(
    strategy: str, percentage: int | None, yard_ids: list[str] | None
) -> tuple[int | None, list[str] | None]:
    if strategy not in ROLLOUT_STRATEGIES:
        raise AppError(
            "INVALID_ROLLOUT_STRATEGY",
            f"灰度策略只能为 {ROLLOUT_STRATEGIES}。",
            http_status=400,
        )
    if strategy == "yard_allowlist":
        allowlist = [t for t in (yard_ids or []) if t and t.strip()]
        if not allowlist:
            raise AppError(
                "INVALID_ROLLOUT_STRATEGY", "名单策略需要提供非空 yard_ids。", http_status=400
            )
        return None, allowlist
    if percentage is None or not 0 <= percentage <= 100:
        raise AppError(
            "INVALID_ROLLOUT_STRATEGY",
            "百分比策略的 percentage 必须在 0-100 之间。",
            http_status=400,
        )
    return percentage, None


def _audit_rollout(
    db: Session,
    ctx: YardContext,
    action: str,
    rollout: SkillRollout,
    request_id: str,
) -> None:
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action=action,
        resource_type="skill_rollout",
        resource_id=rollout.id,
        result="success",
        request_id=request_id or ctx.request_id,
        metadata={
            "skill_code": rollout.skill_code,
            "stable_version": rollout.stable_version,
            "rollout_version": rollout.rollout_version,
            "strategy": rollout.strategy,
            "percentage": rollout.percentage,
            "status": rollout.status,
        },
    )


# ---------------------------------------------------------------- 版本解析
def resolve_skill_version(db: Session, yard_id: str, skill_code: str) -> str | None:
    """解析某小院应使用的 Skill 版本。

    灰度命中（名单内 / 百分比哈希命中）→ rollout_version；
    未命中或灰度暂停 → 灰度行上的 stable_version；
    无灰度时 → 该小院该 Skill 最新的 published 版本（带 yard_id 过滤）；
    既无灰度又无 published 版本 → None。
    """
    rollout = _get_active_rollout_for_resolve(db, yard_id, skill_code)
    if rollout is not None:
        # paused = 灰度暂停回滚，全量走稳定版
        if rollout.status == "paused":
            return rollout.stable_version
        if rollout.strategy == "yard_allowlist":
            if yard_id in (rollout.yard_allowlist or []):
                return rollout.rollout_version
        elif rollout.strategy == "percentage":
            pct = rollout.percentage or 0
            if _yard_hash_bucket(skill_code, yard_id) < pct:
                return rollout.rollout_version
        return rollout.stable_version

    latest = (
        db.query(Skill)
        .filter(
            Skill.yard_id == yard_id,
            Skill.skill_code == skill_code,
            Skill.status == "published",
        )
        .order_by(Skill.created_at.desc())
        .first()
    )
    return latest.version if latest is not None else None


# ---------------------------------------------------------------- 灰度管理
def set_rollout(
    db: Session,
    ctx: YardContext,
    skill_id: str,
    rollout_version: str,
    strategy: str,
    percentage: int | None = None,
    yard_ids: list[str] | None = None,
    request_id: str = "",
) -> SkillRollout:
    """在 published 技能上设置/更新灰度。skill_id 指向的 published 版本为稳定版。"""
    _require_review_permission(ctx)
    stable = _get_skill(db, ctx, skill_id)
    if stable.status != "published":
        raise AppError(
            "INVALID_SKILL_TRANSITION", "只有已发布的 Skill 版本可以设置灰度。", http_status=409
        )
    if rollout_version == stable.version:
        raise AppError("INVALID_ROLLOUT_VERSION", "灰度版本不能与稳定版本相同。", http_status=400)
    _get_published_skill(db, ctx.yard_id, stable.skill_code, rollout_version)
    pct, allowlist = _validate_strategy(strategy, percentage, yard_ids)

    rollout = _get_rollout_for_code(db, ctx.yard_id, stable.skill_code)
    if rollout is None:
        rollout = SkillRollout(
            yard_id=ctx.yard_id,
            skill_id=stable.id,
            skill_code=stable.skill_code,
            stable_version=stable.version,
            rollout_version=rollout_version,
            strategy=strategy,
            percentage=pct,
            yard_allowlist=allowlist,
            status="active",
        )
        db.add(rollout)
    else:
        rollout.skill_id = stable.id
        rollout.stable_version = stable.version
        rollout.rollout_version = rollout_version
        rollout.strategy = strategy
        rollout.percentage = pct
        rollout.yard_allowlist = allowlist
        rollout.status = "active"
    db.flush()
    _audit_rollout(db, ctx, "skill.rollout.set", rollout, request_id)
    return rollout


def get_rollout(db: Session, ctx: YardContext, skill_id: str) -> SkillRollout | None:
    """查询某 Skill（任意版本）所属 code 的灰度配置，无灰度时返回 None。"""
    skill = _get_skill(db, ctx, skill_id)
    return _get_rollout_for_code(db, ctx.yard_id, skill.skill_code)


def promote_rollout(
    db: Session, ctx: YardContext, skill_id: str, request_id: str = ""
) -> SkillRollout:
    """灰度全量：rollout_version 成为唯一 published，旧稳定版 deprecated，灰度标记 completed。"""
    _require_review_permission(ctx)
    skill = _get_skill(db, ctx, skill_id)
    rollout = _get_rollout_for_code(db, ctx.yard_id, skill.skill_code)
    if rollout is None:
        raise AppError("ROLLOUT_NOT_FOUND", "该 Skill 没有进行中的灰度。", http_status=404)

    stable = (
        db.query(Skill)
        .filter(
            Skill.yard_id == ctx.yard_id,
            Skill.skill_code == rollout.skill_code,
            Skill.version == rollout.stable_version,
        )
        .first()
    )
    if stable is None:
        raise AppError(
            "SKILL_NOT_FOUND",
            f"稳定版 {rollout.skill_code}@{rollout.stable_version} 已不存在。",
            http_status=404,
        )
    if stable.status == "published":
        _transition(stable, "deprecated")
    rollout.status = "completed"
    db.flush()
    _audit_rollout(db, ctx, "skill.rollout.promote", rollout, request_id)
    return rollout


def halt_rollout(
    db: Session, ctx: YardContext, skill_id: str, request_id: str = ""
) -> SkillRollout:
    """暂停灰度回滚到稳定版：状态置 paused，流量解析全部回到 stable_version。"""
    _require_review_permission(ctx)
    skill = _get_skill(db, ctx, skill_id)
    rollout = _get_rollout_for_code(db, ctx.yard_id, skill.skill_code)
    if rollout is None:
        raise AppError("ROLLOUT_NOT_FOUND", "该 Skill 没有进行中的灰度。", http_status=404)
    if rollout.status != "active":
        raise AppError("INVALID_ROLLOUT_STATUS", "只有进行中的灰度可以暂停。", http_status=409)
    rollout.status = "paused"
    db.flush()
    _audit_rollout(db, ctx, "skill.rollout.halt", rollout, request_id)
    return rollout
