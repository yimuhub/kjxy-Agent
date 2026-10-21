"""API v1 总路由：挂载各模块 router。"""

from fastapi import APIRouter

from app.modules.admin.router import router as admin_router
from app.modules.agents.router import router as agents_router
from app.modules.agriculture.router import router as agriculture_router
from app.modules.audit.router import router as audit_router
from app.modules.auth.router import router as auth_router
from app.modules.devices.router import router as devices_router
from app.modules.evolution.router import router as evolution_router
from app.modules.knowledge.router import router as knowledge_router
from app.modules.llm.router import router as llm_router
from app.modules.market.router import router as market_router
from app.modules.mcp.router import router as mcp_router
from app.modules.plugins.router import router as plugins_router
from app.modules.policy.router import router as policy_router
from app.modules.push.router import router as push_router
from app.modules.tasks.router import router as tasks_router
from app.modules.tenants.quota_router import router as tenant_quota_router
from app.modules.tenants.router import router as tenants_router
from app.modules.tenants.signup_router import router as tenant_signup_router
from app.modules.tools.approvals_router import router as tool_approvals_router
from app.modules.tools.router import router as tools_router

router = APIRouter()

router.include_router(auth_router)
router.include_router(tenants_router)
router.include_router(tenant_quota_router)
router.include_router(tenant_signup_router)
router.include_router(tasks_router)
router.include_router(knowledge_router)
router.include_router(agriculture_router)
router.include_router(evolution_router)
router.include_router(llm_router)  # 含 /llm/status 与供应商管理 /llm/providers*
router.include_router(tools_router)
router.include_router(tool_approvals_router)
router.include_router(audit_router)
router.include_router(policy_router)
router.include_router(plugins_router)
router.include_router(admin_router)
router.include_router(agents_router)
router.include_router(market_router)
router.include_router(devices_router)
router.include_router(push_router)  # /settings/push-channels（W4 事件推送通道）
router.include_router(mcp_router)  # /settings/mcp-servers（W5a MCP 工具服务器）


@router.get("/ping", include_in_schema=False)
def ping():
    return {"data": {"pong": True}}
