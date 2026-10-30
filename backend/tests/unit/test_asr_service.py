"""语音转写（v3.30）：校验、路由、状态与本机模型清单。

口径：
1. 只信文件头：扩展名与内容不一致一律 415；
2. 关闭即拒绝（403），显式本机但模型未就绪不得偷偷转线上（409）；
3. 本机跑完但结果为空 → 422，不再把同一段音频传到线上；
4. 在线通道没有可用供应商 → 503，绝不编造文本；
5. 模型清单的校验和是安装前提（空 sha256 视为清单未就绪）。
"""

import io

import pytest
from app.core.deps import YardContext
from app.core.errors import AppError
from app.modules.asr import catalog, downloader, service
from fastapi import UploadFile

WAV_BYTES = b"RIFF" + b"\x00\x00\x00\x00" + b"WAVE" + b"\x00" * 64
WEBM_BYTES = b"\x1aE\xdf\xa3" + b"\x00" * 64
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _ctx(yard_id: str = "t1", user_id: str = "u1", roles=("yard_admin",)) -> YardContext:
    return YardContext(yard_id=yard_id, user_id=user_id, roles=list(roles), request_id="r1")


def _audio(filename: str, data: bytes) -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=filename)


def _settings(mode: str = "auto"):
    return lambda db, yard_id: {"mode": mode, "local_model_id": catalog.default_model_id()}


def _online(model: str = "whisper-1"):
    return {
        "supplier_id": "sup-1",
        "provider": "本地测试供应商",
        "model": model,
        "level": "purpose_binding",
        "explicit": True,
        "base_url": "http://127.0.0.1:9/v1",
        "api_key": "test-key",
    }


# ---------------------------------------------------------------------------
# 输入校验
# ---------------------------------------------------------------------------


def test_validate_audio_rejects_wrong_extension():
    with pytest.raises(AppError) as exc:
        service.validate_audio("clip.mp4", WEBM_BYTES)
    assert exc.value.code == "UNSUPPORTED_AUDIO_TYPE"
    assert exc.value.http_status == 415


def test_validate_audio_rejects_renamed_content():
    with pytest.raises(AppError) as exc:
        service.validate_audio("fake.wav", PNG_BYTES)
    assert exc.value.code == "UNSUPPORTED_AUDIO_TYPE"
    assert exc.value.http_status == 415


def test_validate_audio_rejects_empty_and_oversize(monkeypatch):
    with pytest.raises(AppError) as empty:
        service.validate_audio("a.wav", b"")
    assert empty.value.code == "INVALID_REQUEST"

    from app.core.config import settings

    monkeypatch.setattr(settings, "transcribe_max_mb", 0)
    with pytest.raises(AppError) as big:
        service.validate_audio("a.wav", WAV_BYTES)
    assert big.value.code == "FILE_TOO_LARGE"


def test_validate_audio_accepts_known_containers():
    assert service.validate_audio("a.wav", WAV_BYTES) == ("a.wav", ".wav")
    assert service.validate_audio("../evil/a.webm", WEBM_BYTES) == ("a.webm", ".webm")


# ---------------------------------------------------------------------------
# 状态
# ---------------------------------------------------------------------------


def test_status_defaults_to_auto_without_local_or_online(db_session, monkeypatch):
    monkeypatch.setattr(service, "_settings", _settings("auto"))
    # 不依赖开发机实际装没装模型：显式置为「未就绪 + 引擎缺失」
    monkeypatch.setattr(
        service.downloader, "status_payload", lambda mid: {"ready": False, "status": "idle"}
    )
    monkeypatch.setattr(service.local_engine, "engine_available", lambda: False)
    view = service.status(db_session, "yard-x")
    assert view["mode"] == "auto"
    assert view["effective"] == "none"
    assert view["local"]["usable"] is False
    assert view["online"]["available"] is False
    assert view["platform"]["engine"] == "sherpa-onnx"
    assert view["models"] and view["models"][0]["id"] == catalog.default_model_id()
    assert any("在线转写通道" in w for w in view["warnings"])


