"""指挥官服务：计划 → 选兵 → 并行执行 → 汇总。

对外契约（主 Agent 按此集成）：

- ``await create_plan_run(db, ctx=..., session_id=..., user_content=..., context=...)``
  → 落 ``commander_runs`` + ``commander_subtasks``，``status="queued"``；
- ``run_out(run, subtasks)`` / ``subtask_out(st)`` → 稳定字典；
- ``get_run(db, yard_id, run_id)`` → ``(run, subtasks)``，不存在 404；
- ``await execute_run(run_id, on_event=None)`` → 自建 Session 执行并汇总；
- ``await run_in_background(run_id)`` → 执行后写一条 assistant 聊天消息；
- ``retry_run`` / ``cancel_run`` / ``plan_message_payload``。

执行语义（报告 §5.5 / §5.6）：
- 并发由 ``ConcurrencyGate`` 控制，容量 = 设置 ``max_parallel``；
- 子任务按 ``depends_on`` 动态调度，依赖失败 → 下游 ``skipped``；
- 单子任务超时 + 运行总超时；上游 429/5xx/超时重试 1 次并把并发降到
  ``fallback_parallel``（``run.degraded=true``）；
- 失败后自主补救：换方法/换助手/新建助手/指挥官带工具亲自执行，轮数由
  ``remediate_rounds`` 限制，全程不向用户提问；轮尽才报告能力缺口；
- 每个子任务独立 Session 落库，最后用基础模型汇总。

实现说明：``create_plan_run`` 必须调用异步 LLM，因此它是 ``async``（与
``execute_run`` 一致，主 Agent 需 ``await``）。``commander 代执行`` 使用
``llm_purpose="commander_merge"``——规划用途 ``commander_plan`` 只服务计划/修复，
避免把「代写答案」混进计划链路。
"""

from __future__ import annotations

import asyncio
import re
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TypedDict

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.agents import declarative
from app.modules.agents.models import (
    CREATED_FROM_COMMANDER,
    STATUS_DELETED,
    STATUS_DRAFT,
    STATUS_PUBLISHED,
    YardAgent,
)
from app.modules.audit.service import log_audit
from app.modules.chat import models as chat_models
from app.modules.commander import deliverables, feasibility, matcher, planner
from app.modules.commander import settings as commander_settings
from app.modules.commander.models import (
    RUN_CANCELLED,
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_PARTIAL,
    RUN_QUEUED,
    RUN_RUNNING,
    SOURCE_COMMANDER,
    SOURCE_EXISTING,
    SOURCE_GENERATED,
    SUB_ERROR,
    SUB_OK,
    SUB_PENDING,
    SUB_RUNNING,
    SUB_SKIPPED,
    CommanderRun,
    CommanderSubtask,
)
from app.modules.llm.schemas import ChatMessage
from app.modules.llm.service import complete_with_logging
from app.modules.quota import engine as quota_engine

log = get_logger(__name__)

#: 执行器自建 Session 的工厂（测试可注入内存库会话工厂）
_session_factory: Callable[[], Session] | None = None

#: 瞬时错误重试前的退避（秒）；测试可 monkeypatch 为 0
RETRY_BACKOFF_SECONDS = 0.2
#: 调度轮询上限（秒）：让「用户取消」在长任务执行中也能被及时发现
SCHEDULER_POLL_SECONDS = 1.0
#: 运行级错误码
ERR_RUN_NOT_FOUND = "COMMANDER_RUN_NOT_FOUND"
ERR_RUN_BUSY = "COMMANDER_RUN_BUSY"
ERR_RUN_CANCELLED = "COMMANDER_RUN_CANCELLED"
ERR_RUN_STATE = "COMMANDER_RUN_STATE"
ERR_DISABLED = "COMMANDER_DISABLED"
#: 信息不足、等待用户澄清（不是失败，统计与重试都应忽略）
ERR_NEEDS_INPUT = "COMMANDER_NEEDS_INPUT"
#: 自主补救轮尽：现有助手与工具能力不足（不是用户缺信息）
ERR_CAPABILITY_GAP = "COMMANDER_CAPABILITY_GAP"

#: 子任务「空手而归」判据：短且含拒答话术，不能当作下游素材
_REFUSAL_MARKERS = (
    "无法确认",
    "检索资料为空",
    "未检索到",
    "没有检索到",
    "暂无相关资料",
    "缺乏相关",
    "无法提供",
    "资料不足",
    "没有找到相关",
)
_REFUSAL_MAX_CHARS = 600
#: 指挥官子任务交付契约：一句话目标必须落地，资料不足也要给出可用草稿
_DELIVERY_CONTRACT = (
    "【指挥官交付要求】这是自动化流水线的一步，结果会直接交给下一步或用户。"
    "即使检索为空或资料不足，也要基于通用行业常识给出可用的草稿、结构与要点，"
    "把不确定的具体数字、案例标注为「待补充」，不要只回复「无法确认」「资料不足」而空手返回。"
)


def _insufficient_excerpt(text: str) -> bool:
    """短且含拒答话术的输出视为空手而归，不能当作下游素材。"""
    body = (text or "").strip()
    if not body:
        return True
    # 文档生成回执（“已由指挥官生成…”）不是素材，不能喂给下游继续生成
    if body.startswith("已由「指挥官」生成") or body.startswith("已生成"):
        return True
    if len(body) >= _REFUSAL_MAX_CHARS:
        return False
    return any(marker in body for marker in _REFUSAL_MARKERS)


def _with_delivery_contract(question: str) -> str:
    return f"{question.strip()}\n\n{_DELIVERY_CONTRACT}" if question.strip() else _DELIVERY_CONTRACT


#: 要求交付文件但结果里没有对应格式的产物
ERR_DELIVERABLE_MISSING = "COMMANDER_DELIVERABLE_MISSING"

#: 本进程内串行化写提交（SQLite 单写者；桌面版并发落库必须排队）
_COMMIT_LOCK = threading.Lock()
#: ``database is locked`` 的退避（秒）：最多重试 3 次
COMMIT_RETRY_DELAYS = (0.05, 0.1, 0.2)

_TRANSIENT_CATEGORIES = frozenset({"rate_limit", "provider_5xx", "timeout", "connection"})
_ARTIFACT_KEYS = ("artifacts", "attachments", "documents", "files", "downloads")
_ARTIFACT_URL_RE = re.compile(
    r"https?://[^\s)\]\}\"'<>]+?\.(?:pptx|docx|xlsx|pdf|png|jpe?g|gif|webp|zip|md|csv)",
    re.I,
)


def _get_session_factory() -> Callable[[], Session]:
    if _session_factory is not None:
        return _session_factory
    from app.db.session import SessionLocal

    return SessionLocal


def _safe_commit(db: Session) -> None:
    """进程内串行化提交；SQLite ``database is locked`` 按 0.05/0.1/0.2 秒重试。

    其它 ``OperationalError`` 原样抛出（连接串错误、表不存在等重试也无意义）。
    重试 3 次仍锁库时抛出最后一次异常，由上层按失败矩阵处理。
    """
    for attempt in range(len(COMMIT_RETRY_DELAYS) + 1):
        try:
            with _COMMIT_LOCK:
                db.commit()
            return
        except OperationalError as exc:
            db.rollback()
            if "database is locked" not in str(exc).lower():
                raise
            if attempt >= len(COMMIT_RETRY_DELAYS):
                raise
        time.sleep(COMMIT_RETRY_DELAYS[attempt])


def _precheck_quota(db: Session, ctx: YardContext) -> None:
    """运行前配额预检：不足直接 429，不吞异常（前端已有额度入口）。"""
    quota_engine.preflight(db, ctx.yard_id, requester_id=ctx.user_id)


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _emit(on_event: Callable[[str, dict], None] | None, kind: str, payload: dict) -> None:
    if on_event is None:
        return
    try:
        on_event(kind, payload)
    except Exception as exc:  # noqa: BLE001 - 事件回调失败不得打断执行
        log.warning("commander event callback failed: %s", exc)


# ---------------------------------------------------------------------------
# 输出契约
# ---------------------------------------------------------------------------


def subtask_out(st: CommanderSubtask) -> dict[str, Any]:
    return {
        "id": st.id,
        "seq": int(st.seq),
        "title": st.title,
        "instruction": st.instruction,
        "expected_output": st.expected_output,
        "agent_id": st.agent_id,
        "agent_code": st.agent_code,
        "agent_name": st.agent_name,
        "agent_source": st.agent_source,
        "status": st.status,
        "depends_on": [int(x) for x in (st.depends_on_json or [])],
        "attempts": int(st.attempts or 0),
        "remediation": list(st.remediation_json or []),
        "tokens": int(st.input_tokens or 0) + int(st.output_tokens or 0),
        "output_excerpt": st.output_excerpt,
        "artifact_json": st.artifact_json,
        "error_code": st.error_code,
        "started_at": _iso(st.started_at),
        "finished_at": _iso(st.finished_at),
    }


def run_out(run: CommanderRun, subtasks: list[CommanderSubtask]) -> dict[str, Any]:
    return {
        "id": run.id,
        "session_id": run.session_id,
        "goal": run.goal,
        "status": run.status,
        "max_parallel": int(run.max_parallel or 1),
        "effective_parallel": int(run.effective_parallel or run.max_parallel or 1),
        "degraded": bool(run.degraded),
        "approval_mode": str(getattr(run, "approval_mode", None) or "ask"),
        "tokens_total": int(run.tokens_total or 0),
        "latency_ms": int(run.latency_ms or 0),
        "created_at": _iso(run.created_at),
        "plan_fallback": bool((run.plan_json or {}).get("fallback")),
        # 降级原因：clarify_disabled / parse_failed / llm_error；空串表示没有降级
        "plan_fallback_reason": str((run.plan_json or {}).get("fallback_reason") or ""),
        "clarify": list((run.plan_json or {}).get("clarify") or []),
        "subtasks": [subtask_out(st) for st in subtasks],
    }


