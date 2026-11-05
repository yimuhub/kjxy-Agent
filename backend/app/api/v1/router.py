"""API v1 总路由：挂载各模块 router。"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.deps import get_db_session
from app.core.responses import ok
from app.modules.a2a.router import router as a2a_router
from app.modules.admin.router import router as admin_router
from app.modules.agents.router import router as agents_router
from app.modules.agriculture.router import router as agriculture_router
from app.modules.asr.router import router as asr_router
from app.modules.audit.router import router as audit_router
from app.modules.auth.router import router as auth_router
from app.modules.auth.sso_router import router as sso_router
from app.modules.bots.router import router as bots_router
from app.modules.branding.router import router as branding_router
from app.modules.chat.router import media_router as chat_media_router
from app.modules.chat.router import mention_router as chat_mention_router
from app.modules.chat.router import router as chat_router
from app.modules.cluster.router import router as cluster_router
from app.modules.commander.router import router as commander_router
from app.modules.devices.router import router as devices_router
from app.modules.evolution.router import router as evolution_router
from app.modules.farm.agent_router import router as farm_agent_router
from app.modules.farm.router import router as farm_router
from app.modules.growth.router import router as growth_router
from app.modules.hubs.router import router as hubs_router
from app.modules.journal.router import router as journal_router
from app.modules.knowledge.router import router as knowledge_router
from app.modules.library.router import router as library_router
from app.modules.llm.router import router as llm_router
from app.modules.local_runtime.router import router as local_runtime_router
from app.modules.market.router import router as market_router
from app.modules.mcp.router import router as mcp_router
from app.modules.memory.router import router as memory_router
from app.modules.org.router import router as org_router
from app.modules.pairing.router import router as pairing_router
from app.modules.plugins.router import router as plugins_router
from app.modules.policy.router import router as policy_router
from app.modules.push.router import router as push_router
from app.modules.quick_actions.router import router as quick_actions_router
from app.modules.quota.router import router as quota_router
from app.modules.search.router import router as search_router
from app.modules.self_improve.router import router as self_improve_router
from app.modules.share.router import public_router as share_public_router
from app.modules.share.router import router as share_router
from app.modules.system.router import router as system_router
from app.modules.tasks.router import router as tasks_router
from app.modules.tools.approvals_router import router as tool_approvals_router
from app.modules.tools.router import router as tools_router
from app.modules.workspace.router import router as workspace_router
from app.modules.yards.invite_router import router as yard_invite_router
from app.modules.yards.owner_router import router as yard_owner_router
from app.modules.yards.permissions_router import router as yard_permissions_router
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
router.include_router(yard_permissions_router)  # 逐人授权：/yards/current/members/{id}/permissions
router.include_router(yard_signup_router)
router.include_router(yard_invite_router)
router.include_router(yard_settings_router)  # /yards/current/settings|roles（系统设置 + 角色权限）
router.include_router(tasks_router)
router.include_router(knowledge_router)
router.include_router(library_router)  # /library（官方技能库/插件库：清单/同步/更新/回滚 v3.35）
router.include_router(agriculture_router)
router.include_router(farm_router)  # /farm（农业台账：地块/周期/农事记录/报表）
router.include_router(farm_agent_router)  # /farm/automation（Agent 自动执行 v3.28）
router.include_router(growth_router)  # /growth/posters（海报出图与签名取图）
router.include_router(quick_actions_router)  # /quick-actions（一句话业务：预览/确认 v3.22）
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
router.include_router(cluster_router)  # /cluster/nodes（平台管理员：多集群登记与版本体检 v3.48）
router.include_router(agents_router)
router.include_router(org_router)  # /org（组织架构：单元树/岗位/Agent 绑定 v3.57）
router.include_router(local_runtime_router)  # /local-runtime（本机能力插件通道 v3.60）
router.include_router(workspace_router)  # /workspace（工作区插件 v3.63）
router.include_router(market_router)
router.include_router(devices_router)
router.include_router(push_router)  # /settings/push-channels（W4 事件推送通道）
router.include_router(mcp_router)  # /settings/mcp-servers（W5a MCP 工具服务器）
router.include_router(chat_router)  # /chat/sessions（对话模式：会话+消息，执行复用 supervisor）
router.include_router(chat_mention_router)  # /chat/mention-candidates（@ 点名候选集合资源）
router.include_router(chat_media_router)  # /chat/transcribe（语音转写，v3.11）
router.include_router(commander_router)  # /commander/runs（指挥官编排：执行/重试/取消）
router.include_router(memory_router)  # /commander/memories（记忆检索/列表/日复盘，v3.43）
router.include_router(asr_router)  # /asr/*（语音转写状态/本机模型/测试，v3.30）
router.include_router(quota_router)  # /quota/*（分级额度策略、申请与审批，v3.32）
router.include_router(pairing_router)  # /pairing（手机扫码配对：建码/交换/设备台账）
router.include_router(bots_router)  # /bots（IM 机器人网关：配置/测试/会话批准/账本）
router.include_router(search_router)  # /search（联网状态探测 + 连通性测试）
router.include_router(self_improve_router)  # /yards/current/self-improve*（自我总结提升 v3.4）
router.include_router(hubs_router)
router.include_router(branding_router)  # /branding（白标：logo/名称/主色/域名）+ /platform/grants
router.include_router(journal_router)
router.include_router(system_router)  # /system/logs（负责人查看脱敏运行日志，v3.41）


@router.get("/meta", tags=["meta"])
def meta(request: Request, db: Session = Depends(get_db_session)):
    """版本握手：前端据此识别「后端进程旧了」，而不是等用户点按钮才报 405。

    公开端点，只返回版本与数据库迁移 head，不含任何环境密钥。
    """
    try:
        head = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except SQLAlchemyError:
        # 未执行迁移的全新库/测试库没有 alembic_version：如实返回空
        db.rollback()
        head = None
    return ok({"version": request.app.version, "migration_head": head}, request)


@router.get("/ping", include_in_schema=False)
def ping():
    return {"data": {"pong": True}}
