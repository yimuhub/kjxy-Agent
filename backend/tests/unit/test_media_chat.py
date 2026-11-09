"""对话内媒体（v3.73）：意图识别、签名存储、叠字、授权与额度。"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta

import pytest
from app.core.deps import YardContext
from app.core.errors import AppError
from app.modules.chat import media as chat_media
from app.modules.media import overlay, service, store
from app.modules.media.models import KIND_IMAGE, KIND_VIDEO, MediaAsset
from app.modules.media.overlay import render_poster
from app.modules.yards.repository import create_yard
from PIL import Image


def _ctx(yard_id: str, *, roles: list[str] | None = None, user_id: str = "u-member") -> YardContext:
    return YardContext(
        yard_id=yard_id,
        user_id=user_id,
        roles=roles or ["member"],
        request_id="req-test",
    )


@pytest.mark.parametrize(
    ("text", "kind", "poster", "duration"),
    [
        ("我需要一个关于牡丹特色小院的宣传海报", KIND_IMAGE, True, 5),
        ("给我来一个科技小院 5 秒视频，葡萄园清晨", KIND_VIDEO, False, 5),
        ("生成一张竖版葡萄园插画", KIND_IMAGE, False, 5),
        ("/视频 10秒 麦田航拍", KIND_VIDEO, False, 10),
        ("帮我画个横版葡萄园插画", KIND_IMAGE, False, 5),
    ],
)
def test_detect_media_intent(text, kind, poster, duration):
    intent = chat_media.detect(text)
    assert intent is not None
    assert intent.kind == kind
    assert intent.poster is poster
    assert intent.duration_seconds == duration


@pytest.mark.parametrize(
    "text",
    [
        "",
        "帮我写个海报文案",
        "海报怎么做才好看",
        "我需要一个助手",
        "视频生成怎么配置",
    ],
)
def test_detect_keeps_non_media_turns(text):
    assert chat_media.detect(text) is None


def test_detect_picks_orientation_and_clamps_duration():
    vertical = chat_media.detect("/图 竖版 玉米地")
    assert vertical is not None and vertical.size == "1024x1536"
    long_video = chat_media.detect("/视频 99秒 麦田")
    assert long_video is not None and long_video.duration_seconds == 30


def test_detect_image_count_and_zero_remaining_quota(db_session):
    intent = chat_media.detect("帮我生成 4 张葡萄园插画")
    assert intent is not None and intent.count == 4

    yard = create_yard(db_session, name="媒体额度不足小院")
    db_session.flush()
    ctx = _ctx(yard.id)
    cfg = service.settings_for(db_session, yard.id)
    for index in range(cfg["image"]["daily_limit"]):
        db_session.add(
            MediaAsset(
                yard_id=yard.id,
                created_by=ctx.user_id,
                kind=KIND_IMAGE,
                status="ready",
                prompt=f"插画 {index}",
            )
        )
    db_session.flush()
    with pytest.raises(AppError) as exc:
        service.enforce_quota(db_session, ctx, KIND_IMAGE, cfg, count=1)
    assert exc.value.code == "MEDIA_QUOTA_EXCEEDED"


def test_store_signature_roundtrip_and_traversal():
    key = "ab/0123456789abcdef.mp4"
    url = store.media_url(key)
    signature = url.split("sig=")[1]
    assert store.verify_key(key, signature) is True
    assert store.verify_key(key, "0" * 32) is False
    assert store.verify_key("ab/other.mp4", signature) is False
    with pytest.raises(ValueError):
        store.media_storage().path("../escape.txt")


def test_store_write_range_and_delete():
    key = store.save_bytes(b"0123456789", "video/mp4")
    assert store.size_of(key) == 10
    assert store.read_range(key, 4, 4) == b"4567"
    assert store.read_bytes(key).startswith(b"0123")
    assert store.mime_for_key(key) == "video/mp4"
    store.delete(key)
    with pytest.raises(FileNotFoundError):
        store.read_bytes(key)


def _png_bytes(size=(512, 768), color=(200, 220, 180)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_render_poster_overlays_chinese_text():
    source = _png_bytes()
    data, mime, overlaid = render_poster(
        source,
        title="牡丹特色科技小院",
        subtitle="科技助农 · 花期管理",
        footer="小院电话 138-0000-0000",
    )
    assert mime == "image/png"
    # CI 的 Linux runner 没有系统中文字体：此时不伪造叠字，原图返回并如实标记
    if not overlay.has_cjk_font():
        assert overlaid is False
        assert data == source
        return
    assert overlaid is True
    assert data != source
    with Image.open(io.BytesIO(data)) as image:
        assert image.size == (512, 768)


def test_render_poster_without_cjk_font_is_honest(monkeypatch):
    monkeypatch.setattr(overlay, "font_path", lambda: "")
    monkeypatch.setattr(overlay, "has_cjk_font", lambda: False)
    source = _png_bytes()
    data, mime, overlaid = render_poster(source, title="牡丹特色科技小院")
    assert (data, mime, overlaid) == (source, "image/png", False)


def test_video_authorization_and_budget(db_session):
    yard = create_yard(db_session, name="媒体授权小院")
    db_session.flush()
    cfg = service.settings_for(db_session, yard.id)

    member = _ctx(yard.id)
    commander_agent = _ctx(yard.id)
    admin = _ctx(yard.id, roles=["yard_admin"])
    assert service.video_authorized(member, cfg, executor_is_commander=False) is False
    assert service.video_authorized(commander_agent, cfg, executor_is_commander=True) is True
    assert service.video_authorized(admin, cfg, executor_is_commander=False) is True
    assert service.budget_seconds(cfg, member) == 10
    assert service.budget_seconds(cfg, admin) == 30


def test_quota_counts_assets_and_card_payload(db_session):
    yard = create_yard(db_session, name="媒体额度小院")
    db_session.flush()
    ctx = _ctx(yard.id)
    cfg = service.settings_for(db_session, yard.id)

    image = MediaAsset(
        yard_id=yard.id,
        created_by=ctx.user_id,
        kind=KIND_IMAGE,
        status="ready",
        prompt="葡萄园插画",
        storage_key="ef/0123456789abcdef.png",
        url=store.media_url("ef/0123456789abcdef.png"),
        mime="image/png",
        size_bytes=1024,
        width=1024,
        height=1024,
    )
    failed_video = MediaAsset(
        yard_id=yard.id,
        created_by=ctx.user_id,
        kind=KIND_VIDEO,
        status="failed",
        prompt="失败的视频",
        error_message="供应商不可用",
    )
    db_session.add_all([image, failed_video])
    db_session.flush()

    snapshot = service.quota_snapshot(db_session, ctx, KIND_IMAGE, cfg)
    assert snapshot["used"] == 1 and snapshot["limit"] == 20
    # 提交前失败、没有上游任务号的视频不占额度
    video_snapshot = service.quota_snapshot(db_session, ctx, KIND_VIDEO, cfg)
    assert video_snapshot["used"] == 0

    card = service.card_payload([image], prompt="葡萄园插画")
    assert card["type"] == "media" and card["status"] == "ready"
    assert card["assets"][0]["width"] == 1024

    assert service.public_asset(db_session, ctx, image.id)["id"] == image.id
    stranger = _ctx(yard.id, user_id="u-other")
    with pytest.raises(AppError):
        service.public_asset(db_session, stranger, image.id)


def test_expire_media_clears_file_and_marks_failed(db_session):
    yard = create_yard(db_session, name="媒体过期小院")
    db_session.flush()
    ctx = _ctx(yard.id)
    key = store.save_bytes(b"fake-video", "video/mp4")
    asset = MediaAsset(
        yard_id=yard.id,
        created_by=ctx.user_id,
        kind=KIND_VIDEO,
        status="ready",
        prompt="过期视频",
        storage_key=key,
        url=store.media_url(key),
        mime="video/mp4",
        size_bytes=10,
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    db_session.add(asset)
    db_session.flush()

    assert service.expire_media(db_session) == 1
    db_session.refresh(asset)
    assert asset.status == "failed"
    assert asset.error_code == "MEDIA_EXPIRED"
    assert asset.url is None
    with pytest.raises(FileNotFoundError):
        store.read_bytes(key)


def test_card_payload_reports_running_video():
    asset = MediaAsset(
        id="asset-running-001",
        yard_id="y",
        created_by="u",
        kind=KIND_VIDEO,
        status="running",
        prompt="麦田航拍",
        upstream_task_id="task-1",
        duration_seconds=5,
        created_at=datetime.now(UTC),
    )
    card = service.card_payload([asset], prompt="麦田航拍", hint="")
    assert card["status"] == "running"
    assert card["hint"]
    assert card["retention_days"] == 30
