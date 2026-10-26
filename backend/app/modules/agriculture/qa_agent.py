"""农业问答 Agent。

code=agriculture_qa，version=1.0.0。

真实依赖（均已就位；仍保留 try/except 兼容，缺失时自动降级）：
- agents 协议：app.modules.agents（Agent/AgentRegistry；AgentResult 在 schemas）
  ※ 注：全局 get_global_registry 并不存在，注册使用本模块内单例（见 _register_agent）。
- 知识检索：优先 app.modules.knowledge.service.search(db, ctx, query, top_k)；
  其次尝试全局工具注册表执行 knowledge.search（registry.execute，参数为 query/top_k）。
- 大模型：app.modules.llm.service.complete_with_logging(db, LLMRequest)
 （db 由调用方经 QAAgentContext 传入；无 db 时降级为检索原文摘要）。
"""

from __future__ import annotations

import inspect
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.core.deps import YardContext as CoreYardContext
from app.core.logging import get_logger
from app.modules.agriculture.prompts import SYSTEM_PROMPT, build_user_prompt
from app.modules.agriculture.schemas import QAOutput

log = get_logger(__name__)

# ---------------------------------------------------------------- agents 协议
try:
    from app.modules.agents import Agent, AgentRegistry, get_global_registry  # 真实协议
    from app.modules.agents.schemas import AgentContext as ProtocolAgentContext
    from app.modules.agents.schemas import AgentResult as ProtocolAgentResult
except ImportError:
    # 本地兼容（agents 模块缺失时保证 import 不失败）

    @dataclass
    class ProtocolAgentContext:  # type: ignore[no-redef]
        yard_id: str
        user_id: str = ""
        roles: list[str] = field(default_factory=list)
        request_id: str = "req_unknown"

    @dataclass
    class ProtocolAgentResult:  # type: ignore[no-redef]
        status: str
        output: dict[str, Any] = field(default_factory=dict)
        citations: list[dict[str, Any]] = field(default_factory=list)
        warnings: list[str] = field(default_factory=list)
        error: str | None = None

    class Agent(Protocol):  # type: ignore[no-redef]
        code: str
        version: str

        async def run(self, input_data: dict, context: Any) -> Any: ...

    class AgentRegistry:  # type: ignore[no-redef]
        def __init__(self) -> None:
            self._agents: dict[str, Any] = {}

        def register(self, code: str, agent: Any) -> None:
            self._agents[code] = agent

        def get(self, code: str) -> Any | None:
            return self._agents.get(code)

    _fallback_registry = AgentRegistry()

    def get_global_registry() -> Any:  # type: ignore[misc]
        return _fallback_registry


# 对外仍导出协议名（兼容任务书中的引用）
AgentContext = ProtocolAgentContext
AgentResult = ProtocolAgentResult


def _make_agent_result(**kwargs: Any) -> Any:
    """构造 AgentResult：真实 pydantic 模型与兼容 dataclass 均支持同名字段。"""
    try:
        return AgentResult(**kwargs)
    except Exception:
        # 极端兜底：只保留 status/output
        return AgentResult(status=kwargs.get("status", "failed"), output=kwargs.get("output", {}))


# 全局 agent 注册表由 agents 模块提供；缺失时上面的 except 分支给出进程内兜底。


@dataclass
class QAAgentContext:
    """问答 Agent 运行上下文（内部载体）。

    真实 AgentContext（agents.schemas）要求 task_id/deadline_seconds 等编排字段，
    同步问答场景下由本上下文承载；run() 同时兼容通过 getattr 读取的任意上下文。
    """

    yard_id: str
    user_id: str
    roles: list[str] = field(default_factory=list)
    request_id: str = "req_unknown"
    db: Any = None  # SQLAlchemy Session（llm 日志与 knowledge 检索需要）


# ---------------------------------------------------------------- llm 兼容
# 真实 llm 模块已就位（messages 版 LLMRequest + complete_with_logging(db, req)）；
# 保留 try/except 作为缺失时的自动降级。降级时两者为 None，统一声明为 Any 供类型检查。
LLMRequest: Any
complete_with_logging: Any
try:
    from app.modules.llm.schemas import LLMRequest
    from app.modules.llm.service import complete_with_logging