def plan_message_payload(
    run: CommanderRun, subtasks: list[CommanderSubtask] | None = None
) -> dict[str, Any]:
    """对话层「计划卡」的载荷（SSE ``commander-plan`` 直接发送）。

    ``subtasks`` 省略时按运行所属 Session 现查，方便对话层只拿 run 直接调用。
    """
    if subtasks is None:
        state = getattr(run, "_sa_instance_state", None)
        session = state.session if state is not None else None
        if session is not None:
            subtasks = list(
                session.scalars(
                    select(CommanderSubtask)
                    .where(CommanderSubtask.run_id == run.id)
                    .order_by(CommanderSubtask.seq.asc())
                )
            )
        else:
            subtasks = []
    return {
        "type": "commander_plan",
        "run_id": run.id,
        "goal": run.goal,
        "status": run.status,
        "degraded": bool(run.degraded),
        "max_parallel": int(run.max_parallel or 1),
        "effective_parallel": int(run.effective_parallel or run.max_parallel or 1),
        "plan_fallback": bool((run.plan_json or {}).get("fallback")),
        "plan_fallback_reason": str((run.plan_json or {}).get("fallback_reason") or ""),
        "clarify": list((run.plan_json or {}).get("clarify") or []),
        # 技能优先：把这次命中的技能随计划卡下发，用户能看到「用了哪些技能」
        "skills": [
            {"code": str(item.get("code") or ""), "name": str(item.get("name") or "")}
            for item in (run.plan_json or {}).get("skills") or []
            if isinstance(item, dict) and item.get("code")
        ],
        "token_usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": int(run.tokens_total or 0),
            "model": "commander",
        },
        "subtasks": [
            {
                "id": st.id,
                "seq": int(st.seq),
                "title": st.title,
                # 计划卡可编辑：带上指令，前端打开编辑即可改而不是先拉一次详情
                "instruction": st.instruction,
                "expected_output": st.expected_output,
                "agent_id": st.agent_id,
                "agent_code": st.agent_code,
                "agent_name": st.agent_name,
                "agent_source": st.agent_source,
                "status": st.status,
                "depends_on": [int(x) for x in (st.depends_on_json or [])],
            }
            for st in subtasks
        ],
    }


# ---------------------------------------------------------------------------
# 读取 / 权限
# ---------------------------------------------------------------------------


def get_run(db: Session, yard_id: str, run_id: str) -> tuple[CommanderRun, list[CommanderSubtask]]:
    run = db.get(CommanderRun, run_id)
    if run is None or run.yard_id != yard_id:
        raise AppError(
            ERR_RUN_NOT_FOUND,
            "指挥官运行不存在或无权访问。",
            http_status=404,
        )
    subtasks = list(
        db.scalars(
            select(CommanderSubtask)
            .where(
                CommanderSubtask.run_id == run.id,
                CommanderSubtask.yard_id == yard_id,
            )
            .order_by(CommanderSubtask.seq.asc())
        )
    )
    return run, subtasks


def can_access_run(db: Session, run: CommanderRun, *, user_id: str, roles: list[str]) -> bool:
    if run.user_id == user_id or "platform_admin" in roles:
        return True
    from app.modules.yards.authz import is_owner

    return is_owner(db, run.yard_id, user_id)


def require_run_access(db: Session, run: CommanderRun, *, user_id: str, roles: list[str]) -> None:
    if not can_access_run(db, run, user_id=user_id, roles=roles):
        # 不泄露他人运行是否存在
        raise AppError(ERR_RUN_NOT_FOUND, "指挥官运行不存在或无权访问。", http_status=404)


# ---------------------------------------------------------------------------
# 计划创建
# ---------------------------------------------------------------------------


def _unrealizable_plan(text: str, check: feasibility.Assessment) -> dict[str, Any]:
    """现实不可执行时的唯一合法降级：如实说明 + 给出可照着做的替代路径。

    这类目标无论怎么补能力都做不到（需要身体动作/到场/实物交付），所以不拆
    执行任务，只交一份「为什么做不到 + 你可以怎么做」的说明，由指挥官直接答。
    """
    verb = check.verb or "该动作"
    guidance = check.guidance or (
        f"「{verb}」需要人在现实里完成，我无法代替你的身体动作。"
        "我可以把该做的事整理成一份可执行的清单，你照着做即可。"
    )
    return {
        "goal": (text or "完成用户请求")[:500],
        "success_criteria": [],
        "assumptions": [],
        "merge_notes": "",
        "fallback": True,
        "fallback_reason": feasibility.REASON_UNREALIZABLE,
        "feasibility": check.to_json(),
        "subtasks": [
            {
                "seq": 1,
                "title": "说明原因与替代方案",
                "instruction": (
                    f"用户目标：{text}\n"
                    f"该目标需要现实中的身体动作（{verb}），系统无法执行。\n"
                    f"已知的替代路径参考：{guidance}\n"
                    "请直接回答：1) 为什么做不到（一句话）；2) 你能替他做到哪一步"
                    "（查资料/写清单/出方案）；3) 他照着做时需要准备什么、分几步。"
                    "不要假装已经完成，也不要只回一句「无法完成」。"
                ),
                "expected_output": "原因说明 + 可直接照做的替代清单",
                "agent_hint": "指挥官",
                "agent_code": "",
                "depends_on": [],
                "executor": "commander",
            }
        ],
        "clarify": [],
    }


def _delivery_format(run: CommanderRun, subtask: Any) -> str | None:
    """这个子任务是否真的产文件。

    计划里常有多个任务提到 ``.pptx``（设计版式、质量终检…），但真正渲染文件的只有
    ``plan.deliverable_seq`` 指定的那一个；其余按内容任务执行，避免同一份文件被
    渲染多遍、把预算烧在重复出片上。
    """
    fmt = deliverables.subtask_format(subtask)
    if not fmt:
        return None
    plan = getattr(run, "plan_json", None) or {}
    target = plan.get("deliverable_seq")
    if target is None:
        return fmt
    try:
        return fmt if int(getattr(subtask, "seq", 0)) == int(target) else None
    except (TypeError, ValueError):
        return fmt


def _resolve_plan_skills(
    db: Session,
    ctx: YardContext,
    *,
    text: str,
    fmt: str | None,
    capabilities: list[str],
) -> list[Any]:
    """规划前检索技能库：拿不到就返回空，绝不阻塞计划生成。"""
    from app.modules.market import skill_router

    try:
        return skill_router.resolve(
            db,
            ctx.yard_id,
            text=text,
            fmt=fmt,
            capabilities=capabilities,
            limit=4,
        )
    except Exception as exc:  # noqa: BLE001 —— 技能检索是增强，坏了不能拖垮指挥官
        log.warning("commander skill resolve failed: %s", exc)
        return []


async def create_plan_run(
    db: Session,
    *,
    ctx: YardContext,
    session_id: str,
    user_content: str,
    context: str = "",
    allow_clarify: bool = True,
) -> CommanderRun:
    """读设置 → 规划 → clamp → 选兵 → 落库（queued）。"""
    text = (user_content or "").strip()
    if not text:
        raise AppError("INVALID_REQUEST", "目标不能为空。", http_status=400)
    settings = commander_settings.settings_for(db, ctx.yard_id)
    if not settings.get("enabled"):
        raise AppError(
            ERR_DISABLED,
            "本小院已关闭指挥官模式，可在系统设置中开启。",
            http_status=403,
        )
    _precheck_quota(db, ctx)

    # 会话边界：存在时必须属于当前小院与当前用户（不存在则允许，便于任务入口复用）
    session = db.get(chat_models.ChatSession, session_id) if session_id else None
    if session is not None and (
        session.yard_id != ctx.yard_id or session.created_by != ctx.user_id
    ):
        raise AppError(ERR_RUN_NOT_FOUND, "会话不存在或无权访问。", http_status=404)
    # 完全控制继承：run 快照创建时会话的授权模式，执行器不随时间漂移
    approval_mode = str(getattr(session, "approval_mode", None) or "ask")

    catalog_agents = planner.visible_published_agents(db, yard_id=ctx.yard_id, user_id=ctx.user_id)
    max_subtasks = int(settings["max_subtasks"])
    required_fmt = deliverables.detect(text)
    # 先判能力边界：只有现实不可执行才允许降级；能做的必须做出来
    check = feasibility.assess(text, fmt=required_fmt)
    # 技能优先：动手之前先检索技能库，命中的技能既进提示词，也写进计划
    resolved_skills = _resolve_plan_skills(
        db, ctx, text=text, fmt=required_fmt, capabilities=check.capabilities
    )
    usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
    repaired = False
    plan_error: str | None = None
    plan_fallback = False

    if check.blocked:
        plan = _unrealizable_plan(text, check)
        plan["plan_repaired"] = False
        plan_fallback = True
        log.info("commander target unrealizable: verb=%s goal=%s", check.verb, text[:80])
    else:
        raw_plan, usage, repaired, plan_error = await planner.request_plan(
            db,
            ctx=ctx,
            user_content=text,
            context=context,
            catalog=planner.catalog_lines(catalog_agents),
            max_subtasks=max_subtasks,
            settings=settings,
            deliverable=required_fmt,
            capability_hint=feasibility.plan_hint(check),
            skills_hint="、".join(f"{skill.name}（{skill.code}）" for skill in resolved_skills),
        )
        plan = planner.normalize_plan(raw_plan, max_subtasks=max_subtasks, fallback_goal=text)

    if not check.blocked:
        if plan is not None:
            clarify: list[dict[str, Any]] = list(plan.get("clarify") or [])
        else:
            clarify = []
        if plan is not None and clarify and not plan.get("subtasks"):
            if (
                allow_clarify
                and settings.get("clarify_enabled")
                and approval_mode != chat_models.APPROVAL_FULL
            ):
                # 信息不足：落一条「待澄清」运行，前端弹表单卡片逐项收集；
                # 这不是失败——用户答完即可继续，无需重开对话。
                plan["plan_repaired"] = repaired
                plan["feasibility"] = check.to_json()
                run = CommanderRun(
                    yard_id=ctx.yard_id,
                    session_id=session_id,
                    user_id=ctx.user_id,
                    turn_id=uuid.uuid4().hex[:12],
                    goal=str(plan.get("goal") or text)[:500],
                    plan_json=plan,
                    status=RUN_CANCELLED,
                    max_parallel=int(settings["max_parallel"]),
                    effective_parallel=int(settings["max_parallel"]),
                    degraded=False,
                    tokens_total=usage["input_tokens"] + usage["output_tokens"],
                    latency_ms=0,
                    error_code=ERR_NEEDS_INPUT,
                    roles_json=list(ctx.roles),
                    approval_mode=approval_mode,
                )
                db.add(run)
                db.flush()
                log_audit(
                    db,
                    yard_id=ctx.yard_id,
                    actor_id=ctx.user_id,
                    action="commander_plan_created",
                    resource_type="commander_run",
                    resource_id=run.id,
                    result="success",
                    request_id=ctx.request_id,
                    metadata={
                        "session_id": session_id,
                        "subtasks": 0,
                        "fallback": False,
                        "clarify": len(clarify),
                        "repaired": repaired,
                        "plan_tokens": run.tokens_total,
                    },
                )
                _safe_commit(db)
                return run
            # 澄清被跳过（开关关闭或完全控制）：不降级，按能力目录直接排可执行计划
            plan = planner.capability_plan(
                text,
                goal=plan.get("goal"),
                fmt=required_fmt,
                capabilities=check.capabilities,
                reason="clarify_skipped",
            )
        elif plan is None:
            # 规划模型不可用/输出不可用：只要目标可达，就按能力目录排出计划，
            # 不再退化成「单任务直答」——PPT/Excel 这类必须继续产出文件。
            plan = planner.capability_plan(
                text,
                fmt=required_fmt,
                capabilities=check.capabilities,
                reason=plan_error or "parse_failed",
            )
            log.warning(
                "commander plan unusable, capability plan applied: deliverable=%s reason=%s raw=%s",
                required_fmt,
                plan_error or "parse_failed",
                str(raw_plan)[:400],
            )
        plan["feasibility"] = check.to_json()
        plan["plan_repaired"] = repaired

    # 用户要文件但计划没覆盖时补齐交付子任务：宁可由指挥官确定性出文件，
    # 也不能让「模型只回一段大纲」当成完成。
    if required_fmt:
        added = deliverables.ensure_plan_deliverable(
            plan, text, required_fmt, max_subtasks=max_subtasks
        )
        # 单文件交付：前置任务收敛成一个，别让预算在交付前被前置步骤吃光
        compacted = deliverables.compact_for_delivery(plan, required_fmt)
        # 只让最后那个交付任务真的产文件；同格式的前置任务按内容任务执行
        delivery_seqs = [
            int(item.get("seq") or 0)
            for item in plan.get("subtasks") or []
            if isinstance(item, dict) and deliverables.subtask_format(item) == required_fmt
        ]
        if delivery_seqs:
            plan["deliverable_seq"] = max(delivery_seqs)
        log.info(
            "commander plan deliverable=%s covered=%s added=%s compacted=%s subtasks=%d",
            required_fmt,
            not added,
            added,
            compacted,
            len(plan.get("subtasks") or []),
        )
    # 技能命中结果随计划落库：界面与后续交付都能看到「这次用了哪些技能」
    if resolved_skills:
        plan["skills"] = [skill.to_json() for skill in resolved_skills]
        log.info(
            "commander skills resolved=%s goal=%s",
            ",".join(skill.code for skill in resolved_skills),
            str(plan.get("goal") or "")[:80],
        )
    log.info(
        "commander plan subtasks=%d fallback=%s repaired=%s deliverable=%s goal=%s",
        len(plan.get("subtasks") or []),
        plan_fallback,
        repaired,
        required_fmt or "-",
        str(plan.get("goal") or "")[:120],
    )

    picks = await matcher.assign_agents(
        db,
        ctx=ctx,
        subtasks=plan["subtasks"],
        settings=settings,
        plan_fallback=plan_fallback,
        plan_skills=[skill.code for skill in resolved_skills],
    )

    run = CommanderRun(
        yard_id=ctx.yard_id,
        session_id=session_id,
        user_id=ctx.user_id,
        turn_id=uuid.uuid4().hex[:12],
        goal=str(plan.get("goal") or text)[:500],
        plan_json=plan,
        status=RUN_QUEUED,
        max_parallel=int(settings["max_parallel"]),
        effective_parallel=int(settings["max_parallel"]),
        degraded=False,
        tokens_total=usage["input_tokens"] + usage["output_tokens"],
        latency_ms=0,
        roles_json=list(ctx.roles),
        approval_mode=approval_mode,
    )
    db.add(run)
    db.flush()

    generated: list[dict[str, Any]] = []
    for subtask, pick in zip(plan["subtasks"], picks, strict=True):
        row = CommanderSubtask(
            run_id=run.id,
            yard_id=ctx.yard_id,
            seq=int(subtask["seq"]),
            title=str(subtask.get("title") or "")[:200],
            instruction=str(subtask.get("instruction") or ""),
            expected_output=str(subtask.get("expected_output") or ""),
            agent_id=pick["agent_id"],
            agent_code=pick["agent_code"],
            agent_name=pick["agent_name"],
            agent_source=pick["agent_source"],
            status=SUB_PENDING,
            depends_on_json=list(subtask.get("depends_on") or []),
            attempts=0,
            input_tokens=0,
            output_tokens=0,
        )
        db.add(row)
        if pick["agent_source"] == SOURCE_GENERATED:
            generated.append(
                {
                    "seq": int(subtask["seq"]),
                    "agent_id": pick["agent_id"],
                    "agent_code": pick["agent_code"],
                    "agent_name": pick["agent_name"],
                }
            )
    plan["generated_agents"] = generated
    run.plan_json = plan
    db.flush()

    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="commander_plan_created",
        resource_type="commander_run",
        resource_id=run.id,
        result="success",
        request_id=ctx.request_id,
        metadata={
            "session_id": session_id,
            "subtasks": len(plan["subtasks"]),
            "fallback": plan_fallback,
            "repaired": repaired,
            "plan_tokens": run.tokens_total,
            "generated_agents": len(generated),
            "auto_created": any(p["agent_source"] == SOURCE_GENERATED for p in picks),
        },
    )
    _safe_commit(db)
    return run


