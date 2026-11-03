"""技能 / 插件市场 HTTP 接口。

- GET  /market/catalog：内置种子包目录；risk_level 服务端用 assess_risk 现算；
- POST /market/install：按编码装载 Agent 种子包（本次沿用既有实现）；
- POST /market/items/search、GET /market/items/{item_key}：内置农业技能 +
  market_items 缓存检索/详情，显式要求“网上/开源”时才调 GitHub；
- POST /market/items/{item_key}/install：技能/插件安装（离线可用）；
- GET  /market/installed：本小院已安装/导入摘要；
- POST /market/skills/import、/market/plugins/import：本地文件导入（默认待审）；
- POST /market/github/search、/preview：GitHub 只读检索与 SKILL.md 预览；
- POST /market/agent/request、/generate、/install：一句话技能/插件链路
  （检索→生成→安装；generate 按 kind 分流，plugin 一律落待审）。

权限：市场动作走 require_yard_permission("market:install")；插件导入额外要求
"market:review"；一句话生成走 "skills:generate"（只产私有草稿）。
归属只认登录上下文，请求体禁带 yard_id。

（Get catalog/install 的 yard_admin 闸门为既有能力市场口径。）
"""

from typing import Any

from fastapi import APIRouter, Body, Depends, File, Request, UploadFile
from sqlalchemy.orm import Session

from app.core.deps import (
    CurrentUser,
    YardContext,
    get_current_user,
    get_db_session,
    get_yard_context,
    require_roles,
    require_yard_permission,
)
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.agents import declarative
from app.modules.evolution.models import Skill
from app.modules.market import installer, skill_agent
from app.modules.market.github_source import preview_skill_from_repo, search_github_skills
from app.modules.market.importer import (
    MAX_PLUGIN_BYTES,
    MAX_SKILL_BYTES,
    import_plugin_zip,
    import_skill_file,
)
from app.modules.market.seeds import SEED_AGENTS, SEED_CODE_PREFIX
from app.modules.market.service import (
    MAX_INSTALL_CODES,
    install_seed_codes,
    installed_codes,
)
from app.modules.plugins.models import PluginManifest

router = APIRouter(prefix="/market", tags=["market"])

# 小院侧闸门：yard_admin（platform_admin 直通，ADR-0005 分级审核口径）
_yard_admin_gate = require_roles("yard_admin")

# 市场安装/审核权限点（DB 角色覆盖 + 个人授权，唯一权限源）
_market_install_gate = require_yard_permission("market:install")
_market_review_gate = require_yard_permission("market:review")
# 一句话技能生成：普通成员/操作员/专家均有（只产私有草稿，不改小院公共库）
_skills_generate_gate = require_yard_permission("skills:generate")

ITEM_TYPES = {"skill", "plugin", "agent"}
ITEM_SOURCES = {"builtin", "remote", "github", "local"}


def _installed_codes(db: Session, yard_id: str) -> set[str]:
    """本小院已存在的种子相关编码（含小院自建同码——一律视为已占用）。"""
    return installed_codes(db, yard_id)


@router.get("/catalog")
def get_catalog(
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_yard_admin_gate),
    db: Session = Depends(get_db_session),
):
    """种子包目录：风险服务端现算，installed 只反映本小院（跨小院不可见）。"""
    installed_codes = _installed_codes(db, ctx.yard_id)
    items = []
    for seed in SEED_AGENTS:
        code = str(seed["agent_code"])
        items.append(
            {
                "agent_code": code,
                "name": seed["name"],
                "emoji": seed["emoji"],
                "description": seed["description"],
                "domain": seed["domain"],
                "suggested_questions": list(seed["suggested_questions"]),
                # 宁严勿松：不读种子自报，用同一把尺子（assess_risk）现场判定
                "risk_level": declarative.assess_risk(
                    list(seed["allowed_tools"]), list(seed["knowledge_scopes"])
                ),
                "installed": code in installed_codes or SEED_CODE_PREFIX + code in installed_codes,
            }
        )
    return ok({"items": items}, request)


def _parse_install_request(body: dict[str, Any]) -> list[str]:
    """解析并去重 codes；归属字段注入 → 422，其余非法 → 400（与 declarative 口径一致）。"""
    if not isinstance(body, dict):
        raise AppError("INVALID_REQUEST", "请求体必须为 JSON 对象。", http_status=400)
    if "yard_id" in body:
        raise AppError(
            "VALIDATION_ERROR",
            "请求体不允许携带 yard_id：小院归属由登录上下文决定。",
            http_status=422,
        )
    codes_raw = body.get("codes")
    if not isinstance(codes_raw, list) or not codes_raw:
        raise AppError("INVALID_REQUEST", "codes 必须为 1-20 个种子编码的数组。", http_status=400)
    if len(codes_raw) > MAX_INSTALL_CODES:
        raise AppError(
            "INVALID_REQUEST", f"单次最多装载 {MAX_INSTALL_CODES} 个种子包。", http_status=400
        )
    codes: list[str] = []
    for raw in codes_raw:
        code = str(raw).strip()
        if not code:
            raise AppError("INVALID_REQUEST", "codes 不允许为空项。", http_status=400)
        if code not in codes:  # 同请求内去重：重复项不会造成重复创建
            codes.append(code)
    return codes