except ImportError:  # pragma: no cover - 降级分支
    LLMRequest = None
    complete_with_logging = None

# ---------------------------------------------------------------- 常量
# 高风险红线词（S4）：只扩不缩——原有词全部保留，扩展种衣剂/拌种等农药
# 配比与用药安全类词汇；命中任一即触发风险提示并强制转专家复核。
HIGH_RISK_KEYWORDS = (
    "农药",
    "剂量",
    "施用",
    "农残",
    "防疫",
    "兽药",
    "种衣剂",
    "拌种",
    "药种比",
    "稀释倍数",
    "施药",
    "用药量",
    "安全间隔期",
    "农药残留",
)

DISCLAIMER = (
    "本回答由 AI 基于知识库检索生成，仅供学习参考，不构成专业农业技术建议；"
    "生产决策请咨询当地农技推广部门或持证专家。"
)
NO_HIT_ANSWER = "根据当前知识库资料，暂未找到与该问题直接相关的可靠资料，建议转交专家或补充资料。"
RISK_WARNING = (
    "该问题涉及农药/兽药使用、剂量或施用时期等高风险内容，错误操作可能造成药害、"
    "人畜安全风险或农残超标，请务必咨询当地农业技术推广部门或持证专家确认后再操作。"
)
LLM_UNAVAILABLE_WARNING = "模型生成服务暂不可用，已降级为检索原文摘要；建议转交专家确认。"

_QA_SCHEMA_PATHS = [
    Path(__file__).resolve().parents[4] / "contracts" / "json-schema" / "qa-output.schema.json",
    Path(__file__).resolve().parents[3] / "contracts" / "json-schema" / "qa-output.schema.json",
]


def _load_qa_schema() -> dict | None:
    try:
        import jsonschema  # noqa: F401
    except ImportError:
        return None
    for p in _QA_SCHEMA_PATHS:
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return None
    return None


# 测试可覆盖的工具注册表（单元/集成测试中注入 stub；生产环境不用）
_tool_registry_override: Any | None = None


def _get_tool_registry() -> Any | None:
    if _tool_registry_override is not None:
        return _tool_registry_override
    # 真实全局注册表：当前 tools/agents 模块均未提供 get_global_registry，保留尝试以备未来
    for modpath in ("app.modules.tools.registry", "app.modules.tools"):
        try:
            mod = __import__(modpath, fromlist=["get_global_registry"])
            ggr = getattr(mod, "get_global_registry", None)
            if callable(ggr):
                return ggr()
        except ImportError:
            continue
    return None


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _yard_context_from(context: Any) -> CoreYardContext:
    return CoreYardContext(
        yard_id=str(getattr(context, "yard_id", "") or ""),
        user_id=str(getattr(context, "user_id", "") or getattr(context, "actor_id", "") or ""),
        roles=list(getattr(context, "roles", []) or []),
        request_id=str(getattr(context, "request_id", "req_unknown") or "req_unknown"),
    )


def _hit_to_dict(h: Any) -> dict[str, Any]:
    if isinstance(h, dict):
        return h
    return {
        "document_id": getattr(h, "document_id", None),
        "chunk_id": getattr(h, "chunk_id", None),
        "title": getattr(h, "title", None),
        "source": getattr(h, "source", None),
        "page_number": getattr(h, "page_number", None),
        "quote": getattr(h, "quote", None),
    }


