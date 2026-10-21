"""LLM 模块 Pydantic 契约。"""

from typing import Any

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """一条会话消息。

    工具循环扩展（P0 内核）：
    - ``content`` 允许为 None（OpenAI 协议中带 tool_calls 的 assistant 消息
      content 可为 null）；
    - ``tool_call_id``：role=tool 的工具结果回填消息所指向的调用 id；
    - ``tool_calls``：assistant 消息携带的结构化工具调用列表（OpenAI 帧形，
      原样透传给兼容 provider）。
    """

    role: str
    content: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class ToolCall(BaseModel):
    """模型发起的一次工具调用（provider 解析后的结构化形态）。

    ``args_json`` 保留模型输出的参数原文（JSON 字符串）：非法 JSON 不在
    provider 层猜测，由工具循环按失败回填（宁诚实勿修复）。
    """

    id: str
    name: str
    args_json: str


class LLMMetadata(BaseModel):
    tenant_id: str | None = None
    task_id: str | None = None
    request_id: str | None = None
    # W4 观测透传：调用用途（declarative|factory|eval|…，缺省 platform）与调用方引用
    # （声明式=agent_code，工厂=factory run id）。只进 llm_traces，不影响 provider 行为。
    purpose: str | None = None
    agent_ref: str | None = None

    model_config = {"extra": "ignore"}


class LLMRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    model: str | None = None
    max_tokens: int | None = Field(default=None, gt=0)
    temperature: float | None = Field(default=None, ge=0, le=2)
    # OpenAI function-calling 工具定义（{"type":"function","function":{...}} 列表）；
    # None/空 = 不启用工具。不支持工具的 provider 必须忽略此字段（零回归）。
    tools: list[dict[str, Any]] | None = None
    metadata: LLMMetadata = Field(default_factory=LLMMetadata)


class LLMResponse(BaseModel):
    content: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    model: str
    latency_ms: int
    finish_reason: str | None = None
    # 模型请求的工具调用（provider 已解析为结构化形态）；None/空 = 纯文本回答
    tool_calls: list[ToolCall] | None = None


class LLMStatusOut(BaseModel):
    provider: str
    model: str
    configured: bool
    mode: str  # mock|live|unknown；绝不包含密钥
    # 选择该提供方的可读理由（env 已配 / fake 兜底），供前端展示；不含任何密钥值
    reason: str = ""