@router.post("/install")
def install_seeds(
    request: Request,
    body: dict[str, Any] = Body(...),
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_yard_admin_gate),
    db: Session = Depends(get_db_session),
):
    """装载种子包：逐码 创建→发布→审计→提交；已存在同码 skipped 不覆盖。"""
    codes = _parse_install_request(body)
    result = install_seed_codes(
        db,
        yard_id=ctx.yard_id,
        actor_id=user.user_id,
        roles=list(user.roles),
        codes=codes,
        request_id=getattr(request.state, "request_id", ""),
    )
    return ok(result, request)


# ---------------------------------------------------------------------------
# 技能/插件市场（内置种子 + 缓存 + GitHub + 本地导入）
# ---------------------------------------------------------------------------


def _body_dict(body: dict[str, Any] | None) -> dict[str, Any]:
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise AppError("INVALID_REQUEST", "请求体必须为 JSON 对象。", http_status=400)
    return body


def _installed_for(db: Session, ctx: YardContext, item: Any) -> bool:
    if item.item_type == "skill":
        return (
            db.query(Skill.id)
            .filter(Skill.yard_id == ctx.yard_id, Skill.skill_code == item.code)
            .first()
            is not None
        )
    if item.item_type == "plugin":
        return (
            db.query(PluginManifest.id)
            .filter(
                PluginManifest.plugin_id == item.code,
                PluginManifest.yard_id == ctx.yard_id,
            )
            .first()
            is not None
        )
    return False


@router.post("/items/search")
def search_market_items(
    request: Request,
    body: dict[str, Any] | None = Body(default=None),
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_market_install_gate),
    db: Session = Depends(get_db_session),
):
    """检索市场条目：默认只查内置与本地缓存；显式要“网上/开源”才联网。"""
    data = _body_dict(body)
    query = str(data.get("query") or "").strip()
    item_type = data.get("item_type")
    source = data.get("source")
    if item_type is not None and item_type not in ITEM_TYPES:
        raise AppError("INVALID_REQUEST", "item_type 仅支持 skill/plugin/agent。", http_status=400)
    if source is not None and source not in ITEM_SOURCES:
        raise AppError(
            "INVALID_REQUEST", "source 仅支持 builtin/remote/github/local。", http_status=400
        )
    try:
        limit = int(data.get("limit") or 20)
    except (TypeError, ValueError):
        raise AppError("INVALID_REQUEST", "limit 必须为整数。", http_status=400) from None
    result = installer.search_items(
        db,
        ctx,
        query=query,
        item_type=item_type,
        source=source,
        limit=limit,
    )
    # GitHub 候选需要落缓存，供下一次安装请求按 item_key 命中
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(result, request)


@router.get("/items/{item_key}")
def get_market_item(
    item_key: str,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_market_install_gate),
    db: Session = Depends(get_db_session),
):
    """条目详情（含 definition/manifest 正文，供安装前预览）。"""
    item = installer.get_item(db, item_key)
    if item is None or not hasattr(item, "item_type"):
        raise AppError(
            "MARKET_ITEM_NOT_FOUND",
            "市场条目不存在或已下架。",
            http_status=404,
            details={"item_key": item_key},
        )
    out = installer.serialize_item(item, include_definition=True)
    out["installed"] = _installed_for(db, ctx, item)
    return ok(out, request)


@router.post("/items/{item_key}/install")
def install_market_item(
    item_key: str,
    request: Request,
    body: dict[str, Any] | None = Body(default=None),
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_market_install_gate),
    db: Session = Depends(get_db_session),
):
    """安装条目：skill 内置/已核验直接发布，其余待审；插件仅管理员/负责人。"""
    _body_dict(body)  # 仅做类型校验；归属与角色只认登录上下文
    try:
        result = installer.install_item(db, ctx, item_key, actor_role=list(user.roles or ctx.roles))
        db.commit()
    except AppError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    return ok(result, request)


@router.get("/installed")
def list_installed_items(
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_market_install_gate),
    db: Session = Depends(get_db_session),
):
    """本小院已安装/导入的 Skill 与插件摘要。"""
    return ok(installer.list_installed(db, ctx.yard_id), request)


@router.post("/skills/import")
def import_skill(
    request: Request,
    file: UploadFile = File(...),
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_market_install_gate),
    db: Session = Depends(get_db_session),
):
    """上传 .md/.markdown/.txt 技能文件（≤256KB），默认生成待审 Skill。"""
    data = file.file.read(MAX_SKILL_BYTES + 1)
    try:
        result = import_skill_file(db, ctx, file.filename or "", data)
        db.commit()
    except AppError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    return ok(result, request)