async def _search_knowledge(
    question: str, top_k: int, context: Any, db: Any = None
) -> list[dict[str, Any]]:
    """知识检索，按优先级：
    ① 测试注入的 stub registry；② 真实 knowledge.service.search（需 db）；
    ③ 全局工具注册表 registry.execute（若可获得）。
    返回归一化 hit 列表（缺 document_id/chunk_id 的丢弃，绝不伪造引用）。
    """
    # ① 测试注入
    if _tool_registry_override is not None:
        registry = _tool_registry_override
        getter = getattr(registry, "get", None)
        tool = getter("knowledge.search") if callable(getter) else None
        if tool is not None:
            raw = await _call_legacy_tool(tool, question, top_k, context)
            return _finalize_hits(raw)

    # ② 真实 knowledge.service.search
    if db is not None:
        try:
            from app.modules.knowledge.service import search as knowledge_search

            tctx = _yard_context_from(context)
            hits = knowledge_search(db, tctx, question, top_k)
            if hits:
                return _finalize_hits([_hit_to_dict(h) for h in hits])
        except Exception as e:
            log.warning("knowledge.service.search failed: %s", e)

    # ③ 全局工具注册表（真实 registry.execute：参数为 query/top_k）
    registry = _get_tool_registry()
    if registry is not None and db is not None:
        execute = getattr(registry, "execute", None)
        if callable(execute):
            try:
                tctx = _yard_context_from(context)
                raw = await _maybe_await(
                    execute(
                        db,
                        "knowledge.search",
                        {"query": question, "top_k": top_k},
                        tctx,
                        tctx.request_id,
                    )
                )
                hits = raw.get("hits", []) if isinstance(raw, dict) else []
                return _finalize_hits([_hit_to_dict(h) for h in hits])
            except Exception as e:
                log.warning("tool registry knowledge.search failed: %s", e)

    return []


async def _call_legacy_tool(tool: Any, question: str, top_k: int, context: Any) -> Any:
    """兼容旧约定：tool.execute(payload, context=...) 或直接调用。"""
    payload = {"question": question, "top_k": top_k}
    try:
        if hasattr(tool, "execute"):
            return await _maybe_await(tool.execute(payload, context=context))
        if callable(tool):
            try:
                return await _maybe_await(tool(payload, context))
            except TypeError:
                return await _maybe_await(tool(payload))
    except Exception as e:
        log.warning("legacy knowledge.search tool failed: %s", e)
    return []


def _finalize_hits(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        for key in ("hits", "results", "chunks", "data"):
            if isinstance(raw.get(key), list):
                raw = raw[key]
                break
        else:
            return []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for h in raw:
        d = h if isinstance(h, dict) else _hit_to_dict(h)
        n = _normalize_hit(d)
        if n:
            out.append(n)
    return out


def _normalize_hit(h: dict[str, Any]) -> dict[str, Any] | None:
    """hit 归一化。缺少 document_id/chunk_id 的片段无法诚实引用，直接丢弃。"""
    document_id = h.get("document_id")
    chunk_id = h.get("chunk_id")
    if not document_id or not chunk_id:
        return None
    quote = h.get("quote") or h.get("text") or h.get("content") or h.get("snippet") or ""
    page = h.get("page_number")
    try:
        page_number = int(page) if page is not None else None
    except (TypeError, ValueError):
        page_number = None
    return {
        "document_id": str(document_id),
        "chunk_id": str(chunk_id),
        "title": str(h.get("title") or ""),
        "source": str(h.get("source") or ""),
        "page_number": page_number,
        "quote": str(quote).strip(),
    }


def _build_citations(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "document_id": h["document_id"],
            "chunk_id": h["chunk_id"],
            "title": h["title"],
            "source": h["source"],
            "page_number": h["page_number"],
            "quote": h["quote"],
        }
        for h in hits
    ]


# ---------------------------------------------------------------- 新旧资料数值冲突检测（S4）
# 配比/用量数值：比例写法（如 1:50）与带单位数值（如 100毫升、2.5kg、3亩）。
_RATIO_PATTERNS = (
    re.compile(r"\d+:\d+"),
    re.compile(r"\d+(?:\.\d+)?(?:kg|克|毫升|L|ml|亩)", re.IGNORECASE),
)
# 冲突判定所需的两类来源：新（专家纠错）与旧（普通上传/小院资料）
_EXPERT_SOURCE = "expert_correction"
# 保留 tenant_material：数据迁移改写前，迁移前缓存/序列化结果里的引用仍带旧拼写，
# 冲突检测漏识别它会把"新旧资料打架"误判为无冲突（S4 红线漏报）。
_LEGACY_SOURCES = ("upload", "yard_material", "tenant_material")


