"""Agent 工厂蓝图：LLM 提示词、蓝图解析（容忍围栏/坏 JSON）与框架选角兜底。

设计要点：
- LLM 只被允许输出一个 JSON 对象 ``{"agents": [...]}``；解析链路对
  ```json 代码围栏与前后杂注容忍，但对结构缺失/角色过少/JSON 非法一律
  抛 ValueError —— 由调用方落框架选角兜底，并把 generated_by 诚实标为
  ``heuristic``（FakeLLM 的确定性 canned-text 必然走这条路，不伪装成 llm）。
- 角色数量 3-6 个并带工作流阶段（分析→方案→执行→交付→审核）；审核角色
  固定排最后，班组运行时担任 critic。
- 工具约束：提示词与解析过滤共用同一白名单 = 注册表中「只读、无副作用」
  的工具（默认只有 knowledge.search）；副作用类工具（workorder.dispatch
  等）不进蓝图，从源头保证工厂产物可低风险自批。
- 框架选角兜底：按行业描述 + 侧重 + 联网情报给 11 类行业框架打分（另含通用框架），跨框架
  轮转选角色，再补齐分析/审核阶段；不同描述得到不同的角色组合，不再是
  固定四角色。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from app.core.logging import get_logger
from app.modules.agents import factory_frameworks as ff

log = get_logger(__name__)

#: 工厂产物的默认（且兜底）工具；assess_risk 对只读工具判 low 的前提
DEFAULT_BLUEPRINT_TOOL = "knowledge.search"
#: 知识范围默认值（assess_risk：scopes 为空一律 high，故必填）
DEFAULT_KNOWLEDGE_SCOPES = ["yard"]
#: 蓝图角色数硬边界（与提示词一致）
MIN_ROLES, MAX_ROLES = ff.MIN_ROLES, ff.MAX_ROLES
#: 每个角色的建议问题条数
SUGGESTED_QUESTION_COUNT = 5

_BLUEPRINT_SCHEMA_HINT = (
    '{"agents": [{"agent_code": "小写蛇形编码", "name": "角色名", "emoji": "单个表情", '
    '"description": "一句话职责", "stage": "analysis|plan|execution|delivery|review", '
    '"persona_prompt": "系统人设（你是谁、面向什么行业、如何回答）", '
    '"task_prompt_template": "必须包含 {{question}} 占位的任务模板", '
    '"allowed_tools": ["knowledge.search"], "knowledge_scopes": ["yard"], '
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


def build_blueprint_messages(
    industry_brief: str, focus: str | None, research: str = ""
) -> list[dict[str, str]]:
    """LLM 蓝图规划的 messages（system 定规则与工具白名单，user 给行业输入）。"""
    allowed = ", ".join(tool_whitelist()) or DEFAULT_BLUEPRINT_TOOL
    focus_line = f"\n侧重方向：{focus}" if focus else ""
    research_block = (
        f"\n近期行业公开资料（仅作参考，不保证准确）：\n{research[:1500]}" if research else ""
    )
    system = (
        "你是科技小院平台的 Agent 工厂规划器。根据行业描述设计一组互补的声明式 Agent "
        "角色（配置即 Agent：persona + 任务模板 + 工具白名单 + 知识范围）。\n"
        "工作要求：\n"
        "0. 先完整理解描述：提炼经营主体、作物/品类、核心目标、约束与交付物；"
        "再据此决定需要哪些职能，不要只看关键词片段。\n"
        "1. 只输出一个 JSON 对象，不要输出解释文字、注释或多余文本；"
        f"结构：{_BLUEPRINT_SCHEMA_HINT}\n"
        f"2. 角色数量 {MIN_ROLES}-{MAX_ROLES} 个：职能复杂就多设，简单就少设，"
        "不得凑数、不得重复定位。\n"
        "3. 每个角色必须标注 stage：analysis（分析取证）/ plan（方案设计）/ "
        "execution（执行落地）/ delivery（交付汇总）/ review（审核把关）；"
        "至少覆盖分析、方案或执行、审核三类，review 角色只有一个且必须排在最后。\n"
        "4. agent_code 只允许小写字母、数字、下划线，且以字母开头。\n"
        "5. task_prompt_template 必须包含 {{question}} 占位符，运行时替换为用户问题；"
        "模板要体现该阶段该做的动作（如诊断先鉴别、执行先排期、审核先复核依据）。\n"
        f"6. allowed_tools 只能从白名单 [{allowed}] 中选择；没有把握时只给 "
        f"{DEFAULT_BLUEPRINT_TOOL}，禁止发明工具名。\n"
        '7. knowledge_scopes 固定为 ["yard"]；suggested_questions 恰好 5 条，'
        "必须是该行业一线用户真实会问的中文问题。\n"
        "8. 所有文案使用简体中文，紧扣行业描述与侧重方向；"
        "资料不足的部分要在 persona 里要求如实说明，不得编造。"
    )
    user = f"行业描述：{industry_brief}{focus_line}{research_block}"
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


_STAGE_ALIASES: dict[str, str] = {
    "analysis": ff.STAGE_ANALYSIS,
    "analyze": ff.STAGE_ANALYSIS,
    "分析": ff.STAGE_ANALYSIS,
    "诊断": ff.STAGE_ANALYSIS,
    "取证": ff.STAGE_ANALYSIS,
    "plan": ff.STAGE_PLAN,
    "planning": ff.STAGE_PLAN,
    "方案": ff.STAGE_PLAN,
    "计划": ff.STAGE_PLAN,
    "execution": ff.STAGE_EXECUTION,
    "execute": ff.STAGE_EXECUTION,
    "执行": ff.STAGE_EXECUTION,
    "落地": ff.STAGE_EXECUTION,
    "delivery": ff.STAGE_DELIVERY,
    "交付": ff.STAGE_DELIVERY,
    "汇总": ff.STAGE_DELIVERY,
    "review": ff.STAGE_REVIEW,
    "审核": ff.STAGE_REVIEW,
    "质检": ff.STAGE_REVIEW,
    "复核": ff.STAGE_REVIEW,
}


def _normalize_stage(raw: Any, index: int, total: int) -> str:
    """阶段规整：别名归一；LLM 漏填时按位置兜底（首=分析、末=审核、次=方案）。"""
    stage = _STAGE_ALIASES.get(str(raw or "").strip().lower(), "")
    if stage:
        return stage
    if index == 0:
        return ff.STAGE_ANALYSIS
    if total > 2 and index == total - 1:
        return ff.STAGE_REVIEW
    if index == 1:
        return ff.STAGE_PLAN
    return ff.STAGE_EXECUTION


def _normalize_item(
    raw: Any, index: int, known_tools: list[str], total: int = 0, max_questions: int = 8
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
        "suggested_skills": _clean_str_list(raw.get("suggested_skills"))[:5],
        "stage": _normalize_stage(raw.get("stage"), index, total or index + 1),
        "framework": str(raw.get("framework") or "").strip()[:60],
    }


def order_by_stage(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按工作流阶段排序：分析→方案→执行→交付→审核，审核恒在最后。"""
    indexed = list(enumerate(items))
    indexed.sort(
        key=lambda pair: (
            ff.STAGE_ORDER.get(str(pair[1].get("stage") or ff.STAGE_EXECUTION), 50),
            pair[0],
        )
    )
    return [item for _, item in indexed]


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
    total = len(agents_raw)
    for index, entry in enumerate(agents_raw):
        item = _normalize_item(entry, index, whitelist, total=total)
        if item is not None:
            items.append(item)
    if len(items) < MIN_ROLES:
        raise ValueError(f"有效角色仅 {len(items)} 个，少于下限 {MIN_ROLES}")
    return order_by_stage(items[:MAX_ROLES])


