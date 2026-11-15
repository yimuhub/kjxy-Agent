"""模型模态推断与上游目录元数据解析（从 supplier_service 拆分，保持对外重导出兼容）。"""

from __future__ import annotations

from typing import Any

#: 关键字 → 模态（顺序敏感：先命中先返回，避免 "qwen-vl" 被 "qwen" 误判为文本）
_MODALITY_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("rerank", "rerank"),
    ("embedding", "embedding"),
    ("bge", "embedding"),
    ("gte", "embedding"),
    ("tts", "tts"),
    ("whisper", "asr"),
    ("paraformer", "asr"),
    ("sensevoice", "asr"),
    ("asr", "asr"),
    ("dall-e", "image"),
    ("dalle", "image"),
    ("gpt-image", "image"),
    ("imagen", "image"),
    ("midjourney", "image"),
    ("kolors", "image"),
    ("flux", "image"),
    ("wanx", "image"),
    ("seedream", "image"),
    ("stable-diffusion", "image"),
    ("sdxl", "image"),
    ("veo", "video"),
    ("kling", "video"),
    ("hailuo", "video"),
    ("sora", "video"),
    ("wan", "video"),
    ("vl", "multimodal"),
    ("vision", "multimodal"),
    ("omni", "multimodal"),
)


def guess_modality_detail(model_id: str) -> tuple[str, bool]:
    """按模型名关键字推断模态，返回 ``(modality, matched)``。

    ``matched=False`` 表示只是兜底成 text，不能当作识别成功；
    前端据此决定是否继续拉上游目录或让用户手动选择。
    """
    name = (model_id or "").strip().lower()
    for keyword, modality in _MODALITY_KEYWORDS:
        if keyword in name:
            return modality, True
    return "text", False


def guess_modality(model_id: str) -> str:
    """按模型名关键字猜测模态（仅作导入预填，不代替人工确认）。

    ``wan`` 既可能是通义万相（图像）也可能是视频；这里按 v3.33 口径归 video，
    因为 ``wanx``（文生图）已在前面的 image 规则命中。
    """
    return guess_modality_detail(model_id)[0]


#: 上游目录可能出现的模态别名
_MODALITY_ALIASES = {
    "text": "text",
    "language": "text",
    "chat": "text",
    "image": "image",
    "vision": "multimodal",
    "multimodal": "multimodal",
    "video": "video",
    "audio": "audio",
    "speech": "audio",
    "embedding": "embedding",
    "embeddings": "embedding",
    "rerank": "rerank",
    "reranker": "rerank",
    "asr": "asr",
    "transcription": "asr",
    "stt": "asr",
    "tts": "tts",
    "speech_synthesis": "tts",
}


def _normalize_modality(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return _MODALITY_ALIASES.get(value.strip().lower().replace("-", "_"))


def _collect_modalities(value: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(value, str):
        item = _normalize_modality(value)
        if item:
            out.add(item)
    elif isinstance(value, list | tuple | set):
        for raw in value:
            out.update(_collect_modalities(raw))
    elif isinstance(value, dict):
        for key, enabled in value.items():
            if enabled is False or enabled is None:
                continue
            item = _normalize_modality(str(key))
            if item:
                out.add(item)
    return out


def _modality_from_catalog_entry(entry: dict[str, Any]) -> str | None:
    """从上游模型目录条目读取模态；目录没给元数据时返回 None。

    兼容 OpenRouter 的 ``architecture.input_modalities/output_modalities``、
    常见的 ``modality/type`` 字段，以及 ``capabilities`` 能力位。
    """
    outputs: set[str] = set()
    inputs: set[str] = set()
    for key in ("modality", "type", "model_type", "output_modality", "output_modalities"):
        outputs.update(_collect_modalities(entry.get(key)))
    arch = entry.get("architecture")
    if isinstance(arch, dict):
        inputs.update(_collect_modalities(arch.get("input_modalities")))
        outputs.update(_collect_modalities(arch.get("output_modalities")))
        outputs.update(_collect_modalities(arch.get("modality")))
    caps = entry.get("capabilities")
    if isinstance(caps, dict):
        outputs.update(_collect_modalities(caps))
    if not inputs and not outputs:
        return None
    if "video" in outputs:
        return "video"
    if "image" in outputs:
        return "image"
    if "audio" in outputs:
        return "tts" if "text" not in inputs else "asr"
    if "embedding" in outputs or "rerank" in outputs:
        return "embedding" if "embedding" in outputs else "rerank"
    if "image" in inputs or "video" in inputs:
        return "multimodal"
    if "audio" in inputs:
        return "asr"
    if outputs == {"text"} and inputs <= {"text"}:
        return "text"
    if len(inputs | outputs) > 1:
        return "multimodal"
    return next(iter(outputs | inputs), None)


def protocol_for_modality(modality: str, default: str = "openai_chat") -> str:
    """模态 → 默认协议（显式给出协议时以显式值为准）。"""
    return {
        "image": "openai_images",
        "video": "openai_video",
        "embedding": "openai_embedding",
        "asr": "openai_audio",
        "tts": "openai_audio",
        "text": default,
        "multimodal": default,
        "audio": default,
        "rerank": default,
    }.get(modality, default)


__all__ = [
    "_MODALITY_ALIASES",
    "_MODALITY_KEYWORDS",
    "_collect_modalities",
    "_modality_from_catalog_entry",
    "_normalize_modality",
    "guess_modality",
    "guess_modality_detail",
    "protocol_for_modality",
]