def test_status_off_mode_has_no_effective_channel(db_session, monkeypatch):
    monkeypatch.setattr(service, "_settings", _settings("off"))
    view = service.status(db_session, "yard-x")
    assert view["effective"] == "none"


def test_status_effective_local_when_model_ready(db_session, monkeypatch):
    monkeypatch.setattr(service, "_settings", _settings("auto"))
    monkeypatch.setattr(
        service.downloader, "status_payload", lambda mid: {"ready": True, "status": "ready"}
    )
    monkeypatch.setattr(service.local_engine, "engine_available", lambda: True)
    view = service.status(db_session, "yard-x")
    assert view["local"]["usable"] is True
    assert view["effective"] == "local"


# ---------------------------------------------------------------------------
# 转写路由
# ---------------------------------------------------------------------------


async def test_transcribe_rejected_when_disabled(db_session, monkeypatch):
    monkeypatch.setattr(service, "_settings", _settings("off"))
    with pytest.raises(AppError) as exc:
        await service.transcribe(db_session, _ctx(), _audio("a.wav", WAV_BYTES))
    assert exc.value.code == "ASR_DISABLED"
    assert exc.value.http_status == 403


async def test_local_mode_not_ready_never_goes_online(db_session, monkeypatch):
    monkeypatch.setattr(service, "_settings", _settings("local"))
    monkeypatch.setattr(
        service.downloader, "status_payload", lambda mid: {"ready": False, "status": "idle"}
    )
    called: list[str] = []
    monkeypatch.setattr(
        service, "_online_candidates", lambda db, yard_id: called.append("online") or [_online()]
    )
    with pytest.raises(AppError) as exc:
        await service.transcribe(db_session, _ctx(), _audio("a.wav", WAV_BYTES))
    assert exc.value.code == "ASR_LOCAL_NOT_READY"
    assert exc.value.http_status == 409
    assert called == []


async def test_auto_uses_local_when_ready(db_session, monkeypatch):
    monkeypatch.setattr(service, "_settings", _settings("auto"))
    monkeypatch.setattr(
        service.downloader, "status_payload", lambda mid: {"ready": True, "status": "ready"}
    )
    monkeypatch.setattr(service.local_engine, "engine_available", lambda: True)

    async def fake_local(model, data):
        assert data == WAV_BYTES
        return "三号田有稻飞虱"

    monkeypatch.setattr(service, "_run_local", fake_local)
    monkeypatch.setattr(
        service, "_online_candidates", lambda db, yard_id: pytest.fail("本机就绪不应走在线")
    )
    result = await service.transcribe(db_session, _ctx(), _audio("a.wav", WAV_BYTES))
    assert result["engine"] == "local"
    assert result["text"] == "三号田有稻飞虱"


async def test_local_empty_result_is_422_without_online_upload(db_session, monkeypatch):
    monkeypatch.setattr(service, "_settings", _settings("auto"))
    monkeypatch.setattr(
        service.downloader, "status_payload", lambda mid: {"ready": True, "status": "ready"}
    )
    monkeypatch.setattr(service.local_engine, "engine_available", lambda: True)

    async def empty_local(model, data):
        return ""

    monkeypatch.setattr(service, "_run_local", empty_local)
    monkeypatch.setattr(
        service, "_online_candidates", lambda db, yard_id: pytest.fail("结果为空不应转线上")
    )
    with pytest.raises(AppError) as exc:
        await service.transcribe(db_session, _ctx(), _audio("a.wav", WAV_BYTES))
    assert exc.value.code == "ASR_EMPTY_RESULT"
    assert exc.value.http_status == 422


