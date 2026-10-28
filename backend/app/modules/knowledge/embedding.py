"""Embedding 提供方。业务只依赖 EmbeddingProvider Protocol，不绑定具体供应商。"""

import hashlib
import math
import random
from typing import Protocol

import httpx

from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import get_logger

log = get_logger(__name__)


class EmbeddingProvider(Protocol):
    @property
    def dim(self) -> int: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class MockEmbeddingProvider:
    """确定性 Mock：字符 bigram 词向量平均并归一化。

    共享词越多的文本余弦越接近 1（相同文本恒为 1.0），无关文本接近 0；
    仅为开发/CI 的 plumbing 替身，不代表真实语义质量。
    """

    def __init__(self, dim: int | None = None) -> None:
        self._dim = dim or settings.embedding_dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    @staticmethod
    def _bigrams(text: str) -> list[str]:
        t = text.lower()
        if len(t) < 2:
            return [t] if t else []
        return [t[i : i + 2] for i in range(len(t) - 1)]

    @staticmethod
    def _token_vector(token: str, dim: int) -> list[float]:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        seed = int.from_bytes(digest[:8], "big")
        rng = random.Random(seed)
        return [rng.gauss(0.0, 1.0) for _ in range(dim)]

    def _embed_one(self, text: str) -> list[float]:
        tokens = self._bigrams(text)
        if not tokens:
            return [0.0] * self._dim
        dim = self._dim
        acc = [0.0] * dim
        for tok in tokens:
            vec = self._token_vector(tok, dim)
            for i, v in enumerate(vec):
                acc[i] += v
        n = len(tokens)
        mean = [v / n for v in acc]
        norm = math.sqrt(sum(v * v for v in mean)) or 1.0
        return [v / norm for v in mean]


class OpenAICompatibleEmbedding:
    """OpenAI 兼容 Embedding：POST {base_url}/embeddings。"""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.api_key = api_key or settings.llm_api_key
        self.model = model or settings.embedding_model
        self.timeout_seconds = timeout_seconds or settings.llm_timeout_seconds
        if not (self.base_url and self.api_key and self.model):
            raise AppError(
                "LLM_NOT_CONFIGURED",
                "向量模型尚未配置，请联系管理员完成模型接入配置。",
                http_status=503,
            )
        self._dim: int | None = None

    @property
    def dim(self) -> int:
        # 维度在首次成功调用后获知；调用前用配置默认值占位
        return self._dim or settings.embedding_dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        batch_size = max(1, int(settings.embedding_batch_size))
        vectors: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            vectors.extend(self._embed_batch(texts[start : start + batch_size]))
        if len(vectors) != len(texts):
            # 条数不一致会让 chunk 与向量错位入库，必须整批失败而不是静默截断。
            raise AppError(
                "LLM_ERROR",
                f"向量模型返回数量与请求不一致（请求 {len(texts)} 条，返回 {len(vectors)} 条）。",
                http_status=502,
            )
        dims = {len(vec) for vec in vectors}
        if len(dims) > 1:
            raise AppError("LLM_ERROR", "向量模型返回的维度不一致。", http_status=502)
        if vectors:
            self._dim = len(vectors[0])
        return vectors

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        try:
            resp = httpx.post(
                f"{self.base_url}/embeddings",
                json={"model": self.model, "input": texts},
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout_seconds,
            )
            resp.raise_for_status()
            data = resp.json()["data"]
        except AppError:
            raise
        except Exception as e:
            log.warning("embedding request failed: %s", e)
            raise AppError("LLM_ERROR", "向量模型调用失败，请稍后重试。", http_status=502) from e
        return [item["embedding"] for item in sorted(data, key=lambda d: d["index"])]


def get_embedding_provider() -> EmbeddingProvider:
    if settings.embedding_provider == "openai_compatible":
        return OpenAICompatibleEmbedding()
    return MockEmbeddingProvider()
