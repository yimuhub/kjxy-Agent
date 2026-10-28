"""演进路由：反馈、知识候选审核、Skill 版本管理。"""

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.deps import YardContext, get_db_session, get_yard_context
from app.core.errors import AppError
from app.core.pagination import pagination_params
from app.core.responses import ok, paged
from app.modules.evolution import attestation as attestation_service
from app.modules.evolution import precheck as precheck_service
from app.modules.evolution import rollout as rollout_service
from app.modules.evolution import service
from app.modules.evolution import suggest as suggest_service
from app.modules.evolution.models import EvalRun
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
    SkillScope,
    SkillSuggest,
)

router = APIRouter(tags=["evolution"])


@router.post("/feedback", status_code=201)
def create_feedback(
    body: FeedbackCreate,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
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
        status_code=201,
        content=ok(FeedbackOut.model_validate(feedback).model_dump(mode="json"), request),
    )


@router.get("/feedback")
def get_feedback(
    request: Request,
    page_page: tuple[int, int] = Depends(pagination_params),
    ctx: YardContext = Depends(get_yard_context),
    db=Depends(get_db_session),
):
    page, page_size = page_page
    items, total = service.list_feedback(db, ctx, page, page_size)
    return paged(
        [FeedbackOut.model_validate(i).model_dump(mode="json") for i in items],
        page,
        page_size,
        total,
        request,
    )


