"""GitHub 技能检索与预览（只读文本，绝不执行仓库脚本）。

离线优先：任何网络失败都转成明确的 AppError，由上层降级为本地能力，
不向上冒 500。
"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import quote

import httpx

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

GITHUB_API = "https://api.github.com"
RAW_BASE = "https://raw.githubusercontent.com"
TIMEOUT_SECONDS = 10
ALLOWED_LICENSES = frozenset({"MIT", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0"})
MAX_PREVIEW_BYTES = 256 * 1024

_transport_override: httpx.BaseTransport | None = None


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


def _offline(exc: Exception | None = None) -> AppError:
    if exc:
        log.warning("github network unavailable: %s", exc)
    return AppError(
        "NETWORK_UNAVAILABLE",
        "暂时无法连接 GitHub，可继续使用内置技能或本地导入。",
        http_status=409,
        details={"mode": "local"},
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
            resp = client.get(f"{GITHUB_API}/search/repositories", params=params)
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


def preview_skill_from_repo(repo: str, ref: str | None = None) -> dict[str, Any]:
    """读取 GitHub 仓库中的 SKILL.md 并生成 definition_json。"""
    full_name = _normalize_repo(repo)
    repo_meta = _get_json(f"{GITHUB_API}/repos/{full_name}")
    branch = (ref or str(repo_meta.get("default_branch") or "main")).strip() or "main"
    tree_ref = quote(branch, safe="")
    tree = _get_json(f"{GITHUB_API}/repos/{full_name}/git/trees/{tree_ref}?recursive=1")
    path = _pick_skill_path(tree)
    if not path:
        raise AppError(
            "SKILL_NOT_FOUND",
            "该仓库没有 SKILL.md，无法作为技能预览。",
            http_status=404,
            details={"repo": full_name},
        )
    raw_url = f"{RAW_BASE}/{full_name}/{branch}/{path}"
    text = _get_text(raw_url)
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
]