# ---------------------------------------------------------------------------
# 计划编辑
# ---------------------------------------------------------------------------


def _bind_plan_agent(db: Session, ctx: YardContext, agent_id: str | None) -> dict[str, Any]:
    """校验执行者绑定：空 = 指挥官代执行；否则必须可见可用。"""
    if not agent_id:
        return {
            "agent_id": None,
            "agent_code": None,
            "agent_name": "指挥官",
            "agent_source": SOURCE_COMMANDER,
        }
    agent = db.get(YardAgent, str(agent_id))
    if agent is None or agent.yard_id != ctx.yard_id or agent.status == STATUS_DELETED:
        raise AppError(
            "INVALID_REQUEST",
            "所选助手不存在、不属于当前小院或已被删除。",
            http_status=400,
        )
    visible = agent.visibility == "public" or agent.created_by == ctx.user_id
    if not visible:
        raise AppError(
            "INVALID_REQUEST",
            f"无权使用助手「{agent.name}」（仅创建者可用）。",
            http_status=400,
        )
    generated_draft = agent.created_from == CREATED_FROM_COMMANDER and agent.status == STATUS_DRAFT
    if agent.status != STATUS_PUBLISHED and not generated_draft:
        raise AppError(
            "INVALID_REQUEST",
            f"助手「{agent.name}」当前状态为 {agent.status}，"
            "只有已发布助手或指挥官生成的草稿可用。",
            http_status=400,
        )
    return {
        "agent_id": agent.id,
        "agent_code": agent.agent_code,
        "agent_name": agent.name,
        "agent_source": SOURCE_GENERATED if generated_draft else SOURCE_EXISTING,
    }


def _edit_depends_on(raw: Any, *, position: int) -> list[int]:
    """按新数组位置（1-based）解释依赖，只保留指向更早位置的合法项。"""
    if not isinstance(raw, list):
        return []
    deps: list[int] = []
    for item in raw:
        if isinstance(item, bool) or not isinstance(item, int):
            continue
        if 1 <= item < position and item not in deps:
            deps.append(item)
    return deps


def update_plan(
    db: Session, ctx: YardContext, run_id: str, subtasks_payload: list[dict[str, Any]]
) -> CommanderRun:
    """用计划卡提交的内容整表替换子任务（仅 queued 运行可编辑）。"""
    run, existing = get_run(db, ctx.yard_id, run_id)
    require_run_access(db, run, user_id=ctx.user_id, roles=ctx.roles)
    if run.status != RUN_QUEUED:
        raise AppError(
            ERR_RUN_STATE,
            f"当前状态为 {run.status}，只有排队中的运行可以编辑计划。",
            http_status=409,
        )

    settings = commander_settings.settings_for(db, run.yard_id)
    limit = int(settings["max_subtasks"])
    if not 1 <= len(subtasks_payload) <= limit:
        raise AppError(
            "INVALID_REQUEST",
            f"子任务数量需在 1-{limit} 之间。",
            http_status=400,
        )

    plan_subtasks: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    for position, item in enumerate(subtasks_payload, start=1):
        title = str(item.get("title") or "").strip()
        instruction = str(item.get("instruction") or "").strip()
        if not title or len(title) > 200:
            raise AppError(
                "INVALID_REQUEST",
                f"第 {position} 个子任务标题不能为空且不超过 200 字。",
                http_status=400,
            )
        if not instruction or len(instruction) > 8000:
            raise AppError(
                "INVALID_REQUEST",
                f"第 {position} 个子任务指令不能为空且不超过 8000 字。",
                http_status=400,
            )
        binding = _bind_plan_agent(db, ctx, item.get("agent_id"))
        deps = _edit_depends_on(item.get("depends_on"), position=position)
        bindings.append(binding)
        plan_subtasks.append(
            {
                "seq": position,
                "title": title,
                "instruction": instruction,
                "expected_output": str(item.get("expected_output") or "").strip()[:1000],
                "agent_hint": binding["agent_name"] or "",
                "agent_code": binding["agent_code"] or "",
                "depends_on": deps,
            }
        )

    removed = max(0, len(existing) - len(plan_subtasks))
    # 用户要文件：编辑时删掉交付任务也要补回来（否则改完计划就出不了文件）
    required_fmt = deliverables.detect(str(run.goal or "")) or str(
        (run.plan_json or {}).get("deliverable", {}).get("format") or ""
    )
    if required_fmt:
        before = len(plan_subtasks)
        temp = {"subtasks": plan_subtasks}
        if deliverables.ensure_plan_deliverable(
            temp, str(run.goal or ""), required_fmt, max_subtasks=limit
        ):
            plan_subtasks = temp["subtasks"]
            commander_binding = {
                "agent_id": None,
                "agent_code": None,
                "agent_name": "指挥官",
                "agent_source": SOURCE_COMMANDER,
            }
            if len(plan_subtasks) > before:
                bindings.append(commander_binding)
            else:
                bindings[-1] = commander_binding
            log.info(
                "commander plan edited: deliverable=%s auto_restored=%s",
                required_fmt,
                len(plan_subtasks) > before,
            )
    # 先删后插：绕开 (run_id, seq) 唯一约束（同事务内 flush 保证删除先落）
    for row in existing:
        db.delete(row)
    db.flush()

    new_rows: list[CommanderSubtask] = []
    for plan_subtask, binding in zip(plan_subtasks, bindings, strict=True):
        row = CommanderSubtask(
            run_id=run.id,
            yard_id=ctx.yard_id,
            seq=int(plan_subtask["seq"]),
            title=str(plan_subtask["title"])[:200],
            instruction=str(plan_subtask["instruction"]),
            expected_output=str(plan_subtask["expected_output"]),
            agent_id=binding["agent_id"],
            agent_code=binding["agent_code"],
            agent_name=binding["agent_name"],
            agent_source=binding["agent_source"],
            status=SUB_PENDING,
            depends_on_json=list(plan_subtask["depends_on"]),
            attempts=0,
            input_tokens=0,
            output_tokens=0,
        )
        db.add(row)
        new_rows.append(row)

    plan = dict(run.plan_json or {})
    plan.setdefault("clarify", [])
    plan["subtasks"] = plan_subtasks
    if required_fmt:
        plan["deliverable"] = {
            "format": required_fmt,
            "label": deliverables.label(required_fmt),
        }
    plan["generated_agents"] = [
        {
            "seq": int(plan_subtask["seq"]),
            "agent_id": binding["agent_id"],
            "agent_code": binding["agent_code"],
            "agent_name": binding["agent_name"],
        }
        for plan_subtask, binding in zip(plan_subtasks, bindings, strict=True)
        if binding["agent_source"] == SOURCE_GENERATED
    ]
    run.plan_json = plan
    db.add(run)
    db.flush()

    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="commander_plan_edited",
        resource_type="commander_run",
        resource_id=run.id,
        result="success",
        request_id=ctx.request_id,
        metadata={"subtasks": len(new_rows), "deleted": removed},
    )
    _safe_commit(db)
    return run