# ---------------------------------------------------------------------------
# 框架选角兜底（LLM 不可用/输出不可解析时的确定性产物）
# ---------------------------------------------------------------------------


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
    """为蓝图角色生成小院内唯一、命中 ``^[a-z][a-z0-9_]*$`` 的最终 agent_code。

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


def industry_label(brief: str) -> str:
    """展示用行业短语（兼容旧调用；实现见 factory_frameworks）。"""
    return ff.industry_label(brief)


def workflow_from_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    """把角色按阶段组成接力工作流；审核阶段恒在最后（班组 critic）。"""
    stages: list[dict[str, Any]] = []
    for stage in ff.STAGES:
        codes = [str(i["agent_code"]) for i in items if str(i.get("stage") or "") == stage]
        if codes:
            stages.append({"key": stage, "label": ff.STAGE_LABELS[stage], "roles": codes})
    return {
        "mode": "sequential",
        "handoff": "前序成员的输出作为后序输入；审核角色最后复核并给出修订结论。",
        "stages": stages,
    }


def build_blueprint_payload(
    industry_brief: str,
    focus: str | None,
    generated_by: str,
    items: list[dict[str, Any]],
    research: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """蓝图落库结构（factory_runs.blueprint_json 的顶层契约）。"""
    return {
        "schema": "agent_factory_blueprint.v1",
        "industry_brief": industry_brief,
        "focus": focus,
        "generated_by": generated_by,
        "role_count": len(items),
        "workflow": workflow_from_items(items),
        "research": research or {"used": False, "reason": "未启用联网情报"},
        "agents": items,
    }


def _questions(role: ff.RoleTemplate, label: str) -> list[str]:
    out: list[str] = []
    for question in role.questions:
        try:
            out.append(question.format(label=label))
        except (KeyError, IndexError):
            out.append(question.replace("{label}", label))
    return out[:SUGGESTED_QUESTION_COUNT]


def _role_item(
    framework_code: str,
    role: ff.RoleTemplate,
    profile: ff.BriefProfile,
    focus: str | None,
    research_hits: int,
) -> dict[str, Any]:
    label = profile.label
    business = f"服务对象：{profile.business}。" if profile.business else ""
    goals = f"关注目标：{'、'.join(profile.goals)}。" if profile.goals else ""
    focus_part = f"重点关注方向：{focus}。" if focus else ""
    research_part = (
        f"可参考近期行业公开资料（{research_hits} 条），但以本小院知识库与现场信息为准。"
        if research_hits
        else ""
    )
    persona = (
        f"你是面向「{label}」的{role.name}，负责{role.duty}。"
        f"{business}{goals}{focus_part}"
        "先完整理解用户的问题与上下文，再给出结论、依据和可执行建议；"
        "资料不足时明确说明，不编造。"
        f"{research_part}"
    )
    return {
        "agent_code": f"{_ascii_slug(label, max_len=20)}_{role.code}",
        "name": f"{role.name}（{label}）"[:100],
        "emoji": role.emoji,
        "description": f"{label}方向的{role.name}：{role.duty}。",
        "persona_prompt": persona,
        "task_prompt_template": role.template,
        "allowed_tools": [DEFAULT_BLUEPRINT_TOOL],
        "knowledge_scopes": list(DEFAULT_KNOWLEDGE_SCOPES),
        "suggested_questions": _questions(role, label),
        "suggested_skills": list(role.skills),
        "stage": role.stage,
        "framework": framework_code,
    }


def heuristic_blueprint(
    industry_brief: str,
    focus: str | None = None,
    research_text: str = "",
    research_hits: int = 0,
) -> list[dict[str, Any]]:
    """框架选角兜底：按描述 + 侧重 + 情报匹配行业框架，组合 3-6 个角色。

    触发条件：LLM 调用失败或输出解析失败。不同描述得到不同的角色组合与数量
    （如「棉花+农机」= 种植+设备+经营；「水产」= 水质+养殖+审核），不再固定
    四角色。产物字段与 LLM 蓝图同构，suggested_questions 每个角色 5 条。
    """
    profile, picked = ff.compose_roles(industry_brief, focus, research_text)
    return [
        _role_item(framework_code, role, profile, focus, research_hits)
        for framework_code, role in picked
    ]
