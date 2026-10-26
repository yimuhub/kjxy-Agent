"""专业 Agent HTTP 接口（第二期）+ 声明式 Agent（M2）。

- GET /agents：列出已启用的 Agent 定义（含 4 个专业 Agent）。
- POST /agents/{code}/run：同步运行指定 Agent；平台注册表 miss 时回落到
  本小院已发布的声明式 Agent（配置即 Agent，ADR-0005）。
- /agents/declarative*：小院侧声明式 Agent CRUD/发布/停用。
- /agents/admin/agent-review*：平台审核队列（platform_admin）。

Agent 实现由各模块的 agent.py 提供并在 import 时注册到 global_registry；
本路由只做鉴权/参数透传与结果封装，不含业务逻辑。
"""

from typing import Any

from fastapi import APIRouter, Body, Depends, File, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.deps import (
    CurrentUser,
    YardContext,
    get_current_user,
    get_db_session,
    get_yard_context,
    require_roles,
)
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.agents import (
    covers,
    declarative,
    get_global_registry,
    run_steps,
    run_stream,
    supervisor,
)
from app.modules.agents.definitions import AGENT_DEFINITIONS
from app.modules.agents.factory_router import router as factory_router
from app.modules.agents.schemas import AgentContext, AgentResult
from app.modules.agents.teams.router import router as teams_router
from app.modules.agents.teams.router import runs_router as team_runs_router
from app.modules.audit.service import log_audit
from app.modules.tools.registry import tool_registry

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentRunRequest(BaseModel):
    input: dict = Field(default_factory=dict, description="Agent 输入（各 Agent 的 input schema）")
    task_id: str | None = Field(default=None, description="可选：关联任务 ID，不传则自动生成")


_AGENT_MODULES = {
    "pest_alert": "app.modules.pest_alert.agent",
    "orchestrator": "app.modules.agents.orchestrator",
    "agriculture_qa": "app.modules.agriculture.qa_agent",
    "crop_diagnosis": "app.modules.crop_diagnosis.agent",
    "farm_planner": "app.modules.farm_planner.agent",
    "research_assistant": "app.modules.research_assistant.agent",
    "data_analyst": "app.modules.data_analyst.agent",
}


def _ensure_imported(code: str) -> None:
    """按 code 触发对应 Agent 模块的 import（触发自注册）。

    总控运行的工作流可引用任意专业 Agent，因此需保证全部模块已注册。
    """
    codes = _AGENT_MODULES if code == "orchestrator" else (code,)
    for item in codes:
        module_name = _AGENT_MODULES.get(item)
        if module_name:
            try:
                __import__(module_name)
            except ImportError:
                pass


def _skill_summaries(allowed_tools: list[str] | None) -> list[dict]:
    """把 Agent 的 allowed_tools code 解析为精简 skill 摘要。

    数据源：只读 ``definitions.py`` 的 allowed_tools + ``ToolRegistry`` 的实际注册；
    未在注册表中找到的 code 自动跳过（不改变任何注册逻辑）。
    """
    summaries: list[dict] = []
    for code in allowed_tools or []:
        try:
            tool = tool_registry.get(code)
        except AppError:
            continue
        summaries.append(
            {
                "code": tool.code,
                "name": tool.name,
                "description": tool.description,
                "risk_level": tool.risk_level,
            }
        )
    return summaries


@router.get("")
def list_agents(request: Request, ctx: YardContext = Depends(get_yard_context)):
    """列出已启用的 Agent 定义（小院内可见）。

    每个 Agent 附 ``skills`` 字段：出生自带技能的精简列表
    （code/name/description/risk_level），供前端"初始技能"区域展示。
    """
    # 触发全部专业 Agent 模块的自注册，保证 definitions 与 registry 一致
    for code in _AGENT_MODULES:
        _ensure_imported(code)
    agents = []
    for d in AGENT_DEFINITIONS:
        if not d.get("enabled", True):
            continue
        # 拷贝后再加字段，避免污染 AGENT_DEFINITIONS 共享结构
        enriched = dict(d)
        enriched["skills"] = _skill_summaries(d.get("allowed_tools"))
        agents.append(enriched)
    return ok({"agents": agents}, request)