# ---------------------------------------------------------------------------
# 并发闸门
# ---------------------------------------------------------------------------


class ConcurrencyGate:
    """可动态缩容的并发闸门（降级时把容量改小，不影响已运行任务）。"""

    def __init__(self, capacity: int) -> None:
        self.capacity = max(1, int(capacity))
        self._inflight = 0
        self._condition = asyncio.Condition()

    async def acquire(self) -> None:
        async with self._condition:
            await self._condition.wait_for(lambda: self._inflight < self.capacity)
            self._inflight += 1

    async def release(self) -> None:
        async with self._condition:
            self._inflight = max(0, self._inflight - 1)
            self._condition.notify_all()

    async def set_capacity(self, capacity: int) -> None:
        async with self._condition:
            self.capacity = max(1, int(capacity))
            self._condition.notify_all()

    async def __aenter__(self) -> ConcurrencyGate:
        await self.acquire()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.release()


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------


class _Failure:
    __slots__ = ("code", "message", "transient")

    def __init__(self, code: str, message: str, *, transient: bool) -> None:
        self.code = code
        self.message = message
        self.transient = transient


def _is_transient(exc: BaseException) -> bool:
    """429 / 5xx / 超时 / 连接错误可重试；其余错误原样失败，不放大成本。"""
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, AppError):
        category = str((exc.details or {}).get("category") or "")
        if category in _TRANSIENT_CATEGORIES:
            return True
        if exc.http_status in (429, 500, 502, 503, 504):
            return True
    text = str(exc).lower()
    return any(
        token in text
        for token in ("429", "rate limit", " 500", " 502", " 503", " 504", "timed out", "timeout")
    )


def _failure_from(exc: BaseException) -> _Failure:
    if isinstance(exc, TimeoutError):
        return _Failure("COMMANDER_SUBTASK_TIMEOUT", "子任务执行超时。", transient=True)
    if isinstance(exc, AppError):
        return _Failure(exc.code, exc.message, transient=_is_transient(exc))
    return _Failure(
        "COMMANDER_SUBTASK_ERROR",
        f"子任务执行失败（{type(exc).__name__}）。",
        transient=_is_transient(exc),
    )


def _extract_artifacts(payload: Any, answer: str) -> list[dict[str, Any]]:
    """从 AgentResult 或正文中提取产物卡（文档/图片/链接）。"""
    found: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(item: Any) -> None:
        if len(found) >= 20:
            return
        artifact: dict[str, Any]
        if isinstance(item, str):
            url = item.strip()
            if not url:
                return
            artifact = {"type": "link", "title": url.rsplit("/", 1)[-1] or url, "url": url}
        elif isinstance(item, dict):
            url = str(
                item.get("url")
                or item.get("download_url")
                or item.get("href")
                or item.get("path")
                or ""
            ).strip()
            title = str(
                item.get("title") or item.get("name") or item.get("filename") or url
            ).strip()
            artifact = {
                "type": str(item.get("type") or item.get("kind") or "file"),
                "title": title,
                "url": url or None,
                "format": item.get("format") or item.get("ext"),
                "mime": item.get("mime") or item.get("content_type"),
            }
            for key in ("storage_key", "doc_format", "generated"):
                if key in item:
                    artifact[key] = item[key]
        else:
            return
        fingerprint = f"{artifact.get('url') or ''}|{artifact.get('title') or ''}"
        if fingerprint in seen:
            return
        seen.add(fingerprint)
        found.append({k: v for k, v in artifact.items() if v not in (None, "")})

    def _walk(node: Any, depth: int) -> None:
        if depth > 4 or not isinstance(node, dict):
            return
        for key in _ARTIFACT_KEYS:
            value = node.get(key)
            if isinstance(value, list):
                for item in value:
                    _add(item)
        for value in node.values():
            if isinstance(value, dict):
                _walk(value, depth + 1)

    if isinstance(payload, dict):
        _walk(payload, 0)
    for match in _ARTIFACT_URL_RE.findall(answer or ""):
        _add(match)
    return found


def _artifact_matches_format(artifacts: Any, fmt: str) -> bool:
    """产物里是否有指定格式的文件（按 format/文件名/下载地址判断）。"""
    ext = deliverables.FORMAT_EXT.get(fmt, f".{fmt}").lower()
    want = fmt.lower().lstrip(".")
    for item in artifacts or []:
        if not isinstance(item, dict):
            continue
        declared = str(item.get("format") or "").strip().lower().lstrip(".")
        if declared == want:
            return True
        for key in ("title", "filename", "name", "url", "download_url", "path"):
            value = str(item.get(key) or "").strip().lower()
            if value and value.endswith(ext):
                return True
    return False


async def _deliver_document(
    db: Session,
    *,
    run: CommanderRun,
    subtask: CommanderSubtask,
    ctx: YardContext,
    fmt: str,
    context: str,
) -> dict[str, Any]:
    """确定性交付：直接走对话文档链路生成真实文件，不依赖模型是否会调工具。"""
    from app.modules.chat import document as document_service

    agent = db.get(YardAgent, subtask.agent_id) if subtask.agent_id else None
    # 交付阶段吃全部前序素材（不只直接依赖）：逐页文案常被上游写成 JSON，
    # 真正的技术要点在更早的子任务里；只取直接依赖会丢掉可用内容。
    material = _dependency_context(db, run.id, list(range(1, int(subtask.seq or 1))))
    if not material:
        material = context
    instruction = deliverables.clean_requirement(subtask.instruction or "")
    goal = deliverables.clean_requirement(run.goal or "")
    parts = [part for part in (goal, instruction) if part]
    if material:
        parts.append("请优先使用随附资料中的大纲、结构与数据组织页面，不要只写封面和目录。")
    request_text = "\n\n".join(parts).strip()
    # 计划里带过来的技能编码：交付阶段即使没有绑定助手也要按技能规范写
    plan_skills = [
        str(item.get("code") or "")
        for item in (getattr(run, "plan_json", None) or {}).get("skills") or []
        if isinstance(item, dict)
    ]
    card, note, usage = await document_service.generate_document_from_chat(
        db,
        ctx=ctx,
        session_id=run.session_id,
        request_text=request_text[:6000] or goal or instruction,
        context=material,
        fmt=fmt,
        agent_name=str(getattr(agent, "name", "") or "指挥官"),
        agent_persona=str(getattr(agent, "persona_prompt", "") or ""),
        agent_bound_skills=list(getattr(agent, "bound_skills", None) or []),
        preferred_skill_codes=[code for code in plan_skills if code],
    )
    got = str(card.get("format") or "").strip().lower().lstrip(".")
    if got != fmt:
        raise AppError(
            ERR_DELIVERABLE_MISSING,
            f"需要交付{deliverables.label(fmt)}，但渲染器只产出 {got or '未知格式'}。",
            http_status=502,
            details={"format": fmt, "produced": got},
        )
    base = f"/chat/sessions/{card['session_id']}/attachments/{card['attachment_id']}"
    artifact = {
        **card,
        "type": "document",
        "title": str(card.get("filename") or deliverables.label(fmt)),
        "format": fmt,
        "url": f"{base}/download",
        "download_url": f"{base}/download",
        "preview_url": f"{base}/preview",
    }
    log.info(
        "commander deliverable generated run=%s seq=%s fmt=%s file=%s size=%s model=%s "
        "builder=%s",
        run.id,
        subtask.seq,
        fmt,
        card.get("filename"),
        card.get("size_bytes"),
        usage.get("model"),
        usage.get("builder"),
    )
    return {
        "answer": note,
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "artifacts": [artifact],
        "model": usage.get("model"),
    }


def _run_ctx(run: CommanderRun) -> YardContext:
    return YardContext(
        yard_id=run.yard_id,
        user_id=run.user_id,
        roles=list(run.roles_json or []),
        request_id=f"cmd-{run.id[:12]}",
        approval_mode=str(getattr(run, "approval_mode", None) or "ask"),
        session_id=run.session_id,
    )


def _dependency_context(db: Session, run_id: str, depends_on: list[int]) -> str:
    deps = [int(x) for x in (depends_on or [])]
    if not deps:
        return ""
    rows = db.scalars(
        select(CommanderSubtask)
        .where(
            CommanderSubtask.run_id == run_id,
            CommanderSubtask.seq.in_(deps),
            CommanderSubtask.status == SUB_OK,
        )
        .order_by(CommanderSubtask.seq.asc())
    )
    parts: list[str] = []
    dropped = 0
    for row in rows:
        excerpt = (row.output_excerpt or "").strip()
        if not excerpt:
            dropped += 1
            continue
        if _insufficient_excerpt(excerpt):
            dropped += 1
            continue
        parts.append(f"[子任务 {row.seq}「{row.title}」]\n{excerpt}")
    if dropped:
        log.info(
            "commander dependency context run=%s dropped=%d usable=%d",
            run_id,
            dropped,
            len(parts),
        )
    return "\n\n".join(parts)[:12000]


async def _commander_answer(
    db: Session,
    *,
    run: CommanderRun,
    question: str,
    ctx: YardContext,
    settings: dict[str, Any],
) -> dict[str, Any]:
    """指挥官代执行：用汇总用途模型直接完成该子任务（无合适/创建失败时兜底）。"""
    messages = [
        ChatMessage(
            role="system",
            content=(
                "你是「指挥官」，正在亲自完成一个子任务。"
                "直接给出可交付结果：结论先行、结构清晰、不编造事实；"
                "不确定的信息明确说明，不要输出空话。"
            ),
        ),
        ChatMessage(role="user", content=question),
    ]
    resp = await complete_with_logging(
        db,
        planner.build_request(
            db,
            ctx,
            settings,
            task_id=f"cmd-run-{run.id[:10]}-{uuid.uuid4().hex[:6]}",
            messages=messages,
            temperature=0.3,
            purpose="commander_merge",
        ),
    )
    return {
        "answer": str(resp.content or ""),
        "input_tokens": int(resp.input_tokens or 0),
        "output_tokens": int(resp.output_tokens or 0),
        "artifacts": _extract_artifacts({"answer": resp.content}, str(resp.content or "")),
        "model": resp.model,
    }