def _extract_ratio_values(text: str) -> set[str]:
    """从文本中抽取配比/用量数值集合（比例 + 带单位数值两种模式）。"""
    values: set[str] = set()
    for pat in _RATIO_PATTERNS:
        values.update(m.group(0) for m in pat.finditer(text or ""))
    return values


def _detect_conflicting_values(answer: str, citations: list[dict[str, Any]]) -> str | None:
    """检测新旧资料对同一问题给出不同配比数值（S4 红线）。

    触发条件：citations 同时包含 source=expert_correction 与
    source=upload/yard_material（含历史拼写 tenant_material）的文档，且答案区（答案正文 + 引用原文）
    出现的多于一种配比/用量数值。命中时返回警示语（列出各值与来源），
    否则返回 None。宁可多提示不可漏报：多来源并存且数值不一致一律转专家。
    """
    sources = {str(c.get("source") or "") for c in citations}
    if _EXPERT_SOURCE not in sources or not any(s in _LEGACY_SOURCES for s in sources):
        return None
    # 值 → 出现该值的引用来源集合（用于"列出各值与来源"）
    value_sources: dict[str, set[str]] = {}
    for c in citations:
        src = str(c.get("source") or "") or "unknown"
        for v in _extract_ratio_values(str(c.get("quote") or "")):
            value_sources.setdefault(v, set()).add(src)
    all_values = set(value_sources) | _extract_ratio_values(answer or "")
    if len(all_values) <= 1:
        return None
    parts = []
    for v in sorted(all_values):
        srcs = sorted(value_sources.get(v) or {"答案正文"})
        parts.append(f"{v}（{'/'.join(srcs)}）")
    return (
        "检测到新旧资料对同一问题给出不同数值（"
        + "；".join(parts)
        + "），已转专家复核，请以专家意见为准。"
    )


async def _generate_answer(
    question: str,
    hits: list[dict[str, Any]],
    *,
    yard_id: str,
    request_id: str,
    db: Any = None,
) -> str:
    """调用大模型生成答案正文；不可用时抛异常由上层降级为检索原文摘要。"""
    if complete_with_logging is None or LLMRequest is None:
        raise RuntimeError("llm service not available")
    try:
        from app.modules.llm.schemas import ChatMessage, LLMMetadata

        real_available = True
    except ImportError:
        real_available = False

    if real_available and db is not None:
        # 真实签名：complete_with_logging(db, LLMRequest(messages, metadata))
        req = LLMRequest(
            messages=[
                ChatMessage(role="system", content=SYSTEM_PROMPT),
                ChatMessage(role="user", content=build_user_prompt(question, hits)),
            ],
            metadata=LLMMetadata(yard_id=yard_id, request_id=request_id),
        )
        try:
            result = await _maybe_await(complete_with_logging(db, req))
        except TypeError:
            # 兼容单参数签名 complete_with_logging(req)
            result = await _maybe_await(complete_with_logging(req))
    else:
        # 旧兼容签名
        user_prompt = build_user_prompt(question, hits)
        try:
            req = LLMRequest(system_prompt=SYSTEM_PROMPT, prompt=user_prompt)
        except Exception:
            req = LLMRequest(prompt=user_prompt)  # type: ignore[call-arg]
        result = await _maybe_await(complete_with_logging(req))

    text = (
        getattr(result, "content", None)
        or getattr(result, "text", None)
        or getattr(result, "answer", None)
    )
    if not text:
        raise RuntimeError("llm returned empty content")
    return str(text).strip()


def _fallback_answer_from_hits(hits: list[dict[str, Any]]) -> str:
    lines = ["（模型服务暂不可用，以下为与问题相关的检索资料原文摘要，仅供参考）", ""]
    for i, h in enumerate(hits, start=1):
        title = h["title"] or "未知标题"
        lines.append(f"【来源{i}】{title}")
        lines.append(h["quote"] or "（片段内容为空）")
        lines.append("")
    return "\n".join(lines).strip()


