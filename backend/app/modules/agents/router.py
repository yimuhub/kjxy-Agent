"""专业 Agent HTTP 接口（第二期）。

- GET /agents：列出已启用的 Agent 定义（含 4 个专业 Agent）。
- POST /agents/{code}/run：同步运行指定 Agent，输入为各 Agent 的 input schema。

Agent 实现由各模块的 agent.py 提供并在 import 时注册到 global_registry；
本路由只做鉴权/参数透传与结果封装，不含业务逻辑。
"""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.core.deps import TenantContext, get_db_session, get_tenant_context
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.agents import get_global_registry
from app.modules.agents.definitions import AGENT_DEFINITIONS
from app.modules.agents.schemas import AgentContext, AgentResult
from app.modules.tools.registry import tool_registry

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentRunRequest(BaseModel):
    input: dict = Field(default_factory=dict, description="Agent 输入（各 Agent 的 input schema）")
    task_id: str | None = Field(default=None, description="可选：关联任务 ID，不传则自动生成")


def _ensure_imported(code: str) -> None:
    """按 code 触发对应 Agent 模块的 import（触发自注册）。"""
    module_by_code = {
        "crop_diagnosis": "app.modules.crop_diagnosis.agent",
        "farm_planner": "app.modules.farm_planner.agent",
        "research_assistant": "app.modules.research_assistant.agent",
        "data_analyst": "app.modules.data_analyst.agent",
    }
    module_name = module_by_code.get(code)
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
def list_agents(request: Request, ctx: TenantContext = Depends(get_tenant_context)):
    """列出已启用的 Agent 定义（租户内可见）。

    每个 Agent 附 ``skills`` 字段：出生自带技能的精简列表
    （code/name/description/risk_level），供前端"初始技能"区域展示。
    """
    # 触发全部专业 Agent 模块的自注册，保证 definitions 与 registry 一致
    for code in ("crop_diagnosis", "farm_planner", "research_assistant", "data_analyst"):
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
    ctx: TenantContext = Depends(get_tenant_context),
    db=Depends(get_db_session),
):
    """同步运行指定 Agent。"""
    import uuid

    _ensure_imported(code)
    registry = get_global_registry()
    try:
        agent = registry.get(code)
    except AppError:
        raise AppError("AGENT_NOT_FOUND", f"未知的 Agent：{code}。", http_status=404) from None
    if not registry.is_enabled(code):
        raise AppError("AGENT_DISABLED", f"Agent 已停用：{code}。", http_status=403)

    agent_ctx = AgentContext(
        task_id=body.task_id or f"agent-{uuid.uuid4().hex[:12]}",
        tenant_id=ctx.tenant_id,
        actor_id=ctx.user_id,
        request_id=getattr(request.state, "request_id", "req_unknown"),
        allowed_tools=getattr(agent, "definition", {}).get("allowed_tools", []) or [],
        deadline_seconds=120,
        metadata={"db_session_id": id(db)},
    )
    # 把 db 会话挂到 metadata 供 Agent 内部取用（knowledge/llm 需要）
    agent_ctx.metadata["db"] = db

    result: AgentResult = await agent.run(dict(body.input or {}), agent_ctx)
    return ok(result.model_dump(), request)