def _self_executor_agent(ctx: YardContext, *, allow_doc_tool: bool = True) -> YardAgent:
    """指挥官亲自执行时的临时执行器：带全部已注册工具，不再反问用户。

    不是长期 Agent：只在本次子任务里构造，不落 yard_agents 表；工具体系仍按
    ctx.approval_mode 走风险闸——完全控制直接执行，其余模式高风险工具仍会拦截。
    ``allow_doc_tool=False`` 用于非交付子任务：避免「整理大纲」被自作主张生成文件。
    """
    from app.modules.tools.registry import tool_registry

    tools = {str(getattr(tool, "code", "") or "") for tool in tool_registry.list_tools()}
    if not allow_doc_tool:
        tools.discard("doc.generate")
    return YardAgent(
        id=f"commander-self-{ctx.yard_id[:8]}",
        yard_id=ctx.yard_id,
        agent_code="commander_executor",
        name="指挥官（亲自执行）",
        description="指挥官兜底执行器：自带可用工具，必要时联网搜索、抓取与生成文档",
        emoji="🧭",
        persona_prompt=(
            "你是「指挥官」的兜底执行器。目标是**把任务做完并交付成品**，不是向用户提问。"
            "优先调用工具获取事实（联网搜索/网页抓取/知识检索/文档生成）；"
            "某个工具失败就换一种方法继续；确实无法完成时，明确说明缺少的具体能力，"
            "不要编造结果，也不要把问题抛回给用户。"
        ),
        task_prompt_template="完成任务：{{question}}",
        allowed_tools=sorted(code for code in tools if code),
        bound_skills=[],
        knowledge_scopes=[],
        status="published",
        visibility="private",
        created_by=ctx.user_id,
        created_from="commander",
        version=1,
    )


async def _commander_tool_answer(
    db: Session,
    *,
    run: CommanderRun,
    subtask: CommanderSubtask,
    question: str,
    ctx: YardContext,
    settings: dict[str, Any],
    allow_doc_tool: bool = True,
) -> dict[str, Any]:
    """指挥官自执行：优先走声明式工具循环；工具链不可用时退回纯答复。"""
    from app.modules.agents import declarative

    try:
        result = await declarative.run_declarative_agent(
            db,
            agent=_self_executor_agent(ctx, allow_doc_tool=allow_doc_tool),
            input_data={"question": question},
            ctx=ctx,
            task_id=f"cmd-self-{run.id[:10]}-{subtask.seq}-{uuid.uuid4().hex[:6]}",
            llm_purpose="commander_merge",
        )
    except AppError as exc:
        if exc.code in ("TOOL_RISK_BLOCKED", "TOOL_NOT_ALLOWED"):
            # 风险闸必须透传：不能用纯答复绕过审批/权限
            raise
        log.warning("commander self tool execution failed, fallback to plain answer: %s", exc)
        return await _commander_answer(db, run=run, question=question, ctx=ctx, settings=settings)
    except Exception as exc:  # noqa: BLE001 - 工具链异常退化为纯答复
        log.warning("commander self tool execution crashed, fallback: %s", exc)
        return await _commander_answer(db, run=run, question=question, ctx=ctx, settings=settings)

    payload = result.model_dump() if hasattr(result, "model_dump") else dict(result)
    raw_output = payload.get("output")
    output: dict[str, Any] = raw_output if isinstance(raw_output, dict) else {}
    answer = str(output.get("answer") or "")
    raw_usage = output.get("token_usage")
    usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
    return {
        "answer": answer,
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "artifacts": _extract_artifacts(payload, answer),
        "model": output.get("model"),
    }


async def _invoke_agent(
    db: Session,
    *,
    run: CommanderRun,
    subtask: CommanderSubtask,
    ctx: YardContext,
    settings: dict[str, Any],
    context: str,
) -> dict[str, Any]:
    """执行一个子任务。测试通过 monkeypatch 本函数控制成功/失败/并发时序。"""
    instruction = (subtask.instruction or subtask.title or "").strip()
    question = instruction
    if context:
        question = f"{instruction}\n\n上游子任务结果（可直接引用）：\n{context}"
    question = _with_delivery_contract(question)

    # 要文件的子任务走确定性文档链路：不依赖上游模型是否会调用工具
    fmt = _delivery_format(run, subtask)
    if fmt:
        log.info(
            "commander subtask run=%s seq=%s deliverable=%s executor=doc_pipeline",
            run.id,
            subtask.seq,
            fmt,
        )
        return await _deliver_document(
            db, run=run, subtask=subtask, ctx=ctx, fmt=fmt, context=context
        )

    if subtask.agent_source == SOURCE_COMMANDER or not subtask.agent_id:
        return await _commander_tool_answer(
            db,
            run=run,
            subtask=subtask,
            question=question,
            ctx=ctx,
            settings=settings,
            # 非交付子任务不给文档工具：避免「整理大纲」被自作主张生成文件
            allow_doc_tool=False,
        )

    agent = db.get(YardAgent, subtask.agent_id)
    if agent is None or agent.status == "deleted":
        raise AppError(
            "AGENT_NOT_FOUND",
            f"子任务绑定的助手「{subtask.agent_name or subtask.agent_code or ''}」已被删除。",
            http_status=404,
        )
    result = await declarative.run_declarative_agent(
        db,
        agent=agent,
        input_data={"question": question},
        ctx=ctx,
        task_id=f"cmd-{run.id[:12]}-{subtask.seq}-{uuid.uuid4().hex[:6]}",
        llm_purpose="declarative",
        context=context,
    )
    payload = result.model_dump() if hasattr(result, "model_dump") else dict(result)
    raw_output = payload.get("output")
    output: dict[str, Any] = raw_output if isinstance(raw_output, dict) else {}
    answer = str(output.get("answer") or "")
    raw_usage = output.get("token_usage")
    usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
    return {
        "answer": answer,
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "artifacts": _extract_artifacts(payload, answer),
        "model": output.get("model"),
    }


#: 自主补救动作（轨迹与文案共用）
_REMEDIATION_LABELS = {
    "retry": "原执行者重试",
    "alt_agent": "换用其他助手",
    "new_agent": "新建专用助手",
    "self": "指挥官亲自执行",
}
#: 命中这些错误时换人无意义：要么需要用户改权限，要么重试只会放大成本
_REMEDIATION_HARD_STOP = frozenset(
    {"TOOL_RISK_BLOCKED", "QUOTA_EXCEEDED", "FORBIDDEN", "UNAUTHORIZED"}
)


def _remediation_queue(subtask: CommanderSubtask) -> list[str]:
    """首次失败后依次尝试的动作；指挥官派系的初始执行已带工具，无需再排队「亲自执行」。"""
    if subtask.agent_source == SOURCE_COMMANDER or not subtask.agent_id:
        return ["retry", "alt_agent", "new_agent"]
    return ["retry", "alt_agent", "new_agent", "self"]


def _next_remediation_action(queue: list[str], failure: _Failure, *, retried: bool) -> str | None:
    """瞬时错误优先原样重试一次；其余错误跳过 retry，按队列换人/建人/自执行。"""
    if failure.transient and not retried and "retry" in queue:
        return "retry"
    while queue:
        action = queue.pop(0)
        if action == "retry":
            continue
        return action
    return None


def _alternative_agent(
    db: Session, *, ctx: YardContext, subtask: CommanderSubtask
) -> YardAgent | None:
    """挑一个与当前执行者不同、且与子任务关键词相关的已发布助手。"""
    text = " ".join(filter(None, (subtask.title, subtask.instruction, subtask.expected_output)))
    candidates = [
        agent
        for agent in matcher.match_candidates(db, yard_id=ctx.yard_id, user_id=ctx.user_id)
        if agent.id != subtask.agent_id
    ]
    if not candidates:
        return None
    best, score = matcher.best_candidate(text, candidates, matcher.agent_stats(db, ctx.yard_id))
    if best is None or score <= 0:
        return None
    return best


async def _apply_remediation(
    db: Session,
    *,
    subtask: CommanderSubtask,
    ctx: YardContext,
    settings: dict[str, Any],
    action: str,
) -> str | None:
    """执行一个补救动作并改写子任务执行者；动作不可用时返回 None（调用方继续下一个）。"""
    before = subtask.agent_name or "指挥官"
    if action == "retry":
        return f"{_REMEDIATION_LABELS['retry']}（{before}）"
    if action == "self":
        subtask.agent_id = None
        subtask.agent_code = None
        subtask.agent_name = "指挥官"
        subtask.agent_source = SOURCE_COMMANDER
        return f"{_REMEDIATION_LABELS['self']}（原本：{before}）"
    if action == "alt_agent":
        agent = _alternative_agent(db, ctx=ctx, subtask=subtask)
        if agent is None:
            return None
        subtask.agent_id = agent.id
        subtask.agent_code = agent.agent_code
        subtask.agent_name = agent.name
        subtask.agent_source = SOURCE_EXISTING
        return f"{_REMEDIATION_LABELS['alt_agent']}：「{before}」→「{agent.name}」"
    if action == "new_agent":
        text = " ".join(filter(None, (subtask.title, subtask.instruction)))
        agent = await matcher.create_remediation_agent(db, ctx=ctx, text=text, settings=settings)
        if agent is None:
            return None
        subtask.agent_id = agent.id
        subtask.agent_code = agent.agent_code
        subtask.agent_name = agent.name
        subtask.agent_source = SOURCE_GENERATED
        return f"{_REMEDIATION_LABELS['new_agent']}：「{agent.name}」接手"
    return None


def _capability_gap_message(
    failure: _Failure, *, attempts: int, remediations: list[dict[str, Any]]
) -> str:
    labels: list[str] = []
    for item in remediations:
        label = _REMEDIATION_LABELS.get(str(item.get("action") or ""))
        if label and label not in labels:
            labels.append(label)
    tried = "、".join(labels) or "无可用补救手段"
    return (
        f"已自主尝试 {attempts} 轮（{tried}），仍未完成：{failure.message[:240]} "
        "原因：现有助手与可用工具不具备完成该任务的能力，这不是需要你补充信息的问题；"
        "换上具备相应设备/工具的助手后重试即可。"
    )


