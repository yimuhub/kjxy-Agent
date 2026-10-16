"""Mock 模型 Provider：不联网，返回确定性中文回答，专供开发/CI。"""

import random

from app.core.config import settings
from app.modules.llm.schemas import LLMRequest, LLMResponse

MODEL_NAME = "fake-mock-1"
# Mock 调用不产生真实成本
PRICE_PER_1K_USD = 0.0


def estimate_tokens(text: str) -> int:
    """按字符数估算 token（Mock 专用，不代表真实分词结果）。"""
    return max(1, len(text))


class FakeProvider:
    """确定性 Mock：相同 messages 永远返回相同正文；latency 为随机小值。"""

    PRICE_PER_1K_USD = PRICE_PER_1K_USD

    def __init__(self, model: str | None = None):
        self.model = model or settings.llm_model or MODEL_NAME

    async def complete(self, request: LLMRequest) -> LLMResponse:
        question = next(
            (m.content for m in reversed(request.messages) if m.role == "user"),
            "",
        )
        summary = " ".join(question.split())[:48]
        content = (
            "【Mock 模型回答】这是开发/CI 环境的确定性模拟回答，未调用真实模型。\n"
            f"您的问题摘要：「{summary}」。\n"
            "如需真实回答，请将 LLM_PROVIDER 切换为 openai_compatible 并配置密钥。"
        )
        input_text = "\n".join(f"{m.role}:{m.content}" for m in request.messages)
        return LLMResponse(
            content=content,
            input_tokens=estimate_tokens(input_text),
            output_tokens=estimate_tokens(content),
            model=request.model or self.model,
            latency_ms=random.randint(5, 50),
            finish_reason="stop",
        )
