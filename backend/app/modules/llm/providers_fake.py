"""Mock 模型 Provider：不联网，返回确定性中文回答，专供开发/CI。

工具循环测试注入（P0 内核）：模块级脚本 ``set_scripted_tool_responses([...])``
按调用顺序回放结构化响应；脚本耗尽后自动回落到确定性 Mock 文本。脚本是
进程级状态，仅供测试使用——测试必须负责 reset（fixture teardown），
防止串到其他用例。
"""

import json
import random

from app.core.config import settings
from app.core.errors import AppError
from app.modules.llm.schemas import LLMRequest, LLMResponse, ToolCall

MODEL_NAME = "fake-mock-1"
# Mock 调用不产生真实成本
PRICE_PER_1K_USD = 0.0

#: 方案合成的系统提示前缀（chat/plan.py）。命中时回放带章节的可解析方案，
#: 否则分章渲染、取证统计与导出链路在 CI 里永远只能看到「缺章」。
PLAN_SYSTEM_MARKER = "你是科技小院的方案合成器"

# ------------------------------------------------------------ 脚本回放（测试注入）
#: 待回放脚本队列：每项 {"content": str|None, "tool_calls": [{"id","name","args"}],
#: "error": {"code","message"}}；error 项模拟 provider 抛 AppError（LLM 失败路径）
_SCRIPT: list[dict] = []


def set_scripted_tool_responses(items: list[dict]) -> None:
    """注入脚本化响应序列（按 complete() 调用顺序消费）。仅测试使用。"""
    global _SCRIPT
    _SCRIPT = [dict(item) for item in items]


def reset_scripted_tool_responses() -> None:
    """清空脚本（测试 teardown 必调，防止串测）。"""
    global _SCRIPT
    _SCRIPT = []


def estimate_tokens(text: str) -> int:
    """按字符数估算 token（Mock 专用，不代表真实分词结果）。"""
    return max(1, len(text))


def _plan_mock_content(messages: list) -> str | None:
    """方案合成请求 → 按系统提示里的章节契约回放可解析的 Mock 方案。

    章节标题直接从提示词里取，避免在 Mock 里复制一份章节表；正文固定且足够长，
    满足「章节写实」判定，让 CI 能覆盖分章渲染、取证统计与导出流程。
    """
    system = "\n".join(m.content for m in messages if m.role == "system")
    if PLAN_SYSTEM_MARKER not in system:
        return None
    headings = [line[3:].strip() for line in system.splitlines() if line.startswith("## ")]
    if not headings:
        return None
    blocks = []
    for index, heading in enumerate(headings, 1):
        blocks.append(
            f"## {heading}\n"
            f"（Mock 方案，仅供开发与 CI 验证链路。）第 {index} 节按小院现有资料执行："
            "先核对田块与作物现状，再按计划实施并记录用量，完成后回填验收数据；"
            "未覆盖的证据缺口写入待确认事项，投产前需由当地专家复核。（模型推断，需当地核实）"
        )
    return "\n\n".join(blocks)


class FakeProvider:
    """确定性 Mock：相同 messages 永远返回相同正文；latency 为随机小值。

    ``supports_tools=True``：fake 不联网、永远不会因 function-calling 协议出错，
    工具调用完全由测试脚本决定，因此声明具备工具能力；真实供应商仍需
    suppliers.supports_tools 显式声明（默认 false，零回归）。
    """

    PRICE_PER_1K_USD = PRICE_PER_1K_USD
    supports_tools = True

    def __init__(self, model: str | None = None):
        self.model = model or settings.llm_model or MODEL_NAME

    def _from_script(self, item: dict, request: LLMRequest) -> LLMResponse | None:
        """消费一条脚本项；返回 None 表示无可回放（回落确定性文本）。"""
        if item.get("error"):
            err = item["error"]
            raise AppError(
                str(err.get("code") or "LLM_ERROR"),
                str(err.get("message") or "脚本注入的模拟失败。"),
                http_status=int(err.get("http_status") or 502),
                details={"category": "scripted"},
            )
        tool_calls: list[ToolCall] = []
        for i, tc in enumerate(item.get("tool_calls") or []):
            args = tc.get("args", {})
            args_json = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
            tool_calls.append(
                ToolCall(
                    id=str(tc.get("id") or f"fake_call_{i}"),
                    name=str(tc.get("name") or ""),
                    args_json=args_json,
                )
            )
        content = item.get("content")
        input_text = "\n".join(f"{m.role}:{m.content}" for m in request.messages)
        output_text = content or json.dumps(
            [t.model_dump() for t in tool_calls], ensure_ascii=False, default=str
        )
        return LLMResponse(
            content=content or "",
            input_tokens=estimate_tokens(input_text),
            output_tokens=estimate_tokens(output_text),
            model=request.model or self.model,
            latency_ms=random.randint(5, 50),
            finish_reason="tool_calls" if tool_calls else "stop",
            tool_calls=tool_calls or None,
        )

    async def complete(self, request: LLMRequest) -> LLMResponse:
        if _SCRIPT:
            scripted = self._from_script(_SCRIPT.pop(0), request)
            if scripted is not None:
                return scripted
        plan = _plan_mock_content(request.messages)
        if plan is not None:
            content = plan
        else:
            question = next(
                (m.content for m in reversed(request.messages) if m.role == "user"),
                "",
            )
            summary = " ".join(str(question).split())[:48]
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
