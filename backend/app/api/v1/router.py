"""API v1 总路由：挂载各模块 router。"""

from fastapi import APIRouter

from app.modules.a2a.router import router as a2a_router
from app.modules.admin.router import router as admin_router
from app.modules.agents.router import router as agents_router
from app.modules.agriculture.router import router as agriculture_router
from app.modules.audit.router import router as audit_router
from app.modules.auth.router import router as auth_router
from app.modules.auth.sso_router import router as sso_router
from app.modules.bots.router import router as bots_router
from app.modules.chat.router import media_router as chat_media_router
from app.modules.chat.router import mention_router as chat_mention_router
from app.modules.chat.router import router as chat_router
from app.modules.devices.router import router as devices_router
from app.modules.evolution.router import router as evolution_router
from app.modules.farm.router import router as farm_router
from app.modules.knowledge.router import router as knowledge_router
from app.modules.llm.router import router as llm_router
from app.modules.market.router import router as market_router
from app.modules.mcp.router import router as mcp_router
from app.modules.pairing.router import router as pairing_router
from app.modules.plugins.router import router as plugins_router
from app.modules.policy.router import router as policy_router
from app.modules.push.router import router as push_router
from app.modules.search.router import router as search_router
from app.modules.self_improve.router import router as self_improve_router
from app.modules.share.router import public_router as share_public_router
from app.modules.share.router import router as share_router
from app.modules.tasks.router import router as tasks_router
from app.modules.tools.approvals_router import router as tool_approvals_router
from app.modules.tools.router import router as tools_router
from app.modules.yards.invite_router import router as yard_invite_router
from app.modules.yards.owner_router import router as yard_owner_router
from app.modules.yards.quota_router import router as yard_quota_router
from app.modules.yards.router import router as yards_router
from app.modules.yards.settings_router import router as yard_settings_router
from app.modules.yards.signup_router import router as yard_signup_router

router = APIRouter()

router.include_router(auth_router)
router.include_router(sso_router)  # /auth/sso/*（公开）与 /settings/sso-providers（管理员）
router.include_router(a2a_router)  # /a2a/rpc（入站）与 /settings/a2a*（协作方管理）
router.include_router(yards_router)
router.include_router(yard_quota_router)
router.include_router(yard_owner_router)  # /yards/current/owner（负责人驾驶舱/授权/转让）
router.include_router(yard_signup_router)
router.include_router(yard_invite_router)
router.include_router(yard_settings_router)  # /yards/current/settings|roles（系统设置 + 角色权限）
router.include_router(tasks_router)
router.include_router(knowledge_router)
router.include_router(agriculture_router)
router.include_router(farm_router)  # /farm（农业台账：地块/周期/农事记录/报表）
router.include_router(share_router)  # /shares（跨院只读分享：创建/列表/撤销）
router.include_router(share_public_router)  # /public/shares/{token}（匿名只读快照）
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
router.include_router(chat_router)  # /chat/sessions（对话模式：会话+消息，执行复用 supervisor）
router.include_router(chat_mention_router)  # /chat/mention-candidates（@ 点名候选集合资源）
router.include_router(chat_media_router)  # /chat/transcribe（语音转写，v3.11）
router.include_router(pairing_router)  # /pairing（手机扫码配对：建码/交换/设备台账）
router.include_router(bots_router)  # /bots（IM 机器人网关：配置/测试/会话批准/账本）
router.include_router(search_router)  # /search（联网状态探测 + 连通性测试）
router.include_router(self_improve_router)  # /yards/current/self-improve*（自我总结提升 v3.4）


@router.get("/ping", include_in_schema=False)
def ping():
    return {"data": {"pong": True}}