async def _run_one(
    *,
    run_id: str,
    subtask_id: str,
    ctx: YardContext,
    settings: dict[str, Any],
    gate: ConcurrencyGate,
    on_event: Callable[[str, dict], None] | None,
) -> dict[str, Any]:
    """一个子任务的完整生命周期：独立 Session、超时、多轮自主补救、落库、事件。

    补救顺序（``auto_remediate`` 开启时）：原执行者重试 → 换其他助手 → 新建专用
    助手 → 指挥官带工具亲自执行；轮数由 ``remediate_rounds`` 限制。全程不问用户，
    轮尽才以 ``COMMANDER_CAPABILITY_GAP`` 如实报告能力缺口。
    """
    factory = _get_session_factory()
    async with gate:
        db = factory()
        try:
            subtask = db.get(CommanderSubtask, subtask_id)
            run = db.get(CommanderRun, run_id)
            if subtask is None or run is None:
                return {"seq": 0, "status": SUB_SKIPPED, "tokens": 0, "degraded": False}
            if subtask.status in (SUB_OK, SUB_SKIPPED):
                return {
                    "seq": int(subtask.seq),
                    "status": subtask.status,
                    "tokens": 0,
                    "degraded": False,
                }

            subtask.status = SUB_RUNNING
            subtask.started_at = _now()
            subtask.finished_at = None
            subtask.error_code = None
            _safe_commit(db)
            _emit(on_event, "subtask", subtask_out(subtask))
            log.info(
                "commander subtask start run=%s seq=%s agent=%s source=%s deliverable=%s",
                run_id,
                subtask.seq,
                subtask.agent_name or "指挥官",
                subtask.agent_source,
                deliverables.subtask_format(subtask) or "-",
            )

            context = _dependency_context(db, run_id, list(subtask.depends_on_json or []))
            auto_remediate = bool(settings.get("auto_remediate", True))
            max_rounds = max(1, int(settings.get("remediate_rounds") or 1)) if auto_remediate else 1
            queue = _remediation_queue(subtask)
            remediations = list(subtask.remediation_json or [])
            used_retry = False
            degraded = False
            result: dict[str, Any] | None = None
            failure: _Failure | None = None
            attempt = 0
            timeout_seconds = int(settings["subtask_timeout_seconds"])
            if _delivery_format(run, subtask):
                # 文档交付要多次调用模型（大纲 + 分批写页 + 过薄重写），
                # 按普通子任务的 3 分钟会反复超时、被当成失败重试
                timeout_seconds = max(timeout_seconds, 600)

            while attempt < max_rounds:
                attempt += 1
                subtask.attempts = int(subtask.attempts or 0) + 1
                _safe_commit(db)
                try:
                    result = await asyncio.wait_for(
                        _invoke_agent(
                            db,
                            run=run,
                            subtask=subtask,
                            ctx=ctx,
                            settings=settings,
                            context=context,
                        ),
                        timeout=timeout_seconds,
                    )
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - 统一转失败矩阵
                    failure = _failure_from(exc)
                    result = None
                    if failure.transient:
                        degraded = True
                    if attempt >= max_rounds:
                        break
                    if failure.code in _REMEDIATION_HARD_STOP:
                        break

                    # 失败后换 Session 状态继续：rollback 拿回干净对象再改写执行者
                    db.rollback()
                    subtask = db.get(CommanderSubtask, subtask_id)
                    run = db.get(CommanderRun, run_id)
                    if subtask is None or run is None:
                        return {
                            "seq": 0,
                            "status": SUB_ERROR,
                            "tokens": 0,
                            "degraded": degraded,
                        }

                    applied = False
                    while True:
                        action = _next_remediation_action(queue, failure, retried=used_retry)
                        if action is None:
                            # 换人/建人都试过后，剩余轮数交给指挥官亲自执行（工具最全）
                            action = "self"
                        if action is None:
                            break
                        if action == "retry":
                            used_retry = True
                        note = await _apply_remediation(
                            db,
                            subtask=subtask,
                            ctx=ctx,
                            settings=settings,
                            action=action,
                        )
                        if note is None:
                            continue
                        entry = {
                            "round": attempt,
                            "action": action,
                            "agent_name": subtask.agent_name or "指挥官",
                            "code": failure.code,
                            "message": f"{note}；原因：{failure.message[:160]}",
                        }
                        remediations = [*remediations, entry]
                        subtask.remediation_json = list(remediations)
                        subtask.error_code = None
                        _safe_commit(db)
                        log.info(
                            "commander remediation run=%s seq=%s round=%d action=%s "
                            "agent=%s cause=%s",
                            run_id,
                            subtask.seq,
                            attempt,
                            action,
                            subtask.agent_name or "指挥官",
                            failure.code,
                        )
                        log_audit(
                            db,
                            yard_id=run.yard_id,
                            actor_id=run.user_id,
                            action="commander_subtask_remediated",
                            resource_type="commander_subtask",
                            resource_id=subtask.id,
                            result="success",
                            request_id=ctx.request_id,
                            metadata={
                                "run_id": run.id,
                                "seq": int(subtask.seq),
                                "round": attempt,
                                "action": action,
                                "agent_id": subtask.agent_id,
                                "agent_name": subtask.agent_name,
                                "code": failure.code,
                            },
                        )
                        _safe_commit(db)
                        _emit(on_event, "subtask", subtask_out(subtask))
                        applied = True
                        break
                    if not applied:
                        break
                    if RETRY_BACKOFF_SECONDS > 0:
                        await asyncio.sleep(RETRY_BACKOFF_SECONDS)

            subtask = db.get(CommanderSubtask, subtask_id)
            if subtask is None:
                return {"seq": 0, "status": SUB_ERROR, "tokens": 0, "degraded": degraded}
            if result is not None:
                input_tokens = int(result.get("input_tokens") or 0)
                output_tokens = int(result.get("output_tokens") or 0)
                subtask.status = SUB_OK
                subtask.input_tokens = int(subtask.input_tokens or 0) + input_tokens
                subtask.output_tokens = int(subtask.output_tokens or 0) + output_tokens
                # 摘录既给下游当素材，也给前端展示：4000 字能保住整份逐页大纲
                subtask.output_excerpt = str(result.get("answer") or "").strip()[:4000] or None
                subtask.artifact_json = list(result.get("artifacts") or []) or None
                subtask.error_code = None
                subtask.finished_at = _now()
                _safe_commit(db)
                _emit(on_event, "subtask", subtask_out(subtask))
                log.info(
                    "commander subtask ok run=%s seq=%s agent=%s tokens=%d attempts=%d "
                    "latency_ms=%d artifacts=%d",
                    run_id,
                    subtask.seq,
                    subtask.agent_name or "指挥官",
                    input_tokens + output_tokens,
                    int(subtask.attempts or 0),
                    int((subtask.finished_at - subtask.started_at).total_seconds() * 1000)
                    if subtask.started_at and subtask.finished_at
                    else 0,
                    len(subtask.artifact_json or []),
                )
                return {
                    "seq": int(subtask.seq),
                    "status": SUB_OK,
                    "tokens": input_tokens + output_tokens,
                    "degraded": degraded,
                }

            assert failure is not None  # noqa: S101 - 防御性：失败路径必有原因
            subtask.status = SUB_ERROR
            if failure.code == "TOOL_RISK_BLOCKED":
                subtask.error_code = failure.code[:50]
                subtask.output_excerpt = (
                    "该任务需要执行高风险工具，当前会话权限模式拦截了自动执行；"
                    "把会话权限切换为「完全控制」后重试即可自动继续。"
                )
            elif failure.code in _REMEDIATION_HARD_STOP:
                subtask.error_code = failure.code[:50]
                subtask.output_excerpt = failure.message[:1200]
            elif auto_remediate:
                subtask.error_code = ERR_CAPABILITY_GAP
                subtask.output_excerpt = _capability_gap_message(
                    failure, attempts=attempt, remediations=remediations
                )[:1200]
            else:
                subtask.error_code = failure.code[:50]
                subtask.output_excerpt = failure.message[:1200]
            subtask.finished_at = _now()
            _safe_commit(db)
            _emit(on_event, "subtask", subtask_out(subtask))
            log.warning(
                "commander subtask failed run=%s seq=%s agent=%s code=%s attempts=%d " "reason=%s",
                run_id,
                subtask.seq,
                subtask.agent_name or "指挥官",
                subtask.error_code,
                int(subtask.attempts or 0),
                str(failure.message or "")[:200],
            )
            return {
                "seq": int(subtask.seq),
                "status": SUB_ERROR,
                "tokens": 0,
                "degraded": degraded,
            }
        finally:
            db.close()


def _mark_unfinished(
    db: Session,
    subtasks: list[CommanderSubtask],
    *,
    code: str,
    include_running: bool,
) -> bool:
    changed = False
    now = _now()
    for st in subtasks:
        if st.status == SUB_PENDING or (include_running and st.status == SUB_RUNNING):
            st.status = SUB_SKIPPED
            st.error_code = code
            st.finished_at = now
            db.add(st)
            changed = True
    if changed:
        _safe_commit(db)
    return changed


def _ready(state: dict[int, str], depends_on: list[int]) -> bool:
    return all(state.get(int(dep)) == SUB_OK for dep in depends_on)


async def _stop_inflight(
    db: Session,
    *,
    run: CommanderRun,
    subtasks: list[CommanderSubtask],
    inflight: dict[int, asyncio.Task],
    tokens_total: int,
) -> int:
    """取消在途任务并把「刚好完成」的成果/token 记账保留下来（取消/超时/预算共用）。"""
    for task in inflight.values():
        task.cancel()
    if inflight:
        await asyncio.gather(*inflight.values(), return_exceptions=True)
    for task in inflight.values():
        if not task.done() or task.cancelled():
            continue
        try:
            result = task.result()
        except Exception:  # noqa: BLE001 - 已失败的任务不再记 token
            continue
        tokens_total += int(result.get("tokens") or 0)
        if result.get("degraded"):
            run.degraded = True
    inflight.clear()
    run.tokens_total = tokens_total
    _safe_commit(db)
    for st in subtasks:
        db.refresh(st)
    return tokens_total


