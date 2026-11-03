"""一句话生成工具（v3.25）：自然语言用途 → 工具定义草稿。

复用系统既有的 Agent+LLM 能力（与「一句话生成技能」同一套纪律）：
- 模型只产出**规格**（code/name/description/input_schema/risk/impl），不落库；
- 落库必须是 ``enabled=false`` 草稿，由人工在「可用工具」里确认后启用；
- 模型不可用或输出非法 JSON 时走确定性兜底规格，绝不留半成品，也绝不编造参数。
"""

import json
import re
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.llm.schemas import ChatMessage, LLMMetadata, LLMRequest
from app.modules.llm.service import complete_with_logging
from app.modules.tools.models import ToolDefinition

log = get_logger(__name__)

JSON_RE = re.compile(r"\{.*\}", re.S)
MAX_LLM_ATTEMPTS = 2
GENERATE_PURPOSE = "tool_generation"

RISK_LEVELS = ("read_only", "draft", "write_reversible", "external_side_effect")
IMPL_KINDS = ("prompt", "http")

#: 内置实现（registry）的工具不可由模型生成，也不能被删除——这里只服务自定义工具
_CODE_RE = re.compile(r"^[a-z][a-z0-9_.]{2,60}$")

_SYSTEM_PROMPT = """你是工具设计助手。用户用一句话描述想要的工具，你输出一份可执行的工具定义。

严格只输出一个 JSON 对象，不要输出解释文字、不要代码块围栏。字段与约束：
{
  "code": "小写字母开头，仅含小写字母/数字/下划线/点号，3-60 位",
  "name": "中文展示名，不超过 30 字",
  "description": "一句话说明这个工具做什么、什么时候该用它，不超过 120 字",
  "input_schema": {
    "type": "object",
    "properties": {"参数名": {"type": "string", "description": "中文说明"}},
    "required": ["必填参数名"],
    "additionalProperties": false
  },
  "impl_kind": "prompt 或 http",
  "impl": {
    "prompt_template": "impl_kind=prompt 时必填：给模型的指令，用 {参数名} 引用入参",
    "url": "impl_kind=http 时必填，完整 http(s) 地址",
    "method": "GET|POST|PUT|PATCH|DELETE",
    "headers": {"头部名": "头部值"}
  },
  "risk_level": "read_only | draft | write_reversible | external_side_effect",
  "side_effect": true 或 false,
  "required_permissions": ["tools:execute"]
}

规则：
1. 只描述用户要的能力，不要臆造他没提到的参数；参数尽量精简（1-4 个）。
2. impl_kind=prompt 表示执行时调用小院已配置的模型完成推理；http 表示调用外部接口。
3. 会改动外部世界（发消息、下单、写库、控制设备）的一律 external_side_effect + side_effect=true。
4. 拿不准就选 prompt + read_only，宁保守勿越权。
"""