@router.post("/{code}/run")
async def run_agent(
    code: str,
    body: AgentRunRequest,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db=Depends(get_db_session),
):
    """同步运行指定 Agent：平台注册表优先，miss 则回落本小院已发布声明式 Agent。"""
    import uuid

    _ensure_imported(code)
    registry = get_global_registry()
    try:
        agent = registry.get(code)
    except AppError:
        # 声明式回落：只认本小院 status=published 的配置（跨小院一律 404）
        return await _run_declarative_fallback(db, ctx, request, code, body)
    if not registry.is_enabled(code):
        raise AppError("AGENT_DISABLED", f"Agent 已停用：{code}。", http_status=403)

    agent_ctx = AgentContext(
        task_id=body.task_id or f"agent-{uuid.uuid4().hex[:12]}",
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        request_id=getattr(request.state, "request_id", "req_unknown"),
        allowed_tools=getattr(agent, "definition", {}).get("allowed_tools", []) or [],
        deadline_seconds=120,
        metadata={"db_session_id": id(db)},
        roles=list(ctx.roles),
    )
    # 把 db 会话挂到 metadata 供 Agent 内部取用（knowledge/llm 需要）
    agent_ctx.metadata["db"] = db

    result: AgentResult = await agent.run(dict(body.input or {}), agent_ctx)
    return ok(result.model_dump(), request)


async def _run_declarative_fallback(
    db, ctx: YardContext, request: Request, code: str, body: AgentRunRequest
) -> dict[str, Any]:
    """声明式 Agent 运行分支：记账口径与 knowledge_qa 同步问答一致。

    同步执行只写审计（action=agent_run）+ llm_calls 行（complete_with_logging
    内部落库并做配额），不建 tasks 行——异步任务不在本次范围。
    """
    import uuid

    decl = declarative.get_published_by_code(
        db, yard_id=ctx.yard_id, agent_code=code, user_id=ctx.user_id
    )
    if decl is None:
        raise AppError("AGENT_NOT_FOUND", f"未知的 Agent：{code}。", http_status=404)

    task_id = body.task_id or f"declarative-{uuid.uuid4().hex[:12]}"
    try:
        result = await declarative.run_declarative_agent(
            db, agent=decl, input_data=dict(body.input or {}), ctx=ctx, task_id=task_id
        )
    except AppError as e:
        declarative.log_run_audit(
            db,
            yard_id=ctx.yard_id,
            actor_id=ctx.user_id,
            agent=decl,
            task_id=task_id,
            request_id=ctx.request_id,
            result="failure",
            metadata={"error_code": e.code},
        )
        db.commit()
        raise
    declarative.log_run_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        agent=decl,
        task_id=task_id,
        request_id=ctx.request_id,
        result="success",
        metadata={"citations": len(result.citations), "model": result.output.get("model")},
    )
    db.commit()
    return ok(result.model_dump(), request)


# ---------------------------------------------------------------------------
# 声明式 Agent（M2）：小院侧 CRUD/发布 + 平台审核队列
# ---------------------------------------------------------------------------

# 平台审核闸门：仅 platform_admin
_platform_admin_gate = require_roles("platform_admin")


def _require_platform_admin(user: CurrentUser) -> None:
    """平台审核队列：必须是 platform_admin（小院角色一律 403）。"""
    if "platform_admin" not in user.roles:
        raise AppError("FORBIDDEN", "需要平台管理员权限。", http_status=403)


def _require_manage(db: Session, *, ctx: YardContext, agent_id: str) -> None:
    """写动作（编辑/复制/停用/删除/首图）的前置鉴权：管理员全量，成员限自建私有助手。

    跨小院/已软删除仍按 404（``get_agent`` 的口径），只在本院可见时才谈 403。
    """
    agent = declarative.get_agent(db, yard_id=ctx.yard_id, agent_id=agent_id)
    declarative.require_manage_access(agent, user_id=ctx.user_id, roles=list(ctx.roles))


@router.post("/declarative")
def create_declarative_agent(
    request: Request,
    body: dict[str, Any] = Body(...),
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db_session),
):
    """创建声明式 Agent（一律 draft；返回含服务端计算的 risk_level）。

    成员也能建：可见性由 ``_resolve_visibility`` 按角色判定——管理员可建 public
    或 private，普通成员只能建 private（"只能自己使用"），请求 public 直接 403
    而不是静默降级。
    """
    req = declarative.parse_create_request(body)
    try:
        agent = declarative.create_agent(
            db,
            yard_id=ctx.yard_id,
            actor_id=user.user_id,
            req=req,
            request_id=ctx.request_id,
            roles=list(user.roles),
        )
        db.commit()
    except AppError:
        db.rollback()
        raise
    return ok(declarative.to_out(agent, db), request)


