"""GitHub 检索与 SKILL.md 预览：许可证过滤、frontmatter 解析、离线降级、国内镜像。"""

from __future__ import annotations

import json
import time

import httpx
import pytest
from app.core.errors import AppError
from app.modules.market import github_source as gh

REPO = "farmfriend-labs/agent-skills-farming"
RAW_URL = f"https://raw.githubusercontent.com/{REPO}/main/SKILL.md"
SKILL_MD = """---
name: Soil Health Builder
description: Build and maintain healthy soil.
metadata:
  version: "2.3.0"
---
# Soil Health Builder

## Purpose

Improve soil structure and organic matter with locally measured data.

## Instructions

1. Collect soil test results from the grower.
2. Identify the limiting factor before recommending amendments.
3. Schedule a follow-up test after the season.

## Tools

- soil-test-reader
"""


@pytest.fixture(autouse=True)
def _reset_transport():
    yield
    gh.set_test_transport(None)
    gh._mirror_healthy.clear()
    gh._mirror_cooldown.clear()


def _transport(handler):
    return httpx.MockTransport(handler)


def _set_mirrors(monkeypatch, raw: str | None = None, zip_: str | None = None) -> None:
    """测试内固定镜像配置（避免依赖第三方真实域名）。"""
    if raw is not None:
        monkeypatch.setattr(gh.settings, "github_raw_mirrors", raw)
    if zip_ is not None:
        monkeypatch.setattr(gh.settings, "github_zip_mirrors", zip_)


def test_search_filters_license_and_hides_token(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret_token_value")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search/repositories"
        assert "agriculture" in request.url.params["q"]
        assert request.headers["authorization"] == "Bearer ghp_secret_token_value"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "full_name": "owner/mit-repo",
                        "description": "MIT farm skill",
                        "stargazers_count": 12,
                        "license": {"spdx_id": "MIT"},
                        "updated_at": "2026-01-01T00:00:00Z",
                        "default_branch": "main",
                        "html_url": "https://github.com/owner/mit-repo",
                    },
                    {
                        "full_name": "owner/gpl-repo",
                        "description": "GPL farm skill",
                        "stargazers_count": 99,
                        "license": {"spdx_id": "GPL-3.0"},
                        "default_branch": "main",
                    },
                    {
                        "full_name": "owner/no-license",
                        "description": "No license",
                        "stargazers_count": 5,
                        "license": None,
                        "default_branch": "main",
                    },
                ]
            },
        )

    gh.set_test_transport(_transport(handler))
    items = gh.search_github_skills("soil", limit=10)
    assert [i["repo"] for i in items] == ["owner/mit-repo"]
    assert items[0]["license"] == "MIT"
    assert items[0]["stars"] == 12
    assert items[0]["default_branch"] == "main"
    assert "ghp_secret_token_value" not in json.dumps(items, ensure_ascii=False)


def test_search_network_failure_is_explicit_offline():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    gh.set_test_transport(_transport(handler))
    with pytest.raises(AppError) as err:
        gh.search_github_skills("soil")
    assert err.value.code == "NETWORK_UNAVAILABLE"
    assert err.value.http_status == 409
    assert err.value.details == {"mode": "local"}


def test_preview_reads_skill_md_and_frontmatter():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/repos/{REPO}":
            return httpx.Response(
                200,
                json={
                    "default_branch": "main",
                    "license": {"spdx_id": "MIT"},
                    "stargazers_count": 60,
                },
            )
        if request.url.path == f"/repos/{REPO}/git/trees/main":
            return httpx.Response(
                200,
                json={
                    "tree": [
                        {"path": "README.md", "type": "blob"},
                        {"path": "skills/soil-health-builder/SKILL.md", "type": "blob"},
                    ]
                },
            )
        if request.url.host == "raw.githubusercontent.com":
            assert request.url.path == f"/{REPO}/main/skills/soil-health-builder/SKILL.md"
            return httpx.Response(200, text=SKILL_MD)
        return httpx.Response(404)

    gh.set_test_transport(_transport(handler))
    preview = gh.preview_skill_from_repo(REPO)
    definition = preview["definition_json"]
    assert preview["path"] == "skills/soil-health-builder/SKILL.md"
    assert preview["ref"] == "main"
    assert definition["name"] == "Soil Health Builder"
    assert definition["description"] == "Build and maintain healthy soil."
    assert definition["license"] == "MIT"
    assert "2.3.0" in json.dumps(definition)
    assert "Improve soil structure" in definition["prompt"]
    assert "Collect soil test results" in definition["prompt"]
    assert len(definition["steps"]) == 3
    assert definition["source_url"].endswith("/skills/soil-health-builder/SKILL.md")
    assert "farmfriend-labs/agent-skills-farming" in definition["attribution"]


