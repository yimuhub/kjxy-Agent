"""对话内生成能力：自然语言需求 → 声明式 Agent 草稿 → 能力卡片。

权限口径（与能力中心完全一致，**不开权限旁路**）：
- platform_admin / yard_admin：创建 draft（发布仍走既有发布闸门）；
- 其他角色：不执行创建，只产出"建议卡片"，由管理员在能力中心确认后落地，
  并留审计 ``chat_capability_requested``（来源可追溯）。

安全默认值：工具白名单固定 ``["knowledge.search"]``（只读检索）、知识范围
固定 ``["yard"]``——对应 risk=low；任何副作用工具必须由管理员在能力中心
显式添加并重新评估（与手工创建的闸门一致）。
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.agents import declarative
from app.modules.agents.models import CREATED_FROM_CHAT
from app.modules.audit.service import log_audit
from app.modules.llm.schemas import ChatMessage, LLMMetadata, LLMRequest
from app.modules.llm.service import complete_with_logging

log = get_logger(__name__)

#: 默认工具与知识范围（只读 → risk=low）
DEFAULT_TOOLS: list[str] = ["knowledge.search"]
DEFAULT_SCOPES: list[str] = ["yard"]

#: 卡片类型标识
CARD_TYPE_CAPABILITY = "capability"

#: 卡片状态
CARD_CREATED = "created"
CARD_REQUESTED = "requested"

_JSON_RE = re.compile(r"\{.*\}", re.S)
_CODE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_MAX_NAME = 40
_MAX_DESC = 200
_MAX_PROMPT = 1200


def _is_admin(ctx: YardContext) -> bool:
    return bool({"yard_admin", "yard_owner", "platform_admin"} & set(ctx.roles))


def _fallback_code() -> str:
    return f"chat_agent_{uuid.uuid4().hex[:8]}"


def _sanitize_code(raw: str) -> str:
    """把任意文本压成合法 agent_code（^[a-z][a-z0-9_]*$）；不合规则弃用。"""
    text = (raw or "").strip().lower()
    text = re.sub(r"[^a-z0-9_]+", "_", text).strip("_")
    if not text or not _CODE_RE.match(text) or len(text) < 2:
        return ""
    return text[:60]


def _fallback_spec(request_text: str) -> dict[str, Any]:
    """模型不可用时的确定性兜底：按用户原话生成一个可用草稿。"""
    brief = " ".join((request_text or "").split())[:_MAX_NAME]
    return {
        "name": brief or "新助手",
        "description": (request_text or "").strip()[:_MAX_DESC],
        "persona_prompt": (
            f"你是「{brief or '新助手'}」，面向农业场景提供专业、可执行的判断与建议。"
            "回答要点：结论先行、给出依据、无法确认时明确说明。"
        ),
        "task_prompt_template": (
            "请依据问题给出专业、可执行的答复；需要引用资料时保留来源线索，"
            "资料不足时明确说明无法确认。\n\n问题：{{question}}"
        ),
        "emoji": "",
    }


async def _spec_from_llm(db: Session, ctx: YardContext, request_text: str) -> dict[str, Any]:
    """让模型把需求转成能力规格；失败一律降级到确定性兜底。"""
    prompt = (
        "你是农业智能平台的助手设计器。根据用户需求给出助手规格，只输出 JSON："
        '{"name":"助手名称（不超过20字）","description":"一句话职责说明",'
        '"persona_prompt":"角色设定（第二人称，说明专长与口吻，200字内）",'
        '"task_prompt_template":"任务模板，必须包含 {{question}} 占位符，'
        '说明如何回答用户问题",'
        '"emoji":"一个 emoji 或空字符串"}。不要输出 JSON 以外的任何内容。'
    )
    try:
        resp = await complete_with_logging(
            db,
            LLMRequest(
                messages=[
                    ChatMessage(role="system", content=prompt),
                    ChatMessage(
                        role="user",
                        content=f"用户需求：{(request_text or '').strip()[:1000]}",
                    ),
                ],
                metadata=LLMMetadata(
                    yard_id=ctx.yard_id,
                    task_id=f"chat-cap-{uuid.uuid4().hex[:12]}",
                    request_id=ctx.request_id,
                    purpose="chat_capability",
                    agent_ref="chat_capability",
                ),
            ),
        )
    except Exception as e:  # noqa: BLE001 —— 模型不可用不得阻断能力生成
        log.warning("能力规格生成失败，降级为兜底模板：%s", e)
        return _fallback_spec(request_text)

    m = _JSON_RE.search(resp.content or "")
    if not m:
        return _fallback_spec(request_text)
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return _fallback_spec(request_text)
    if not isinstance(data, dict):
        return _fallback_spec(request_text)

    spec = _fallback_spec(request_text)
    for key in ("name", "description", "persona_prompt", "task_prompt_template", "emoji"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            spec[key] = value.strip()
    # 模板必须保留占位符，否则声明式校验会拒——不合规则退回兜底模板
    if declarative.QUESTION_PLACEHOLDER not in spec["task_prompt_template"]:
        spec["task_prompt_template"] = _fallback_spec(request_text)["task_prompt_template"]
    spec["name"] = spec["name"][:_MAX_NAME]
    spec["description"] = spec["description"][:_MAX_DESC]
    spec["persona_prompt"] = spec["persona_prompt"][:_MAX_PROMPT]
    spec["task_prompt_template"] = spec["task_prompt_template"][:_MAX_PROMPT]
    return spec


def _card(
    *,
    state: str,
    name: str,
    description: str,
    agent_id: str = "",
    code: str = "",
    risk_level: str = "",
    status: str = "",
    tools: list[str] | None = None,
    note: str = "",
) -> dict[str, Any]:
    """能力卡片载荷（前端据此渲染可操作控件）。"""
    actions: list[dict[str, Any]] = []
    if state == CARD_CREATED and agent_id:
        actions = [
            {"key": "view", "label": "在能力中心查看"},
            {"key": "publish", "label": "发布", "requires_role": "yard_admin"},
        ]
    return {
        "type": CARD_TYPE_CAPABILITY,
        "state": state,
        "name": name,
        "description": description,
        "agent_id": agent_id,
        "code": code,
        "risk_level": risk_level,
        "status": status,
        "tools": list(tools or []),
        "note": note,
        "actions": actions,
    }


async def generate_capability(
    db: Session, *, ctx: YardContext, request_text: str
) -> tuple[dict[str, Any], str]:
    """按需求生成能力草稿，返回 (卡片载荷, 面向用户的结果说明)。

    非管理员只产出建议卡片（不落库），管理员创建 draft 并返回卡片。
    """
    spec = await _spec_from_llm(db, ctx, request_text)
    name = spec["name"]
    description = spec["description"]

    if not _is_admin(ctx):
        log_audit(
            db,
            yard_id=ctx.yard_id,
            actor_id=ctx.user_id,
            action="chat_capability_requested",
            resource_type="yard_agent",
            resource_id="",
            result="success",
            request_id=ctx.request_id,
            metadata={"name": name},
        )
        card = _card(
            state=CARD_REQUESTED,
            name=name,
            description=description,
            note="你的需求已整理成能力建议，需小院管理员确认后创建。",
        )
        return card, (
            f"已把「{name}」整理成能力建议。"
            "创建草稿需要小院管理员权限，请联系管理员在能力中心确认。"
        )

    body = {
        "agent_code": _fallback_code(),
        "name": name,
        "description": description,
        "emoji": spec.get("emoji") or "",
        "persona_prompt": spec["persona_prompt"],
        "task_prompt_template": spec["task_prompt_template"],
        "allowed_tools": list(DEFAULT_TOOLS),
        "knowledge_scopes": list(DEFAULT_SCOPES),
        "created_from": CREATED_FROM_CHAT,
    }
    req = declarative.parse_create_request(body)
    base_name = name
    suffix = 2
    while True:
        try:
            agent = declarative.create_agent(
                db,
                yard_id=ctx.yard_id,
                actor_id=ctx.user_id,
                req=req,
                request_id=ctx.request_id,
            )
            break
        except AppError as exc:
            # 同名冲突按 D3 口径自动加「-2」「-3」…后缀重试；_require_name_free
            # 在 db.add/flush 之前抛错，会话干净可安全重试。其他错误原样上抛。
            if exc.code != "DUPLICATE_NAME" or suffix > 50:
                raise
            req.name = f"{base_name}-{suffix}"
            suffix += 1
    renamed = agent.name != name
    db.commit()
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="chat_capability_created",
        resource_type="yard_agent",
        resource_id=agent.id,
        result="success",
        request_id=ctx.request_id,
        metadata={"agent_code": agent.agent_code, "risk_level": agent.risk_level},
    )
    db.commit()
    card = _card(
        state=CARD_CREATED,
        name=agent.name,
        description=agent.description or description,
        agent_id=agent.id,
        code=agent.agent_code,
        risk_level=agent.risk_level,
        status=agent.status,
        tools=list(agent.allowed_tools or []),
        note=(
            f"同小院已有助手「{name}」，草稿名称已自动调整为「{agent.name}」。"
            if renamed
            else "已创建草稿，可直接发布；发布后即可在对话中被路由到。"
        ),
    )
    return card, (
        f"已创建能力草稿「{agent.name}」（编码 {agent.agent_code}，"
        f"风险 {agent.risk_level}）。确认无误后点卡片上的「发布」即可启用。"
    )


__all__ = [
    "CARD_CREATED",
    "CARD_REQUESTED",
    "CARD_TYPE_CAPABILITY",
    "DEFAULT_SCOPES",
    "DEFAULT_TOOLS",
    "generate_capability",
]
