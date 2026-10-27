"""一句话插件链路（v3.3）：需求描述 → 模型产出 workflow manifest → 注册闸。

与技能链路（``skill_agent``）同构但更严格：插件是可执行工作流，**不存在
"私有即用"**——生成结果一律落 ``pending_review``，审核通过（``enabled``）后
才能运行。安全边界沿用 plugins/workflow.py 的双闸：

- steps 只能引用注册表内真实存在的工具，模型不得凭空发明；
- llm 步骤的 prompt 里的 ``{{var}}`` 只在运行期以参数 ∪ 产物渲染；
- 生成/重试全程走小院设置 ``skill_lab`` 的同一配额（与技能生成共用日额度）；
- 全链路留审计 ``plugin_generated``。

入口（与 ``/market/agent/generate?kind=plugin`` 和对话 ``plugin_card`` 共用）：

- ``generate_plugin``      → 生成 manifest 并注册为待审工作流插件；
- ``handle_plugin_request``：对话链路入口（``chat/service.py`` 调用）。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.progress import PHASE_GENERATING, STEP_DONE, STEP_FAILED, STEP_RUNNING
from app.modules.audit.service import log_audit
from app.modules.llm.schemas import ChatMessage, LLMMetadata, LLMRequest
from app.modules.llm.service import complete_with_logging
from app.modules.market.skill_agent import (
    MAX_LLM_ATTEMPTS,
    MAX_SOURCES,
    _available_tools,
    _clean_text,
    _collect_sources,
    _consume_quota,
    _daily_limit,
    _ensure_quota,
    _lab_settings,
    _parse_json_object,
)
from app.modules.plugins.models import PluginManifest
from app.modules.plugins.workflow import register_workflow, validate_workflow_manifest

log = get_logger(__name__)

#: 卡片类型与状态（与 frontend/src/api/chat.ts::ChatPluginCard 一致）
CARD_TYPE = "plugin_card"
STATE_CREATED = "created"
STATE_FAILED = "failed"

#: manifest 提示词里展示给模型的工具清单上限（防止提示词无限膨胀）
_MAX_PROMPT_TOOLS = 40


# ---------------------------------------------------------------------------
# 规格生成
# ---------------------------------------------------------------------------


def _tools_hint(available_tools: dict[str, bool]) -> str:
    """提示词里的可用工具清单（生成场景只允许引用只读工具）。"""
    readonly = sorted(code for code, readonly_flag in available_tools.items() if readonly_flag)
    shown = readonly[:_MAX_PROMPT_TOOLS]
    return "、".join(shown) if shown else "（当前没有可用工具，steps 只能全部用 llm 步骤）"


def _manifest_prompt(
    utterance: str, tools_hint: str, sources: list[dict[str, str]]
) -> tuple[str, str]:
    """生成提示词：(system, user)。"""
    source_block = (
        "\n".join(f"- {s['title']} {s['url']}" for s in sources)
        if sources
        else "（没有可用参考来源）"
    )
    system = (
        "你是农业工作流插件设计器。把用户需求转成一个声明式 workflow manifest，"
        "只输出一个 JSON 对象，不要输出 Markdown 代码块围栏，也不要输出任何解释文字。\n"
        "字段（缺一不可，出现未知字段会被服务端拒绝）：\n"
        '{"name":"中文插件名，1-200 字","description":"一句话说明，1-1000 字",'
        '"params":[{"key":"question","label":"问题描述","required":true}],'
        '"steps":[{"kind":"tool","tool":"knowledge.search",'
        '"args":{"query":"{{question}}"},"save_as":"r1"},'
        '{"kind":"llm","prompt":"基于 {{r1}} 回答 {{question}}，给出可执行建议",'
        '"save_as":"answer"}]}\n'
        "硬约束：steps 1-8 条（kind=tool 必须带 tool 与 args，kind=llm 必须带 prompt）；"
        "tool 只能取自可用工具清单；save_as 是可选的标识符，后续步骤可用 {{save_as}} 引用"
        "前面产物，{{question}} 等参数必须先在 params 里声明；"
        "禁止生成 HTTP 直连、脚本、shell 步骤。\n"
        f"可用工具清单：{tools_hint}"
    )
    user = f"用户需求：{(utterance or '').strip()[:1000]}\n" f"可参考的公开资料：\n{source_block}"
    return system, user


def _fallback_manifest(
    utterance: str, sources: list[dict[str, str]], available_tools: dict[str, bool]
) -> dict[str, Any]:
    """模型不可用/输出不合规时的确定性兜底 manifest（纯 llm 步骤，内容保守）。"""
    title = " ".join((utterance or "").split())[:40] or "新建插件"
    steps: list[dict[str, Any]] = [
        {
            "kind": "llm",
            "prompt": (
                "针对 {{question}}：先澄清关键信息（地块、生育期、现场数据），"
                "再结合小院知识库证据给出结论先行、可执行的建议；"
                "资料不足时明确说明缺哪些数据以及如何补测，不编造数值。"
            ),
            "save_as": "answer",
        }
    ]
    # 有只读知识检索工具时，前置一步检索让产出有依据
    if "knowledge.search" in available_tools and available_tools["knowledge.search"]:
        steps.insert(
            0,
            {
                "kind": "tool",
                "tool": "knowledge.search",
                "args": {"query": "{{question}}", "top_k": 5},
                "save_as": "refs",
            },
        )
        steps[1]["prompt"] = (
            "基于以下知识库检索结果回答 {{question}}：\n{{refs}}\n"
            "结论先行、给出可执行建议；证据不足时明确说明缺哪些数据与补测方法，不编造数值。"
        )
    return {
        "name": title,
        "description": f"面向「{title}」的农业工作流插件草稿（模型未产出合规规格，"
        "按保守模板生成，请人工复核后再启用）。",
        "params": [{"key": "question", "label": "问题描述", "required": True}],
        "steps": steps,
    }


async def _build_manifest(
    db: Session,
    ctx: YardContext,
    utterance: str,
    sources: list[dict[str, str]],
    available_tools: dict[str, bool],
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    """产出 workflow manifest：(normalized_manifest, token_usage, model, note)。

    模型输出不合规时最多纠错 2 次；仍不合规则降级到确定性兜底模板（纯 llm/只读
    检索步骤），注册仍会走 pending_review 发布闸，保证不产生可执行的越权插件。
    """
    tools_hint = _tools_hint(available_tools)
    system, user = _manifest_prompt(utterance, tools_hint, sources)
    messages = [
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content=user),
    ]
    usage: dict[str, Any] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "model": "plugin_generator",
    }
    model_name = ""
    last_error = ""

    for attempt in range(MAX_LLM_ATTEMPTS):
        try:
            resp = await complete_with_logging(
                db,
                LLMRequest(
                    messages=messages,
                    metadata=LLMMetadata(
                        yard_id=ctx.yard_id,
                        task_id=f"plugin-gen-{uuid.uuid4().hex[:12]}",
                        request_id=ctx.request_id,
                        purpose="plugin_generation",
                        agent_ref="plugin_agent",
                    ),
                ),
            )
        except AppError as e:
            log.warning("插件 manifest 生成失败（第 %d 次）：%s", attempt + 1, e.message)
            last_error = e.message
            break
        usage["input_tokens"] += int(resp.input_tokens or 0)
        usage["output_tokens"] += int(resp.output_tokens or 0)
        model_name = resp.model or model_name
        usage["model"] = model_name or usage["model"]
        parsed = _parse_json_object(resp.content or "")
        if parsed is None:
            last_error = "模型输出不是合法 JSON 对象。"
            messages.append(ChatMessage(role="assistant", content=(resp.content or "")[:2000]))
            messages.append(
                ChatMessage(
                    role="user",
                    content=f"上一条输出无法解析：{last_error}。请只输出合法 JSON 对象。",
                )
            )
            continue
        try:
            normalized = validate_workflow_manifest(db, parsed)
        except AppError as e:
            last_error = e.message
            messages.append(
                ChatMessage(
                    role="assistant",
                    content=json.dumps(parsed, ensure_ascii=False)[:2000],
                )
            )
            messages.append(
                ChatMessage(
                    role="user",
                    content=f"上一条 manifest 未通过校验：{last_error}。请修正后只输出 JSON 对象。",
                )
            )
            continue
        usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
        return normalized, usage, model_name, ""

    usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
    if last_error:
        log.info("插件 manifest 生成降级为兜底模板：%s", last_error)
    fallback = _fallback_manifest(utterance, sources, available_tools)
    # 兜底 manifest 只含 llm/注册表只读工具步骤，校验恒通过；防御性保留异常路径
    normalized = validate_workflow_manifest(db, fallback)
    return (
        normalized,
        usage,
        model_name,
        "模型未产出合规 manifest，已按保守模板生成草稿，请人工复核后再启用。",
    )


# ---------------------------------------------------------------------------
# 对外服务
# ---------------------------------------------------------------------------


def _brief(record: PluginManifest) -> dict[str, Any]:
    """插件摘要（前端 ChatPluginCard.plugin 形状）。"""
    manifest = record.manifest_json if isinstance(record.manifest_json, dict) else {}
    return {
        "plugin_id": record.plugin_id,
        "name": str(manifest.get("name") or record.plugin_id),
        "description": str(manifest.get("description") or ""),
        "status": record.status,
        "step_count": len(manifest.get("steps") or []),
    }


async def generate_plugin(
    db: Session, *, ctx: YardContext, utterance: str, use_web: bool = True
) -> dict[str, Any]:
    """按一句话需求生成 workflow 插件，注册为待审草稿并返回生成结果。"""
    text = (utterance or "").strip()
    if not text:
        raise AppError("INVALID_REQUEST", "请描述你需要的插件。", http_status=400)
    settings = _lab_settings(db, ctx.yard_id)
    if settings.get("enabled") is False:
        raise AppError("SKILL_LAB_DISABLED", "本小院已关闭插件/技能生成服务。", http_status=403)
    _ensure_quota(ctx.yard_id, _daily_limit(db, ctx.yard_id))

    query = _clean_text(text) or text
    sources: list[dict[str, str]] = []
    if use_web and settings.get("use_web", True):
        sources = await _collect_sources(db, ctx, query, MAX_SOURCES)

    available = _available_tools()
    manifest, usage, model_name, note = await _build_manifest(db, ctx, text, sources, available)
    record = register_workflow(db, ctx, manifest)
    _consume_quota(ctx.yard_id)
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="plugin_generated",
        resource_type="plugin",
        resource_id=record.id,
        result="success",
        request_id=ctx.request_id,
        metadata={
            "plugin_id": record.plugin_id,
            "model": model_name,
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "source_count": len(sources),
            "step_count": len(manifest.get("steps") or []),
            "tools": sorted(
                {s["tool"] for s in manifest.get("steps") or [] if s.get("kind") == "tool"}
            ),
        },
    )
    db.flush()
    return {
        "plugin": _brief(record),
        "manifest": record.manifest_json,
        "sources": sources,
        "note": note,
        "token_usage": usage,
    }


# ---------------------------------------------------------------------------
# 对话链路
# ---------------------------------------------------------------------------


def _card(
    state: str,
    utterance: str,
    *,
    plugin: dict[str, Any] | None = None,
    manifest: dict[str, Any] | None = None,
    sources: list[dict[str, str]] | None = None,
    note: str = "",
    token_usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """插件卡片载荷（前端 ChatPluginCard 契约）。"""
    card: dict[str, Any] = {
        "type": CARD_TYPE,
        "state": state,
        "utterance": utterance,
        "plugin": plugin,
        "manifest": manifest,
        "sources": list(sources or []),
        "note": note,
    }
    if token_usage:
        card["token_usage"] = token_usage
    return card


async def handle_plugin_request(
    db: Session, *, ctx: YardContext, utterance: str, on_step: Any = None
) -> dict[str, Any]:
    """对话链路：一句话生成 workflow 插件（一律待审），返回插件卡片。"""
    text = (utterance or "").strip()
    if not text:
        raise AppError("INVALID_REQUEST", "请描述你需要的插件。", http_status=400)

    if on_step:
        on_step(PHASE_GENERATING, "正在按需求设计工作流插件", STEP_RUNNING)
    try:
        result = await generate_plugin(db, ctx=ctx, utterance=text, use_web=True)
    except AppError as e:
        if on_step:
            on_step(PHASE_GENERATING, f"插件生成失败：{e.message}", STEP_FAILED)
        return _card(STATE_FAILED, text, note=e.message)

    if on_step:
        on_step(PHASE_GENERATING, "插件草稿已生成并提交审核", STEP_DONE)
    plugin = result.get("plugin") or {}
    note = str(result.get("note") or "")
    if not note:
        note = (
            f"已生成工作流插件「{plugin.get('name')}」，已进入待审核状态，" "审核通过后即可运行。"
        )
    return _card(
        STATE_CREATED,
        text,
        plugin=plugin,
        manifest=result.get("manifest"),
        sources=result.get("sources") or [],
        note=note,
        token_usage=result.get("token_usage"),
    )


__all__ = [
    "CARD_TYPE",
    "STATE_CREATED",
    "STATE_FAILED",
    "generate_plugin",
    "handle_plugin_request",
]