def test_preview_without_skill_md_raises_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/repos/{REPO}":
            return httpx.Response(
                200, json={"default_branch": "main", "license": {"spdx_id": "MIT"}}
            )
        if request.url.path == f"/repos/{REPO}/git/trees/main":
            return httpx.Response(200, json={"tree": [{"path": "README.md", "type": "blob"}]})
        return httpx.Response(404)

    gh.set_test_transport(_transport(handler))
    with pytest.raises(AppError) as err:
        gh.preview_skill_from_repo(REPO)
    assert err.value.code == "SKILL_NOT_FOUND"
    assert err.value.http_status == 404


def test_preview_network_failure_details():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    gh.set_test_transport(_transport(handler))
    with pytest.raises(AppError) as err:
        gh.preview_skill_from_repo(REPO)
    assert err.value.code == "NETWORK_UNAVAILABLE"
    assert err.value.details == {"mode": "local"}


def test_preview_rejects_bad_repo_name():
    with pytest.raises(AppError) as err:
        gh.preview_skill_from_repo("not a repo")
    assert err.value.code == "INVALID_REQUEST"


# ---------------------------------------------------------------------------
# 国内镜像降级（全部 MockTransport，禁止真实出网）
# ---------------------------------------------------------------------------


def test_raw_uses_official_first_and_skips_mirrors(monkeypatch):
    """官方可用直连：镜像完全不参与。"""
    _set_mirrors(monkeypatch, raw="https://mirror-a.test/{url}")
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(str(request.url.host))
        if request.url.host == "raw.githubusercontent.com":
            return httpx.Response(200, text=SKILL_MD)
        return httpx.Response(200, text="mirror copy")

    gh.set_test_transport(_transport(handler))
    text = gh._fetch_raw_text(RAW_URL)
    assert "Soil Health Builder" in text
    assert hosts == ["raw.githubusercontent.com"]


def test_raw_falls_back_to_mirror_when_official_blocked(monkeypatch):
    """官方 raw 不可达时按前缀式拼接走镜像。"""
    _set_mirrors(monkeypatch, raw="https://mirror-a.test/{url}")

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "api.github.com":
            if request.url.path == f"/repos/{REPO}":
                return httpx.Response(
                    200,
                    json={"default_branch": "main", "license": {"spdx_id": "MIT"}},
                )
            if request.url.path == f"/repos/{REPO}/git/trees/main":
                return httpx.Response(
                    200,
                    json={
                        "tree": [{"path": "skills/soil-health-builder/SKILL.md", "type": "blob"}]
                    },
                )
            return httpx.Response(404)
        if host == "raw.githubusercontent.com":
            raise httpx.ConnectError("official blocked", request=request)
        # 前缀式拼接：镜像前缀 + 完整原始 URL
        assert host == "mirror-a.test"
        assert request.url.path == (
            f"/https://raw.githubusercontent.com/{REPO}/main/skills/soil-health-builder/SKILL.md"
        )
        return httpx.Response(200, text=SKILL_MD)

    gh.set_test_transport(_transport(handler))
    preview = gh.preview_skill_from_repo(REPO)
    assert preview["definition_json"]["name"] == "Soil Health Builder"
    assert "https://mirror-a.test/{url}" in gh._mirror_healthy


