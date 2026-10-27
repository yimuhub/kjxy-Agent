"""演进服务：反馈、知识候选审核、Skill 版本状态机。

knowledge service（app.modules.knowledge.service）尚未交付时，
review_candidate 的 approve 流程降级为仅标记 status=approved，
待 knowledge 模块落地后联调文档入库与解析触发。
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.eval.runner import EvalReport, run_eval_set
from app.modules.audit.service import log_audit
from app.modules.evolution.models import EvalRun, Feedback, KnowledgeCandidate, Skill
from app.modules.yards.authz import effective_permissions, get_active_membership, is_owner

log = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- 权限
_PERMISSION_ROLES: dict[str, set[str]] = {
    "review:approve": {"expert", "yard_admin", "yard_owner", "platform_admin"},
    "audit:read": {"yard_admin", "yard_owner", "platform_admin", "auditor"},
}


def has_permission(roles: list[str] | None, permission: str) -> bool:
    """最小权限映射（P0）。roles 为空/未命中 → 无权限。"""
    allowed = _PERMISSION_ROLES.get(permission, set())
    return bool(set(roles or []) & allowed)


def can_see_all_private_skills(ctx: YardContext) -> bool:
    """谁能看到他人的私有技能：负责人、管理员与平台管理员（专家需被单独授权）。

    ``skills:publish_any`` 的逐人授权由 yards 权限服务判定，这里只做静态角色兜底，
    避免 evolution 模块反向依赖 yards 的 DB 查询。
    """
    return bool(set(ctx.roles or []) & {"yard_owner", "yard_admin", "platform_admin"})


def _require_review_permission(ctx: YardContext) -> None:
    if not has_permission(ctx.roles, "review:approve"):
        raise AppError("FORBIDDEN", "仅专家或小院管理员可审核。", http_status=403)


# ---------------------------------------------------------------- DB 权限判定（v3.2）
def db_permissions(db: Session, ctx: YardContext) -> frozenset[str]:
    """当前用户在所属小院的有效权限（DB 角色覆盖 ∪ 逐人授权，JWT 角色仅兜底）。"""
    return effective_permissions(
        db, yard_id=ctx.yard_id, user_id=ctx.user_id, jwt_roles=list(ctx.roles or [])
    )


def _require_db_permission(db: Session, ctx: YardContext, permission: str) -> None:
    """按数据库实际权限判定；只改 JWT 角色而库里未授权时一律 403。"""
    if permission not in db_permissions(db, ctx):
        raise AppError(
            "FORBIDDEN",
            f"缺少 {permission} 权限，无法执行该操作。",
            http_status=403,
        )


def is_skill_manager(db: Session, ctx: YardContext) -> bool:
    """是否小院管理者（可代他人把私有技能提交到公共库）。

    口径与私有技能可见范围一致：平台管理员、小院负责人、小院管理员。
    专家虽可审核（skills:review），但不属于管理者——不能替他人决定私有技能转公共。
    """
    if "platform_admin" in set(ctx.roles or []):
        return True
    if is_owner(db, ctx.yard_id, ctx.user_id):
        return True
    membership = get_active_membership(db, ctx.yard_id, ctx.user_id)
    return membership is not None and membership.role == "yard_admin"


# ---------------------------------------------------------------- 反馈
#: 纠错内容最小长度：过短文本无法构成可入库知识，避免审核队列被噪声淹没
MIN_CORRECTION_CHARS = 10


def _candidate_title(feedback_text: str | None, task_id: str | None) -> str:
    """候选标题优先取反馈摘要；没有反馈文本时用任务号兜底，便于审核定位。"""
    if feedback_text and feedback_text.strip():
        first_line = feedback_text.strip().splitlines()[0].strip()
        return f"纠错：{first_line[:60]}"
    if task_id:
        return f"用户纠错·任务 {task_id[-8:]}"
    return "用户纠错"


def _dedupe_candidate(
    db: Session, yard_id: str, corrected_answer: str
) -> KnowledgeCandidate | None:
    """同一小院下内容相同的待审/已通过纠错只保留一条。"""
    return (
        db.query(KnowledgeCandidate)
        .filter(
            KnowledgeCandidate.yard_id == yard_id,
            KnowledgeCandidate.source_type == "user_feedback",
            KnowledgeCandidate.status.in_(("pending_review", "approved")),
            KnowledgeCandidate.content == corrected_answer,
        )
        .first()
    )


def submit_feedback(
    db: Session,
    ctx: YardContext,
    task_id: str | None = None,
    rating: str | None = None,
    feedback_text: str | None = None,
    corrected_answer: str | None = None,
    request_id: str = "",
) -> Feedback:
    if rating is not None and rating not in ("up", "down"):
        raise AppError("INVALID_REQUEST", "rating 只能为 up 或 down。", http_status=400)
    corrected = corrected_answer.strip() if corrected_answer else ""
    if corrected and len(corrected) < MIN_CORRECTION_CHARS:
        raise AppError(
            "FEEDBACK_TOO_SHORT",
            f"纠错内容至少需要 {MIN_CORRECTION_CHARS} 个字符，便于专家审核。",
            http_status=400,
        )
    feedback = Feedback(
        yard_id=ctx.yard_id,
        task_id=task_id,
        actor_id=ctx.user_id,
        rating=rating,
        feedback_text=feedback_text,
        corrected_answer=corrected or corrected_answer,
        status="open",
    )
    db.add(feedback)
    db.flush()

    candidate = None
    deduped = False
    if corrected:
        candidate = _dedupe_candidate(db, ctx.yard_id, corrected)
        if candidate is not None:
            deduped = True
        else:
            candidate = KnowledgeCandidate(
                yard_id=ctx.yard_id,
                source_feedback_id=feedback.id,
                title=_candidate_title(feedback_text, task_id),
                content=corrected,
                source_type="user_feedback",
                status="pending_review",
            )
            db.add(candidate)
            db.flush()

    db.flush()
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="feedback.submit",
        resource_type="feedback",
        resource_id=feedback.id,
        result="success",
        request_id=request_id or ctx.request_id,
        metadata={
            "rating": rating,
            "candidate_id": candidate.id if candidate else None,
            "candidate_deduped": deduped,
        },
    )
    return feedback


def list_feedback(
    db: Session, ctx: YardContext, page: int, page_size: int
) -> tuple[list[Feedback], int]:
    q = db.query(Feedback).filter(Feedback.yard_id == ctx.yard_id)
    total = q.count()
    items = (
        q.order_by(Feedback.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    )
    return items, total


# ---------------------------------------------------------------- 知识候选审核
def review_candidate(
    db: Session,
    ctx: YardContext,
    candidate_id: str,
    action: str,
    note: str | None,
    request_id: str = "",
) -> KnowledgeCandidate:
    _require_review_permission(ctx)
    if action not in ("approve", "reject"):
        raise AppError(
            "INVALID_REVIEW_ACTION", "审核动作只能为 approve 或 reject。", http_status=409
        )
    candidate = (
        db.query(KnowledgeCandidate)
        .filter(
            KnowledgeCandidate.id == candidate_id,
            KnowledgeCandidate.yard_id == ctx.yard_id,
        )
        .first()
    )
    if candidate is None:
        raise AppError("CANDIDATE_NOT_FOUND", "待审核知识不存在或无权访问。", http_status=404)
    if candidate.status != "pending_review":
        raise AppError("INVALID_REVIEW_ACTION", "该候选已审核，不可重复操作。", http_status=409)

    candidate.reviewed_by = ctx.user_id
    candidate.reviewed_at = _utcnow()
    candidate.review_note = note
    if action == "approve":
        candidate.status = "approved"
        ingest_ok, ingest_error = _ingest_approved_candidate(db, ctx, candidate)
        if not ingest_ok and candidate.status != "approved":
            # 入库失败路径的审计兜底可能回滚整个事务——回填审核结论，
            # 保证"审核已提交"这一事实不因入库失败而丢失
            candidate.status = "approved"
            candidate.reviewed_by = ctx.user_id
            candidate.reviewed_at = _utcnow()
            candidate.review_note = note
        # S1：入库结果显式透出。ingest_ok/ingest_error 是非映射的运行时属性，
        # 由 CandidateOut 的同名默认字段读取后随响应返回；失败原因同时写入
        # knowledge.ingest_failed 审计事件的 metadata（持久化留痕）。
        setattr(candidate, "ingest_ok", ingest_ok)  # noqa: B010
        setattr(candidate, "ingest_error", ingest_error)  # noqa: B010
    else:
        candidate.status = "rejected"
    db.flush()
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action=f"knowledge.candidate.{action}",
        resource_type="knowledge_candidate",
        resource_id=candidate.id,
        result="success",
        request_id=request_id or ctx.request_id,
    )
    return candidate


def _ingest_approved_candidate(
    db: Session, ctx: YardContext, candidate: KnowledgeCandidate
) -> tuple[bool, str | None]:
    """approve 后将纠错内容作为文档写入知识库并触发解析。

    返回 ``(入库是否成功, 失败原因)``（S1）。入库失败不向调用方抛异常——
    审核状态照常落库，但失败原因会返回给 review_candidate，由其写入审计
    metadata 并经响应层的 ingest_ok/ingest_error 透出，调用方不会看到纯成功。
    """
    try:
        from app.modules.knowledge.service import upload_document
    except ImportError as e:
        # 开发裁剪场景：knowledge 模块尚未交付，保留降级路径；
        # 但"没入库"同样不是入库成功，显式上报原因，禁止伪报纯成功。
        log.info(
            "knowledge service not available; candidate %s approved without ingestion: %s",
            candidate.id,
            e,
        )
        return False, f"knowledge 模块不可用（开发裁剪场景）：{e}"
    try:
        import io

        from fastapi import UploadFile

        text = (
            f"# {candidate.title}\n\n"
            f"来源：用户反馈纠错（feedback_id={candidate.source_feedback_id or '未知'}）\n\n"
            f"{candidate.content}\n"
        )
        upload = UploadFile(
            file=io.BytesIO(text.encode("utf-8")),
            filename="expert_correction.md",
        )
        out = upload_document(db, ctx, upload, title=candidate.title, request_id=ctx.request_id)
        # knowledge 模块写死 source="upload"；按任务书要求纠偏为 expert_correction
        try:
            from app.modules.knowledge.models import KnowledgeDocument

            doc = db.get(KnowledgeDocument, out.id)
            if doc is not None:
                doc.source = "expert_correction"
                db.flush()
        except Exception as e:
            log.warning("set expert_correction source failed: %s", e)
    except Exception as e:
        # 降级：仅标记 approved，不阻断审核流程；但失败必须可诊断且可感知
        # （规范 §3.3.5 失败可解释；S1 禁止调用方在入库失败时看到纯成功）
        log.exception("knowledge ingestion failed for candidate %s", candidate.id)
        error_text = f"{type(e).__name__}: {e}"
        try:
            from app.modules.audit.service import log_audit

            log_audit(
                db,
                yard_id=ctx.yard_id,
                actor_id=ctx.user_id,
                action="knowledge.ingest_failed",
                resource_type="knowledge_candidate",
                resource_id=candidate.id,
                result="failure",
                request_id=ctx.request_id,
                metadata={
                    "note": "候选已通过审核，但写入知识库失败，请检查存储/解析配置",
                    "ingest_error": error_text,
                },
            )
            db.commit()
        except Exception:
            db.rollback()
        return False, error_text
    return True, None


def batch_review(
    db: Session,
    ctx: YardContext,
    ids: list[str],
    action: str,
    review_note: str | None,
    request_id: str = "",
) -> dict[str, Any]:
    """批量审核知识候选：逐条复用单条 review 逻辑，单条失败不影响其余。

    权限与单条审核一致（review:approve），无权限整体 403。
    返回 ``{"approved": [...], "rejected": [...], "failed": [{id, error_code,
    error_message}]}``；failed 只表示审核动作本身失败（如已审核/不存在），
    单条入库失败（ingest_ok=false）仍按审核成功计入 approved。
    """
    _require_review_permission(ctx)
    if action not in ("approve", "reject"):
        raise AppError(
            "INVALID_REVIEW_ACTION", "审核动作只能为 approve 或 reject。", http_status=409
        )
    approved: list[str] = []
    rejected: list[str] = []
    failed: list[dict[str, str]] = []
    for candidate_id in ids:
        try:
            reviewed = review_candidate(
                db, ctx, candidate_id, action, review_note, request_id=request_id
            )
            (approved if action == "approve" else rejected).append(reviewed.id)
        except AppError as e:
            failed.append({"id": candidate_id, "error_code": e.code, "error_message": e.message})
        except Exception as e:  # pragma: no cover - 防御分支：非业务异常也不阻断其余条目
            log.exception("batch review item %s failed unexpectedly", candidate_id)
            failed.append(
                {
                    "id": candidate_id,
                    "error_code": "INTERNAL_ERROR",
                    "error_message": f"{type(e).__name__}: {e}",
                }
            )
    return {"approved": approved, "rejected": rejected, "failed": failed}


def list_candidates(
    db: Session,
    ctx: YardContext,
    page: int,
    page_size: int,
    status: str | None = None,
) -> tuple[list[KnowledgeCandidate], int]:
    q = db.query(KnowledgeCandidate).filter(KnowledgeCandidate.yard_id == ctx.yard_id)
    if status:
        q = q.filter(KnowledgeCandidate.status == status)
    total = q.count()
    items = (
        q.order_by(KnowledgeCandidate.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return items, total


# ---------------------------------------------------------------- Skill 状态机
# 合法转移：draft→testing→pending_review→published→deprecated；
# 另允许 testing→draft、pending_review→draft（驳回返工）、deprecated→published（rollback 恢复）。
_SKILL_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"testing"},
    "testing": {"pending_review", "draft"},
    "pending_review": {"published", "draft"},
    "published": {"deprecated"},
    "deprecated": {"published"},
}


def _transition(skill: Skill, to_status: str) -> None:
    allowed = _SKILL_TRANSITIONS.get(skill.status, set())
    if to_status not in allowed:
        raise AppError(
            "INVALID_SKILL_TRANSITION",
            f"Skill 状态 {skill.status} 不允许转移到 {to_status}。",
            http_status=409,
        )
    skill.status = to_status


def _get_skill(db: Session, ctx: YardContext, skill_id: str) -> Skill:
    skill = db.query(Skill).filter(Skill.id == skill_id, Skill.yard_id == ctx.yard_id).first()
    if skill is None:
        raise AppError("SKILL_NOT_FOUND", "Skill 不存在或无权访问。", http_status=404)
    return skill


def _check_version_unique(db: Session, ctx: YardContext, skill_code: str, version: str) -> None:
    exists = (
        db.query(Skill)
        .filter(
            Skill.yard_id == ctx.yard_id,
            Skill.skill_code == skill_code,
            Skill.version == version,
        )
        .first()
    )
    if exists is not None:
        raise AppError(
            "SKILL_VERSION_CONFLICT",
            f"Skill {skill_code} 版本 {version} 已存在。",
            http_status=409,
        )


def create_skill(
    db: Session,
    ctx: YardContext,
    skill_code: str,
    version: str,
    definition: dict,
    created_from: str | None = None,
    request_id: str = "",
    *,
    visibility: str = "yard",
    owner_user_id: str | None = None,
    origin: str | None = None,
) -> Skill:
    if visibility not in ("private", "yard", "platform"):
        raise AppError("INVALID_REQUEST", "技能可见性不合法。", http_status=400)
    _check_version_unique(db, ctx, skill_code, version)
    skill = Skill(
        yard_id=ctx.yard_id,
        skill_code=skill_code,
        version=version,
        definition_json=definition,
        status="draft",
        created_from=created_from,
        visibility=visibility,
        owner_user_id=owner_user_id,
        origin=origin,
    )
    db.add(skill)
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise AppError(
            "SKILL_VERSION_CONFLICT",
            f"Skill {skill_code} 版本 {version} 已存在。",
            http_status=409,
        ) from e
    db.flush()
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="skill.create",
        resource_type="skill",
        resource_id=skill.id,
        result="success",
        request_id=request_id or ctx.request_id,
        metadata={
            "skill_code": skill_code,
            "version": version,
            "visibility": visibility,
            "origin": origin,
        },
    )
    return skill


# ---------------------------------------------------------------- 评测门禁（第四期 4.2）
# 规范 V3.0 §11.2 / §11.4：发布前必须跑固定评测集回归测试并达到通过率。
#
# 门禁触发条件：Skill definition 含 `evaluation` 配置节。
# 未配置 evaluation 的 Skill（存量 Skill）直接放行，保证向后兼容。
# evaluation 字段：
#   test_set_id      必填：backend/eval/<test_set_id>.json（如 qa_eval_v1）
#   minimum_pass_rate 可选，缺省 0.8
#   agent_code       可选，默认 agriculture_qa（默认答题函数的用例答题 Agent）
#   skip_gate        可选，true 时跳过门禁（测试/演示用；审计日志记一笔）
#
# 测试时用 unittest.mock.patch("app.modules.evolution.service.run_eval_set")
# 把同步长耗时的 runner mock 掉（注意：必须 patch 服务模块内的这个引用，
# 因为本模块在顶层绑定了 run_eval_set）。
_EVAL_GATE_DEFAULT_MIN_PASS_RATE = 0.8
_EVAL_GATE_DEFAULT_AGENT_CODE = "agriculture_qa"


def _eval_gate_config(skill: Skill) -> dict | None:
    """取 Skill definition 中的 evaluation 配置；未声明 → 不触发门禁。"""
    definition = skill.definition_json or {}
    evaluation = definition.get("evaluation")
    return evaluation if isinstance(evaluation, dict) and evaluation else None


def _default_gate_answer_fn(
    db: Session, ctx: YardContext, agent_code: str
) -> Callable[[str, str], dict]:
    """默认答题函数：把评估用例问题交给指定的问答 Agent。

    yard 标签（用例里的 "A"/"B"）直接映射到当前审核小院——门禁用小院自己的
    知识库回答自己小院的评估集；跨小院隔离由评估集中的 cross_yard 用例覆盖。
    懒加载 agriculture 模块，避免 evolution 顶层依赖。
    """

    try:
        from app.modules.agriculture.qa_agent import QAAgentContext, get_global_registry
    except ImportError as e:
        raise AppError(
            "EVAL_GATE_UNAVAILABLE",
            "评测门禁不可用：问答 Agent 模块缺失，请显式传入 answer_fn。",
            http_status=503,
        ) from e

    agent = get_global_registry().get(agent_code)
    if agent is None:
        raise AppError(
            "EVAL_GATE_UNAVAILABLE",
            f"评测门禁用到的 Agent {agent_code!r} 未注册，无法执行评估集。",
            http_status=503,
        )

    import asyncio

    def _adapt(result: Any) -> dict:
        output = getattr(result, "output", None) or {}
        if isinstance(output, dict):
            answer = str(output.get("answer") or "")
            needs_review = bool(output.get("needs_expert_review"))
        else:
            answer = str(getattr(output, "answer", "") or "")
            needs_review = bool(getattr(output, "needs_expert_review", False))
        citations = getattr(result, "citations", None) or []
        return {
            "answer": answer,
            "citations": [dict(c) for c in citations],
            "needs_expert_review": needs_review,
            "http_status": 200 if getattr(result, "status", "") == "succeeded" else 500,
        }

    def answer_fn(question: str, yard_label: str) -> dict:  # noqa: ARG001
        context = QAAgentContext(
            yard_id=ctx.yard_id,
            user_id=ctx.user_id,
            roles=list(ctx.roles or []),
            request_id=ctx.request_id,
            db=db,
        )
        result = asyncio.run(agent.run({"question": question, "top_k": 5}, context))
        return _adapt(result)

    return answer_fn


def _run_eval_gate(
    db: Session,
    ctx: YardContext,
    skill: Skill,
    evaluation: dict,
    answer_fn: Callable[[str, str], dict] | None = None,
    request_id: str = "",
) -> EvalReport | None:
    """执行评测门禁，返回评估报告（skip_gate 时返回 None）。

    每次实际跑完评估集都向 eval_runs 插一条；未达通过率抛 EVAL_GATE_BLOCKED（409）。
    """
    request_id = request_id or ctx.request_id

    if evaluation.get("skip_gate"):
        log_audit(
            db,
            yard_id=ctx.yard_id,
            actor_id=ctx.user_id,
            action="skill.eval_gate_skipped",
            resource_type="skill",
            resource_id=skill.id,
            result="success",
            request_id=request_id,
            metadata={
                "skill_code": skill.skill_code,
                "version": skill.version,
                "reason": "evaluation.skip_gate=true（测试/演示跳过）",
            },
        )
        log.warning(
            "eval gate skipped for skill %s %s (skip_gate=true)",
            skill.skill_code,
            skill.version,
        )
        return None

    test_set_id = evaluation.get("test_set_id")
    if not test_set_id or not isinstance(test_set_id, str):
        raise AppError(
            "EVAL_GATE_BLOCKED",
            "Skill 声明了评测门禁但未配置 evaluation.test_set_id，无法执行评估，禁止发布。",
            http_status=409,
            details={"skill_code": skill.skill_code, "version": skill.version},
        )

    minimum = evaluation.get("minimum_pass_rate", _EVAL_GATE_DEFAULT_MIN_PASS_RATE)
    try:
        minimum = float(minimum)
    except (TypeError, ValueError) as e:
        raise AppError(
            "INVALID_REQUEST",
            "evaluation.minimum_pass_rate 非法，必须是 (0, 1] 之间的数字。",
            http_status=400,
        ) from e
    if not 0 < minimum <= 1:
        raise AppError(
            "INVALID_REQUEST",
            "evaluation.minimum_pass_rate 非法，必须是 (0, 1] 之间的数字。",
            http_status=400,
        )

    fn = answer_fn or _default_gate_answer_fn(
        db, ctx, str(evaluation.get("agent_code") or _EVAL_GATE_DEFAULT_AGENT_CODE)
    )

    try:
        report = run_eval_set(test_set_id, fn)
    except (FileNotFoundError, ValueError) as e:
        raise AppError(
            "EVAL_GATE_BLOCKED",
            f"评测集 {test_set_id!r} 加载失败，无法执行评测门禁，禁止发布。",
            http_status=409,
            details={
                "skill_code": skill.skill_code,
                "version": skill.version,
                "test_set_id": test_set_id,
                "reason": str(e),
            },
        ) from e

    # 结果留痕：每次门禁跑完插一条
    run = EvalRun(
        yard_id=ctx.yard_id,
        skill_code=skill.skill_code,
        skill_version=skill.version,
        test_set_id=report.test_set_id,
        pass_rate=report.pass_rate,
        passed=report.passed,
        failed=report.failed,
        total=report.total,
        created_by=ctx.user_id,
    )
    db.add(run)
    db.flush()

    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="skill.eval_gate_run",
        resource_type="skill",
        resource_id=skill.id,
        result="success",
        request_id=request_id,
        metadata={
            "skill_code": skill.skill_code,
            "version": skill.version,
            "test_set_id": report.test_set_id,
            "pass_rate": round(report.pass_rate, 4),
            "passed": report.passed,
            "failed": report.failed,
            "total": report.total,
        },
    )

    if report.pass_rate < minimum:
        failed_case_ids = [cr.case_id for cr in report.case_results if not cr.passed]
        raise AppError(
            "EVAL_GATE_BLOCKED",
            f"评测门禁未通过：通过率 {report.pass_rate:.1%} "
            f"低于最低要求 {minimum:.1%}，禁止发布。",
            http_status=409,
            details={
                "skill_code": skill.skill_code,
                "version": skill.version,
                "test_set_id": report.test_set_id,
                "pass_rate": round(report.pass_rate, 4),
                "minimum_pass_rate": minimum,
                "passed": report.passed,
                "failed": report.failed,
                "total": report.total,
                "failed_case_ids": failed_case_ids,
                "eval_run_id": run.id,
            },
        )
    return report


def review_skill(
    db: Session,
    ctx: YardContext,
    skill_id: str,
    action: str,
    note: str | None,
    request_id: str = "",
    answer_fn: Callable[[str, str], dict] | None = None,
) -> Skill:
    """Skill 审核动作：submit|approve|reject|rollback|submit_public。

    权限一律按数据库实际角色 + 逐人授权判定（JWT 角色仅兜底）：
    - approve/reject：需要 ``skills:review``；
    - submit_public：需要技能所有者本人，或小院管理者（负责人/管理员/平台管理员）；
    - submit/rollback：保持既有语义。
    """
    if action == "rollback":
        return rollback_skill(db, ctx, skill_id, request_id)
    if action in ("approve", "reject"):
        _require_db_permission(db, ctx, "skills:review")
    if action not in ("submit", "approve", "reject", "submit_public"):
        raise AppError("INVALID_REQUEST", f"未知 Skill 审核动作：{action}。", http_status=400)

    skill = _get_skill(db, ctx, skill_id)

    if action == "submit":
        # draft→testing，或 testing→pending_review（提交审核）
        target = "testing" if skill.status == "draft" else "pending_review"
        _transition(skill, target)
    elif action == "submit_public":
        _submit_public(db, ctx, skill)
    elif action == "approve":
        _check_published_version_unique(db, ctx, skill)
        gate_evaluation = _eval_gate_config(skill)
        if gate_evaluation is not None:
            _run_eval_gate(
                db, ctx, skill, gate_evaluation, answer_fn=answer_fn, request_id=request_id
            )
        _transition(skill, "published")
        skill.approved_by = ctx.user_id
        if skill.visibility == "private":
            # 私有技能提交公共库后审核通过：私有→小院级，并记录审核痕迹
            skill.visibility = "yard"
            skill.reviewed_by = ctx.user_id
            skill.reviewed_at = _utcnow()
            skill.review_note = note
    elif action == "reject":
        # pending_review→draft（驳回返工）；公共库提交被拒时保持 private
        _transition(skill, "draft")
        skill.review_note = note

    db.flush()
    metadata: dict[str, Any] = {
        "skill_code": skill.skill_code,
        "version": skill.version,
        "note": note,
    }
    if action in ("approve", "reject", "submit_public"):
        # 审核结论同时留痕可见性变化（approve 私有→小院时 metadata 里已是 yard）
        metadata["visibility"] = skill.visibility
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action=f"skill.{action}",
        resource_type="skill",
        resource_id=skill.id,
        result="success",
        request_id=request_id or ctx.request_id,
        metadata=metadata,
    )
    return skill


def _submit_public(db: Session, ctx: YardContext, skill: Skill) -> None:
    """私有技能提交到小院公共库：仅所有者本人或小院管理者，draft/testing → pending_review。

    只是"申请公开"，可见性仍是 private，待审核 approve 后才真正变 yard；
    reject 则回到 draft 并保持 private（不会因一次提交失败而泄漏给全小院）。
    """
    if skill.visibility != "private":
        raise AppError(
            "INVALID_SKILL_TRANSITION",
            "只有私有技能可以提交到小院公共库。",
            http_status=409,
        )
    if skill.owner_user_id != ctx.user_id and not is_skill_manager(db, ctx):
        raise AppError(
            "FORBIDDEN",
            "只有技能所有者或小院管理者可以提交公共库。",
            http_status=403,
        )
    if skill.status not in ("draft", "testing"):
        raise AppError(
            "INVALID_SKILL_TRANSITION",
            f"Skill 状态 {skill.status} 不允许提交公共库。",
            http_status=409,
        )
    skill.status = "pending_review"
    # 新一轮提交：清空上一轮审核痕迹，避免驳回意见带到待审状态
    skill.reviewed_by = None
    skill.reviewed_at = None
    skill.review_note = None


def promote_skill(db: Session, ctx: YardContext, skill_id: str, request_id: str = "") -> Skill:
    """负责人推广：任意私有技能 → 小院级并发布（需 skills:publish_any，可逐人下放）。

    已非 private 时幂等返回当前对象（不报错、不重复写审计）。
    """
    _require_db_permission(db, ctx, "skills:publish_any")
    skill = _get_skill(db, ctx, skill_id)
    if skill.visibility != "private":
        return skill

    if skill.status != "published":
        # 跳过重复发布：同小院同 code+version 已有 published 行时拒绝，避免版本歧义
        _check_published_version_unique(db, ctx, skill)
    original_owner = skill.owner_user_id
    skill.visibility = "yard"
    skill.status = "published"
    skill.reviewed_by = ctx.user_id
    skill.reviewed_at = _utcnow()
    db.flush()
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="skill.force_publish",
        resource_type="skill",
        resource_id=skill.id,
        result="success",
        request_id=request_id or ctx.request_id,
        metadata={
            "skill_code": skill.skill_code,
            "version": skill.version,
            "owner_user_id": original_owner,
            "from_visibility": "private",
            "visibility": skill.visibility,
        },
    )
    # 通知创建者（v3.3）：私有技能被负责人转公共必须可感知。
    # 走推送通道事件（订阅 skill_force_published 的通道逐条推送），永不影响主流程。
    from app.modules.push.models import EVENT_SKILL_FORCE_PUBLISHED
    from app.modules.push.service import dispatch as push_dispatch

    push_dispatch(
        db,
        yard_id=ctx.yard_id,
        event_type=EVENT_SKILL_FORCE_PUBLISHED,
        payload={
            "title": f"你的私有技能「{skill.skill_code}」已被转为小院公共",
            "skill_code": skill.skill_code,
            "version": skill.version,
            "operator_id": ctx.user_id,
            "owner_user_id": original_owner or "",
        },
        request_id=request_id or ctx.request_id,
    )
    return skill


def _check_published_version_unique(db: Session, ctx: YardContext, skill: Skill) -> None:
    """发布时版本号唯一校验：同小院同 code 同 version 不得已有另一 published 行。"""
    conflict = (
        db.query(Skill)
        .filter(
            Skill.yard_id == ctx.yard_id,
            Skill.skill_code == skill.skill_code,
            Skill.version == skill.version,
            Skill.status == "published",
            Skill.id != skill.id,
        )
        .first()
    )
    if conflict is not None or skill.status == "published":
        raise AppError(
            "SKILL_VERSION_CONFLICT",
            f"Skill {skill.skill_code} 版本 {skill.version} 已发布，版本冲突。",
            http_status=409,
        )


def rollback_skill(db: Session, ctx: YardContext, skill_id: str, request_id: str = "") -> Skill:
    """回滚：当前 published→deprecated；最近一个历史版本重新置 published。"""
    _require_review_permission(ctx)
    skill = _get_skill(db, ctx, skill_id)
    if skill.status != "published":
        raise AppError(
            "INVALID_SKILL_TRANSITION",
            "只有已发布的 Skill 版本可以回滚。",
            http_status=409,
        )
    previous = (
        db.query(Skill)
        .filter(
            Skill.yard_id == ctx.yard_id,
            Skill.skill_code == skill.skill_code,
            Skill.id != skill.id,
        )
        .order_by(Skill.created_at.desc())
        .first()
    )
    if previous is None:
        raise AppError("SKILL_NOT_FOUND", "没有可回滚的历史版本。", http_status=404)

    _transition(skill, "deprecated")
    if previous.status != "published":
        _transition(previous, "published")
    db.flush()
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="skill.rollback",
        resource_type="skill",
        resource_id=skill.id,
        result="success",
        request_id=request_id or ctx.request_id,
        metadata={
            "skill_code": skill.skill_code,
            "from_version": skill.version,
            "to_version": previous.version,
        },
    )
    return skill


def list_skills(
    db: Session,
    ctx: YardContext,
    page: int,
    page_size: int,
    status: str | None = None,
    skill_code: str | None = None,
    scope: str | None = None,
) -> tuple[list[Skill], int]:
    """技能列表。

    ``scope``（v3.2）：
    - ``None``：保持现行为——仅本小院行；
    - ``mine``：本小院内 owner_user_id 为自己的行（含自己的私有技能）；
    - ``yard``：本小院且非 private（小院公共库）；
    - ``platform``：平台级（yard_id IS NULL）且 visibility=platform。

    基础可见性规则不变：负责人/管理员/平台管理员可看全部私有，
    其余用户（含专家）只看得到自己的私有技能。
    """
    if scope not in (None, "mine", "yard", "platform"):
        raise AppError("INVALID_REQUEST", "scope 只能为 mine|yard|platform。", http_status=400)

    q = db.query(Skill)
    if scope == "platform":
        q = q.filter(Skill.yard_id.is_(None), Skill.visibility == "platform")
    elif scope == "mine":
        # 仍限定本小院：owner 是全局唯一用户，"我的"不应跨小院泄漏
        q = q.filter(Skill.yard_id == ctx.yard_id, Skill.owner_user_id == ctx.user_id)
    elif scope == "yard":
        q = q.filter(Skill.yard_id == ctx.yard_id, Skill.visibility != "private")
    else:
        q = q.filter(Skill.yard_id == ctx.yard_id)

    # 私有技能只对创建者与管理者（负责人/管理员/平台）可见，其余角色仅见非私有
    if scope != "mine" and not can_see_all_private_skills(ctx):
        q = q.filter(or_(Skill.visibility != "private", Skill.owner_user_id == ctx.user_id))
    if status:
        q = q.filter(Skill.status == status)
    if skill_code:
        q = q.filter(Skill.skill_code == skill_code)
    total = q.count()
    items = (
        q.order_by(Skill.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    )
    return items, total


def visible_skill_ids(db: Session, yard_id: str, user_id: str, roles: list[str]) -> set[str] | None:
    """返回该用户可见的技能 id 集合；``None`` 表示"可见全部"（管理者）。

    供绑定校验（agents/bindable）等调用方复用同一可见性口径：
    - 管理者（平台管理员 / 小院负责人 / 小院管理员）→ ``None``，不做 id 级收窄；
    - 其他人 → 本小院非私有（yard 级）+ 自己的私有 + 平台级（yard_id IS NULL
      且 visibility=platform）的技能 id 集合。

    注意：这里只回答"可见性"，不做状态（published/draft）过滤——调用方若只允许
    绑定已发布技能，应再与 ``status == "published"`` 的集合作交。
    """
    if "platform_admin" in set(roles or []):
        return None
    if is_owner(db, yard_id, user_id):
        return None
    membership = get_active_membership(db, yard_id, user_id)
    if membership is not None and membership.role in ("yard_owner", "yard_admin"):
        return None

    stmt = select(Skill.id).where(
        or_(
            and_(
                Skill.yard_id == yard_id,
                or_(
                    Skill.visibility != "private",
                    Skill.owner_user_id == user_id,
                ),
            ),
            and_(Skill.yard_id.is_(None), Skill.visibility == "platform"),
        )
    )
    return set(db.scalars(stmt).all())


def list_eval_runs(
    db: Session,
    ctx: YardContext,
    skill_id: str,
    page: int,
    page_size: int,
) -> tuple[list[EvalRun], int]:
    """查询某 Skill（同 code+version）的评测门禁运行历史。"""
    skill = _get_skill(db, ctx, skill_id)
    q = db.query(EvalRun).filter(
        EvalRun.yard_id == ctx.yard_id,
        EvalRun.skill_code == skill.skill_code,
        EvalRun.skill_version == skill.version,
    )
    total = q.count()
    items = (
        q.order_by(EvalRun.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    )
    return items, total