@router.get("/declarative")
def list_declarative_agents(
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db_session),
):
    """本小院声明式 Agent 列表（含 draft；隐藏软删除；private 仅创建者可见）。"""
    agents = declarative.list_agents(db, yard_id=ctx.yard_id, user_id=user.user_id)
    return ok({"agents": declarative.to_out_list(db, agents)}, request)


@router.patch("/declarative/{agent_id}")
def update_declarative_agent(
    agent_id: str,
    request: Request,
    body: dict[str, Any] = Body(...),
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db_session),
):
    """编辑声明式 Agent：重算 risk_level，version 不变；跨小院 404。"""
    req = declarative.parse_update_request(body)
    _require_manage(db, ctx=ctx, agent_id=agent_id)
    try:
        agent = declarative.update_agent(
            db,
            yard_id=ctx.yard_id,
            actor_id=user.user_id,
            agent_id=agent_id,
            req=req,
            request_id=ctx.request_id,
            roles=list(user.roles),
        )
        db.commit()
    except AppError:
        db.rollback()
        raise
    return ok(declarative.to_out(agent, db), request)


@router.post("/declarative/{agent_id}/publish")
def publish_declarative_agent(
    agent_id: str,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db_session),
):
    """发布（分级）：低风险自批 published；高风险只能进 pending_platform_review。

    普通成员只能发布自己的低风险私有助手（``publish_agent`` 里判定），
    这样"@ 我自建的小助手"才成立；公开助手与高风险仍归管理员/平台。
    """
    try:
        agent = declarative.publish_agent(
            db,
            yard_id=ctx.yard_id,
            actor_id=user.user_id,
            agent_id=agent_id,
            roles=list(user.roles),
            request_id=ctx.request_id,
        )
        db.commit()
    except AppError:
        db.rollback()
        raise
    return ok(declarative.to_out(agent, db), request)


@router.post("/declarative/{agent_id}/duplicate")
def duplicate_declarative_agent(
    agent_id: str,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db_session),
):
    """复制助手：新编码 + ``原名-N`` 名称服务端生成，副本一律 draft。

    复制等于把 persona/模板/技能白名单整份读走，因此与编辑同权：管理员可复制
    任意助手，成员只能复制自己的私有助手。
    """
    _require_manage(db, ctx=ctx, agent_id=agent_id)
    try:
        agent = declarative.duplicate_agent(
            db,
            yard_id=ctx.yard_id,
            actor_id=user.user_id,
            agent_id=agent_id,
            request_id=ctx.request_id,
            roles=list(user.roles),
        )
        db.commit()
    except AppError:
        db.rollback()
        raise
    return ok(declarative.to_out(agent, db), request)


@router.post("/declarative/{agent_id}/deprecate")
def deprecate_declarative_agent(
    agent_id: str,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db_session),
):
    """停用：published 立即退出可运行态（管理员或私有助手创建者）。"""
    _require_manage(db, ctx=ctx, agent_id=agent_id)
    try:
        agent = declarative.deprecate_agent(
            db,
            yard_id=ctx.yard_id,
            actor_id=user.user_id,
            agent_id=agent_id,
            request_id=ctx.request_id,
        )
        db.commit()
    except AppError:
        db.rollback()
        raise
    return ok(declarative.to_out(agent, db), request)


@router.delete("/declarative/{agent_id}")
def delete_declarative_agent(
    agent_id: str,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db_session),
):
    """删除助手（管理员或私有助手创建者，本小院范围）：软删除 status='deleted'。

    行保留——chat 历史 choice_json、班组 members_json、运行 checkpoint 仍引用
    本行 id，硬删会在用户可见历史里留悬空引用；name 确定性改名释放唯一约束。
    """
    _require_manage(db, ctx=ctx, agent_id=agent_id)
    try:
        agent = declarative.delete_agent(
            db,
            yard_id=ctx.yard_id,
            actor_id=user.user_id,
            agent_id=agent_id,
            request_id=ctx.request_id,
        )
        db.commit()
    except AppError:
        db.rollback()
        raise
    return ok(declarative.to_out(agent, db), request)


@router.post("/declarative/{agent_id}/cover")
def upload_declarative_cover(
    agent_id: str,
    request: Request,
    file: UploadFile = File(...),
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db_session),
):
    """设置卡片首图（PNG/JPG/WEBP，≤2MB）：与编辑同权，出参带回新的 cover_image_url。"""
    try:
        agent = covers.set_cover(db, ctx=ctx, agent_id=agent_id, file=file)
        db.commit()
    except AppError:
        db.rollback()
        raise
    return ok(declarative.to_out(agent, db), request)