def test_failed_mirror_skipped_during_cooldown(monkeypatch):
    """失败镜像进入 5 分钟冷却期后被跳过；健康镜像直接命中。"""
    _set_mirrors(monkeypatch, raw="https://mirror-a.test/{url},https://mirror-b.test/{url}")
    hits = {"a": 0, "b": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "raw.githubusercontent.com":
            raise httpx.ConnectError("official blocked", request=request)
        if host == "mirror-a.test":
            hits["a"] += 1
            raise httpx.ConnectError("mirror-a down", request=request)
        if host == "mirror-b.test":
            hits["b"] += 1
            return httpx.Response(200, text=SKILL_MD)
        return httpx.Response(404)

    gh.set_test_transport(_transport(handler))
    assert "Soil Health Builder" in gh._fetch_raw_text(RAW_URL)
    assert hits == {"a": 1, "b": 1}
    # 第二次：mirror-a 已「恢复」，但仍在冷却期内必须被跳过
    assert "Soil Health Builder" in gh._fetch_raw_text(RAW_URL)
    assert hits == {"a": 1, "b": 2}


def test_mirror_cooldown_expires(monkeypatch):
    _set_mirrors(monkeypatch, raw="https://mirror-a.test/{url}")
    hits = {"a": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "raw.githubusercontent.com":
            raise httpx.ConnectError("official blocked", request=request)
        hits["a"] += 1
        if hits["a"] == 1:
            raise httpx.ConnectError("mirror-a down", request=request)
        return httpx.Response(200, text=SKILL_MD)

    gh.set_test_transport(_transport(handler))
    with pytest.raises(AppError) as err:
        gh._fetch_raw_text(RAW_URL)
    assert err.value.code == "NETWORK_UNAVAILABLE"
    # 回拨冷却时间戳，模拟 5 分钟冷却期结束
    gh._mirror_cooldown["https://mirror-a.test/{url}"] = (
        time.time() - gh.MIRROR_COOLDOWN_SECONDS - 1
    )
    assert "Soil Health Builder" in gh._fetch_raw_text(RAW_URL)
    assert hits["a"] == 2


def test_healthy_mirror_tried_before_untested(monkeypatch):
    """成功镜像缓存健康状态 60s：恢复后的镜像排在其后。"""
    _set_mirrors(monkeypatch, raw="https://mirror-a.test/{url},https://mirror-b.test/{url}")
    order: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "raw.githubusercontent.com":
            raise httpx.ConnectError("official blocked", request=request)
        order.append(str(host))
        if host == "mirror-a.test" and order.count(host) == 1:
            raise httpx.ConnectError("mirror-a down", request=request)
        return httpx.Response(200, text=SKILL_MD)

    gh.set_test_transport(_transport(handler))
    assert "Soil Health Builder" in gh._fetch_raw_text(RAW_URL)
    # 回拨 a 的冷却（不再跳过），但 b 健康优先：b 首个命中即成功，a 不再被触达
    gh._mirror_cooldown["https://mirror-a.test/{url}"] = (
        time.time() - gh.MIRROR_COOLDOWN_SECONDS - 1
    )
    order.clear()
    assert "Soil Health Builder" in gh._fetch_raw_text(RAW_URL)
    assert order == ["mirror-b.test"]


def test_all_sources_offline_raises_network_unavailable(monkeypatch):
    """官方 + 镜像全部失败：NETWORK_UNAVAILABLE，消息注明已尝试国内镜像。"""
    _set_mirrors(monkeypatch, raw="https://mirror-a.test/{url}")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    gh.set_test_transport(_transport(handler))
    with pytest.raises(AppError) as err:
        gh._fetch_raw_text(RAW_URL)
    assert err.value.code == "NETWORK_UNAVAILABLE"
    assert err.value.http_status == 409
    assert "已尝试国内镜像" in err.value.message
    assert err.value.details == {"mode": "local"}
    assert "https://mirror-a.test/{url}" in gh._mirror_cooldown


def test_raw_oversize_rejected(monkeypatch):
    _set_mirrors(monkeypatch, raw="https://mirror-a.test/{url}")
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(str(request.url.host))
        return httpx.Response(200, text="x" * (gh.MAX_PREVIEW_BYTES + 1))

    gh.set_test_transport(_transport(handler))
    with pytest.raises(AppError) as err:
        gh._fetch_raw_text(RAW_URL)
    assert err.value.code == "SKILL_TOO_LARGE"
    assert err.value.http_status == 413
    assert hosts == ["raw.githubusercontent.com"]  # 官方超限即拒，不再尝试镜像


def test_official_404_is_authoritative(monkeypatch):
    _set_mirrors(monkeypatch, raw="https://mirror-a.test/{url}")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "raw.githubusercontent.com":
            return httpx.Response(404)
        return httpx.Response(200, text=SKILL_MD)

    gh.set_test_transport(_transport(handler))
    with pytest.raises(AppError) as err:
        gh._fetch_raw_text(RAW_URL)
    assert err.value.code == "SKILL_NOT_FOUND"


def test_mirror_404_does_not_mask_official_outage(monkeypatch):
    _set_mirrors(monkeypatch, raw="https://mirror-a.test/{url},https://mirror-b.test/{url}")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "raw.githubusercontent.com":
            raise httpx.ConnectError("official blocked", request=request)
        if request.url.host == "mirror-a.test":
            return httpx.Response(404)  # 该镜像缺失但不代表文件不存在
        return httpx.Response(200, text=SKILL_MD)

    gh.set_test_transport(_transport(handler))
    assert "Soil Health Builder" in gh._fetch_raw_text(RAW_URL)


def test_invalid_mirror_entry_skipped(monkeypatch):
    """非 http(s) 的镜像配置（如 ftp://、无 scheme）一律跳过。"""
    _set_mirrors(monkeypatch, raw="ftp://bad.example/{url},https://mirror-a.test/{url}")
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(str(request.url.host))
        if request.url.host == "raw.githubusercontent.com":
            raise httpx.ConnectError("official blocked", request=request)
        return httpx.Response(200, text=SKILL_MD)

    gh.set_test_transport(_transport(handler))
    assert "Soil Health Builder" in gh._fetch_raw_text(RAW_URL)
    assert hosts == ["raw.githubusercontent.com", "mirror-a.test"]


def test_api_base_reads_config(monkeypatch):
    """API 基址读配置：可整体切换到路径兼容的国内网关。"""
    monkeypatch.setattr(gh.settings, "github_api_base", "https://api.example-gw.test")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.example-gw.test"
        assert request.url.path == "/search/repositories"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "full_name": "owner/mit-repo",
                        "description": "MIT farm skill",
                        "stargazers_count": 3,
                        "license": {"spdx_id": "MIT"},
                        "default_branch": "main",
                        "html_url": "https://github.com/owner/mit-repo",
                    }
                ]
            },
        )

    gh.set_test_transport(_transport(handler))
    items = gh.search_github_skills("soil")
    assert [i["repo"] for i in items] == ["owner/mit-repo"]