async def _schedule(
    db: Session,
    *,
    run: CommanderRun,
    subtasks: list[CommanderSubtask],
    settings: dict[str, Any],
    ctx: YardContext,
    gate: ConcurrencyGate,
    on_event: Callable[[str, dict], None] | None,
) -> None:
    """按 depends_on 动态调度；并发由 gate 控制，支持降级/取消/预算/总超时。"""
    by_seq = {int(st.seq): st for st in subtasks}
    state = {int(st.seq): st.status for st in subtasks}
    inflight: dict[int, asyncio.Task] = {}
    tokens_total = int(run.tokens_total or 0)
    budget = int(settings["token_budget"])
    # 预算兜底只放行一次交付任务，避免极端情况下反复进入同一分支空转
    delivery_recovery_used = False
    # 超时兜底只给交付任务开一次收尾窗口
    delivery_grace_used = False
    loop = asyncio.get_running_loop()
    deadline = loop.time() + int(settings["run_timeout_seconds"])

    def _emit_out(st: CommanderSubtask) -> None:
        _emit(on_event, "subtask", subtask_out(st))

    while True:
        db.refresh(run)

        # ① 依赖失败传播：error/skipped → 下游 skipped（不是 error）
        changed = False
        for st in subtasks:
            seq = int(st.seq)
            if state.get(seq) != SUB_PENDING:
                continue
            deps = [int(x) for x in (st.depends_on_json or [])]
            if any(state.get(dep) in (SUB_ERROR, SUB_SKIPPED) for dep in deps):
                state[seq] = SUB_SKIPPED
                st.status = SUB_SKIPPED
                st.error_code = "COMMANDER_DEPENDENCY_FAILED"
                st.finished_at = _now()
                db.add(st)
                changed = True
                _emit_out(st)
        if changed:
            _safe_commit(db)

        if run.status == RUN_CANCELLED:
            tokens_total = await _stop_inflight(
                db, run=run, subtasks=subtasks, inflight=inflight, tokens_total=tokens_total
            )
            _mark_unfinished(db, subtasks, code=ERR_RUN_CANCELLED, include_running=True)
            break

        if tokens_total >= budget:
            tokens_total = await _stop_inflight(
                db, run=run, subtasks=subtasks, inflight=inflight, tokens_total=tokens_total
            )
            # 交付任务不因预算被饿死：它是整轮唯一产出，成本有界（页数已受限）
            pending_delivery = [
                st
                for st in subtasks
                if state.get(int(st.seq)) == SUB_PENDING and _delivery_format(run, st)
            ]
            if pending_delivery and not delivery_recovery_used:
                delivery_recovery_used = True
                log.warning(
                    "commander budget reached but delivery pending: tokens=%s budget=%s seq=%s",
                    tokens_total,
                    budget,
                    [int(st.seq) for st in pending_delivery],
                )
                delivery_seqs = {int(st.seq) for st in pending_delivery}
                for st in subtasks:
                    if int(st.seq) in delivery_seqs:
                        # 断掉依赖，让交付任务下一轮就能跑（素材缺口由目标本身兜住）
                        st.depends_on_json = []
                        db.add(st)
                        continue
                    if st.status == SUB_PENDING:
                        st.status = SUB_SKIPPED
                        st.error_code = "COMMANDER_BUDGET_EXCEEDED"
                        st.finished_at = _now()
                        db.add(st)
                        _emit_out(st)
                _safe_commit(db)
            elif _mark_unfinished(
                db, subtasks, code="COMMANDER_BUDGET_EXCEEDED", include_running=True
            ):
                for st in subtasks:
                    if st.status == SUB_SKIPPED and st.error_code == "COMMANDER_BUDGET_EXCEEDED":
                        _emit_out(st)
                break
            else:
                break

        now = loop.time()
        if now >= deadline:
            tokens_total = await _stop_inflight(
                db, run=run, subtasks=subtasks, inflight=inflight, tokens_total=tokens_total
            )
            pending_delivery = [
                st
                for st in subtasks
                if state.get(int(st.seq)) == SUB_PENDING and _delivery_format(run, st)
            ]
            if pending_delivery and not delivery_grace_used:
                # 整轮唯一产出还没开始：给它一个单独的收尾窗口，否则前面白跑
                delivery_grace_used = True
                deadline = now + max(300, int(settings["subtask_timeout_seconds"]))
                delivery_seqs = {int(st.seq) for st in pending_delivery}
                for st in subtasks:
                    if int(st.seq) in delivery_seqs:
                        st.depends_on_json = []
                        db.add(st)
                    elif st.status == SUB_PENDING:
                        st.status = SUB_SKIPPED
                        st.error_code = "COMMANDER_RUN_TIMEOUT"
                        st.finished_at = _now()
                        db.add(st)
                        _emit_out(st)
                _safe_commit(db)
                log.warning(
                    "commander run timeout, delivery grace window opened: seq=%s seconds=%s",
                    sorted(delivery_seqs),
                    int(deadline - now),
                )
                continue
            if _mark_unfinished(db, subtasks, code="COMMANDER_RUN_TIMEOUT", include_running=True):
                for st in subtasks:
                    if st.error_code == "COMMANDER_RUN_TIMEOUT":
                        _emit_out(st)
            break

        ready = [
            st
            for st in subtasks
            if state.get(int(st.seq)) == SUB_PENDING
            and int(st.seq) not in inflight
            and _ready(state, [int(x) for x in (st.depends_on_json or [])])
        ]
        if not ready and not inflight:
            # 理论不可达（依赖只指向更早 seq）；防御性收尾，避免空转
            if _mark_unfinished(
                db, subtasks, code="COMMANDER_DEPENDENCY_FAILED", include_running=True
            ):
                for st in subtasks:
                    if st.error_code == "COMMANDER_DEPENDENCY_FAILED":
                        _emit_out(st)
            break

        for st in ready:
            inflight[int(st.seq)] = asyncio.create_task(
                _run_one(
                    run_id=run.id,
                    subtask_id=st.id,
                    ctx=ctx,
                    settings=settings,
                    gate=gate,
                    on_event=on_event,
                )
            )
        if not inflight:
            break

        timeout = max(0.05, min(deadline - loop.time(), SCHEDULER_POLL_SECONDS))
        done, _ = await asyncio.wait(
            inflight.values(), timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        if not done:
            continue

        for seq, task in list(inflight.items()):
            if task not in done:
                continue
            inflight.pop(seq)
            try:
                result = task.result()
            except asyncio.CancelledError:
                continue
            except Exception as exc:  # noqa: BLE001 - 子任务异常不得拖垮整轮
                log.warning("commander subtask %s crashed: %s", seq, exc)
                result = {"seq": seq, "status": SUB_ERROR, "tokens": 0, "degraded": False}
            state[seq] = str(result.get("status") or SUB_ERROR)
            row = by_seq.get(seq)
            if row is not None:
                db.refresh(row)
            tokens_total += int(result.get("tokens") or 0)
            run.tokens_total = tokens_total
            if result.get("degraded") and not run.degraded:
                run.degraded = True
                fallback = min(int(settings["fallback_parallel"]), gate.capacity)
                await gate.set_capacity(fallback)
                run.effective_parallel = gate.capacity
                log_audit(
                    db,
                    yard_id=run.yard_id,
                    actor_id=run.user_id,
                    action="commander_degraded",
                    resource_type="commander_run",
                    resource_id=run.id,
                    result="success",
                    request_id=ctx.request_id,
                    metadata={
                        "from_parallel": int(settings["max_parallel"]),
                        "to_parallel": gate.capacity,
                        "seq": seq,
                    },
                )
            _safe_commit(db)

    # 收尾：若仍有未决状态（取消/超时之外的意外），统一标记 skipped
    if any(st.status == SUB_PENDING for st in subtasks):
        if _mark_unfinished(db, subtasks, code="COMMANDER_NOT_SCHEDULED", include_running=True):
            for st in subtasks:
                if st.error_code == "COMMANDER_NOT_SCHEDULED":
                    _emit_out(st)


def _failed_items(subtasks: list[CommanderSubtask]) -> list[dict[str, Any]]:
    """失败/跳过原因：优先给人能读懂的结果说明，错误码只做兜底。"""
    return [
        {
            "seq": int(st.seq),
            "title": st.title,
            "reason": ((st.output_excerpt or "").strip() or st.error_code or "未完成")[:200],
        }
        for st in subtasks
        if st.status in (SUB_ERROR, SUB_SKIPPED)
    ]


def _collect_artifacts(subtasks: list[CommanderSubtask]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for st in subtasks:
        for item in st.artifact_json or []:
            if not isinstance(item, dict):
                continue
            out.append(
                {
                    **item,
                    "subtask_seq": int(st.seq),
                    "subtask_title": st.title,
                    "agent_name": st.agent_name,
                }
            )
    return out[:50]


def _deterministic_answer(
    run: CommanderRun, subtasks: list[CommanderSubtask], *, failed: list[dict[str, Any]]
) -> str:
    lines = [f"目标：{run.goal}"]
    ok = [st for st in subtasks if st.status == SUB_OK]
    if ok:
        lines.append("已完成：")
        for st in ok:
            excerpt = (st.output_excerpt or "").strip()
            lines.append(f"- {st.title}（{st.agent_name or '指挥官'}）")
            if excerpt:
                lines.append(f"  {excerpt[:400]}")
    if failed:
        lines.append("未完成：")
        for item in failed:
            lines.append(f"- {item['title']}：{item['reason']}")
    if not ok and not failed:
        lines.append("本次没有可交付的子任务结果。")
    return "\n".join(lines)


async def _summarize(
    db: Session,
    *,
    run: CommanderRun,
    subtasks: list[CommanderSubtask],
    settings: dict[str, Any],
    ctx: YardContext,
) -> tuple[str, int]:
    ok = [st for st in subtasks if st.status == SUB_OK]
    failed = _failed_items(subtasks)
    if not ok:
        return _deterministic_answer(run, subtasks, failed=failed), 0

    lines = [f"目标：{run.goal}"]
    notes = str((run.plan_json or {}).get("merge_notes") or "").strip()
    if notes:
        lines.append(f"汇总要求：{notes}")
    for st in ok:
        lines.append(
            f"### 子任务 {st.seq}「{st.title}」（{st.agent_name or '指挥官'}）\n"
            f"{(st.output_excerpt or '').strip()[:1200]}"
        )
    if failed:
        lines.append(
            "未完成：" + "；".join(f"{item['title']}（{item['reason']}）" for item in failed)
        )
    lines.append(
        "请用中文输出最终答复：结论先行，合并重复信息，列出产物与失败项（如实说明），"
        "并给出 1-3 条下一步建议；不要逐条复述子任务全文。"
    )
    messages = [
        ChatMessage(
            role="system",
            content=(
                "你是「指挥官」的结果汇总器：把多个子任务的产出整合成一份可直接交付的答复。"
                "不得编造子任务没有给出的数据或链接。"
            ),
        ),
        ChatMessage(role="user", content="\n\n".join(lines)[:12000]),
    ]
    try:
        resp = await complete_with_logging(
            db,
            planner.build_request(
                db,
                ctx,
                settings,
                task_id=f"cmd-merge-{uuid.uuid4().hex[:10]}",
                messages=messages,
                temperature=0.3,
                purpose="commander_merge",
            ),
        )
    except Exception as exc:  # noqa: BLE001 - 汇总失败退化为确定性拼接
        log.warning("commander summary llm failed: %s", exc)
        return _deterministic_answer(run, subtasks, failed=failed), 0
    answer = str(resp.content or "").strip()
    if not answer:
        answer = _deterministic_answer(run, subtasks, failed=failed)
    return answer, int(resp.input_tokens or 0) + int(resp.output_tokens or 0)


class CommanderSummary(TypedDict):
    """``execute_run`` 的汇总返回（同时兼容对话层的 content/payload 字段）。"""

    run_id: str
    answer: str
    artifacts: list[dict[str, Any]]
    failed: list[dict[str, Any]]
    tokens_total: int
    degraded: bool
    content: str
    payload: dict[str, Any]
    citations: list[Any]
    token_usage: dict[str, Any]


def _summary_payload(
    run: CommanderRun,
    subtasks: list[CommanderSubtask],
    *,
    answer: str,
    artifacts: list[dict[str, Any]],
    failed: list[dict[str, Any]],
) -> CommanderSummary:
    tokens = int(run.tokens_total or 0)
    return {
        "run_id": run.id,
        "answer": answer,
        "artifacts": artifacts,
        "failed": failed,
        "tokens_total": tokens,
        "degraded": bool(run.degraded),
        "content": answer,
        "payload": {
            "type": "commander_summary",
            "run_id": run.id,
            "answer": answer,
            "artifacts": artifacts,
            "failed": failed,
            "tokens_total": tokens,
            "degraded": bool(run.degraded),
            "subtasks": [subtask_out(st) for st in subtasks],
        },
        "citations": [],
        "token_usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": tokens,
            "model": "commander",
        },
    }


async def execute_run(
    run_id: str, on_event: Callable[[str, dict], None] | None = None
) -> CommanderSummary:
    """真正执行一轮指挥官运行（自建 Session，可被后台任务/测试直接调用）。"""
    db = _get_session_factory()()
    try:
        run = db.get(CommanderRun, run_id)
        if run is None:
            raise AppError(ERR_RUN_NOT_FOUND, "指挥官运行不存在。", http_status=404)
        subtasks = list(
            db.scalars(
                select(CommanderSubtask)
                .where(CommanderSubtask.run_id == run.id)
                .order_by(CommanderSubtask.seq.asc())
            )
        )
        if run.status == RUN_RUNNING:
            raise AppError(ERR_RUN_BUSY, "该指挥官运行正在执行中。", http_status=409)
        if run.status == RUN_CANCELLED:
            raise AppError(ERR_RUN_CANCELLED, "该指挥官运行已取消。", http_status=409)
        if run.status == RUN_COMPLETED:
            failed = _failed_items(subtasks)
            return _summary_payload(
                run,
                subtasks,
                answer=_deterministic_answer(run, subtasks, failed=failed),
                artifacts=_collect_artifacts(subtasks),
                failed=failed,
            )

        settings = commander_settings.settings_for(db, run.yard_id)
        ctx = _run_ctx(run)
        # 运行前配额预检：不足直接 429，不进入 running（避免跑到一半才失败）
        _precheck_quota(db, ctx)
        run.status = RUN_RUNNING
        run.error_code = None
        run.degraded = False
        run.effective_parallel = int(settings["max_parallel"])
        run.max_parallel = int(settings["max_parallel"])
        _safe_commit(db)
        started = time.perf_counter()
        log_audit(
            db,
            yard_id=run.yard_id,
            actor_id=run.user_id,
            action="commander_run_started",
            resource_type="commander_run",
            resource_id=run.id,
            result="success",
            request_id=ctx.request_id,
            metadata={
                "subtasks": len(subtasks),
                "max_parallel": int(run.max_parallel or 1),
            },
        )
        _safe_commit(db)
        _emit(on_event, "plan", run_out(run, subtasks))

        gate = ConcurrencyGate(int(settings["max_parallel"]))
        await _schedule(
            db,
            run=run,
            subtasks=subtasks,
            settings=settings,
            ctx=ctx,
            gate=gate,
            on_event=on_event,
        )

        db.refresh(run)
        for st in subtasks:
            db.refresh(st)
        for st in subtasks:
            if st.status == SUB_PENDING:
                st.status = SUB_SKIPPED
                st.error_code = "COMMANDER_NOT_SCHEDULED"
                st.finished_at = _now()
                db.add(st)
        _safe_commit(db)

        answer, summary_tokens = await _summarize(
            db, run=run, subtasks=subtasks, settings=settings, ctx=ctx
        )

        ok_count = sum(1 for st in subtasks if st.status == SUB_OK)
        failed = _failed_items(subtasks)
        if run.status == RUN_CANCELLED:
            status = RUN_CANCELLED
        elif ok_count == 0:
            status = RUN_FAILED
        elif failed:
            status = RUN_PARTIAL
        else:
            status = RUN_COMPLETED

        run.status = status
        run.tokens_total = int(run.tokens_total or 0) + summary_tokens
        run.effective_parallel = gate.capacity
        run.latency_ms = int((time.perf_counter() - started) * 1000)
        if ok_count:
            run.error_code = None
        elif {st.error_code for st in subtasks if st.status == SUB_ERROR} == {ERR_CAPABILITY_GAP}:
            # 全部失败且都是补救轮尽：运行级也标成能力缺口，前端可直接给建议
            run.error_code = ERR_CAPABILITY_GAP
        else:
            run.error_code = "COMMANDER_RUN_FAILED"
        _safe_commit(db)

        artifacts = _collect_artifacts(subtasks)
        summary = _summary_payload(run, subtasks, answer=answer, artifacts=artifacts, failed=failed)
        log_audit(
            db,
            yard_id=run.yard_id,
            actor_id=run.user_id,
            action="commander_run_finished",
            resource_type="commander_run",
            resource_id=run.id,
            result="success" if ok_count else "failed",
            request_id=ctx.request_id,
            metadata={
                "status": status,
                "subtasks": len(subtasks),
                "ok": ok_count,
                "failed": len(failed),
                "degraded": bool(run.degraded),
                "tokens_total": int(run.tokens_total or 0),
                "latency_ms": run.latency_ms,
            },
        )
        _safe_commit(db)
        _emit(on_event, "summary", {**run_out(run, subtasks), **summary})
        return summary
    finally:
        db.close()


async def run_in_background(run_id: str) -> None:
    """执行后把汇总写成一条 assistant 聊天消息（不 import chat.service，避免循环依赖）。"""
    summary: CommanderSummary | None = None
    try:
        summary = await execute_run(run_id)
    except Exception as exc:  # noqa: BLE001 - 后台任务必须兜底记录失败
        log.exception("commander run %s failed: %s", run_id, exc)
        db = _get_session_factory()()
        try:
            run = db.get(CommanderRun, run_id)
            if run is not None and run.status not in (RUN_CANCELLED, RUN_COMPLETED):
                run.status = RUN_FAILED
                run.error_code = "COMMANDER_EXECUTE_FAILED"
                _safe_commit(db)
        finally:
            db.close()
        return

    db = _get_session_factory()()
    try:
        run = db.get(CommanderRun, run_id)
        if run is None:
            return
        session = db.get(chat_models.ChatSession, run.session_id)
        if session is None or session.yard_id != run.yard_id:
            log.warning("commander run %s has no matching chat session", run_id)
            return
        current = db.scalar(
            select(chat_models.ChatMessage.seq)
            .where(chat_models.ChatMessage.session_id == run.session_id)
            .order_by(chat_models.ChatMessage.seq.desc())
            .limit(1)
        )
        seq = int(current or 0) + 1
        message = chat_models.ChatMessage(
            session_id=run.session_id,
            yard_id=run.yard_id,
            seq=seq,
            role="assistant",
            content=summary["answer"] or "指挥官已完成本轮任务。",
            status="ok",
            routed_by="commander",
            choice_json={
                "kind": "commander",
                "id": run.id,
                "name": "指挥官",
                "run_id": run.id,
            },
            reason="指挥官编排",
            payload_json={
                "type": "commander_summary",
                "run_id": run.id,
                "answer": summary["answer"],
                "artifacts": summary["artifacts"],
                "failed": summary["failed"],
                "tokens_total": summary["tokens_total"],
            },
            token_usage_json={
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": int(summary["tokens_total"] or 0),
                "model": "commander",
            },
        )
        db.add(message)
        _safe_commit(db)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 重试 / 取消
# ---------------------------------------------------------------------------


def retry_run(db: Session, ctx: YardContext, run_id: str) -> CommanderRun:
    """把 error/skipped 子任务重置 pending（attempts 不清零），运行回到 queued。"""
    run, subtasks = get_run(db, ctx.yard_id, run_id)
    require_run_access(db, run, user_id=ctx.user_id, roles=ctx.roles)
    if run.status == RUN_RUNNING:
        raise AppError(ERR_RUN_BUSY, "运行正在执行中，请先取消或等待完成。", http_status=409)
    if run.status == RUN_CANCELLED:
        raise AppError(ERR_RUN_STATE, "已取消的运行不能重试。", http_status=409)

    retried = 0
    for st in subtasks:
        if st.status in (SUB_ERROR, SUB_SKIPPED):
            st.status = SUB_PENDING
            st.error_code = None
            st.output_excerpt = None
            st.artifact_json = None
            st.input_tokens = 0
            st.output_tokens = 0
            st.started_at = None
            st.finished_at = None
            db.add(st)
            retried += 1
    if retried == 0:
        raise AppError(
            "COMMANDER_NOTHING_TO_RETRY",
            "没有失败或跳过的子任务可重试。",
            http_status=400,
        )
    settings = commander_settings.settings_for(db, run.yard_id)
    run.status = RUN_QUEUED
    run.error_code = None
    run.degraded = False
    run.max_parallel = int(settings["max_parallel"])
    run.effective_parallel = int(settings["max_parallel"])
    db.add(run)
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="commander_subtask_retried",
        resource_type="commander_run",
        resource_id=run.id,
        result="success",
        request_id=ctx.request_id,
        metadata={"retried": retried},
    )
    _safe_commit(db)
    return run


