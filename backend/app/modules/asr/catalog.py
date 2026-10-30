"""内置 ASR 模型清单（跨平台同一份 ONNX 文件）。

新增模型只改这里：下载链接必须来自受信任域名（ModelScope / GitHub Release），
``sha256`` 必须填真实值——下载完成后先校验再落盘，不允许“边下边用”。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: 模型校验清单版本：模型清单本身随应用发布，便于前端提示“有新模型”
CATALOG_VERSION = 1


@dataclass(frozen=True)
class ModelFile:
    name: str
    size: int
    sha256: str
    mirrors: tuple[str, ...]


@dataclass(frozen=True)
class ModelSpec:
    id: str
    name: str
    description: str
    languages: tuple[str, ...]
    min_ram_mb: int
    license: str
    tier: str  # recommended | high_accuracy
    files: tuple[ModelFile, ...]
    decoder: str = "paraformer"  # paraformer | sense-voice

    @property
    def size_bytes(self) -> int:
        return sum(item.size for item in self.files)

    def payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "languages": list(self.languages),
            "min_ram_mb": self.min_ram_mb,
            "license": self.license,
            "tier": self.tier,
            "engine": "sherpa-onnx",
            "decoder": self.decoder,
            "size_bytes": self.size_bytes,
        }


_HF = "https://huggingface.co/csukuangfj/sherpa-onnx-paraformer-zh-small-2024-03-09/resolve/main"
_HF_MIRROR = (
    "https://hf-mirror.com/csukuangfj/sherpa-onnx-paraformer-zh-small-2024-03-09/resolve/main"
)
_HF_SV = "https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main"
_HF_SV_MIRROR = "https://hf-mirror.com/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main"

MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        id="paraformer-zh-small",
        name="中文语音转写（轻量）",
        description="Paraformer-zh-small int8：普通话语音转文字，离线可用，适合按住说话。",
        languages=("zh",),
        min_ram_mb=1024,
        license="FunASR Paraformer（以模型卡为准）",
        tier="recommended",
        files=(
            ModelFile(
                name="model.int8.onnx",
                size=81_828_675,
                sha256="3ef6c19369b912f7caf3cef8e545c5ccd1a33d9d7ec792a46668dc41c4b229ec",
                mirrors=(
                    f"{_HF}/model.int8.onnx",
                    f"{_HF_MIRROR}/model.int8.onnx",
                ),
            ),
            ModelFile(
                name="tokens.txt",
                size=75_352,
                sha256="4b2d964e18b9cf139b473003b6698fb2ed9a2a5ec55b93daa677b28f578897aa",
                mirrors=(f"{_HF}/tokens.txt", f"{_HF_MIRROR}/tokens.txt"),
            ),
        ),
    ),
    ModelSpec(
        id="sense-voice-small",
        name="中文语音转写（高精度）",
        description=(
            "SenseVoice-Small int8：带标点与数字规整，农业术语识别更稳；"
            "体积与内存占用约为轻量版的 3 倍，建议内存 2GB 以上的电脑使用。"
        ),
        languages=("zh", "en", "ja", "ko", "yue"),
        min_ram_mb=2048,
        license="FunAudioLLM SenseVoice（以模型卡为准）",
        tier="high_accuracy",
        decoder="sense-voice",
        files=(
            ModelFile(
                name="model.int8.onnx",
                size=239_233_841,
                sha256="c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51",
                mirrors=(
                    f"{_HF_SV}/model.int8.onnx",
                    f"{_HF_SV_MIRROR}/model.int8.onnx",
                ),
            ),
            ModelFile(
                name="tokens.txt",
                size=315_894,
                sha256="f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc",
                mirrors=(f"{_HF_SV}/tokens.txt", f"{_HF_SV_MIRROR}/tokens.txt"),
            ),
        ),
    ),
)

_BY_ID: dict[str, ModelSpec] = {item.id: item for item in MODELS}


def get_model(model_id: str) -> ModelSpec | None:
    return _BY_ID.get(model_id)


def default_model_id() -> str:
    return MODELS[0].id if MODELS else ""


def payload_list() -> list[dict[str, Any]]:
    return [item.payload() for item in MODELS]