@router.post("/plugins/import")
def import_plugin(
    request: Request,
    file: UploadFile = File(...),
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_market_install_gate),
    reviewer: CurrentUser = Depends(_market_review_gate),
    db: Session = Depends(get_db_session),
):
    """上传插件 zip（≤2MB）：需 market:install + market:review，落地待审插件。"""
    data = file.file.read(MAX_PLUGIN_BYTES + 1)
    try:
        result = import_plugin_zip(db, ctx, file.filename or "", data)
        db.commit()
    except AppError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    return ok(result, request)


@router.post("/github/search")
def github_search(
    request: Request,
    body: dict[str, Any] | None = Body(default=None),
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_market_install_gate),
    db: Session = Depends(get_db_session),
):
    """GitHub 仓库检索（服务端代理，绝不回传 token）；离线时返回空 + warning。"""
    data = _body_dict(body)
    query = str(data.get("query") or "").strip()
    try:
        limit = int(data.get("limit") or 10)
    except (TypeError, ValueError):
        raise AppError("INVALID_REQUEST", "limit 必须为整数。", http_status=400) from None
    try:
        items = search_github_skills(query, limit=limit)
    except AppError as e:
        if e.code == "NETWORK_UNAVAILABLE":
            return ok({"items": [], "offline": True, "warning": e.message}, request)
        raise
    rows = installer.cache_github_candidates(db, items)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(
        {
            "items": [installer.serialize_item(row) for row in rows],
            "offline": False,
            "warning": None,
        },
        request,
    )


@router.post("/github/preview")
def github_preview(
    request: Request,
    body: dict[str, Any] | None = Body(default=None),
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_market_install_gate),
    db: Session = Depends(get_db_session),
):
    """预览仓库 SKILL.md 并生成 definition_json（只读文本，不执行脚本）。"""
    data = _body_dict(body)
    repo = str(data.get("repo") or "").strip()
    if not repo:
        raise AppError("INVALID_REQUEST", "repo 不能为空。", http_status=400)
    ref = str(data.get("ref") or "").strip() or None
    preview = preview_skill_from_repo(repo, ref=ref)
    # 预览即落库为可安装条目，前端确认后可直接安装（技能仍进待审）
    preview["item_key"] = installer.upsert_github_preview(db, preview)
    db.commit()
    return ok(preview, request)


# ---------------------------------------------------------------------------
# 一句话技能链路（v3.2）：检索 → 生成 → 安装
# ---------------------------------------------------------------------------


@router.get("/official/packs")
def official_packs_list(
    request: Request,
    types: str = "",
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """官方技能包目录：带本院启用状态与画像推荐标记（登录即可看）。"""
    from app.modules.market import official_packs

    types_list = [t.strip() for t in (types or "").split(",") if t.strip()]
    return ok(official_packs.list_official_packs(db, ctx.yard_id, types_list), request)


@router.post("/official/packs/{pack_key}/sync")
def official_pack_sync(
    pack_key: str,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_market_install_gate),
    db: Session = Depends(get_db_session),
):
    """一键启用官方技能包：published 直通、幂等（负责人/管理员，market:install）。"""
    from app.modules.market import official_packs

    result = official_packs.sync_official_pack(db, ctx, pack_key)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(result, request)


@router.post("/agent/request")
def agent_request(
    request: Request,
    body: dict[str, Any] | None = Body(default=None),
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_skills_generate_gate),
    db: Session = Depends(get_db_session),
):
    """一句话检索：本地内置/缓存优先，无命中再试 GitHub（离线即降级本地）。"""
    data = _body_dict(body)
    utterance = str(data.get("utterance") or "").strip()
    result = skill_agent.request_skill(db, ctx=ctx, utterance=utterance)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(result, request)


@router.post("/agent/generate")
async def agent_generate(
    request: Request,
    body: dict[str, Any] | None = Body(default=None),
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_skills_generate_gate),
    db: Session = Depends(get_db_session),
):
    """一句话生成（kind=skill 技能草稿 / kind=plugin 待审工作流插件）。"""
    data = _body_dict(body)
    utterance = str(data.get("utterance") or "").strip()
    use_web = data.get("use_web", True)
    kind = str(data.get("kind") or "skill").strip().lower() or "skill"
    if kind == "plugin":
        from app.modules.market import plugin_agent

        result = await plugin_agent.generate_plugin(
            db, ctx=ctx, utterance=utterance, use_web=bool(use_web)
        )
    else:
        result = await skill_agent.generate_skill(
            db, ctx=ctx, utterance=utterance, use_web=bool(use_web)
        )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(result, request)


@router.post("/agent/install")
def agent_install(
    request: Request,
    body: dict[str, Any] | None = Body(default=None),
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_market_install_gate),
    db: Session = Depends(get_db_session),
):
    """安装技能：按 item_key 装市场条目，或按 skill_id 把自己的私有草稿送审。"""
    data = _body_dict(body)
    result = skill_agent.install_skill(db, ctx=ctx, payload=data)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(result, request)
