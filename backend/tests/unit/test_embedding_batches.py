"""真实向量模型接入护栏（3.7）：分批请求、条数与维度一致性校验。"""

import pytest
from app.core.config import settings
from app.core.errors import AppError
from app.modules.knowledge import embedding as embedding_mod


class _Resp:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:  # noqa: D401 - 测试桩
        return None

    def json(self) -> dict:
        return self._payload


def _provider(monkeypatch, reply):
    calls: list[list[str]] = []

    def fake_post(url, json, headers, timeout):  # noqa: A002 - 对齐 httpx 签名
        calls.append(list(json["input"]))
        return _Resp(reply(json["input"]))

    monkeypatch.setattr(embedding_mod.httpx, "post", fake_post)
    provider = embedding_mod.OpenAICompatibleEmbedding(
        base_url="https://llm.example/v1", api_key="k", model="embed-1"
    )
    return provider, calls


def test_embed_splits_large_input_into_batches(monkeypatch):
    monkeypatch.setattr(settings, "embedding_batch_size", 2)

    def reply(batch):
        # index 与请求顺序绑定，验证跨批次的顺序还原
        return {
            "data": [{"index": i, "embedding": [float(len(t)), 0.0]} for i, t in enumerate(batch)]
        }

    provider, calls = _provider(monkeypatch, reply)
    vectors = provider.embed(["a", "bb", "ccc", "dddd", "eeeee"])

    assert calls == [["a", "bb"], ["ccc", "dddd"], ["eeeee"]]
    assert vectors == [[1.0, 0.0], [2.0, 0.0], [3.0, 0.0], [4.0, 0.0], [5.0, 0.0]]
    assert provider.dim == 2


def test_embed_empty_input_makes_no_request(monkeypatch):
    provider, calls = _provider(monkeypatch, lambda batch: {"data": []})
    assert provider.embed([]) == []
    assert calls == []


def test_embed_rejects_count_mismatch(monkeypatch):
    monkeypatch.setattr(settings, "embedding_batch_size", 4)
    provider, _ = _provider(monkeypatch, lambda batch: {"data": [{"index": 0, "embedding": [1.0]}]})

    with pytest.raises(AppError) as exc:
        provider.embed(["a", "b"])
    assert exc.value.code == "LLM_ERROR"
    assert "数量" in exc.value.message


def test_embed_rejects_inconsistent_dimensions(monkeypatch):
    provider, _ = _provider(
        monkeypatch,
        lambda batch: {
            "data": [
                {"index": 0, "embedding": [1.0, 2.0]},
                {"index": 1, "embedding": [1.0]},
            ]
        },
    )

    with pytest.raises(AppError) as exc:
        provider.embed(["a", "b"])
    assert exc.value.code == "LLM_ERROR"
    assert "维度" in exc.value.message