@router.get("/knowledge/candidates")
def get_candidates(
    request: Request,
    status: str | None = Query(default=None, description="pending_review|approved|rejected"),
    page_page: tuple[int, int] = Depends(pagination_params),
    ctx: YardContext = Depends(get_yard_context),
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
    return paged(payloads, page, page_size, total, request)


@router.post("/knowledge/candidates/{candidate_id}/review")
async def review_candidate(
    candidate_id: str,
    body: CandidateReview,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
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
        # 深检是审核成功后的附加判定：不占小院配额、不进 llm_calls；
        # 任何异常都在 deep_precheck 内部降级为 error 说明，禁止反噬审核响应。
        from app.modules.llm.provider import get_provider

        payload["deep_precheck"] = await precheck_service.deep_precheck(candidate, get_provider())
    return ok(payload, request)


@router.post("/knowledge/candidates/batch-review")
def batch_review_candidates(
    body: CandidateBatchReview,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
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
    return ok(out.model_dump(mode="json"), request)


@router.get("/skills")
def get_skills(
    request: Request,
    status: str | None = Query(
        default=None, description="draft|testing|pending_review|published|deprecated"
    ),
    skill_code: str | None = Query(default=None),
    scope: SkillScope | None = Query(
        default=None,
        description="mine=我的技能（含私有）|yard=小院公共库|platform=平台级；缺省为本小院全部",
    ),
    page_page: tuple[int, int] = Depends(pagination_params),
    ctx: YardContext = Depends(get_yard_context),
    db=Depends(get_db_session),
):
    page, page_size = page_page
    items, total = service.list_skills(
        db, ctx, page, page_size, status=status, skill_code=skill_code, scope=scope
    )
    return paged(
        [SkillOut.model_validate(i).model_dump(mode="json", by_alias=True) for i in items],
        page,
        page_size,
        total,
        request,
    )


@router.post("/skills", status_code=201)
def create_skill(
    body: SkillCreate,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db=Depends(get_db_session),
):
    if body.visibility == "platform" and "platform_admin" not in set(ctx.roles or []):
        raise AppError("FORBIDDEN", "只有平台管理员可以创建平台级技能。", http_status=403)
    skill = service.create_skill(
        db,
        ctx,
        skill_code=body.skill_code,
        version=body.version,
        definition=body.definition,
        created_from=body.created_from,
        request_id=ctx.request_id,
        visibility=body.visibility,
        owner_user_id=ctx.user_id,
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return JSONResponse(
        status_code=201,
        content=ok(SkillOut.model_validate(skill).model_dump(mode="json", by_alias=True), request),
    )


@router.post("/skills/suggest", status_code=201)
async def suggest_skills(
    body: SkillSuggest,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db=Depends(get_db_session),
):
    """AI 技能总结（v1.3-B C1，yard_admin）：近 7 天证据 → 候选卡 → 审核队列。

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
            },
            request,
        ),
    )


@router.post("/skills/{skill_id}/review")
def review_skill(
    skill_id: str,
    body: SkillReview,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
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
    return ok(SkillOut.model_validate(skill).model_dump(mode="json", by_alias=True), request)


@router.post("/skills/{skill_id}/promote")
def promote_skill(
    skill_id: str,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db=Depends(get_db_session),
):
    """负责人推广：私有技能直接置为小院级并发布（需 skills:publish_any，可逐人下放）。

    已非 private 时幂等返回当前对象，方便前端重复点击。
    """
    skill = service.promote_skill(db, ctx, skill_id, request_id=ctx.request_id)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(SkillOut.model_validate(skill).model_dump(mode="json", by_alias=True), request)


@router.get("/skills/signing-key")
def get_skill_signing_key(
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
):
    """技能签名公钥（Ed25519）：用于离线校验已发布技能的完整性。"""
    return ok(attestation_service.signing_key_info(), request)


@router.get("/skills/{skill_id}/attestation")
def get_skill_attestation(
    skill_id: str,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db=Depends(get_db_session),
):
    """技能摘要/签名/验签结论与最近一份评测报告。"""
    return ok(service.attestation_view(db, ctx, skill_id), request)


@router.get("/skills/{skill_id}/eval-runs/{run_id}")
def get_skill_eval_run(
    skill_id: str,
    run_id: str,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db=Depends(get_db_session),
):
    """单次评测报告全文（含检查项与逐用例结论）。"""
    skill = service.get_skill(db, ctx, skill_id)
    run = (
        db.query(EvalRun)
        .filter(
            EvalRun.id == run_id,
            EvalRun.yard_id == ctx.yard_id,
            EvalRun.skill_code == skill.skill_code,
            EvalRun.skill_version == skill.version,
        )
        .first()
    )
    if run is None:
        raise AppError("EVAL_RUN_NOT_FOUND", "评测记录不存在或不属于该技能版本。", http_status=404)
    return ok(
        {
            "id": run.id,
            "skill_code": run.skill_code,
            "skill_version": run.skill_version,
            "kind": run.kind,
            "test_set_id": run.test_set_id,
            "pass_rate": run.pass_rate,
            "passed": run.passed,
            "failed": run.failed,
            "total": run.total,
            "report_sha256": run.report_sha256,
            "dataset_sha256": run.dataset_sha256,
            "created_at": run.created_at,
            "report": run.report_json,
        },
        request,
    )


@router.get("/skills/{skill_id}/eval-runs")
def get_skill_eval_runs(
    skill_id: str,
    request: Request,
    page_page: tuple[int, int] = Depends(pagination_params),
    ctx: YardContext = Depends(get_yard_context),
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
        request,
    )


@router.post("/skills/{skill_id}/rollout", status_code=201)
def set_skill_rollout(
    skill_id: str,
    body: RolloutCreate,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db=Depends(get_db_session),
):
    rollout = rollout_service.set_rollout(
        db,
        ctx,
        skill_id,
        rollout_version=body.rollout_version,
        strategy=body.strategy,
        percentage=body.percentage,
        yard_ids=body.yard_ids,
        request_id=ctx.request_id,
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return JSONResponse(
        status_code=201,
        content=ok(
            RolloutOut.model_validate(rollout).model_dump(mode="json", by_alias=True), request
        ),
    )


@router.get("/skills/{skill_id}/rollout")
def get_skill_rollout(
    skill_id: str,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db=Depends(get_db_session),
):
    rollout = rollout_service.get_rollout(db, ctx, skill_id)
    if rollout is None:
        return ok(None, request)
    return ok(RolloutOut.model_validate(rollout).model_dump(mode="json", by_alias=True), request)


@router.post("/skills/{skill_id}/rollout/promote")
def promote_skill_rollout(
    skill_id: str,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db=Depends(get_db_session),
):
    rollout = rollout_service.promote_rollout(db, ctx, skill_id, request_id=ctx.request_id)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(RolloutOut.model_validate(rollout).model_dump(mode="json", by_alias=True), request)


@router.post("/skills/{skill_id}/rollout/halt")
def halt_skill_rollout(
    skill_id: str,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db=Depends(get_db_session),
):
    rollout = rollout_service.halt_rollout(db, ctx, skill_id, request_id=ctx.request_id)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(RolloutOut.model_validate(rollout).model_dump(mode="json", by_alias=True), request)