def test_zip_download_urls_build_mirrors(monkeypatch):
    _set_mirrors(monkeypatch, zip_="https://mirror-z.test/{url},https://raw.gitmirror.test{path}")
    urls = gh.zip_download_urls("owner/repo", "main")
    assert urls[0] == "https://github.com/owner/repo/archive/refs/heads/main.zip"
    # 前缀式：完整原始 URL 整体后置
    assert urls[1] == (
        "https://mirror-z.test/https://github.com/owner/repo/archive/refs/heads/main.zip"
    )
    # 模板式 {path}：host 改写、路径保持
    assert urls[2] == "https://raw.gitmirror.test/owner/repo/archive/refs/heads/main.zip"

    # 非法镜像条目（无 scheme）被跳过
    _set_mirrors(monkeypatch, zip_="not-a-url,https://mirror-z.test/{url}")
    urls = gh.zip_download_urls("owner/repo", "main")
    assert urls == [
        "https://github.com/owner/repo/archive/refs/heads/main.zip",
        "https://mirror-z.test/https://github.com/owner/repo/archive/refs/heads/main.zip",
    ]


def test_preview_encodes_branch_and_path(monkeypatch):
    """分支名里的 / 必须整体编码，文件路径的空格编码但保留分隔符。"""
    raw_calls: list[str] = []

    def fake_json(url: str):
        if "/git/trees/" in url:
            assert "/feature%2Fsoil" in url
            return {"tree": [{"path": "skills/soil helper/SKILL.md", "type": "blob"}]}
        return {
            "default_branch": "feature/soil",
            "license": {"spdx_id": "MIT"},
            "stargazers_count": 3,
        }

    def fake_raw(url: str) -> str:
        raw_calls.append(url)
        return SKILL_MD

    monkeypatch.setattr(gh, "_get_json", fake_json)
    monkeypatch.setattr(gh, "_fetch_raw_text", fake_raw)
    monkeypatch.setattr(gh, "RAW_BASE", "https://raw.test")

    result = gh.preview_skill_from_repo("owner/repo")
    assert result["ref"] == "feature/soil"
    assert raw_calls == ["https://raw.test/owner/repo/feature%2Fsoil/skills/soil%20helper/SKILL.md"]
    assert "feature%2Fsoil" in result["source_url"]
    assert "skills/soil%20helper/SKILL.md" in result["source_url"]