class AgricultureQAAgent:
    """农业问答 Agent：检索增强生成，绝不伪造引用。"""

    code = "agriculture_qa"
    version = "1.0.0"

    async def run(self, input_data: dict, context: Any) -> Any:
        question = str(input_data.get("question", "") or "").strip()
        if not question:
            return _make_agent_result(
                status="failed", output={}, warnings=["问题不能为空。"], error="empty question"
            )
        try:
            top_k = int(input_data.get("top_k", 5))
        except (TypeError, ValueError):
            top_k = 5
        top_k = max(1, min(20, top_k))

        metadata = getattr(context, "metadata", None)
        db = getattr(context, "db", None)
        if db is None and isinstance(metadata, dict):
            db = metadata.get("db")
        yard_id = str(getattr(context, "yard_id", "") or "")
        request_id = str(getattr(context, "request_id", "req_unknown") or "req_unknown")

        hits = await _search_knowledge(question, top_k, context, db)
        high_risk = any(k in question for k in HIGH_RISK_KEYWORDS)

        # 无命中：明确说明资料不足，不编造
        if not hits:
            output = QAOutput(
                answer=NO_HIT_ANSWER,
                citations=[],
                warnings=[DISCLAIMER],
                needs_expert_review=True,
            )
            return _make_agent_result(
                status="succeeded",
                output=output.model_dump(),
                citations=[],
                warnings=output.warnings,
            )

        citations = _build_citations(hits)
        warnings: list[str] = []
        needs_expert_review = False

        try:
            answer = await _generate_answer(
                question, hits, yard_id=yard_id, request_id=request_id, db=db
            )
        except Exception as e:
            log.warning("llm generation failed, fallback to extractive summary: %s", e)
            answer = _fallback_answer_from_hits(hits)
            warnings.append(LLM_UNAVAILABLE_WARNING)
            needs_expert_review = True

        if high_risk:
            needs_expert_review = True
            warnings.append(RISK_WARNING)

        # S4：新旧资料数值冲突红线——专家纠错与普通资料并存且配比数值不一致时，
        # 强制转专家复核并明示各值与来源。
        conflict_msg = _detect_conflicting_values(answer, citations)
        if conflict_msg:
            needs_expert_review = True
            warnings.append(conflict_msg)

        output = QAOutput(
            answer=answer,
            citations=citations,  # type: ignore[arg-type]
            warnings=warnings,
            needs_expert_review=needs_expert_review,
        )
        output = self._validate_or_downgrade(output, citations)
        return _make_agent_result(
            status="succeeded",
            output=output.model_dump(),
            citations=[c for c in output.model_dump()["citations"]],
            warnings=output.warnings,
        )

    @staticmethod
    def _validate_or_downgrade(output: QAOutput, citations: list[dict]) -> QAOutput:
        """用契约 jsonschema 校验输出；失败则降级为纯文本答案 + 真实引用。"""
        schema = _load_qa_schema()
        if schema is None:
            return output  # 无校验器可用时直接放行（已记录）
        try:
            import jsonschema

            jsonschema.validate(instance=output.model_dump(), schema=schema)
            return output
        except Exception as e:
            log.warning("qa output schema validation failed, downgrading: %s", e)
            warnings = list(output.warnings) + ["结构化输出校验未通过，已降级为纯文本答案。"]
            return QAOutput(
                answer=str(output.answer),
                citations=citations,  # type: ignore[arg-type]
                warnings=warnings,
                needs_expert_review=True,
            )


def _register_agent() -> None:
    try:
        get_global_registry().register(AgricultureQAAgent.code, AgricultureQAAgent())
        log.info("registered agent %s v%s", AgricultureQAAgent.code, AgricultureQAAgent.version)
    except Exception as e:
        log.warning("agent registration skipped: %s", e)


_register_agent()
