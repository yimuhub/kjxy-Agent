"""Agent 工厂蓝图：LLM 提示词、蓝图解析（容忍围栏/坏 JSON）与启发式兜底。

设计要点：
- LLM 只被允许输出一个 JSON 对象 ``{"agents": [...]}``；解析链路对
  ```json 代码围栏与前后杂注容忍，但对结构缺失/角色过少/JSON 非法一律
  抛 ValueError —— 由调用方落启发式兜底，并把 generated_by 诚实标为
  ``heuristic``（FakeLLM 的确定性 canned-text 必然走这条路，不伪装成 llm）。
- 工具约束：提示词与解析过滤共用同一白名单 = 注册表中「只读、无副作用」
  的工具（默认只有 knowledge.search）；副作用类工具（workorder.dispatch
  等）不进蓝图，从源头保证工厂产物可低风险自批。
- 启发式兜底：4 个互补标准角色（行业问答助手 / 技术培训师 / 政策解读员 /
  数据汇总助理），persona 按行业描述（含 focus 侧重）定制，字段齐全、
  确定性可测。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)

#: 工厂产物的默认（且兜底）工具；assess_risk 对只读工具判 low 的前提
DEFAULT_BLUEPRINT_TOOL = "knowledge.search"
#: 知识范围默认值（assess_risk：scopes 为空一律 high，故必填）
DEFAULT_KNOWLEDGE_SCOPES = ["tenant"]
#: 蓝图角色数硬边界（与提示词一致）
MIN_ROLES, MAX_ROLES = 3, 6
#: 启发式角色固定 4 个（介于 3-6 的硬边界内）
HEURISTIC_ROLE_COUNT = 4
#: 每个角色的建议问题条数
SUGGESTED_QUESTION_COUNT = 5

_BLUEPRINT_SCHEMA_HINT = (
    '{"agents": [{"agent_code": "小写蛇形编码", "name": "角色名", "emoji": "单个表情", '
    '"description": "一句话职责", "persona_prompt": "系统人设（你是谁、面向什么行业、'
    '如何回答）", "task_prompt_template": "必须包含 {{question}} 占位的任务模板", '
    '"allowed_tools": ["knowledge.search"], "knowledge_scopes": ["tenant"], '
    '"suggested_questions": ["该行业真实用户会问的问题，恰好 5 条"]}]}'
)


def tool_whitelist() -> list[str]:
    """蓝图可选工具：注册表/Builtin 元数据中只读且无副作用的工具 code。

    宁严勿松：risk_level 非 read_only、side_effect=True 或已禁用的工具一律
    不进白名单（工厂产物默认 knowledge.search，保证 assess_risk 判 low）。
    """
    from app.modules.tools.registry import BUILTIN_TOOLS, tool_registry

    codes: set[str] = set()
    try:
        for tool in tool_registry.list_tools():
            if tool.enabled and not tool.side_effect and tool.risk_level == "read_only":
                codes.add(tool.code)
    except Exception as e:  # 注册表异常不能让蓝图规划崩溃
        log.warning("factory tool whitelist: registry unavailable: %s", e)
    for meta in BUILTIN_TOOLS:
        if (
            meta.get("enabled", True)
            and not meta.get("side_effect")
            and str(meta.get("risk_level")) == "read_only"
        ):
            codes.add(str(meta.get("code")))
    return sorted(codes)


def build_blueprint_messages(industry_brief: str, focus: str | None) -> list[dict[str, str]]:
    """LLM 蓝图规划的 messages（system 定规则与工具白名单，user 给行业输入）。"""
    allowed = ", ".join(tool_whitelist()) or DEFAULT_BLUEPRINT_TOOL
    focus_line = f"\n侧重方向：{focus}" if focus else ""
    system = (
        "你是科技小院平台的 Agent 工厂规划器。根据行业描述设计一组互补的声明式 Agent "
        "角色（配置即 Agent：persona + 任务模板 + 工具白名单 + 知识范围）。\n"
        "硬性要求：\n"
        "1. 只输出一个 JSON 对象，不要输出解释文字、注释或多余文本；"
        f"结构：{_BLUEPRINT_SCHEMA_HINT}\n"
        f"2. 角色数量 {MIN_ROLES}-{MAX_ROLES} 个，分工明确且互补，不得重复定位。\n"
        "3. agent_code 只允许小写字母、数字、下划线，且以字母开头。\n"
        "4. task_prompt_template 必须包含 {{question}} 占位符，运行时替换为用户问题。\n"
        f"5. allowed_tools 只能从白名单 [{allowed}] 中选择；没有把握时只给 "
        f"{DEFAULT_BLUEPRINT_TOOL}，禁止发明工具名。\n"
        '6. knowledge_scopes 固定为 ["tenant"]；suggested_questions 恰好 5 条，'
        "必须是该行业一线用户真实会问的中文问题。\n"
        "7. 所有文案使用简体中文，紧扣行业描述与侧重方向。"
    )
    user = f"行业描述：{industry_brief}{focus_line}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# ---------------------------------------------------------------------------
# LLM 输出解析（纯函数：raw text -> 规整角色列表；失败抛 ValueError）
# ---------------------------------------------------------------------------


def _extract_json_object(text: str) -> dict[str, Any] | list[Any]:
    """容忍 ```json 围栏与前后杂注：截取首个 { 到末个 } 后 json.loads。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()
    obj_start, obj_end = cleaned.find("{"), cleaned.rfind("}")
    list_start = cleaned.find("[")
    # 顶层直接给数组的宽松情形：[ ... ]（且不是对象内部的 [）
    if obj_start < 0 and list_start >= 0:
        arr_end = cleaned.rfind("]")
        if arr_end <= list_start:
            raise ValueError("蓝图文本中找不到完整 JSON 数组")
        parsed = json.loads(cleaned[list_start : arr_end + 1])
        if not isinstance(parsed, list):
            raise ValueError("蓝图 JSON 数组解析异常")
        return parsed
    if obj_start < 0 or obj_end <= obj_start:
        raise ValueError("蓝图文本中找不到 JSON 对象")
    parsed = json.loads(cleaned[obj_start : obj_end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("蓝图 JSON 顶层不是对象")
    return parsed


def _clean_str_list(raw: Any) -> list[str]:
    """字符串列表清洗：逐条 strip、去空、按序去重。"""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for v in raw:
        s = str(v).strip()
        if s and s not in out:
            out.append(s)
    return out


def _normalize_item(
    raw: Any, index: int, known_tools: list[str], max_questions: int = 8
) -> dict[str, Any] | None:
    """单角色规整：必填字段缺失返回 None（丢弃该角色）；缺省字段安全补齐。"""
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    persona = str(raw.get("persona_prompt") or "").strip()
    template = str(raw.get("task_prompt_template") or "").strip()
    if not name or not persona or not template:
        return None
    if "{{question}}" not in template:
        # 容忍漏写占位符：自动补齐，保证运行时可替换（宁保守不崩）
        template = f"{template}\n\n用户问题：{{{{question}}}}"
    requested = _clean_str_list(raw.get("allowed_tools"))
    # 白名单硬过滤：LLM 发明的工具名一律丢弃；全被丢弃则回落默认只读工具
    tools = [t for t in requested if t in known_tools] or [DEFAULT_BLUEPRINT_TOOL]
    scopes = _clean_str_list(raw.get("knowledge_scopes")) or list(DEFAULT_KNOWLEDGE_SCOPES)
    questions = _clean_str_list(raw.get("suggested_questions"))[:max_questions]
    return {
        "agent_code": str(raw.get("agent_code") or f"agent_{index + 1}").strip(),
        "name": name[:100],
        "emoji": str(raw.get("emoji") or "").strip()[:32],
        "description": str(raw.get("description") or "").strip()[:1000],
        "persona_prompt": persona,
        "task_prompt_template": template,
        "allowed_tools": tools,
        "knowledge_scopes": scopes,
        "suggested_questions": questions,
    }


def parse_blueprint(
    raw: str, allowed_tools: list[str] | None = None, known_tools: list[str] | None = None
) -> list[dict[str, Any]]:
    """把 LLM 原始输出解析为规整角色列表；任何结构性问题抛 ValueError。

    - ``allowed_tools``：旧参数名（保留兼容），非 None 时作为可选工具白名单；
    - ``known_tools``：真正的白名单参数；两者都缺省时运行时取注册表白名单。
    """
    whitelist = known_tools if known_tools is not None else (allowed_tools or tool_whitelist())
    parsed = _extract_json_object(raw)
    agents_raw = parsed if isinstance(parsed, list) else parsed.get("agents")
    if not isinstance(agents_raw, list) or not agents_raw:
        raise ValueError("蓝图缺少非空 agents 数组")
    items: list[dict[str, Any]] = []
    for index, entry in enumerate(agents_raw):
        item = _normalize_item(entry, index, whitelist)
        if item is not None:
            items.append(item)
    if len(items) < MIN_ROLES:
        raise ValueError(f"有效角色仅 {len(items)} 个，少于下限 {MIN_ROLES}")
    return items[:MAX_ROLES]


# ---------------------------------------------------------------------------
# 启发式兜底蓝图（LLM 不可用/输出不可解析时的确定性产物）
# ---------------------------------------------------------------------------


def industry_label(brief: str) -> str:
    """展示用行业短语：首行、压缩空白、截 16 字并去尾部标点。"""
    first_line = (brief or "").strip().splitlines()[0] if (brief or "").strip() else "行业"
    label = re.sub(r"\s+", "", first_line)[:16]
    return label.rstrip("，。；、,.;:：!！?？") or "行业"


def _ascii_slug(text: str, *, max_len: int = 24, default: str = "ind") -> str:
    """agent_code 用 ASCII 蛇形片段：只保留英数，其余折叠为下划线。"""
    slug = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    if not slug:
        digest = hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:6]
        slug = f"{default}_{digest}" if default else digest
    slug = slug[:max_len].strip("_")
    if not slug or not slug[0].isalpha():
        slug = f"{default}_{slug}" if default else f"a{slug}"
    return slug


def finalize_codes(items: list[dict[str, Any]], run_id: str) -> list[dict[str, Any]]:
    """为蓝图角色生成租户内唯一、命中 ``^[a-z][a-z0-9_]*$`` 的最终 agent_code。

    规则：slug(原 code) + "_" + run 短 id（8 位十六进制）。同 run 内去重
    （追加序号），并把 LLM 可能给出的内置 Agent 撞码（data_analyst 等）
    天然错开；结果写回 item["agent_code"]，与最终草稿一致。
    """
    run6 = re.sub(r"[^a-z0-9]", "", (run_id or "").lower())[:8] or "run00000"
    seen: set[str] = set()
    for index, item in enumerate(items):
        base = _ascii_slug(str(item.get("agent_code") or f"agent_{index + 1}"), max_len=64)
        suffix = f"_{run6}"
        candidate = f"{base}{suffix}"
        dedup = index
        while candidate in seen:
            dedup += 1
            candidate = f"{base}{suffix}_{dedup}"
        if len(candidate) > 100:
            # 超长时裁剪 base，保留完整 run 后缀（唯一性优先）
            candidate = base[: max(1, 100 - len(suffix))] + suffix
        seen.add(candidate)
        item["agent_code"] = candidate
    return items


def build_blueprint_payload(
    industry_brief: str, focus: str | None, generated_by: str, items: list[dict[str, Any]]
) -> dict[str, Any]:
    """蓝图落库结构（factory_runs.blueprint_json 的顶层契约）。"""
    return {
        "schema": "agent_factory_blueprint.v1",
        "industry_brief": industry_brief,
        "focus": focus,
        "generated_by": generated_by,
        "role_count": len(items),
        "agents": items,
    }


# 启发式四角色定义：(code 后缀, 名称后缀, emoji, 定位, 任务模板)
_HEURISTIC_ROLES: list[tuple[str, str, str, str, str]] = [
    (
        "qa",
        "问答助手",
        "💬",
        "回答该行业从业者提出的各类实操问题",
        "请依据下方资料，直接、具体地回答用户的问题，必要时给出分步建议：{{question}}",
    ),
    (
        "trainer",
        "技术培训师",
        "🎓",
        "面向新手与一线人员设计培训方案、讲解操作要领和常见误区",
        "请以培训讲师的口吻，把下面的问题讲清楚：先给结论，再讲步骤与注意事项：{{question}}",
    ),
    (
        "policy",
        "政策解读员",
        "📜",
        "梳理与本行业相关的补贴、资质、合规与扶持政策要点并给出申领建议",
        "请从政策与合规角度解读下面的问题，指出适用条件、办理路径与风险提示：{{question}}",
    ),
    (
        "summary",
        "数据汇总助理",
        "📊",
        "把零散的记录、台账与数据整理成结构化摘要、对比表和汇报口径",
        "请把下面问题涉及的信息整理成结构化要点（必要时用表格口径），并注明数据缺口：{{question}}",
    ),
]


def heuristic_blueprint(industry_brief: str, focus: str | None = None) -> list[dict[str, Any]]:
    """内置启发式蓝图：4 个互补标准角色，persona/slogan 按行业描述定制。

    触发条件：LLM 调用失败或输出解析失败。产物字段与 LLM 蓝图完全同构，
    suggested_questions 固定 5 条（含侧重方向），保证覆盖探测可直接进行。
    """
    label = industry_label(industry_brief)
    focus_part = f"重点关注方向：{focus}。" if focus else ""
    items: list[dict[str, Any]] = []
    for suffix, name_tail, emoji, duty, template in _HEURISTIC_ROLES:
        persona = (
            f"你是面向「{label}」行业的{name_tail}，负责{duty}。"
            f"回答基于本租户知识库资料，简洁、务实、可操作，资料不足时如实说明。{focus_part}"
        )
        questions = [
            f"{label}从业需要掌握哪些关键要点？",
            f"{label}新手入门的培训和操作步骤是什么？",
            f"{label}行业目前有哪些补贴与扶持政策可以申请？",
            f"{label}日常经营数据应该如何记录和汇总？",
            (f"{focus}应该如何落地？" if focus else f"{label}常见问题有哪些处理办法和合规要求？"),
        ][:SUGGESTED_QUESTION_COUNT]
        items.append(
            {
                "agent_code": _ascii_slug(industry_brief, max_len=20) + f"_{suffix}",
                "name": f"{label}{name_tail}"[:100],
                "emoji": emoji,
                "description": f"由 Agent 工厂按行业描述自动生成的{name_tail}：{duty}。",
                "persona_prompt": persona,
                "task_prompt_template": template,
                "allowed_tools": [DEFAULT_BLUEPRINT_TOOL],
                "knowledge_scopes": list(DEFAULT_KNOWLEDGE_SCOPES),
                "suggested_questions": questions,
            }
        )
    return items
