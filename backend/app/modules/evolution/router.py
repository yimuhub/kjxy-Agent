"""演进路由：反馈、知识候选审核、Skill 版本管理。"""

from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.deps import TenantContext, get_db_session, get_tenant_context
from app.core.pagination import pagination_params
from app.core.responses import ok, paged
from app.modules.evolution import precheck as precheck_service
from app.modules.evolution import rollout as rollout_service
from app.modules.evolution import service
from app.modules.evolution import suggest as suggest_service
from app.modules.evolution.schemas import (
    CandidateBatchFailure,
    CandidateBatchReview,
    CandidateBatchReviewOut,
    CandidateOut,
    CandidateReview,
    EvalRunOut,
    FeedbackCreate,
    FeedbackOut,
    RolloutCreate,
    RolloutOut,
    SkillCreate,
    SkillOut,
    SkillReview,
    SkillSuggest,
)

router = APIRouter(tags=["evolution"])


@router.post("/feedback", status_code=201)
def create_feedback(
    body: FeedbackCreate,
    ctx: TenantContext = Depends(get_tenant_context),
    db=Depends(get_db_session),
):
    feedback = service.submit_feedback(
        db,
        ctx,
        task_id=body.task_id,
        rating=body.rating,
        feedback_text=body.feedback_text,
        corrected_answer=body.corrected_answer,
        request_id=ctx.request_id,
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return JSONResponse(
        status_code=201, content=ok(FeedbackOut.model_validate(feedback).model_dump(mode="json"))
    )


@router.get("/feedback")
def get_feedback(
    page_page: tuple[int, int] = Depends(pagination_params),
    ctx: TenantContext = Depends(get_tenant_context),
    db=Depends(get_db_session),
):
    page, page_size = page_page
    items, total = service.list_feedback(db, ctx, page, page_size)
    return paged(
        [FeedbackOut.model_validate(i).model_dump(mode="json") for i in items],
        page,
        page_size,
        total,
    )


@router.get("/knowledge/candidates")
def get_candidates(
    status: str | None = Query(default=None, description="pending_review|approved|rejected"),
    page_page: tuple[int, int] = Depends(pagination_params),
    ctx: TenantContext = Depends(get_tenant_context),
    db=Depends(get_db_session),
):
    page, page_size = page_page
    items, total = service.list_candidates(db, ctx, page, page_size, status=status)
    payloads: list[dict[str, Any]] = []
    for item in items:
        payload = CandidateOut.model_validate(item).model_dump(mode="json")
        # 双层验收第一层：机器预检（纯函数、零 token）。S1 展示性预筛：
        # 结论只随响应透出供参考，列表/审核行为完全不受影响。
        payload["machine_precheck"] = precheck_service.precheck_candidate(item)
        payloads.append(payload)
    return paged(payloads, page, page_size, total)


@router.post("/knowledge/candidates/{candidate_id}/review")
async def review_candidate(
    candidate_id: str,
    body: CandidateReview,
    ctx: TenantContext = Depends(get_tenant_context),
    db=Depends(get_db_session),
):
    candidate = service.review_candidate(
        db, ctx, candidate_id, body.action, body.note, request_id=ctx.request_id
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    # data 中的 ingest_ok/ingest_error 由 CandidateOut 从运行时属性读取（S1）
    payload = CandidateOut.model_validate(candidate).model_dump(mode="json")
    # 双层验收（均为展示性预筛，审核结论本体以 status/ingest_ok 为准）：
    # 第一层机器预检始终附带；第二层 LLM 深检仅 LLM_PRECHECK_ENABLED=true 时执行。
    payload["machine_precheck"] = precheck_service.precheck_candidate(candidate)
    if settings.llm_precheck_enabled:
        # 深检是审核成功后的附加判定：不占租户配额、不进 llm_calls；
        # 任何异常都在 deep_precheck 内部降级为 error 说明，禁止反噬审核响应。
        from app.modules.llm.provider import get_provider

        payload["deep_precheck"] = await precheck_service.deep_precheck(candidate, get_provider())
    return ok(payload)


@router.post("/knowledge/candidates/batch-review")
def batch_review_candidates(
    body: CandidateBatchReview,
    ctx: TenantContext = Depends(get_tenant_context),
    db=Depends(get_db_session),
):
    """批量审核知识候选：逐条复用单条审核逻辑，单条失败不影响其余。"""
    result = service.batch_review(
        db,
        ctx,
        body.ids,
        action=body.action,
        review_note=body.review_note,
        request_id=ctx.request_id,
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    out = CandidateBatchReviewOut(
        approved=result["approved"],
        rejected=result["rejected"],
        failed=[CandidateBatchFailure(**item) for item in result["failed"]],
    )
    return ok(out.model_dump(mode="json"))


@router.get("/skills")
def get_skills(
    status: str | None = Query(
        default=None, description="draft|testing|pending_review|published|deprecated"
    ),
    skill_code: str | None = Query(default=None),
    page_page: tuple[int, int] = Depends(pagination_params),
    ctx: TenantContext = Depends(get_tenant_context),
    db=Depends(get_db_session),
):
    page, page_size = page_page
    items, total = service.list_skills(
        db, ctx, page, page_size, status=status, skill_code=skill_code
    )
    return paged(
        [SkillOut.model_validate(i).model_dump(mode="json", by_alias=True) for i in items],
        page,
        page_size,
        total,
    )


@router.post("/skills", status_code=201)
def create_skill(
    body: SkillCreate,
    ctx: TenantContext = Depends(get_tenant_context),
    db=Depends(get_db_session),
):
    skill = service.create_skill(
        db,
        ctx,
        skill_code=body.skill_code,
        version=body.version,
        definition=body.definition,
        created_from=body.created_from,
        request_id=ctx.request_id,
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return JSONResponse(
        status_code=201,
        content=ok(SkillOut.model_validate(skill).model_dump(mode="json", by_alias=True)),
    )


@router.post("/skills/suggest", status_code=201)
async def suggest_skills(
    body: SkillSuggest,
    ctx: TenantContext = Depends(get_tenant_context),
    db=Depends(get_db_session),
):
    """AI 技能总结（v1.3-B C1，tenant_admin）：近 7 天证据 → 候选卡 → 审核队列。

    路径说明：DESIGN C1 写作 /evolution/skills/suggest，但本仓库 evolution 路由
    挂载在 /api/v1 根下（现有 /skills、/feedback 均无 /evolution 前缀，FE 零改造），
    故实际形状为 POST /api/v1/skills/suggest，与兄弟端点保持一致。
    """
    skills = await suggest_service.suggest_skills(
        db, ctx, focus_hint=body.focus_hint, request_id=ctx.request_id
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return JSONResponse(
        status_code=201,
        content=ok(
            {
                "created": len(skills),
                "skills": [
                    SkillOut.model_validate(s).model_dump(mode="json", by_alias=True)
                    for s in skills
                ],
            }
        ),
    )


@router.post("/skills/{skill_id}/review")
def review_skill(
    skill_id: str,
    body: SkillReview,
    ctx: TenantContext = Depends(get_tenant_context),
    db=Depends(get_db_session),
):
    skill = service.review_skill(
        db, ctx, skill_id, body.action, body.note, request_id=ctx.request_id
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(SkillOut.model_validate(skill).model_dump(mode="json", by_alias=True))


@router.get("/skills/{skill_id}/eval-runs")
def get_skill_eval_runs(
    skill_id: str,
    page_page: tuple[int, int] = Depends(pagination_params),
    ctx: TenantContext = Depends(get_tenant_context),
    db=Depends(get_db_session),
):
    """查询某 Skill 的评测门禁运行历史（第四期 4.2）。"""
    page, page_size = page_page
    items, total = service.list_eval_runs(db, ctx, skill_id, page, page_size)
    return paged(
        [EvalRunOut.model_validate(i).model_dump(mode="json") for i in items],
        page,
        page_size,
        total,
    )


@router.post("/skills/{skill_id}/rollout", status_code=201)
def set_skill_rollout(
    skill_id: str,
    body: RolloutCreate,
    ctx: TenantContext = Depends(get_tenant_context),
    db=Depends(get_db_session),
):
    rollout = rollout_service.set_rollout(
        db,
        ctx,
        skill_id,
        rollout_version=body.rollout_version,
        strategy=body.strategy,
        percentage=body.percentage,
        tenant_ids=body.tenant_ids,
        request_id=ctx.request_id,
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return JSONResponse(
        status_code=201,
        content=ok(RolloutOut.model_validate(rollout).model_dump(mode="json", by_alias=True)),
    )


@router.get("/skills/{skill_id}/rollout")
def get_skill_rollout(
    skill_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
    db=Depends(get_db_session),
):
    rollout = rollout_service.get_rollout(db, ctx, skill_id)
    if rollout is None:
        return ok(None)
    return ok(RolloutOut.model_validate(rollout).model_dump(mode="json", by_alias=True))


@router.post("/skills/{skill_id}/rollout/promote")
def promote_skill_rollout(
    skill_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
    db=Depends(get_db_session),
):
    rollout = rollout_service.promote_rollout(db, ctx, skill_id, request_id=ctx.request_id)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(RolloutOut.model_validate(rollout).model_dump(mode="json", by_alias=True))


@router.post("/skills/{skill_id}/rollout/halt")
def halt_skill_rollout(
    skill_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
    db=Depends(get_db_session),
):
    rollout = rollout_service.halt_rollout(db, ctx, skill_id, request_id=ctx.request_id)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(RolloutOut.model_validate(rollout).model_dump(mode="json", by_alias=True))