async def test_auto_falls_back_online_when_local_engine_fails(db_session, monkeypatch):
    monkeypatch.setattr(service, "_settings", _settings("auto"))
    monkeypatch.setattr(
        service.downloader, "status_payload", lambda mid: {"ready": True, "status": "ready"}
    )
    monkeypatch.setattr(service.local_engine, "engine_available", lambda: True)

    async def broken_local(model, data):
        raise service.local_engine.LocalEngineError("模型加载失败：RuntimeError")

    async def fake_post(base_url, api_key, filename, mime, data, model):
        assert mime == "audio/wav"
        return " 稻瘟病需要先取样确认 "

    monkeypatch.setattr(service, "_run_local", broken_local)
    monkeypatch.setattr(service, "_online_candidates", lambda db, yard_id: [_online()])
    monkeypatch.setattr(service, "_post_transcription", fake_post)
    result = await service.transcribe(db_session, _ctx(), _audio("a.wav", WAV_BYTES))
    assert result["engine"] == "online"
    assert result["text"] == "稻瘟病需要先取样确认"


async def test_online_empty_result_is_422(db_session, monkeypatch):
    monkeypatch.setattr(service, "_settings", _settings("online"))
    monkeypatch.setattr(service, "_online_candidates", lambda db, yard_id: [_online()])

    async def empty_post(*args, **kwargs):
        return ""

    monkeypatch.setattr(service, "_post_transcription", empty_post)
    with pytest.raises(AppError) as exc:
        await service.transcribe(db_session, _ctx(), _audio("a.wav", WAV_BYTES))
    assert exc.value.code == "ASR_EMPTY_RESULT"


async def test_online_404_tries_next_candidate(db_session, monkeypatch):
    """链上第一个供应商是聊天模型（无转写端点）时，继续试下一个而不是直接报错。"""
    monkeypatch.setattr(service, "_settings", _settings("online"))
    first = {**_online("chat-model"), "supplier_id": "s1"}
    second = {**_online("asr-model"), "supplier_id": "s2"}
    monkeypatch.setattr(service, "_online_candidates", lambda db, yard_id: [first, second])
    calls: list[str] = []

    async def fake_post(base_url, api_key, filename, mime, data, model):
        calls.append(model)
        if model == "chat-model":
            raise AppError(
                "ASR_UPSTREAM_ERROR",
                "语音转写失败：上游返回 404。",
                http_status=502,
                details={"upstream_status": 404},
            )
        return " 第二家可用 "

    monkeypatch.setattr(service, "_post_transcription", fake_post)
    result = await service.transcribe(db_session, _ctx(), _audio("a.wav", WAV_BYTES))
    assert calls == ["chat-model", "asr-model"]
    assert result["text"] == "第二家可用"
    assert result["model"].endswith("asr-model")


async def test_no_channel_is_503(db_session, monkeypatch):
    monkeypatch.setattr(service, "_settings", _settings("auto"))
    monkeypatch.setattr(service, "_online_candidates", lambda db, yard_id: [])
    with pytest.raises(AppError) as exc:
        await service.transcribe(db_session, _ctx(), _audio("a.wav", WAV_BYTES))
    assert exc.value.code == "ASR_NOT_CONFIGURED"
    assert exc.value.http_status == 503


# ---------------------------------------------------------------------------
# 模型清单与安装标记
# ---------------------------------------------------------------------------


def test_catalog_manifest_is_ready():
    for model in catalog.MODELS:
        assert model.files
        for item in model.files:
            assert item.sha256 and len(item.sha256) == 64
            assert item.size > 0
            assert item.mirrors


def test_model_ready_requires_matching_marker(tmp_path, monkeypatch):
    fake = catalog.ModelSpec(
        id="tiny-demo",
        name="演示模型",
        description="单元测试用",
        languages=("zh",),
        min_ram_mb=64,
        license="test",
        tier="recommended",
        files=(catalog.ModelFile(name="model.onnx", size=4, sha256="a" * 64, mirrors=("m",)),),
    )
    monkeypatch.setattr(downloader, "get_model", lambda mid: fake if mid == fake.id else None)
    monkeypatch.setattr(downloader.storage, "model_dir", lambda mid: tmp_path / mid)
    monkeypatch.setattr(
        downloader.storage, "marker_path", lambda mid: tmp_path / mid / ".verified.json"
    )

    assert downloader.is_ready(fake.id) is False
    base = tmp_path / fake.id
    base.mkdir()
    (base / "model.onnx").write_bytes(b"abcd")
    assert downloader.is_ready(fake.id) is False  # 没有标记不算就绪

    downloader._write_marker(fake)
    assert downloader.is_ready(fake.id) is True
    assert downloader.installed_size(fake.id) == 4


