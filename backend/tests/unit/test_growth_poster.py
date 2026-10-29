"""海报生成、签名取图与 image.generate 工具测试（v3.26 D-1.4）。"""

from __future__ import annotations

import pytest
from app.core.errors import AppError
from app.core.security import create_access_token
from app.modules.growth import poster
from app.modules.llm.images import ImageAsset, ImageBatch, ImageRequest
from app.modules.tools.models import ToolDefinition
from app.modules.tools.registry import tool_registry
from app.modules.yards.repository import create_yard


def test_template_svg_escapes_and_sizes():
    svg = poster.build_template_svg(
        title="中秋礼盒 <预售>",
        subtitle="赣南脐橙 · 现摘现发",
        footer="小院电话 138-0000-0000",
        size="1024x1536",
        theme="orchard",
    ).decode("utf-8")
    assert svg.startswith("<svg")
    assert 'width="1024"' in svg and 'height="1536"' in svg
    assert "<预售>" not in svg  # 转义后不能直接出现标签
    assert "&lt;预售&gt;" in svg
    assert "二维码位" in svg
    assert "赣南脐橙" in svg


def test_template_svg_falls_back_to_default_theme():
    svg = poster.build_template_svg(title="海报", theme="not-exist").decode("utf-8")
    assert poster.THEMES[poster.DEFAULT_THEME]["bg1"] in svg


def test_signed_url_roundtrip():
    key = "ab/0123456789abcdef.svg"
    url = poster.poster_url(key)
    assert url.startswith("/api/v1/growth/posters/")
    signature = url.split("sig=")[1]
    assert poster.verify_key(key, signature) is True
    assert poster.verify_key(key, "0" * 32) is False
    assert poster.verify_key("ab/other.svg", signature) is False


@pytest.mark.asyncio
async def test_generate_poster_falls_back_without_supplier(db_session):
    yard = create_yard(db_session, name="海报模板小院")
    db_session.flush()

    result = await poster.generate_poster(
        db_session,
        yard_id=yard.id,
        prompt="给脐橙做一张中秋礼盒预售海报，右下角留二维码",
        title="中秋礼盒预售",
        subtitle="赣南脐橙 · 现摘现发",
        footer="科技小院",
    )
    assert result["ok"] is True
    assert result["fallback"] is True
    assert "尚未配置图像生成供应商" in result["notice"]
    assert result["title"] == "中秋礼盒预售"
    asset = result["assets"][0]
    assert asset["mime"] == "image/svg+xml"
    assert asset["bytes"] > 0
    # 落盘 + 签名可取
    signature = asset["url"].split("sig=")[1]
    assert poster.verify_key(asset["key"], signature)
    with poster.poster_storage().open(asset["key"]) as fh:
        assert fh.read().startswith(b"<svg")


@pytest.mark.asyncio
async def test_generate_poster_uses_provider(db_session, monkeypatch):
    yard = create_yard(db_session, name="海报供应商小院")
    db_session.flush()

    class _Provider:
        provider_label = "supplier:fake-image"

        async def generate(self, request: ImageRequest) -> ImageBatch:
            assert request.prompt
            return ImageBatch(
                assets=(ImageAsset(data=b"\x89PNG\r\n\x1a\n" + b"x" * 8, mime="image/png"),),
                provider_label="supplier:fake-image",
                model="fake-image-1",
                latency_ms=3,
            )

    monkeypatch.setattr(
        poster, "resolve_image_provider", lambda *a, **kw: (_Provider(), "purpose_binding")
    )
    result = await poster.generate_poster(
        db_session, yard_id=yard.id, prompt="给脐橙做一张礼盒海报", size="1024x1024"
    )
    assert result["fallback"] is False
    assert result["notice"] == ""
    assert result["provider"] == "supplier:fake-image"
    assert result["model"] == "fake-image-1"
    assert result["assets"][0]["mime"] == "image/png"


@pytest.mark.asyncio
async def test_generate_poster_degrades_on_provider_error(db_session, monkeypatch):
    yard = create_yard(db_session, name="海报失败降级小院")
    db_session.flush()

    class _Provider:
        provider_label = "supplier:broken"

        async def generate(self, request: ImageRequest) -> ImageBatch:
            raise AppError("IMAGE_ERROR", "图像服务暂时不可用。", http_status=502)

    monkeypatch.setattr(
        poster, "resolve_image_provider", lambda *a, **kw: (_Provider(), "purpose_binding")
    )
    result = await poster.generate_poster(db_session, yard_id=yard.id, prompt="给脐橙做海报")
    assert result["fallback"] is True
    assert "不可用" in result["notice"]
    assert result["assets"][0]["mime"] == "image/svg+xml"


@pytest.mark.asyncio
async def test_generate_poster_rejects_too_short_prompt(db_session):
    with pytest.raises(AppError) as exc:
        await poster.generate_poster(db_session, yard_id="y-1", prompt="海报")
    assert exc.value.code == "INVALID_REQUEST"


def test_image_tool_meta_and_registration():
    meta = poster.tool_meta()
    assert meta["code"] == poster.TOOL_CODE
    assert meta["risk_level"] == "write_reversible"
    assert meta["input_schema_json"]["required"] == ["prompt"]
    tool = poster.register_image_tool()
    assert tool is not None
    assert tool_registry.get(poster.TOOL_CODE) is not None


def test_seed_growth_tools_writes_both_tools(db_session):
    from app.modules.growth.toolseed import seed_growth_tools

    assert seed_growth_tools(db_session) == 2
    assert seed_growth_tools(db_session) == 0
    codes = {row.code for row in db_session.query(ToolDefinition).all()}
    assert {"compliance.check_copy", "image.generate"} <= codes


def test_seed_growth_tools_keeps_enabled_flag(db_session):
    from app.modules.growth.toolseed import seed_growth_tools

    seed_growth_tools(db_session)
    row = db_session.query(ToolDefinition).filter_by(code="image.generate").one()
    row.enabled = False
    db_session.flush()
    assert seed_growth_tools(db_session) == 0
    db_session.refresh(row)
    assert row.enabled is False


def _headers(yard_id: str = "yard-poster") -> dict:
    token = create_access_token(user_id="u-poster", yard_id=yard_id, roles=["yard_admin"])
    return {"Authorization": f"Bearer {token}"}


def test_poster_endpoint_and_signed_read(client):
    created = client.post(
        "/api/v1/growth/posters",
        json={"prompt": "给脐橙做一张中秋礼盒预售海报", "title": "中秋礼盒预售"},
        headers=_headers(),
    )
    assert created.status_code == 200, created.text
    data = created.json()["data"]
    assert data["fallback"] is True
    url = data["assets"][0]["url"]

    fetched = client.get(url)
    assert fetched.status_code == 200
    assert fetched.headers["content-type"].startswith("image/svg+xml")
    assert fetched.content.startswith(b"<svg")

    tampered = client.get(url.replace("sig=", "sig=0"))
    assert tampered.status_code == 404


def test_poster_endpoint_rejects_bad_size(client):
    r = client.post(
        "/api/v1/growth/posters",
        json={"prompt": "给脐橙做一张海报", "size": "4000x4000"},
        headers=_headers(),
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_REQUEST"


def test_poster_endpoint_requires_auth(client):
    assert client.post("/api/v1/growth/posters", json={"prompt": "海报海报"}).status_code == 401
