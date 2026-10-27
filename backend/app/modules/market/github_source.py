"""GitHub 技能检索与预览（只读文本，绝不执行仓库脚本）。

离线优先：任何网络失败都转成明确的 AppError，由上层降级为本地能力，
不向上冒 500。

国内网络降级（v3.2）：api 基址读配置；raw 文件按「官方 → 国内镜像」顺序
尝试，成功镜像缓存健康状态 60s（后续优先），失败镜像进入 5 分钟冷却期
（期间跳过不再首选）。镜像拼接规则见 ``_apply_mirror`` 与 ``.env.example``。
"""

from __future__ import annotations

import os
import re
import time
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.market.skilldoc import (
    extract_list_items,
    extract_sections,
    first_heading,
    first_paragraph,
    parse_frontmatter,
    section_text,
    truncate,
)

log = get_logger(__name__)

RAW_BASE = "https://raw.githubusercontent.com"
TIMEOUT_SECONDS = 10
ALLOWED_LICENSES = frozenset({"MIT", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0"})
MAX_PREVIEW_BYTES = 256 * 1024
# 镜像健康状态：成功后 60s 内视为健康（优先尝试）；失败镜像 5 分钟内跳过（冷却期）。
MIRROR_HEALTHY_TTL_SECONDS = 60
MIRROR_COOLDOWN_SECONDS = 300

_OFFLINE_MESSAGE = "暂时无法连接 GitHub，可继续使用内置技能或本地导入。"
_OFFLINE_WITH_MIRRORS_MESSAGE = (
    "暂时无法连接 GitHub（已尝试国内镜像），可继续使用内置技能或本地导入。"
)

_transport_override: httpx.BaseTransport | None = None
# 进程级镜像健康/冷却状态（key 为配置里的镜像写法本身）
_mirror_healthy: dict[str, float] = {}
_mirror_cooldown: dict[str, float] = {}


def set_test_transport(transport: httpx.BaseTransport | None) -> None:
    """测试注入 MockTransport（与 mcp/client.py 同一做法）。"""
    global _transport_override
    _transport_override = transport


def _headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "kjxy-agent-market",
    }
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _client() -> httpx.Client:
    return httpx.Client(
        timeout=TIMEOUT_SECONDS,
        headers=_headers(),
        transport=_transport_override,
        follow_redirects=True,
    )


def _api_base() -> str:
    """GitHub API 基址：读配置（默认官方 api.github.com），去尾部斜杠。"""
    return (settings.github_api_base or "https://api.github.com").rstrip("/")


def _offline(exc: Exception | None = None, *, message: str = _OFFLINE_MESSAGE) -> AppError:
    if exc:
        log.warning("github network unavailable: %s", exc)
    return AppError(
        "NETWORK_UNAVAILABLE",
        message,
        http_status=409,
        details={"mode": "local"},
    )


def _apply_mirror(mirror: str, url: str) -> str | None:
    """按镜像写法拼接最终 URL（各镜像实际拼接规则见 config.py 与根 .env.example）。

    支持两种写法：
    1) 模板式：镜像串含占位符——``{url}``=完整原始 URL，``{path}``=路径部分
       （含前导 /）。整站改写型镜像（如 raw.gitmirror.com）用 ``{path}``：
       ``https://raw.gitmirror.com{path}`` → ``https://raw.gitmirror.com/owner/repo/...``。
    2) 前缀式：不含占位符时按主流加速器（ghfast.top、gh-proxy.com 等）通用规则，
       整体前置完整原始 URL：
       ``https://ghfast.top`` → ``https://ghfast.top/https://raw.githubusercontent.com/...``。

    拼接结果非 http(s)（如误配 ``file://``、``ftp://``）一律拒绝并跳过该镜像。
    """
    if "{url}" in mirror:
        final = mirror.replace("{url}", url)
    elif "{path}" in mirror:
        final = mirror.replace("{path}", urlsplit(url).path or "/")
    else:
        final = f"{mirror.rstrip('/')}/{url}"
    split = urlsplit(final)
    if split.scheme not in {"http", "https"} or not split.netloc:
        log.warning("invalid github mirror config, skipped: %s", mirror)
        return None
    return final


def _ordered_mirrors(mirrors: list[str]) -> list[str]:
    """镜像尝试顺序：健康镜像（60s 内成功过）优先，冷却中镜像（5 分钟内失败过）跳过。"""
    now = time.time()
    healthy = [
        m for m in mirrors if now - _mirror_healthy.get(m, 0.0) <= MIRROR_HEALTHY_TTL_SECONDS
    ]
    rest = [
        m
        for m in mirrors
        if m not in healthy and now - _mirror_cooldown.get(m, 0.0) >= MIRROR_COOLDOWN_SECONDS
    ]
    return healthy + rest


def _fetch_raw_text(url: str) -> str:
    """raw 文本按「官方 → 国内镜像」顺序获取，任一源成功即返回。

    - 镜像成功 → 记健康状态 60s（后续优先）；网络/限流失败 → 进 5 分钟冷却，
      期间不再首选该镜像；镜像 404 视为该镜像缺失（服务可达），不进冷却；
    - 官方明确 404 视为权威结论，直接抛 SKILL_NOT_FOUND；
    - 任一源返回超限内容 → 立即 SKILL_TOO_LARGE（上限沿用 MAX_PREVIEW_BYTES）；
    - 全部失败 → NETWORK_UNAVAILABLE（消息注明已尝试国内镜像）。
    """
    candidates: list[tuple[str, str]] = [("official", url)]
    for mirror in _ordered_mirrors(settings.github_raw_mirror_list):
        final = _apply_mirror(mirror, url)
        if final:
            candidates.append((mirror, final))
    last_error: Exception | None = None
    for key, candidate in candidates:
        try:
            text = _get_text(candidate)
        except AppError as e:
            if e.code == "SKILL_NOT_FOUND" and key == "official":
                raise  # 官方可达且确认不存在，无需再试镜像
            if e.code == "SKILL_TOO_LARGE":
                raise  # 内容超限与来源无关，直接拒绝
            if key != "official" and e.code == "NETWORK_UNAVAILABLE":
                _mirror_cooldown[key] = time.time()  # 失败镜像进入冷却期
            last_error = e
            continue
        if key != "official":
            _mirror_healthy[key] = time.time()
            _mirror_cooldown.pop(key, None)
        return text
    log.warning("github raw fetch failed on all sources: %s", url)
    used_mirror = len(candidates) > 1
    raise _offline(
        last_error, message=_OFFLINE_WITH_MIRRORS_MESSAGE if used_mirror else _OFFLINE_MESSAGE
    )


def _get_json(url: str) -> dict:
    try:
        with _client() as client:
            resp = client.get(url)
    except httpx.HTTPError as e:
        raise _offline(e) from e
    if resp.status_code == 404:
        raise AppError("REPO_NOT_FOUND", "GitHub 仓库或路径不存在。", http_status=404)
    if resp.status_code in {403, 429}:
        log.warning("github rate limited: %s", resp.status_code)
        raise _offline()
    if resp.status_code >= 400:
        raise _offline()
    try:
        data = resp.json()
    except ValueError as e:
        raise _offline(e) from e
    if not isinstance(data, dict):
        raise _offline()
    return data


def _get_text(url: str) -> str:
    try:
        with _client() as client:
            resp = client.get(url)
    except httpx.HTTPError as e:
        raise _offline(e) from e
    if resp.status_code == 404:
        raise AppError("SKILL_NOT_FOUND", "仓库中未找到可用的 SKILL.md。", http_status=404)
    if resp.status_code >= 400:
        raise _offline()
    if len(resp.content) > MAX_PREVIEW_BYTES:
        raise AppError("SKILL_TOO_LARGE", "SKILL.md 超过 256KB，已拒绝预览。", http_status=413)
    return resp.text


def _normalize_repo(repo: str) -> str:
    value = (repo or "").strip().rstrip("/")
    value = re.sub(r"^https?://(www\.)?github\.com/", "", value, flags=re.I)
    value = value.removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise AppError("INVALID_REQUEST", "repo 需为 owner/name 形式。", http_status=400)
    return value


def _license_of(repo_meta: dict) -> str | None:
    raw = repo_meta.get("license")
    if not isinstance(raw, dict):
        return None
    return raw.get("spdx_id") or raw.get("key")


def _agri_query(query: str) -> str:
    # 农业词与用户词成对叠加：既保证纯农业语义，又避免只命中“awesome 列表”
    return f"{query} agriculture OR {query} farming in:name,description,readme"


def search_github_skills(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """检索农业相关仓库；过滤无许可证/非白名单许可证后返回候选。"""
    keyword = (query or "").strip()
    if not keyword:
        raise AppError("INVALID_REQUEST", "GitHub 检索需要 query。", http_status=400)
    capped = max(1, min(int(limit or 10), 20))
    params = {
        "q": _agri_query(keyword),
        "sort": "stars",
        "order": "desc",
        "per_page": str(min(capped * 3, 50)),
    }
    try:
        with _client() as client:
            resp = client.get(f"{_api_base()}/search/repositories", params=params)
    except httpx.HTTPError as e:
        raise _offline(e) from e
    if resp.status_code in {403, 429, 422}:
        log.warning("github search failed: %s", resp.status_code)
        raise _offline()
    if resp.status_code >= 400:
        raise _offline()
    try:
        payload = resp.json()
    except ValueError as e:
        raise _offline(e) from e

    results: list[dict[str, Any]] = []
    for item in (payload or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        license_id = _license_of(item)
        if not license_id or license_id not in ALLOWED_LICENSES:
            continue
        full_name = str(item.get("full_name") or "").strip()
        if not full_name or "/" not in full_name:
            continue
        results.append(
            {
                "repo": full_name,
                "description": truncate(str(item.get("description") or ""), 500),
                "stars": int(item.get("stargazers_count") or 0),
                "license": license_id,
                "updated_at": item.get("updated_at"),
                "default_branch": str(item.get("default_branch") or "main"),
                "html_url": str(item.get("html_url") or f"https://github.com/{full_name}"),
            }
        )
        if len(results) >= capped:
            break
    return results


def _pick_skill_path(tree: dict) -> str | None:
    paths: list[str] = []
    for node in tree.get("tree") or []:
        if not isinstance(node, dict) or node.get("type") not in {None, "blob"}:
            continue
        path = str(node.get("path") or "")
        if path == "SKILL.md" or path.endswith("/SKILL.md"):
            paths.append(path)
    if not paths:
        return None
    # 优先 skills/*/SKILL.md，其次任意目录下的 SKILL.md，最后仓库根
    under_skills = sorted(p for p in paths if p.startswith("skills/"))
    if under_skills:
        return under_skills[0]
    non_root = sorted(p for p in paths if p != "SKILL.md")
    return non_root[0] if non_root else "SKILL.md"


def _build_definition(
    *, repo: str, branch: str, path: str, meta: dict, body: str, license_id: str | None
) -> dict[str, Any]:
    sections = extract_sections(body)
    purpose = section_text(sections, ("purpose",))
    instructions = section_text(sections, ("instruction",))
    capabilities = section_text(sections, ("capabilit",))
    tools = section_text(sections, ("tool",))
    prompt_text = "\n\n".join(x for x in (purpose, instructions, tools) if x).strip()
    if not prompt_text:
        prompt_text = "\n\n".join(x for x in (capabilities, body) if x).strip()
    prompt = truncate(prompt_text, 1500)

    steps = extract_list_items(instructions, limit=6)
    if not steps:
        steps = extract_list_items(capabilities, limit=6)

    front_name = str(meta.get("name") or "").strip()
    name = front_name or first_heading(body) or repo.split("/")[-1]
    description = str(meta.get("description") or "").strip() or first_paragraph(body)
    raw_metadata = meta.get("metadata")
    metadata: dict = raw_metadata if isinstance(raw_metadata, dict) else {}
    version = str(metadata.get("version") or "1.0.0")

    source_url = f"https://github.com/{repo}/blob/{branch}/{path}"
    return {
        "name": name,
        "description": truncate(description, 500),
        "prompt": prompt,
        "steps": steps,
        "version": version,
        "source_url": source_url,
        "license": license_id,
        "attribution": (
            f"来自 GitHub 仓库 {repo}（{license_id or '许可证未声明'}），"
            "仅读取文本，未执行脚本。"
        ),
        "repo": repo,
        "path": path,
        "branch": branch,
    }


def zip_download_urls(full_name: str, branch: str = "main") -> list[str]:
    """仓库 zip 源码包候选下载地址：官方在前，zip 镜像按配置顺序在后。

    拼接规则同 raw 镜像（前缀式：镜像前缀 + 完整原始 zip URL）；只产出候选
    URL 不做网络请求，供 3.2 安装器/生成器按序尝试。
    """
    name = _normalize_repo(full_name)
    branch = (branch or "main").strip() or "main"
    official = f"https://github.com/{name}/archive/refs/heads/{quote(branch, safe='')}.zip"
    urls = [official]
    for mirror in settings.github_zip_mirror_list:
        final = _apply_mirror(mirror, official)
        if final:
            urls.append(final)
    return urls


def preview_skill_from_repo(repo: str, ref: str | None = None) -> dict[str, Any]:
    """读取 GitHub 仓库中的 SKILL.md 并生成 definition_json。"""
    full_name = _normalize_repo(repo)
    api = _api_base()
    repo_meta = _get_json(f"{api}/repos/{full_name}")
    branch = (ref or str(repo_meta.get("default_branch") or "main")).strip() or "main"
    tree_ref = quote(branch, safe="")
    tree = _get_json(f"{api}/repos/{full_name}/git/trees/{tree_ref}?recursive=1")
    path = _pick_skill_path(tree)
    if not path:
        raise AppError(
            "SKILL_NOT_FOUND",
            "该仓库没有 SKILL.md，无法作为技能预览。",
            http_status=404,
            details={"repo": full_name},
        )
    raw_url = f"{RAW_BASE}/{full_name}/{branch}/{path}"
    text = _fetch_raw_text(raw_url)
    meta, body = parse_frontmatter(text)
    meta = meta if isinstance(meta, dict) else {}
    license_id = _license_of(repo_meta)
    definition = _build_definition(
        repo=full_name,
        branch=branch,
        path=path,
        meta=meta,
        body=body,
        license_id=license_id,
    )
    return {
        "repo": full_name,
        "path": path,
        "ref": branch,
        "license": license_id,
        "stars": int(repo_meta.get("stargazers_count") or 0),
        "description": definition["description"],
        "name": definition["name"],
        "version": definition["version"],
        "source_url": definition["source_url"],
        "definition_json": definition,
    }


__all__ = [
    "ALLOWED_LICENSES",
    "preview_skill_from_repo",
    "search_github_skills",
    "set_test_transport",
    "zip_download_urls",
]
