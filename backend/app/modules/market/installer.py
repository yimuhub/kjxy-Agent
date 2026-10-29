"""市场检索与安装：内置种子 + 本地缓存 + 可选 GitHub 检索。

安装状态机：
- skill：内置或已核验 → published；GitHub/远程/本地 → pending_review；
- plugin：一律 pending_review，且只有 yard_admin/yard_owner/platform_admin 可装；
- agent：本轮委托现有种子装载服务，未命中编码直接报错。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.audit.service import log_audit
from app.modules.evolution.models import Skill
from app.modules.evolution.service import attest_published_skill, create_skill
from app.modules.growth.skills import builtin_items as growth_builtin_items
from app.modules.growth.skills import seed_growth_skills
from app.modules.market.agri_skills import builtin_items, seed_builtin
from app.modules.market.github_source import preview_skill_from_repo, search_github_skills
from app.modules.market.importer import unique_plugin_id, unique_skill_code
from app.modules.market.models import MarketItem
from app.modules.market.skilldoc import slugify
from app.modules.plugins.models import PluginManifest
from app.modules.plugins.service import compute_checksum, validate_manifest

log = get_logger(__name__)

STATUS_AVAILABLE = "available"
STATUS_INSTALLED = "installed"
PLUGIN_INSTALL_ROLES = frozenset({"yard_admin", "yard_owner", "platform_admin"})
ONLINE_HINTS = ("网上", "开源", "github", "互联网", "在线", "远程", "网络")


def _role_set(actor_role: str | list[str] | tuple[str, ...] | None, ctx: YardContext) -> set[str]:
    if actor_role is None:
        return set(ctx.roles or [])
    if isinstance(actor_role, str):
        return {actor_role}
    return {str(r) for r in actor_role}


def _effective_roles(
    db: Session,
    ctx: YardContext,
    actor_role: str | list[str] | tuple[str, ...] | None,
) -> set[str]:
    """插件安装角色：DB 成员角色优先（防 JWT 角色过期提权），查不到时回退入参。"""
    roles = _role_set(actor_role, ctx)
    if "platform_admin" in roles:
        return roles
    from app.modules.yards.authz import get_active_membership, is_owner

    actual: set[str] = set()
    if is_owner(db, ctx.yard_id, ctx.user_id):
        actual.add("yard_owner")
    membership = get_active_membership(db, ctx.yard_id, ctx.user_id)
    if membership is not None:
        actual.add(membership.role)
    return actual or roles


def _installed_index(db: Session, yard_id: str) -> tuple[set[str], set[str]]:
    skill_codes = {
        str(row[0]) for row in db.query(Skill.skill_code).filter(Skill.yard_id == yard_id).all()
    }
    plugin_ids = {
        str(row[0])
        for row in db.query(PluginManifest.plugin_id)
        .filter(or_(PluginManifest.yard_id == yard_id, PluginManifest.yard_id.is_(None)))
        .all()
    }
    return skill_codes, plugin_ids


def _getter(item: MarketItem | dict):
    if isinstance(item, dict):
        return item.get
    return lambda k, d=None: getattr(item, k, d)


def serialize_item(
    item: MarketItem | dict,
    *,
    installed: bool = False,
    include_definition: bool = False,
) -> dict[str, Any]:
    get = _getter(item)
    data: dict[str, Any] = {
        "item_key": get("item_key"),
        "item_type": get("item_type"),
        "source": get("source"),
        "code": get("code"),
        "name": get("name"),
        "version": get("version"),
        "description": get("description"),
        "source_url": get("source_url"),
        "license": get("license"),
        "author": get("author"),
        "risk_level": get("risk_level"),
        "status": get("status"),
        "verified": bool(get("verified")),
        "installed": installed,
    }
    metadata = get("metadata_json") or {}
    if isinstance(metadata, dict):
        for key in ("stars", "repo", "tags", "attribution"):
            if key in metadata:
                data[key] = metadata[key]
    if include_definition:
        data["definition_json"] = get("definition_json")
        data["manifest_json"] = get("manifest_json")
    return data


def _matches(query: str, *fields: str | None) -> bool:
    tokens = [t for t in (query or "").lower().split() if t]
    if not tokens:
        return True
    haystack = " ".join(str(f or "") for f in fields).lower()
    return all(token in haystack for token in tokens)


def _wants_online(query: str, source: str | None) -> bool:
    if source == "github":
        return True
    if source in {"builtin", "local", "remote"}:
        return False
    lowered = (query or "").lower()
    return any(hint in lowered for hint in ONLINE_HINTS)


def cache_github_candidates(db: Session, results: list[dict[str, Any]]) -> list[MarketItem]:
    """把 GitHub 检索结果 upsert 进市场缓存，返回可安装的条目。"""
    rows: list[MarketItem] = []
    for result in results:
        repo = str(result.get("repo") or "")
        code = slugify(repo.replace("/", "-"), fallback="github-skill")
        item_key = f"github:skill:{code}"
        row = db.query(MarketItem).filter(MarketItem.item_key == item_key).first()
        payload: dict[str, Any] = {
            "name": repo.split("/")[-1] or code,
            "description": str(result.get("description") or ""),
            "source_url": str(result.get("html_url") or f"https://github.com/{repo}"),
            "license": result.get("license"),
            "metadata_json": {
                "stars": int(result.get("stars") or 0),
                "repo": repo,
                "default_branch": result.get("default_branch") or "main",
                "updated_at": result.get("updated_at"),
                "tags": ["github", "agriculture"],
            },
        }
        if row is None:
            row = MarketItem(
                item_key=item_key,
                item_type="skill",
                source="github",
                code=code,
                name=payload["name"],
                version="latest",
                description=payload["description"],
                source_url=payload["source_url"],
                license=payload["license"],
                author=repo.split("/")[0],
                risk_level="medium",
                status=STATUS_AVAILABLE,
                definition_json=None,
                manifest_json=None,
                metadata_json=payload["metadata_json"],
                verified=False,
            )
            db.add(row)
            rows.append(row)
            continue
        row.name = payload["name"]
        row.description = payload["description"]
        row.source_url = payload["source_url"]
        row.license = payload["license"]
        row.metadata_json = payload["metadata_json"]
        rows.append(row)
    db.flush()
    return rows


def upsert_github_preview(db: Session, preview: dict[str, Any]) -> str | None:
    """把 GitHub 预览落库为可安装条目，返回 item_key（无 repo 时返回 None）。"""
    repo = str(preview.get("repo") or "").strip()
    if not repo:
        return None
    code = slugify(repo.replace("/", "-"), fallback="github-skill")
    item_key = f"github:skill:{code}"
    definition = preview.get("definition_json")
    row = db.query(MarketItem).filter(MarketItem.item_key == item_key).first()
    if row is None:
        row = MarketItem(
            item_key=item_key,
            item_type="skill",
            source="github",
            code=code,
            name=str(preview.get("name") or repo.split("/")[-1] or code),
            version=str(preview.get("version") or "latest"),
            description=str(preview.get("description") or ""),
            source_url=str(preview.get("source_url") or f"https://github.com/{repo}"),
            license=preview.get("license"),
            author=repo.split("/")[0],
            risk_level="medium",
            status=STATUS_AVAILABLE,
            definition_json=definition if isinstance(definition, dict) else None,
            manifest_json=None,
            metadata_json={
                "stars": int(preview.get("stars") or 0),
                "repo": repo,
                "path": preview.get("path"),
                "default_branch": preview.get("ref"),
                "tags": ["github", "agriculture"],
            },
            verified=False,
        )
        db.add(row)
    else:
        row.name = str(preview.get("name") or row.name)
        row.description = str(preview.get("description") or row.description)
        row.version = str(preview.get("version") or row.version)
        row.source_url = str(preview.get("source_url") or row.source_url)
        row.license = preview.get("license") or row.license
        if isinstance(definition, dict) and definition:
            row.definition_json = definition
        metadata = dict(row.metadata_json or {})
        metadata.update(
            {
                "stars": int(preview.get("stars") or metadata.get("stars") or 0),
                "repo": repo,
                "path": preview.get("path") or metadata.get("path"),
                "default_branch": preview.get("ref") or metadata.get("default_branch"),
            }
        )
        row.metadata_json = metadata
    db.flush()
    return item_key


def search_items(
    db: Session,
    ctx: YardContext,
    query: str = "",
    item_type: str | None = None,
    source: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """合并内置种子与 market_items 缓存；GitHub 仅在显式要求时联网。"""
    keyword = (query or "").strip()
    capped = max(1, min(int(limit or 20), 50))
    warnings: list[str] = []

    if _wants_online(keyword, source):
        try:
            results = search_github_skills(keyword, limit=min(capped, 10))
            cache_github_candidates(db, results)
        except AppError as e:
            warnings.append(f"GitHub 检索暂不可用，已回退本地能力：{e.message}")
        except Exception as e:  # 防御：任何网络层异常都不影响本地检索
            log.warning("github search failed: %s", e)
            warnings.append("GitHub 检索暂不可用，已回退本地能力。")

    candidates: dict[str, MarketItem | dict] = {}
    for row in db.query(MarketItem).all():
        candidates[row.item_key] = row
    for item in [*builtin_items(), *growth_builtin_items()]:
        candidates.setdefault(item["item_key"], item)

    skill_codes, plugin_ids = _installed_index(db, ctx.yard_id)
    matched: list[dict[str, Any]] = []
    for cached in candidates.values():
        get = _getter(cached)
        if item_type and get("item_type") != item_type:
            continue
        if source and get("source") != source:
            continue
        if not _matches(keyword, get("name"), get("description"), get("code")):
            continue
        installed = False
        if get("item_type") == "skill":
            installed = str(get("code")) in skill_codes
        elif get("item_type") == "plugin":
            installed = str(get("code")) in plugin_ids
        matched.append(serialize_item(cached, installed=installed))

    matched.sort(key=lambda x: (not x["installed"], x["source"] != "builtin", x["name"] or ""))
    return {
        "items": matched[:capped],
        "total": len(matched),
        "warning": "；".join(warnings) if warnings else None,
        "offline": bool(warnings),
    }


def get_item(db: Session, item_key: str) -> MarketItem | dict | None:
    row = db.query(MarketItem).filter(MarketItem.item_key == item_key).first()
    if row is not None:
        return row
    # 未落库的内置种子按需补种，保证详情/安装可用
    if item_key.startswith("builtin:"):
        seed_builtin(db)
        seed_growth_skills(db)
        return db.query(MarketItem).filter(MarketItem.item_key == item_key).first()
    return None


def _ensure_skill_definition(db: Session, item: MarketItem) -> dict:
    definition = dict(item.definition_json or {})
    if definition.get("prompt") or definition.get("content"):
        return definition
    if item.source != "github":
        raise AppError(
            "MARKET_ITEM_INVALID",
            "该技能缺少可用的提示词定义。",
            http_status=422,
            details={"item_key": item.item_key},
        )
    metadata = item.metadata_json if isinstance(item.metadata_json, dict) else {}
    repo = str(metadata.get("repo") or "")
    if not repo:
        raise AppError(
            "MARKET_ITEM_INVALID",
            "GitHub 技能缺少仓库信息，请重新检索。",
            http_status=422,
        )
    preview = preview_skill_from_repo(repo)
    definition = dict(preview["definition_json"])
    item.definition_json = definition
    item.name = str(preview.get("name") or item.name)
    item.version = str(preview.get("version") or item.version)
    item.description = str(preview.get("description") or item.description)
    item.source_url = str(preview.get("source_url") or item.source_url)
    item.license = preview.get("license") or item.license
    item.author = repo.split("/")[0]
    db.flush()
    return definition


def _install_skill(db: Session, ctx: YardContext, item: MarketItem) -> dict[str, Any]:
    # 本地导入条目在导入时已落地同码 Skill：直接复用，避免重复待审
    if item.source == "local":
        existing = (
            db.query(Skill)
            .filter(Skill.yard_id == ctx.yard_id, Skill.skill_code == item.code)
            .first()
        )
        if existing is not None:
            return {
                "installed_as": existing.skill_code,
                "skill_id": existing.id,
                "skill_status": existing.status,
                "review_status": existing.status,
            }
    definition = _ensure_skill_definition(db, item)
    code = unique_skill_code(db, ctx.yard_id, item.code)
    definition["market_item_key"] = item.item_key
    definition.setdefault("name", item.name)
    definition.setdefault("description", item.description)
    skill = create_skill(
        db,
        ctx,
        code,
        item.version,
        definition,
        created_from="market",
        request_id=ctx.request_id,
    )
    publish = item.source == "builtin" or bool(item.verified)
    skill.status = "published" if publish else "pending_review"
    if publish:
        skill.approved_by = ctx.user_id
        # 上架即有报告 + 签名：静态问题会直接拦下安装，不留下「已发布但无报告」的行
        attest_published_skill(db, ctx, skill, kind="install", request_id=ctx.request_id)
    db.flush()
    return {
        "installed_as": code,
        "skill_id": skill.id,
        "skill_status": skill.status,
        "review_status": "published" if publish else "pending_review",
    }


def _install_plugin(
    db: Session, ctx: YardContext, item: MarketItem, roles: set[str]
) -> dict[str, Any]:
    if not roles & PLUGIN_INSTALL_ROLES:
        raise AppError(
            "MARKET_FORBIDDEN",
            "插件安装仅限小院管理员或负责人。",
            http_status=403,
            details={"item_key": item.item_key},
        )
    manifest = dict(item.manifest_json or {})
    if not manifest:
        raise AppError("MARKET_ITEM_INVALID", "插件条目缺少 manifest。", http_status=422)
    validate_manifest(manifest, source=item.item_key)
    base_id = str(manifest.get("plugin_id") or item.code)
    if item.source == "local":
        existing = (
            db.query(PluginManifest)
            .filter(
                PluginManifest.yard_id == ctx.yard_id,
                PluginManifest.plugin_id == base_id,
            )
            .first()
        )
        if existing is not None:
            return {
                "installed_as": existing.plugin_id,
                "plugin_id": existing.plugin_id,
                "plugin_status": existing.status,
                "checksum_ok": existing.checksum_ok,
                "review_status": existing.status,
            }
    plugin_id = unique_plugin_id(db, ctx.yard_id, slugify(base_id, fallback="plugin"))
    manifest["plugin_id"] = plugin_id
    declared = str(manifest.get("checksum") or "")
    computed = compute_checksum(manifest)
    checksum_ok = bool(declared) and declared == computed
    if not declared:
        manifest["checksum"] = computed
    record = PluginManifest(
        plugin_id=plugin_id,
        yard_id=ctx.yard_id,
        version=str(manifest.get("version") or item.version),
        type=str(manifest.get("type")),
        status="pending_review",
        manifest_json=manifest,
        checksum=declared or computed,
        checksum_ok=checksum_ok,
        installed_at=datetime.now(UTC),
    )
    db.add(record)
    db.flush()
    return {
        "installed_as": plugin_id,
        "plugin_id": plugin_id,
        "plugin_status": record.status,
        "checksum_ok": checksum_ok,
        "review_status": "pending_review",
    }


def _install_agent(db: Session, ctx: YardContext, item: MarketItem) -> dict[str, Any]:
    from app.modules.market.seeds import find_seed
    from app.modules.market.service import install_seed_codes

    seed = find_seed(item.code)
    if seed is None:
        raise AppError(
            "MARKET_AGENT_UNSUPPORTED",
            "Agent 安装暂未开放，请在能力市场使用种子包。",
            http_status=400,
            details={"item_key": item.item_key},
        )
    result = install_seed_codes(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        roles=list(ctx.roles),
        codes=[item.code],
        request_id=ctx.request_id,
    )
    installed = result["installed"] or result["skipped"]
    if not installed:
        reason = result["failed"][0]["error"] if result["failed"] else "装载失败"
        raise AppError("MARKET_AGENT_UNSUPPORTED", reason, http_status=400)
    return {"installed_as": installed[0], "review_status": "published"}


def install_item(
    db: Session,
    ctx: YardContext,
    item_key: str,
    actor_role: str | list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """安装条目：按类型落地 Skill / PluginManifest；同一小院冲突自动加后缀。"""
    item = get_item(db, item_key)
    if item is None:
        raise AppError(
            "MARKET_ITEM_NOT_FOUND",
            "市场条目不存在或已下架。",
            http_status=404,
            details={"item_key": item_key},
        )
    if not isinstance(item, MarketItem):
        raise AppError("MARKET_ITEM_NOT_FOUND", "市场条目不存在或已下架。", http_status=404)
    if item.status == "blocked":
        raise AppError(
            "MARKET_ITEM_BLOCKED",
            "该条目已被风控拦截，不能安装。",
            http_status=403,
            details={"item_key": item.item_key},
        )

    roles = _effective_roles(db, ctx, actor_role)
    if item.item_type == "skill":
        result = _install_skill(db, ctx, item)
    elif item.item_type == "plugin":
        result = _install_plugin(db, ctx, item, roles)
    elif item.item_type == "agent":
        result = _install_agent(db, ctx, item)
    else:
        raise AppError(
            "MARKET_ITEM_INVALID",
            f"不支持的条目类型：{item.item_type}",
            http_status=422,
        )

    item.status = STATUS_INSTALLED
    db.flush()
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="market_item_installed",
        resource_type="market_item",
        resource_id=item.id,
        result="success",
        request_id=ctx.request_id,
        metadata={
            "item_key": item.item_key,
            "item_type": item.item_type,
            "source": item.source,
            "installed_as": result["installed_as"],
            "review_status": result["review_status"],
        },
    )
    return {
        "item_key": item.item_key,
        "item_type": item.item_type,
        "source": item.source,
        "name": item.name,
        **result,
    }


def list_installed(db: Session, yard_id: str) -> dict[str, Any]:
    """本小院已安装/导入的 Skill 与插件摘要（按创建时间倒序）。"""
    rows = (
        db.query(Skill)
        .filter(Skill.yard_id == yard_id, Skill.created_from.like("market%"))
        .order_by(Skill.created_at.desc())
        .all()
    )
    item_keys = {
        str(item.code): item.item_key
        for item in db.query(MarketItem)
        .filter(MarketItem.source.in_(("builtin", "github", "remote", "local")))
        .all()
        if item.item_type == "skill"
    }
    skills = []
    for row in rows:
        definition = row.definition_json if isinstance(row.definition_json, dict) else {}
        skills.append(
            {
                "id": row.id,
                "code": row.skill_code,
                "name": definition.get("name") or row.skill_code,
                "version": row.version,
                "status": row.status,
                "item_key": definition.get("market_item_key") or item_keys.get(row.skill_code),
                "source": "local" if str(row.created_from or "").endswith("import") else "market",
                "created_at": row.created_at,
            }
        )

    plugin_rows = (
        db.query(PluginManifest)
        .filter(PluginManifest.yard_id == yard_id)
        .order_by(PluginManifest.created_at.desc())
        .all()
    )
    plugin_keys = {
        item.code: item.item_key
        for item in db.query(MarketItem).filter(MarketItem.item_type == "plugin").all()
    }
    plugins = []
    for plugin_row in plugin_rows:
        manifest = plugin_row.manifest_json if isinstance(plugin_row.manifest_json, dict) else {}
        plugins.append(
            {
                "id": plugin_row.id,
                "plugin_id": plugin_row.plugin_id,
                "code": plugin_row.plugin_id,
                "name": manifest.get("name") or plugin_row.plugin_id,
                "version": plugin_row.version,
                "type": plugin_row.type,
                "status": plugin_row.status,
                "item_key": plugin_keys.get(plugin_row.plugin_id),
                "source": "local",
                "created_at": plugin_row.created_at,
            }
        )
    return {"skills": skills, "plugins": plugins, "total": len(skills) + len(plugins)}


__all__ = [
    "PLUGIN_INSTALL_ROLES",
    "cache_github_candidates",
    "get_item",
    "install_item",
    "list_installed",
    "search_items",
    "serialize_item",
    "upsert_github_preview",
]
