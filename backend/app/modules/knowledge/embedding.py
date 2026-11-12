"""Embedding 提供方。业务只依赖 EmbeddingProvider Protocol，不绑定具体供应商。

三处修正（审查 h）：
1. **SSRF 校验**：向量端点同样要做出网校验 —— 全仓 31 处 ``check_egress_url``
   唯独漏了这里，等于给"模型中心"配的地址开了一个不受控的出网口。
2. **不阻塞事件循环**：``embed_async`` 用 httpx.AsyncClient，
   指挥官等异步链路不再被同步 HTTP 调用卡住（此前同步 httpx.post 直接跑在
   事件循环里，一次向量请求能把整个 Worker 的并发堵住）。
3. **走供应商链**：有���院上下文时按「模型中心」的绑定链解析向量模型
   （与对话/图像/视频同一套口径），找不到再回落 settings（零回归）。
"""

import hashlib
import math
from typing import Any, Protocol

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
    """确定性 Mock：字符 bigram 的哈希词袋（sublinear TF + L2 归一化）。

    共享 bigram 越多余弦越接近 1（相同文本恒为 1.0），无关文本接近 0；
    仅为开发/CI 的 plumbing 替身，不代表真实语义质量。

    **为什么不用"随机高斯向量求平均"**：那样共享 token 的信号会被 n·m 个
    交叉项稀释，任意两段文本的余弦都落在 0.00x 量级，比 ``RETRIEVAL_MIN_SCORE``
    低一个数量级 —— mock 模式下知识检索恒为空，且看起来像"检索坏了"。
    改成哈希词袋后余弦 ≈ 共享 bigram 数 / sqrt(|A|·|B|)，与真实 embedding
    同量级（0.0x~0.8），阈值语义恢复一致。
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
    def _bucket(token: str, dim: int) -> int:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big") % dim

    def _embed_one(self, text: str) -> list[float]:
        tokens = self._bigrams(text)
        if not tokens:
            return [0.0] * self._dim
        counts: dict[int, int] = {}
        for tok in tokens:
            bucket = self._bucket(tok, self._dim)
            counts[bucket] = counts.get(bucket, 0) + 1
        vec = [0.0] * self._dim
        for bucket, count in counts.items():
            # sublinear TF：同一个 bigram 出现 10 次不该有 10 倍权重
            vec[bucket] = 1.0 + math.log(count)
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


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

    # -- 请求体 ----------------------------------------------------------

    def _payload(self, texts: list[str]) -> dict[str, Any]:
        return {"model": self.model, "input": texts}

    def _url(self) -> str:
        return f"{self.base_url}/embeddings"

    def _check_egress(self) -> None:
        """出网校验：向量端点不得指向回环/link-local/保留地址。"""
        from app.core.net_guard import check_egress_url

        check_egress_url(self._url(), context="embedding", allow_local=True)

    # -- 同步实现（Celery worker / 种子脚本等非异步场景）--------------------

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        self._check_egress()
        batch_size = max(1, int(settings.embedding_batch_size))
        vectors: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            vectors.extend(self._embed_batch(texts[start : start + batch_size]))
        self._validate(vectors, len(texts))
        if vectors:
            self._dim = len(vectors[0])
        return vectors

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        try:
            resp = httpx.post(
                self._url(),
                json=self._payload(texts),
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

    # -- 异步实现（指挥官等异步链路）---------------------------------------

    async def embed_async(self, texts: list[str]) -> list[list[float]]:
        """异步版本：HTTP 调用不阻塞事件循环。"""
        if not texts:
            return []
        self._check_egress()
        batch_size = max(1, int(settings.embedding_batch_size))
        vectors: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            vectors.extend(await self._embed_batch_async(texts[start : start + batch_size]))
        self._validate(vectors, len(texts))
        if vectors:
            self._dim = len(vectors[0])
        return vectors

    async def _embed_batch_async(self, texts: list[str]) -> list[list[float]]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, trust_env=False) as client:
                resp = await client.post(
                    self._url(),
                    json=self._payload(texts),
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
            resp.raise_for_status()
            data = resp.json()["data"]
        except AppError:
            raise
        except Exception as e:
            log.warning("embedding request failed: %s", e)
            raise AppError("LLM_ERROR", "向量模型调用失败，请稍后重试。", http_status=502) from e
        return [item["embedding"] for item in sorted(data, key=lambda d: d["index"])]

    # -- 校验 -------------------------------------------------------------

    def _validate(self, vectors: list[list[float]], requested: int) -> None:
        if len(vectors) != requested:
            # 条数不一致会让 chunk 与向量错位入库，必须整批失败而不是静默截断。
            raise AppError(
                "LLM_ERROR",
                f"向量模型返回数量与请求不一致（请求 {requested} 条，返回 {len(vectors)} 条）。",
                http_status=502,
            )
        dims = {len(vec) for vec in vectors}
        if len(dims) > 1:
            raise AppError("LLM_ERROR", "向量模型返回的维度不一致。", http_status=502)
        expected_dim = int(settings.embedding_dim)
        if vectors and len(vectors[0]) != expected_dim:
            # 数据库列维度在建表时固定；维度不一致时给出可操作提示，而不是
            # 让 pgvector 抛 "expected N dimensions, not M" 这种难懂的库错。
            raise AppError(
                "LLM_ERROR",
                f"向量模型返回维度 {len(vectors[0])}，与向量列维度 {expected_dim} 不一致；"
                "请把 EMBEDDING_DIM 调整为模型实际维度并执行对应迁移。",
                http_status=502,
            )


def _resolve_supplier_embedding(db, yard_id: str) -> EmbeddingProvider | None:
    """按「模型中心」的绑定链解析向量模型（与对话/图像/视频同一套口径）。

    找不到 embedding 模态的供应商模型时返回 None，由调用方回落 settings。
    """
    try:
        from app.modules.llm import binding_service
        from app.modules.llm.supplier_crypto import decrypt_api_key
    except Exception as e:  # noqa: BLE001 - 供应商体系不可用时回落
        log.warning("向量供应商链解析不可用，回落配置: %s", e)
        return None

    try:
        chain = binding_service.resolve_chain(db, yard_id, purpose="embedding")
    except Exception as e:  # noqa: BLE001 - 解析失败回落配置
        log.warning("向量供应商链解析失败，回落配置: %s", e)
        return None

    for candidate in chain:
        row = candidate.supplier
        model_row = candidate.supplier_model
        modality = str(getattr(model_row, "modality", "") or "").lower()
        protocol = str(getattr(model_row, "protocol", "") or "").lower()
        if modality != "embedding" and protocol != "openai_embedding":
            continue
        model = (
            str(getattr(model_row, "upstream_model_id", "") or "").strip()
            or str(candidate.model_name or "").strip()
            or str(getattr(row, "model_id", "") or "").strip()
        )
        if not model:
            continue
        api_key = decrypt_api_key(row.api_key_enc)
        try:
            return OpenAICompatibleEmbedding(
                base_url=str(getattr(row, "base_url", "") or "") or None,
                api_key=api_key,
                model=model,
            )
        except AppError:
            continue
        finally:
            del api_key
    return None


def get_embedding_provider(db=None, yard_id: str | None = None) -> EmbeddingProvider:  # noqa: ANN001
    """取向量提供方。

    有小院上下文时优先按供应商链解析（运营可为每个小院单独绑定向量模型），
    解析不到再回落 settings（零回归：旧部署行为完全不变）。
    """
    if db is not None and yard_id:
        provider = _resolve_supplier_embedding(db, yard_id)
        if provider is not None:
            return provider
    if settings.embedding_provider == "openai_compatible":
        return OpenAICompatibleEmbedding()
    return MockEmbeddingProvider()


__all__ = [
    "EmbeddingProvider",
    "MockEmbeddingProvider",
    "OpenAICompatibleEmbedding",
    "get_embedding_provider",
]