def cancel_run(db: Session, ctx: YardContext, run_id: str) -> CommanderRun:
    """取消：pending → skipped，已完成的保留，运行标记 cancelled。"""
    run, subtasks = get_run(db, ctx.yard_id, run_id)
    require_run_access(db, run, user_id=ctx.user_id, roles=ctx.roles)
    if run.status in (RUN_COMPLETED, RUN_FAILED, RUN_CANCELLED):
        raise AppError(ERR_RUN_STATE, "该运行已结束，无法取消。", http_status=409)

    now = _now()
    skipped = 0
    for st in subtasks:
        if st.status == SUB_PENDING:
            st.status = SUB_SKIPPED
            st.error_code = ERR_RUN_CANCELLED
            st.finished_at = now
            db.add(st)
            skipped += 1
    run.status = RUN_CANCELLED
    run.error_code = ERR_RUN_CANCELLED
    db.add(run)
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="commander_run_cancelled",
        resource_type="commander_run",
        resource_id=run.id,
        result="success",
        request_id=ctx.request_id,
        metadata={"skipped": skipped},
    )
    _safe_commit(db)
    return run


__all__ = [
    "ConcurrencyGate",
    "ERR_CAPABILITY_GAP",
    "ERR_RUN_NOT_FOUND",
    "RETRY_BACKOFF_SECONDS",
    "SCHEDULER_POLL_SECONDS",
    "cancel_run",
    "can_access_run",
    "create_plan_run",
    "execute_run",
    "get_run",
    "plan_message_payload",
    "require_run_access",
    "retry_run",
    "run_in_background",
    "run_out",
    "subtask_out",
    "update_plan",
]