@router.get("/declarative/{agent_id}/cover")
def read_declarative_cover(
    agent_id: str,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db_session),
):
    """取首图字节（<img> 走登录态取图，未设图 404 让前端回落 emoji）。"""
    data, mime = covers.read_cover(db, ctx=ctx, agent_id=agent_id)
    return Response(
        content=data,
        media_type=mime,
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.delete("/declarative/{agent_id}/cover")
def clear_declarative_cover(
    agent_id: str,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db_session),
):
    """清除首图（连文件一起删）。"""
    try:
        agent = covers.clear_cover(db, ctx=ctx, agent_id=agent_id)
        db.commit()
    except AppError:
        db.rollback()
        raise
    return ok(declarative.to_out(agent, db), request)


@router.get("/admin/agent-review-queue")
def get_agent_review_queue(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db_session),
):
    """平台审核队列：所有小院 pending_platform_review 的声明式 Agent。"""
    _require_platform_admin(user)
    agents = declarative.list_pending_review_queue(db)
    return ok({"agents": declarative.to_out_list(db, agents)}, request)


@router.post("/admin/agent-review/{agent_id}/approve")
def approve_agent_review(
    agent_id: str,
    request: Request,
    user: CurrentUser = Depends(_platform_admin_gate),
    db=Depends(get_db_session),
):
    """平台批准 → published；审核人记入审计 metadata。"""
    _require_platform_admin(user)
    try:
        agent = declarative.review_approve(
            db,
            actor_id=user.user_id,
            agent_id=agent_id,
            request_id=getattr(request.state, "request_id", "req_unknown"),
        )
        db.commit()
    except AppError:
        db.rollback()
        raise
    return ok(declarative.to_out(agent, db), request)


@router.post("/admin/agent-review/{agent_id}/reject")
def reject_agent_review(
    agent_id: str,
    request: Request,
    user: CurrentUser = Depends(_platform_admin_gate),
    db=Depends(get_db_session),
):
    """平台驳回 → 退回 draft（决定与审核人入审计）。"""
    _require_platform_admin(user)
    try:
        agent = declarative.review_reject(
            db,
            actor_id=user.user_id,
            agent_id=agent_id,
            request_id=getattr(request.state, "request_id", "req_unknown"),
        )
        db.commit()
    except AppError:
        db.rollback()
        raise
    return ok(declarative.to_out(agent, db), request)


# ---------------------------------------------------------------------------
# 声明式 Agent 运行 checkpoint（P0 内核）：步骤列表 / 断点续跑 / SSE 步骤流
# ---------------------------------------------------------------------------


def _derive_run_status(steps: list[Any]) -> str:
    if any(s.step_type == "final" and s.status == "ok" for s in steps):
        return "succeeded"
    if any(s.step_type == "final" for s in steps):
        return "failed"
    if any(s.step_type == "llm_call" and s.status != "ok" for s in steps):
        return "failed"
    return "running"


@router.get("/declarative/runs/{run_id}/steps")
def get_run_steps(
    run_id: str,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db=Depends(get_db_session),
):
    """运行步骤列表（小院成员可读；跨小院/不存在一律 404 防枚举）。"""
    steps = run_steps.list_steps(db, yard_id=ctx.yard_id, run_id=run_id)
    if not steps:
        raise AppError("RUN_NOT_FOUND", "运行不存在或无权访问。", http_status=404)
    return ok(
        {
            "run_id": run_id,
            "status": _derive_run_status(steps),
            "steps": [run_steps.step_out(s) for s in steps],
        },
        request,
    )


@router.get("/declarative/runs/{run_id}/events/stream")
def stream_run_steps(
    run_id: str,
    ctx: YardContext = Depends(get_yard_context),
):
    """SSE 步骤事件流（追尾 agent_run_steps，帧协议同 tasks/stream）。

    说明：声明式同步运行不建 tasks 行（记账口径），故复用 tasks/stream 的
    「DB 追尾机制 + 帧协议」而非其 TaskEvent 通道；worker/inline 线程执行时
    可实时观看，纯同步执行可用 steps GET 端点事后查看全量步骤。
    """
    return run_stream.build_stream_response(ctx.yard_id, run_id)