def _parse_json_object(content: str) -> dict[str, Any] | None:
    match = JSON_RE.search(content or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _slug_code(text: str) -> str:
    """任意文本 → 合法工具 code；中文描述压不出 ASCII 时用随机后缀兜底。"""
    slug = re.sub(r"[^a-z0-9_.]+", "_", (text or "").lower()).strip("_.")
    slug = re.sub(r"_+", "_", slug)
    if not _CODE_RE.fullmatch(slug):
        return f"custom.tool_{uuid.uuid4().hex[:8]}"
    return slug[:60]


def fallback_spec(utterance: str) -> dict[str, Any]:
    """模型不可用/输出不合规时的确定性草稿：单参数 + prompt 实现 + 只读风险。"""
    text = " ".join((utterance or "").split())
    title = text[:30] or "自定义工具"
    return {
        "code": _slug_code(text[:24] or ""),
        "name": title,
        "description": (text[:120] or title) + "（模型兜底生成，请人工确认参数与执行方式）",
        "input_schema": {
            "type": "object",
            "properties": {"request": {"type": "string", "description": "本次请求内容"}},
            "required": ["request"],
            "additionalProperties": False,
        },
        "impl_kind": "prompt",
        "impl": {"prompt_template": "请完成下面的任务并给出结果：\n{request}"},
        "risk_level": "read_only",
        "side_effect": False,
        "required_permissions": ["tools:execute"],
    }


def _repair_prompt(errors: list[str]) -> str:
    return "上一份定义不合规：" + "；".join(errors) + "。请只输出修正后的 JSON 对象。"


async def generate_spec(
    db: Session,
    *,
    yard_id: str,
    user_id: str | None,
    request_id: str | None,
    utterance: str,
) -> dict[str, Any]:
    """一句话 → 工具规格草稿（未落库）；返回 ``{spec, source, model, note}``。"""
    text = (utterance or "").strip()
    if not text:
        raise AppError("INVALID_REQUEST", "请先用一句话说明工具要做什么。", http_status=400)

    messages = [
        ChatMessage(role="system", content=_SYSTEM_PROMPT),
        ChatMessage(role="user", content=text[:600]),
    ]
    last_error = ""
    for attempt in range(MAX_LLM_ATTEMPTS):
        try:
            resp = await complete_with_logging(
                db,
                LLMRequest(
                    messages=messages,
                    metadata=LLMMetadata(
                        yard_id=yard_id,
                        user_id=user_id,
                        task_id=f"tool-gen-{uuid.uuid4().hex[:12]}",
                        request_id=request_id,
                        purpose=GENERATE_PURPOSE,
                        agent_ref="tool_generator",
                    ),
                ),
            )
        except AppError as e:
            log.warning("工具规格生成失败（第 %d 次）：%s", attempt + 1, e.message)
            last_error = e.message
            break

        parsed = _parse_json_object(resp.content or "")
        if parsed is None:
            last_error = "模型输出不是合法 JSON 对象。"
            messages.append(ChatMessage(role="assistant", content=(resp.content or "")[:2000]))
            messages.append(ChatMessage(role="user", content=_repair_prompt([last_error])))
            continue

        errors = validate_spec(parsed)
        if errors:
            last_error = "；".join(errors)
            messages.append(ChatMessage(role="assistant", content=(resp.content or "")[:2000]))
            messages.append(ChatMessage(role="user", content=_repair_prompt(errors)))
            continue

        spec = normalize_spec(parsed, utterance=text)
        return {
            "spec": spec,
            "source": "model",
            "model": resp.model or "",
            "note": "草稿未启用，确认后才进入工具列表。",
        }

    return {
        "spec": fallback_spec(text),
        "source": "fallback",
        "model": "",
        "note": f"模型生成不可用（{last_error or '输出不合规'}），已给出保守草稿，请人工修订。",
    }


def validate_spec(raw: dict[str, Any]) -> list[str]:
    """规格静态校验：返回错误列表（空=通过）。错误信息面向操作者，中文可读。"""
    errors: list[str] = []
    code = str(raw.get("code") or "").strip()
    if not _CODE_RE.fullmatch(code):
        errors.append("code 需为小写字母开头、3-60 位的小写字母/数字/下划线/点号")
    schema = raw.get("input_schema")
    if not isinstance(schema, dict):
        errors.append("input_schema 必须是 JSON 对象")
    else:
        if schema.get("type") != "object":
            errors.append("input_schema.type 必须是 object")
        props = schema.get("properties")
        if props is not None and not isinstance(props, dict):
            errors.append("input_schema.properties 必须是对象")
        required = schema.get("required")
        if required is not None and not isinstance(required, list):
            errors.append("input_schema.required 必须是数组")
        elif isinstance(required, list) and required:
            if not isinstance(props, dict) or not props:
                # 有必填项却没有任何参数定义：模型调用时必然校验失败，直接拒收
                errors.append("声明了 required 参数但 properties 为空")
            elif isinstance(props, dict):
                missing = [str(r) for r in required if str(r) not in props]
                if missing:
                    errors.append(f"必填参数未定义：{'、'.join(missing)}")
    kind = str(raw.get("impl_kind") or "").strip()
    impl = raw.get("impl")
    if kind not in IMPL_KINDS:
        errors.append(f"impl_kind 必须是 {'|'.join(IMPL_KINDS)} 之一")
    elif not isinstance(impl, dict):
        errors.append("impl 必须是对象")
    elif kind == "prompt" and not str(impl.get("prompt_template") or "").strip():
        errors.append("prompt 工具需要 prompt_template")
    elif kind == "http":
        url = str(impl.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            errors.append("http 工具的 url 必须是 http(s) 完整地址")
    risk = str(raw.get("risk_level") or "").strip()
    if risk and risk not in RISK_LEVELS:
        errors.append(f"risk_level 必须是 {'|'.join(RISK_LEVELS)} 之一")
    return errors


def normalize_spec(raw: dict[str, Any], *, utterance: str) -> dict[str, Any]:
    """校验通过的规格 → 落库前的确定形态（补齐缺省、收紧字段长度、强制附加属性关闭）。"""
    schema = dict(raw.get("input_schema") or {})
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    schema.setdefault("required", [])
    # 注入防护：自定义工具一律禁止未声明参数（与 MCP 桥同口径）
    schema["additionalProperties"] = False

    kind = str(raw.get("impl_kind") or "prompt").strip()
    impl = dict(raw.get("impl") or {})
    risk = str(raw.get("risk_level") or "read_only").strip() or "read_only"
    side_effect = bool(raw.get("side_effect"))
    if kind == "http":
        method = str(impl.get("method") or "POST").upper()
        impl["method"] = method if method in ("GET", "POST", "PUT", "PATCH", "DELETE") else "POST"
        impl["url"] = str(impl.get("url") or "").strip().rstrip("/")
        # 任意 HTTP 出网都可能外带数据（GET 也能把参数写进 URL），一律走审批闸，
        # 不允许模型或用户自报 read_only 绕过
        risk = "external_side_effect"
        side_effect = True
    else:
        impl = {"prompt_template": str(impl.get("prompt_template") or "").strip()[:4000]}

    permissions = raw.get("required_permissions")
    if not isinstance(permissions, list) or not permissions:
        permissions = ["tools:execute"]

    return {
        "code": str(raw.get("code") or _slug_code(utterance))[:100],
        "version": str(raw.get("version") or "0.1.0")[:20],
        "name": str(raw.get("name") or utterance[:30] or "自定义工具")[:100],
        "description": str(raw.get("description") or "")[:2000],
        "input_schema": schema,
        "impl_kind": kind,
        "impl": impl,
        "risk_level": risk if risk in RISK_LEVELS else "read_only",
        "side_effect": side_effect or risk == "external_side_effect",
        "timeout_seconds": int(impl.get("timeout_seconds") or (20 if kind == "http" else 60)),
        "required_permissions": [str(p) for p in permissions][:8],
    }


def unique_code(db: Session, base: str) -> str:
    """code 全局唯一（内置行同样占位）：冲突时追加短随机后缀。"""
    code = base[:96]
    if not db.query(ToolDefinition).filter(ToolDefinition.code == code).first():
        return code
    return f"{code}_{uuid.uuid4().hex[:6]}"[:100]
