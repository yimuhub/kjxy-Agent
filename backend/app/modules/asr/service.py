"""语音转写统一服务：本机离线优先、在线通道兜底、旧行为兼容。

路由（按模式）：
- ``auto``：本机模型就绪 → 本机；否则用在线通道；
- ``local``：只用本机（失败给明确错误，不偷偷上传音频）；
- ``online``：只用在线通道；
- ``off``：明确关闭（403），前端引导去设置。

在线通道的解析顺序沿用模型中心的 7 档绑定链（purpose=``asr``），
没有显式绑定时落到「当前生效供应商」——这与 v3.11 的旧行为一致。
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from app.core.config import settings
from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.net_guard import check_egress_url
from app.modules.asr import downloader, local_engine
from app.modules.asr.catalog import ModelSpec, default_model_id, get_model, payload_list
from app.modules.audit.service import log_audit
from app.modules.yards.settings_service import get_setting

log = get_logger(__name__)

SETTINGS_KEY = "asr"
MODES = {"auto", "local", "online", "off"}

AUDIO_MIMES: dict[str, str] = {
    ".webm": "audio/webm",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
}


def _settings(db: Any, yard_id: str) -> dict[str, Any]:
    try:
        return get_setting(db, yard_id, SETTINGS_KEY)
    except Exception as e:  # 设置表不可用时按默认值放行，但不静默
        log.warning("ASR 设置读取失败，按默认值处理：%s", e)
        return {}


def _mode(cfg: dict[str, Any]) -> str:
    mode = str(cfg.get("mode") or "auto")
    return mode if mode in MODES else "auto"


def _selected_model(cfg: dict[str, Any]) -> ModelSpec:
    model = get_model(str(cfg.get("local_model_id") or ""))
    if model is None:
        model = get_model(default_model_id())
    if model is None:  # pragma: no cover - 清单为空属于构建事故
        raise AppError("ASR_NOT_CONFIGURED", "内置语音模型清单为空。", http_status=500)
    return model


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def _sniff_audio(data: bytes, ext: str) -> bool:
    """按文件头判定语音容器；认不出即拒绝，不信任客户端声明。"""
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return ext == ".webm"
    if data.startswith(b"OggS"):
        return ext == ".ogg"
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return ext == ".wav"
    if data.startswith(b"ID3") or (len(data) > 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return ext == ".mp3"
    if len(data) > 12 and data[4:8] == b"ftyp":
        return ext == ".m4a"
    return False


def validate_audio(filename: str, data: bytes) -> tuple[str, str]:
    """返回 (filename, ext)；不符合规则抛 AppError。"""
    name = os.path.basename(filename or "").strip()[:255] or "audio.wav"
    ext = os.path.splitext(name)[1].lower()
    if ext not in AUDIO_MIMES:
        raise AppError(
            "UNSUPPORTED_AUDIO_TYPE",
            f"不支持的语音格式：{ext or '未知'}，支持 WAV/WEBM/OGG/MP3/M4A。",
            http_status=415,
        )
    if not data:
        raise AppError("INVALID_REQUEST", "语音文件为空。", http_status=400)
    max_bytes = settings.transcribe_max_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise AppError(
            "FILE_TOO_LARGE",
            f"语音文件超过 {settings.transcribe_max_mb}MB 上限。",
            http_status=413,
        )
    if not _sniff_audio(data, ext):
        raise AppError(
            "UNSUPPORTED_AUDIO_TYPE",
            "文件内容不是受支持的语音格式（扩展名与内容不一致）。",
            http_status=415,
        )
    return name, ext


# ---------------------------------------------------------------------------
# 在线通道
# ---------------------------------------------------------------------------


def _online_candidates(db: Any, yard_id: str) -> list[dict[str, Any]]:
    """按解析链顺序给出可尝试的在线转写供应商（去重；不存在的候选直接跳过）。"""
    try:
        from app.modules.llm.binding_service import resolve_chain
        from app.modules.llm.supplier_service import build_provider_from_supplier

        chain = resolve_chain(db, yard_id, purpose="asr")
    except Exception as e:  # 解析链异常不影响本机通道
        log.warning("ASR 在线通道解析失败：%s", e)
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for head in chain:
        supplier_id = head.supplier.id
        if supplier_id in seen:
            continue
        seen.add(supplier_id)
        try:
            provider = build_provider_from_supplier(head.supplier, model_override=head.model_name)
        except Exception as e:
            log.warning("ASR 在线通道构建失败 supplier=%s err=%s", supplier_id, e)
            continue
        out.append(
            {
                "supplier_id": supplier_id,
                "provider": head.supplier.name,
                "model": provider.model or settings.asr_model,
                "level": head.level,
                "explicit": head.level == "purpose_binding",
                "base_url": provider.base_url,
                "api_key": provider.api_key,
            }
        )
    return out


def _online_candidate(db: Any, yard_id: str) -> dict[str, Any] | None:
    """状态页展示用的第一候选；没有可用供应商时返回 None（不猜测）。"""
    candidates = _online_candidates(db, yard_id)
    return candidates[0] if candidates else None


async def _post_transcription(
    base_url: str, api_key: str, filename: str, mime: str, data: bytes, model: str
) -> str:
    url = f"{base_url.rstrip('/')}/audio/transcriptions"
    check_egress_url(url, context="asr", allow_local=True)
    payload = {"model": model or settings.asr_model}
    if settings.asr_language:
        payload["language"] = settings.asr_language
    async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
        try:
            resp = await client.post(
                url,
                files={"file": (filename, data, mime)},
                data=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
        except httpx.HTTPError as e:
            raise AppError(
                "ASR_UPSTREAM_ERROR",
                "语音转写请求失败：无法连接在线转写服务。",
                http_status=502,
            ) from e
    if resp.status_code >= 400:
        hint = ""
        lowered = resp.text.lower()
        unsupported = (
            resp.status_code in (404, 405)
            or "not found" in lowered
            or "does not support" in lowered
        )
        if unsupported:
            hint = (
                "（该供应商不提供语音转写，请在「系统设置 → 语音转写」下载本机模型，"
                "或在模型中心绑定转写通道）"
            )
        elif resp.status_code in (401, 403):
            hint = "（在线转写凭据无效或无权访问，请联系管理员检查配置）"
        log.warning("ASR 上游失败 status=%s body=%s", resp.status_code, resp.text[:300])
        raise AppError(
            "ASR_UPSTREAM_ERROR",
            f"语音转写失败：上游返回 {resp.status_code}{hint}。",
            http_status=502,
            details={"upstream_status": resp.status_code},
        )
    try:
        return str(resp.json().get("text") or "").strip()
    except ValueError as e:
        raise AppError(
            "ASR_UPSTREAM_ERROR", "语音转写返回了无法解析的内容。", http_status=502
        ) from e


# ---------------------------------------------------------------------------
# 状态
# ---------------------------------------------------------------------------


def status(db: Any, yard_id: str) -> dict[str, Any]:
    cfg = _settings(db, yard_id)
    mode = _mode(cfg)
    model = _selected_model(cfg)
    state = downloader.status_payload(model.id)
    engine_ok = local_engine.engine_available()
    online = _online_candidate(db, yard_id)
    online_view = (
        {
            "available": True,
            "provider": online["provider"],
            "model": online["model"],
            "level": online["level"],
            "explicit": online["explicit"],
        }
        if online
        else {"available": False}
    )
    local_usable = bool(engine_ok and state["ready"])
    if mode == "off":
        effective = "none"
    elif mode == "local":
        effective = "local" if local_usable else "none"
    elif mode == "online":
        effective = "online" if online_view["available"] else "none"
    else:  # auto
        effective = "local" if local_usable else ("online" if online_view["available"] else "none")

    warnings: list[str] = []
    if not engine_ok:
        warnings.append(local_engine.engine_note())
    if not online_view["available"] and mode in ("auto", "online"):
        warnings.append("未发现可用的在线转写通道（需要在模型中心绑定，或下载本机模型）")
    if mode == "local" and not local_usable:
        warnings.append("本机模型尚未就绪，请先下载；或把模式改为「自动」回落在线上")

    models = []
    for item in payload_list():
        entry = dict(item)
        entry["state"] = downloader.status_payload(item["id"])
        models.append(entry)

    return {
        "mode": mode,
        "settings": {"mode": mode, "local_model_id": model.id},
        "platform": local_engine.platform_info(),
        "local": {
            "model": model.payload(),
            "state": state,
            "engine_available": engine_ok,
            "usable": local_usable,
        },
        "online": online_view,
        "effective": effective,
        "models": models,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# 转写
# ---------------------------------------------------------------------------


async def transcribe(db: Any, ctx: YardContext, file: Any) -> dict[str, Any]:
    """语音转写：校验 → 本机/在线 → 返回文本（音频不落库）。"""
    data = file.file.read()
    filename, ext = validate_audio(file.filename or "", data)
    cfg = _settings(db, ctx.yard_id)
    mode = _mode(cfg)
    if mode == "off":
        raise AppError(
            "ASR_DISABLED",
            "本小院已关闭语音转写；可在「系统设置 → 语音转写」重新启用。",
            http_status=403,
        )
    model = _selected_model(cfg)
    state = downloader.status_payload(model.id)
    started = time.perf_counter()
    notes: list[str] = []
    local_usable = bool(state["ready"] and local_engine.engine_available())
    if mode == "local" and not local_usable:
        # 明确选了本机就不能把音频偷偷传到线上；前端据此引导下载模型
        raise AppError(
            "ASR_LOCAL_NOT_READY",
            "本机语音模型尚未就绪：请在「系统设置 → 语音转写」下载模型，"
            "或把模式改为「自动」让在线通道兜底。",
            http_status=409,
        )

    if mode in ("auto", "local") and local_usable:
        try:
            text = await _run_local(model, data)
        except local_engine.LocalEngineError as e:
            notes.append(str(e))
            log.warning("ASR 本机识别失败 model=%s err=%s", model.id, e)
        except Exception as e:  # 防御：本机异常不阻断在线
            notes.append(type(e).__name__)
            log.warning("ASR 本机识别异常 model=%s err=%s", model.id, e)
        else:
            if text:
                return _done(db, ctx, text, "local", model.name, len(data), started)
            # 本机已经完整跑过一遍：确认是静音/无内容，不必再把音频传到线上
            raise AppError(
                "ASR_EMPTY_RESULT",
                "没有识别到语音内容，请靠近麦克风后重试。",
                http_status=422,
            )
        if mode == "local":
            raise AppError(
                "ASR_LOCAL_FAILED",
                f"本机语音转写失败（{'；'.join(notes) or '引擎不可用'}）。"
                "可在「系统设置 → 语音转写」重新下载模型，或改用在线转写。",
                http_status=502,
            )

    candidates = _online_candidates(db, ctx.yard_id)
    if not candidates:
        raise AppError(
            "ASR_NOT_CONFIGURED",
            "还没有可用的语音转写通道：请在「系统设置 → 语音转写」下载本机模型，"
            "或在「模型中心 → 语音转写」绑定在线转写供应商。",
            http_status=503,
        )
    last_error: AppError | None = None
    for online in candidates:
        try:
            text = (
                await _post_transcription(
                    online["base_url"],
                    online["api_key"],
                    filename,
                    AUDIO_MIMES[ext],
                    data,
                    online["model"],
                )
            ).strip()
        except AppError as e:
            # 该供应商没有转写端点（很常见：聊天模型也在这个链里）→ 试下一个；
            # 鉴权/网络等其他失败直接抛出，避免把同一段音频到处乱传。
            if e.details.get("upstream_status") in (404, 405) and len(candidates) > 1:
                log.info("ASR 在线候选不支持转写，尝试下一个 supplier=%s", online["supplier_id"])
                last_error = e
                continue
            raise
        if not text:
            raise AppError(
                "ASR_EMPTY_RESULT",
                "没有识别到语音内容，请靠近麦克风后重试。",
                http_status=422,
            )
        return _done(
            db,
            ctx,
            text,
            "online",
            f"{online['provider']} / {online['model']}",
            len(data),
            started,
        )
    if last_error is not None:
        raise last_error
    raise AppError(
        "ASR_NOT_CONFIGURED",
        "在线转写通道均不可用，请在「系统设置 → 语音转写」检查配置。",
        http_status=503,
    )


async def _run_local(model: ModelSpec, data: bytes) -> str:
    import asyncio

    return await asyncio.to_thread(local_engine.recognize, model, data)


def _done(
    db: Any,
    ctx: YardContext,
    text: str,
    engine: str,
    model_label: str,
    size: int,
    started: float,
) -> dict[str, Any]:
    latency_ms = int((time.perf_counter() - started) * 1000)
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="chat_audio_transcribed",
        resource_type="chat_multimodal",
        resource_id="",
        result="success",
        request_id=ctx.request_id,
        metadata={
            "bytes": size,
            "chars": len(text),
            "engine": engine,
            "model": model_label,
            "latency_ms": latency_ms,
        },
    )
    db.commit()
    return {"text": text, "engine": engine, "model": model_label}


async def test_transcribe(db: Any, ctx: YardContext, file: Any) -> dict[str, Any]:
    """设置页「测试转写」：按当前生效通道真实转写一段音频，返回引擎、模型与文本。"""
    payload = await transcribe(db, ctx, file)
    payload["ok"] = bool(payload.get("text"))
    return payload


__all__ = [
    "AUDIO_MIMES",
    "MODES",
    "SETTINGS_KEY",
    "status",
    "test_transcribe",
    "transcribe",
]