@router.post("/declarative/{agent_code}/runs/{run_id}/retry")
async def retry_run_from_checkpoint(
    agent_code: str,
    run_id: str,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db_session),
):
    """断点续跑：从失败步继续（幂等防副作用重放）。

    语义边界：
    - 已 ok 的 tool_call 步 **不重复执行**，直接复用 output_json 回填会话；
    - 失败的 llm_call 步原位重跑（该步 checkpoint 行被新结果覆盖）；
    - 已 completed（final ok）的运行拒绝重放（409）；
    - 审计 action=retry_from_step（记录续跑点与副作用重放保护计数）。
    """
    decl = declarative.get_published_by_code(
        db, yard_id=ctx.yard_id, agent_code=agent_code, user_id=user.user_id
    )
    if decl is None:
        raise AppError("AGENT_NOT_FOUND", f"未知的 Agent：{agent_code}。", http_status=404)
    steps = run_steps.list_steps(db, yard_id=ctx.yard_id, run_id=run_id)
    if not steps:
        raise AppError("RUN_NOT_FOUND", "运行不存在或无权访问。", http_status=404)
    if run_steps.is_run_completed(steps):
        raise AppError(
            "RUN_ALREADY_COMPLETED", "该运行已完成，不允许断点续跑重放。", http_status=409
        )
    first_meta = steps[0].input_json or {}
    if first_meta.get("agent_id") and first_meta["agent_id"] != decl.id:
        raise AppError(
            "RUN_CHECKPOINT_INVALID",
            "该运行属于该编码的另一版本配置，请对原版本发起新运行。",
            http_status=409,
        )
    from_step_index = next(
        (s.step_index for s in steps if s.step_type == "llm_call" and s.status != "ok"),
        len(steps),
    )
    base_meta: dict[str, Any] = {
        "run_id": run_id,
        "agent_code": agent_code,
        "from_step_index": from_step_index,
        "replayed_steps": sum(1 for s in steps if s.status == "ok"),
        "skipped_tool_side_effects": sum(
            1 for s in steps if s.step_type == "tool_call" and s.status == "ok"
        ),
    }
    try:
        result = await declarative.run_declarative_agent(
            db, agent=decl, input_data={}, ctx=ctx, task_id=run_id, resume_run_id=run_id
        )
    except AppError as e:
        log_audit(
            db,
            yard_id=ctx.yard_id,
            actor_id=user.user_id,
            action="retry_from_step",
            resource_type="yard_agent",
            resource_id=decl.id,
            result="failure",
            request_id=ctx.request_id,
            metadata={**base_meta, "error_code": e.code},
        )
        db.commit()
        raise
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=user.user_id,
        action="retry_from_step",
        resource_type="yard_agent",
        resource_id=decl.id,
        result="success",
        request_id=ctx.request_id,
        metadata={**base_meta, "truncated": bool(result.output.get("truncated"))},
    )
    db.commit()
    return ok(result.model_dump(), request)


# ---------------------------------------------------------------------------
# Agent 工厂（M3）：/agents/factory*（行业描述 → 蓝图 → 人工采纳 → 分级装配）。
# 子路由并入本 mount，api/v1 总路由无需感知工厂端点。
# ---------------------------------------------------------------------------

router.include_router(factory_router)

# ---------------------------------------------------------------------------
# 总控问答入口（W5b）：/agents/supervisor/{ask,candidates}
# 字面路径先于 /teams* 与 /{code}/run 注册，避免路径歧义。
# ---------------------------------------------------------------------------


class SupervisorAskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)


@router.post("/supervisor/ask")
async def supervisor_ask(
    body: SupervisorAskRequest,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db=Depends(get_db_session),
):
    """问总控：规则→LLM→兜底三级路由到本小院最合适的助手/班组并执行。"""
    data = await supervisor.ask(db, ctx=ctx, question=body.question)
    return ok(data, request)


@router.get("/supervisor/candidates")
def supervisor_candidates(
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db=Depends(get_db_session),
):
    """当前候选集与各候选近 7 天调用数（调试/前端展示）。"""
    cands = supervisor.list_candidates(db, ctx.yard_id, user_id=ctx.user_id)
    counts = supervisor._recent_call_counts(db, ctx.yard_id, cands)
    items = [
        {
            "kind": c["kind"],
            "id": c["id"],
            "code": c["code"],
            "name": c["name"],
            "blurb": c["blurb"],
            "recent_calls": counts.get(c["code"] or c["id"], 0),
        }
        for c in cands
    ]
    return ok({"candidates": items}, request)


# ---------------------------------------------------------------------------
# 班组协作（W2）：/agents/teams* 与 /agents/team-runs*（顺序接力 + critic
# 审校 + 成员级断点续跑）。同属 agents 域，随本 mount 一并暴露。
# ---------------------------------------------------------------------------

router.include_router(teams_router)
router.include_router(team_runs_router)
