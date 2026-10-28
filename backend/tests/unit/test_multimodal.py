"""多模态输入（v3.11）：语音转写与图片理解的校验、边界与诚实降级。

口径：
1. 只信文件头：扩展名与内容不一致一律 415；
2. 未配置供应商时明确 503，绝不返回编造内容；
3. 图片识别结果写回附件文本并带“不可信数据”边界进入上下文；
4. 非图片附件不允许识别。
"""

import io
from types import SimpleNamespace

import pytest
from app.core.deps import YardContext
from app.core.errors import AppError
from app.modules.chat import attachments, multimodal
from app.modules.chat.models import ATT_DOCUMENT, ATT_IMAGE
from fastapi import UploadFile

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
WEBM_BYTES = b"\x1aE\xdf\xa3" + b"\x00" * 64


def _ctx(yard_id: str = "t1", user_id: str = "u1") -> YardContext:
    return YardContext(yard_id=yard_id, user_id=user_id, roles=["yard_admin"], request_id="r1")


def _audio(filename: str, data: bytes) -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=filename)


def _provider(base_url: str = "http://127.0.0.1:9/v1", model: str = "vision-x"):
    return SimpleNamespace(base_url=base_url, api_key="test-key", model=model)


@pytest.mark.asyncio
async def test_transcribe_rejects_wrong_extension(db_session):
    with pytest.raises(AppError) as exc:
        await multimodal.transcribe_audio(db_session, _ctx(), _audio("clip.mp4", WEBM_BYTES))
    assert exc.value.code == "UNSUPPORTED_AUDIO_TYPE"
    assert exc.value.http_status == 415


@pytest.mark.asyncio
async def test_transcribe_rejects_renamed_content(db_session):
    with pytest.raises(AppError) as exc:
        await multimodal.transcribe_audio(db_session, _ctx(), _audio("fake.webm", PNG_BYTES))
    assert exc.value.code == "UNSUPPORTED_AUDIO_TYPE"


@pytest.mark.asyncio
async def test_transcribe_rejects_empty_and_oversize(db_session, monkeypatch):
    with pytest.raises(AppError) as empty:
        await multimodal.transcribe_audio(db_session, _ctx(), _audio("a.webm", b""))
    assert empty.value.code == "INVALID_REQUEST"

    from app.core.config import settings

    monkeypatch.setattr(settings, "transcribe_max_mb", 0)
    with pytest.raises(AppError) as big:
        await multimodal.transcribe_audio(db_session, _ctx(), _audio("a.webm", WEBM_BYTES))
    assert big.value.code == "FILE_TOO_LARGE"


@pytest.mark.asyncio
async def test_transcribe_without_provider_is_503(db_session, monkeypatch):
    """fake provider 没有 base_url/key：必须明确报未配置，而不是编造文本。"""
    with pytest.raises(AppError) as exc:
        await multimodal.transcribe_audio(db_session, _ctx(), _audio("a.webm", WEBM_BYTES))
    assert exc.value.code == "MULTIMODAL_NOT_CONFIGURED"
    assert exc.value.http_status == 503


@pytest.mark.asyncio
async def test_transcribe_returns_upstream_text(db_session, monkeypatch):
    monkeypatch.setattr(multimodal, "get_provider", lambda: _provider())

    async def fake_post(base_url, api_key, filename, mime, data):
        assert base_url == "http://127.0.0.1:9/v1"
        assert mime == "audio/webm"
        return " 三号田出现稻飞虱 "

    monkeypatch.setattr(multimodal, "_post_transcription", fake_post)
    result = await multimodal.transcribe_audio(db_session, _ctx(), _audio("a.webm", WEBM_BYTES))
    assert result["text"] == "三号田出现稻飞虱"
    assert result["model"]


@pytest.mark.asyncio
async def test_transcribe_empty_result_is_422(db_session, monkeypatch):
    monkeypatch.setattr(multimodal, "get_provider", lambda: _provider())

    async def fake_post(*args, **kwargs):
        return ""

    monkeypatch.setattr(multimodal, "_post_transcription", fake_post)
    with pytest.raises(AppError) as exc:
        await multimodal.transcribe_audio(db_session, _ctx(), _audio("a.webm", WEBM_BYTES))
    assert exc.value.code == "MULTIMODAL_EMPTY_RESULT"


@pytest.mark.asyncio
async def test_recognize_image_writes_back_text(db_session, monkeypatch):
    att = attachments.upload_attachment(
        db_session,
        ctx=_ctx(),
        session_id="sess-1",
        file=UploadFile(file=io.BytesIO(PNG_BYTES), filename="leaf.png"),
    )
    assert att.kind == ATT_IMAGE and att.extracted_text == ""

    monkeypatch.setattr(multimodal, "get_provider", lambda: _provider())

    async def fake_vision(base_url, api_key, model, data_url):
        assert data_url.startswith("data:image/png;base64,")
        assert "不确定" in multimodal.VISION_PROMPT
        return "叶片出现梭形病斑，边缘褐色；图片文字“试验田 A3”"

    monkeypatch.setattr(multimodal, "_post_vision", fake_vision)
    result = await multimodal.recognize_image(db_session, _ctx(), att)
    assert result["chars"] > 0
    assert att.extracted_text.startswith("[图片理解]")
    assert "梭形病斑" in att.extracted_text

    context = attachments.build_attachment_context([att])
    assert "梭形病斑" in context
    assert "不可信数据" in context


@pytest.mark.asyncio
async def test_recognize_rejects_document(db_session):
    att = attachments.upload_attachment(
        db_session,
        ctx=_ctx(),
        session_id="sess-1",
        file=UploadFile(file=io.BytesIO(b"# title"), filename="note.md"),
    )
    assert att.kind == ATT_DOCUMENT
    with pytest.raises(AppError) as exc:
        await multimodal.recognize_image(db_session, _ctx(), att)
    assert exc.value.code == "INVALID_REQUEST"


def test_api_transcribe_validation(client, db_session):
    from app.core.security import create_access_token
    from app.modules.auth import repository as auth_repo
    from app.modules.yards import repository as yard_repo

    yard = yard_repo.create_yard(db_session, name="多模态小院")
    user = auth_repo.create_user(db_session, username="mm-user", password="pw-mm")
    yard_repo.add_membership(db_session, yard_id=yard.id, user_id=user.id, role="operator")
    db_session.commit()
    token = create_access_token(user_id=user.id, yard_id=yard.id, roles=["operator"])
    headers = {"Authorization": f"Bearer {token}"}

    rejected = client.post(
        "/api/v1/chat/transcribe",
        files={"file": ("clip.mp4", b"\x00" * 16, "video/mp4")},
        headers=headers,
    )
    assert rejected.status_code == 415
    assert rejected.json()["error"]["code"] == "UNSUPPORTED_AUDIO_TYPE"

    not_configured = client.post(
        "/api/v1/chat/transcribe",
        files={"file": ("clip.webm", WEBM_BYTES, "audio/webm")},
        headers=headers,
    )
    assert not_configured.status_code == 503
    assert not_configured.json()["error"]["code"] == "MULTIMODAL_NOT_CONFIGURED"
