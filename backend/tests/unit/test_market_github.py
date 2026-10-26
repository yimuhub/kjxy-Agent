"""GitHub 检索与 SKILL.md 预览：许可证过滤、frontmatter 解析、离线降级。"""

from __future__ import annotations

import json

import httpx
import pytest
from app.core.errors import AppError
from app.modules.market import github_source as gh

REPO = "farmfriend-labs/agent-skills-farming"
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


def _transport(handler):
    return httpx.MockTransport(handler)


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
