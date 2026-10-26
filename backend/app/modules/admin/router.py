"""运营概况：调用量、失败原因、任务耗时、模型成本。

- 平台管理员（无小院）查看全局；小院管理员查看本小院。
- 普通用户无权访问他人的日志正文：本接口只返回聚合数字。
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user, get_db_session
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.admin import doctor
from app.modules.knowledge.models import KnowledgeDocument
from app.modules.llm.models import LLMCall
from app.modules.tasks.models import Task
from app.modules.yards.models import Yard, YardMembership
from app.modules.yards.permissions import has_permission

router = APIRouter(prefix="/admin", tags=["admin"])


def _scope(user: CurrentUser):
    """返回 (yard_filter, 管理员可见范围说明)。"""
    if not has_permission(user.roles, "admin:stats"):
        raise AppError("FORBIDDEN", "权限不足。", http_status=403)
    # 平台管理员且无小院上下文 → 全局
    if "platform_admin" in user.roles and not user.yard_id:
        return None
    if not user.yard_id:
        raise AppError("YARD_REQUIRED", "该操作需要小院上下文。", http_status=400)
    return user.yard_id


@router.get("/yards")
def admin_yard_list(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """平台管理员全局只读：小院列表 + 每小院聚合计数（成员数/任务数）。

    ADR-0002 收敛方案：只给"管理小院"入口，不含任何小院业务正文。
    """
    if not has_permission(user.roles, "admin:stats"):
        raise AppError("FORBIDDEN", "权限不足。", http_status=403)
    if "platform_admin" not in user.roles:
        # 小院管理员看自己的小院用 /yard/current，这里只服务平台全局视图
        raise AppError("FORBIDDEN", "仅平台管理员可查看全局小院列表。", http_status=403)

    yards = db.query(Yard).order_by(Yard.created_at).all()
    out = []
    for t in yards:
        members = (
            db.query(func.count(YardMembership.id))
            .filter(YardMembership.yard_id == t.id, YardMembership.status == "active")
            .scalar()
            or 0
        )
        tasks = db.query(func.count(Task.id)).filter(Task.yard_id == t.id).scalar() or 0
        out.append(
            {
                "id": t.id,
                "name": t.name,
                "status": t.status,
                "province": t.province,
                "scene_type": t.scene_type,
                "active_members": members,
                "task_total": tasks,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
        )
    return ok({"count": len(out), "yards": out})


@router.get("/stats")
def admin_stats(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    yard_id = _scope(user)

    def scoped(query, model):
        if yard_id:
            return query.filter(model.yard_id == yard_id)
        return query

    task_rows = scoped(db.query(Task.status, func.count(Task.id)), Task).group_by(Task.status).all()
    tasks_by_status = {s: c for s, c in task_rows}

    fail_rows = (
        scoped(
            db.query(Task.error_code, func.count(Task.id)).filter(Task.status == "failed"),
            Task,
        )
        .group_by(Task.error_code)
        .order_by(func.count(Task.id).desc())
        .limit(10)
        .all()
    )

    llm_row = scoped(
        db.query(
            func.count(LLMCall.id),
            func.coalesce(func.sum(LLMCall.input_tokens), 0),
            func.coalesce(func.sum(LLMCall.output_tokens), 0),
            func.coalesce(func.sum(LLMCall.estimated_cost), 0.0),
        ),
        LLMCall,
    ).one()
    llm_by_provider = (
        scoped(db.query(LLMCall.provider, func.count(LLMCall.id)), LLMCall)
        .group_by(LLMCall.provider)
        .all()
    )

    doc_rows = (
        scoped(
            db.query(KnowledgeDocument.status, func.count(KnowledgeDocument.id)), KnowledgeDocument
        )
        .group_by(KnowledgeDocument.status)
        .all()
    )

    members = scoped(db.query(func.count(YardMembership.id)), YardMembership).scalar() or 0

    return ok(
        {
            "scope": "global" if yard_id is None else "yard",
            "tasks": {
                "total": sum(tasks_by_status.values()),
                "by_status": tasks_by_status,
                "top_failure_reasons": [
                    {"error_code": code or "UNKNOWN", "count": c} for code, c in fail_rows
                ],
            },
            "llm": {
                "total_calls": llm_row[0],
                "total_input_tokens": int(llm_row[1]),
                "total_output_tokens": int(llm_row[2]),
                "total_estimated_cost": round(float(llm_row[3]), 6),
                "by_provider": {p: c for p, c in llm_by_provider},
            },
            "knowledge": {
                "documents_by_status": {s: c for s, c in doc_rows},
            },
            "members": {"total": members},
        }
    )


@router.get("/doctor")
def admin_doctor(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """Doctor 体检：依赖可用性与配置态自检（仅平台管理员）。

    每项 {name, status, detail, fix_hint}；fix_hint 遵循"失败即教学"：
    给一条可直接照做的下一条动作。detail 绝不含密钥值（见 admin/doctor.py 安全红线）。
    """
    if "platform_admin" not in user.roles:
        raise AppError("FORBIDDEN", "仅平台管理员可执行体检诊断。", http_status=403)
    return ok(doctor.run_doctor(db))
