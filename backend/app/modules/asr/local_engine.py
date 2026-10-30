"""本机离线识别引擎（sherpa-onnx）：懒加载、按模型缓存、可卸载。

设计约束：
1. 纯 CPU、无 GPU/ffmpeg 依赖；输入统一为 16kHz 单声道 PCM16 WAV；
2. 识别器构建失败（缺 wheel、老 CPU、模型损坏）只返回明确错误，由上层回落到在线；
3. Windows 下加载中的文件不能删除——删除/升级模型前先 ``unload()``。
"""

from __future__ import annotations

import array
import io
import os
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.modules.asr.catalog import ModelSpec
from app.modules.asr.storage import model_dir

log = get_logger(__name__)

#: 统一采样率：前端录音与模型输入一致，避免额外重采样依赖
SAMPLE_RATE = 16_000


class LocalEngineError(RuntimeError):
    """本机引擎不可用（缺运行时/模型损坏/加载失败），上层据此回落在线。"""


_LOCK = threading.Lock()
_RECOGNIZERS: dict[str, Any] = {}


def engine_available() -> bool:
    try:
        import sherpa_onnx  # noqa: F401
    except Exception:
        return False
    return True


def engine_note() -> str:
    if engine_available():
        return "sherpa-onnx 已就绪"
    return "本机引擎未安装（sherpa-onnx 依赖缺失）"


def platform_info() -> dict[str, Any]:
    return {
        "os": sys.platform,
        "arch": _arch(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "engine": "sherpa-onnx",
        "engine_available": engine_available(),
        "engine_note": engine_note(),
    }


def _arch() -> str:
    import platform

    machine = platform.machine().lower()
    return {"x86_64": "x86_64", "amd64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}.get(
        machine, machine
    )


def _recognizer(model: ModelSpec) -> Any:
    """按模型目录懒加载识别器（进程内复用；删除模型前必须 unload）。"""
    with _LOCK:
        cached = _RECOGNIZERS.get(model.id)
        if cached is not None:
            return cached
        try:
            import sherpa_onnx
        except Exception as e:  # pragma: no cover - 环境相关
            raise LocalEngineError("本机识别引擎未安装") from e
        base: Path = model_dir(model.id)
        model_path = base / "model.int8.onnx"
        tokens_path = base / "tokens.txt"
        if not model_path.exists() or not tokens_path.exists():
            raise LocalEngineError("模型文件不完整，请重新下载")
        try:
            num_threads = max(1, min(4, os.cpu_count() or 2))
            if model.decoder == "sense-voice":
                recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                    model=str(model_path),
                    tokens=str(tokens_path),
                    num_threads=num_threads,
                    sample_rate=SAMPLE_RATE,
                    feature_dim=80,
                    decoding_method="greedy_search",
                    language="zh",
                    use_itn=True,  # 数字/标点规整：口述剂量与日期时明显更好读
                    debug=False,
                )
            else:
                recognizer = sherpa_onnx.OfflineRecognizer.from_paraformer(
                    paraformer=str(model_path),
                    tokens=str(tokens_path),
                    num_threads=num_threads,
                    sample_rate=SAMPLE_RATE,
                    feature_dim=80,
                    decoding_method="greedy_search",
                    debug=False,
                )
        except Exception as e:
            raise LocalEngineError(f"模型加载失败：{type(e).__name__}") from e
        _RECOGNIZERS[model.id] = recognizer
        log.info("ASR 本机模型已加载 model=%s", model.id)
        return recognizer


def unload(model_id: str | None = None) -> None:
    """卸载识别器（删除/升级模型前调用；Windows 文件锁必需）。"""
    with _LOCK:
        if model_id is None:
            _RECOGNIZERS.clear()
        else:
            _RECOGNIZERS.pop(model_id, None)


def warmup(model: ModelSpec) -> None:
    _recognizer(model)


def recognize(model: ModelSpec, wav_bytes: bytes) -> str:
    """识别一段 WAV；返回文本（可能为空串）。"""
    samples = decode_wav(wav_bytes)
    if not samples:
        return ""
    recognizer = _recognizer(model)
    try:
        stream = recognizer.create_stream()
        stream.accept_waveform(SAMPLE_RATE, samples)
        recognizer.decode_streams([stream])
        text = str(getattr(stream.result, "text", "") or stream.result).strip()
    except Exception as e:
        raise LocalEngineError(f"识别失败：{type(e).__name__}") from e
    return text


def decode_wav(data: bytes) -> list[float]:
    """WAV → 16kHz 单声道 float32 列表（支持 PCM8/16/32 与 float32）。"""
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            channels = wav.getnchannels()
            width = wav.getsampwidth()
            rate = wav.getframerate()
            frames = wav.readframes(wav.getnframes())
    except (wave.Error, EOFError) as e:
        raise LocalEngineError("音频不是可解析的 WAV") from e
    samples = _to_float(frames, width)
    if channels > 1:
        samples = [
            sum(samples[i : i + channels]) / channels for i in range(0, len(samples), channels)
        ]
    if rate != SAMPLE_RATE:
        samples = _resample_linear(samples, rate, SAMPLE_RATE)
    return samples


def _to_float(raw: bytes, width: int) -> list[float]:
    if width == 1:
        return [(b - 128) / 128.0 for b in raw]
    if width == 2:
        pcm = array.array("h")
        pcm.frombytes(raw[: len(raw) - (len(raw) % 2)])
        if sys.byteorder == "big":  # pragma: no cover - 目标平台均为小端
            pcm.byteswap()
        return [v / 32768.0 for v in pcm]
    if width == 4:
        pcm32 = array.array("i")
        pcm32.frombytes(raw[: len(raw) - (len(raw) % 4)])
        if sys.byteorder == "big":  # pragma: no cover
            pcm32.byteswap()
        return [v / 2147483648.0 for v in pcm32]
    raise LocalEngineError(f"不支持的 WAV 位宽：{width * 8} bit")


def _resample_linear(samples: list[float], src: int, dst: int) -> list[float]:
    if not samples or src <= 0 or src == dst:
        return samples
    ratio = dst / src
    out_len = max(1, int(len(samples) * ratio))
    last = len(samples) - 1
    out: list[float] = []
    for i in range(out_len):
        pos = i / ratio
        left = int(pos)
        frac = pos - left
        right = min(left + 1, last)
        out.append(samples[left] * (1 - frac) + samples[right] * frac)
    return out


def timed_recognize(model: ModelSpec, wav_bytes: bytes) -> tuple[str, int]:
    started = time.perf_counter()
    text = recognize(model, wav_bytes)
    return text, int((time.perf_counter() - started) * 1000)