def test_download_start_rejects_unknown_or_unready_manifest(monkeypatch):
    with pytest.raises(KeyError):
        downloader.start("no-such-model")

    broken = catalog.ModelSpec(
        id="no-sha",
        name="缺校验和",
        description="",
        languages=("zh",),
        min_ram_mb=64,
        license="test",
        tier="recommended",
        files=(catalog.ModelFile(name="model.onnx", size=4, sha256="", mirrors=("m",)),),
    )
    monkeypatch.setattr(downloader, "get_model", lambda mid: broken if mid == broken.id else None)
    with pytest.raises(ValueError):
        downloader.start(broken.id)


# ---------------------------------------------------------------------------
# 设置项校验
# ---------------------------------------------------------------------------


def test_settings_rejects_unknown_mode_and_model(db_session):
    from app.modules.yards import settings_service

    with pytest.raises(AppError) as bad_mode:
        settings_service.update_setting(
            db_session,
            yard_id="yard-asr",
            key="asr",
            value={"mode": "always"},
            actor_id="u1",
            request_id="r1",
        )
    assert bad_mode.value.code == "INVALID_REQUEST"

    with pytest.raises(AppError) as bad_model:
        settings_service.update_setting(
            db_session,
            yard_id="yard-asr",
            key="asr",
            value={"local_model_id": "no-such-model"},
            actor_id="u1",
            request_id="r1",
        )
    assert bad_model.value.code == "INVALID_REQUEST"

    saved = settings_service.update_setting(
        db_session,
        yard_id="yard-asr",
        key="asr",
        value={"mode": "local", "local_model_id": "sense-voice-small"},
        actor_id="u1",
        request_id="r1",
    )
    assert saved == {"mode": "local", "local_model_id": "sense-voice-small"}


# ---------------------------------------------------------------------------
# HTTP 入口
# ---------------------------------------------------------------------------


def _auth(client, db_session, username: str, role: str) -> dict[str, str]:
    from app.core.security import create_access_token
    from app.modules.auth import repository as auth_repo
    from app.modules.yards import repository as yard_repo

    yard = yard_repo.create_yard(db_session, name=f"语音小院-{username}")
    user = auth_repo.create_user(db_session, username=username, password="pw-asr")
    yard_repo.add_membership(db_session, yard_id=yard.id, user_id=user.id, role=role)
    db_session.commit()
    token = create_access_token(user_id=user.id, yard_id=yard.id, roles=[role])
    return {"Authorization": f"Bearer {token}"}


def test_api_transcribe_validation(client, db_session):
    headers = _auth(client, db_session, "asr-operator", "operator")

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
    assert not_configured.json()["error"]["code"] == "ASR_NOT_CONFIGURED"


def test_api_status_and_models_are_readable_by_members(client, db_session):
    headers = _auth(client, db_session, "asr-viewer", "operator")

    status = client.get("/api/v1/asr/status", headers=headers)
    assert status.status_code == 200
    body = status.json()["data"]
    assert body["mode"] in {"auto", "local", "online", "off"}
    assert body["platform"]["engine"] == "sherpa-onnx"
    assert body["local"]["model"]["id"] == catalog.default_model_id()

    models = client.get("/api/v1/asr/models", headers=headers)
    assert models.status_code == 200
    assert models.json()["data"]["models"][0]["size_bytes"] > 0


def test_api_download_requires_settings_permission(client, db_session):
    headers = _auth(client, db_session, "asr-junior", "operator")
    model_id = catalog.default_model_id()

    denied = client.post(f"/api/v1/asr/models/{model_id}/download", headers=headers)
    assert denied.status_code == 403

    missing = client.post("/api/v1/asr/models/not-exists/download", headers=headers)
    assert missing.status_code == 403  # 权限先于资源存在性，避免探测
