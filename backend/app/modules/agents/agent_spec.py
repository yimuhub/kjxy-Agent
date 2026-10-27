"""一句话生成智能体的固定框架（AgentSpec）与校验（v3.5）。

框架 = 强制六段：使命 / 职责边界 / 服务对象 / 工作流 / 护栏 / 产出格式。
模型只允许按这一份 JSON 回答，其余一律服务端复算：

- 工具必须在工具注册表内（未知工具 422，避免生成「看起来能跑」的草稿）；
- 风险等级由 ``declarative.assess_risk`` 重算，绝不采信模型自评；
- 编码统一 ``gen_`` 前缀（与内置/工厂/手工创建的编码空间隔开）；
- ``sources`` 为空时前端会提示「未联网取证」，但生成不阻断（离线可用）。

校验失败只抛一条 ``VALIDATION_ERROR``（带可读原因），由生成器回灌给模型纠错。
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.core.errors import AppError

QUESTION_PLACEHOLDER = "{{question}}"
AGENT_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,99}$")
#: 生成编码前缀：单独命名空间，便于区分来源与批量清理
GENERATED_CODE_PREFIX = "gen_"
VALID_SCOPES = ("yard", "crop_public")
DEFAULT_EMOJI = "🤖"

MIN_IN_SCOPE = 3
MAX_IN_SCOPE = 6
MIN_OUT_SCOPE = 2
MAX_OUT_SCOPE = 4
MIN_WORKFLOW = 3
MAX_WORKFLOW = 6
MIN_GUARDRAILS = 2
MAX_GUARDRAILS = 5
MAX_SKILL_NEEDS = 8
MAX_NEED_KEYWORDS = 6


class AgentWorkflowStep(BaseModel):
    """工作流一步：动作 + 可选工具（工具必须来自注册表白名单）。"""

    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=2, max_length=200)
    tool: str | None = Field(default=None, max_length=100)

    @field_validator("action")
    @classmethod
    def _v_action(cls, v: str) -> str:
        text = str(v or "").strip()
        if not text:
            raise ValueError("workflow.action 不能为空")
        return text

    @field_validator("tool")
    @classmethod
    def _v_tool(cls, v: str | None) -> str | None:
        text = str(v or "").strip()
        return text or None


class AgentSkillNeed(BaseModel):
    """模型声明的能力需求：交给服务端做本地技能匹配（模型不写 code，防编造）。"""

    model_config = ConfigDict(extra="forbid")

    need: str = Field(min_length=2, max_length=100)
    keywords: list[str] = Field(default_factory=list)
    reason: str = Field(default="", max_length=300)

    @field_validator("keywords")
    @classmethod
    def _v_keywords(cls, v: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in v or []:
            word = str(raw or "").strip()
            if len(word) >= 2 and word not in cleaned:
                cleaned.append(word)
        if not cleaned:
            raise ValueError("skill_needs.keywords 至少 1 个不少于 2 字的检索词")
        if len(cleaned) > MAX_NEED_KEYWORDS:
            raise ValueError(f"skill_needs.keywords 最多 {MAX_NEED_KEYWORDS} 个")
        return cleaned


class AgentSpec(BaseModel):
    """生成规格：固定框架，未知字段一律拒绝（extra=forbid）。"""

    model_config = ConfigDict(extra="forbid")

    agent_code: str = Field(min_length=4, max_length=100)
    name: str = Field(min_length=2, max_length=30)
    description: str = Field(min_length=4, max_length=200)
    emoji: str = Field(default=DEFAULT_EMOJI, max_length=8)
    mission: str = Field(min_length=4, max_length=120)
    audience: str = Field(min_length=2, max_length=100)
    in_scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    workflow: list[AgentWorkflowStep] = Field(default_factory=list)
    guardrails: list[str] = Field(default_factory=list)
    output_format: str = Field(min_length=2, max_length=40)
    persona_prompt: str = Field(min_length=20, max_length=4000)
    task_prompt_template: str = Field(min_length=1, max_length=4000)
    allowed_tools: list[str] = Field(default_factory=list)
    knowledge_scopes: list[str] = Field(default_factory=lambda: ["yard"])
    skill_needs: list[AgentSkillNeed] = Field(default_factory=list)
    sources: list[dict[str, str]] = Field(default_factory=list)

    @field_validator("agent_code")
    @classmethod
    def _v_code(cls, v: str) -> str:
        code = str(v or "").strip().lower()
        if not AGENT_CODE_PATTERN.match(code):
            raise ValueError("agent_code 必须匹配 ^[a-z][a-z0-9_]{2,99}$")
        if not code.startswith(GENERATED_CODE_PREFIX):
            raise ValueError(f"agent_code 必须以 {GENERATED_CODE_PREFIX} 开头")
        return code

    @field_validator("name", "mission", "audience", "description", "output_format")
    @classmethod
    def _v_text(cls, v: str) -> str:
        text = str(v or "").strip()
        if not text:
            raise ValueError("字段不能为空白")
        return text

    @field_validator("in_scope")
    @classmethod
    def _v_in_scope(cls, v: list[str]) -> list[str]:
        return _bounded_list(v, "in_scope", MIN_IN_SCOPE, MAX_IN_SCOPE)

    @field_validator("out_of_scope")
    @classmethod
    def _v_out_scope(cls, v: list[str]) -> list[str]:
        return _bounded_list(v, "out_of_scope", MIN_OUT_SCOPE, MAX_OUT_SCOPE)

    @field_validator("guardrails")
    @classmethod
    def _v_guardrails(cls, v: list[str]) -> list[str]:
        return _bounded_list(v, "guardrails", MIN_GUARDRAILS, MAX_GUARDRAILS)

    @field_validator("workflow")
    @classmethod
    def _v_workflow(cls, v: list[AgentWorkflowStep]) -> list[AgentWorkflowStep]:
        if not MIN_WORKFLOW <= len(v) <= MAX_WORKFLOW:
            raise ValueError(f"workflow 必须 {MIN_WORKFLOW}-{MAX_WORKFLOW} 步")
        return v

    @field_validator("skill_needs")
    @classmethod
    def _v_needs(cls, v: list[AgentSkillNeed]) -> list[AgentSkillNeed]:
        if len(v) > MAX_SKILL_NEEDS:
            raise ValueError(f"skill_needs 最多 {MAX_SKILL_NEEDS} 项")
        return v

    @field_validator("allowed_tools")
    @classmethod
    def _v_tools(cls, v: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in v or []:
            code = str(raw or "").strip()
            if not code:
                continue
            if "*" in code:
                raise ValueError("allowed_tools 不允许通配符 *")
            if code not in cleaned:
                cleaned.append(code)
        return cleaned

    @field_validator("knowledge_scopes")
    @classmethod
    def _v_scopes(cls, v: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in v or []:
            scope = str(raw or "").strip()
            if scope not in VALID_SCOPES:
                raise ValueError(f"knowledge_scopes 只能是 {VALID_SCOPES}")
            if scope not in cleaned:
                cleaned.append(scope)
        if not cleaned:
            raise ValueError("knowledge_scopes 不能为空（为空会被判为高风险）")
        return cleaned

    @field_validator("task_prompt_template")
    @classmethod
    def _v_template(cls, v: str) -> str:
        if QUESTION_PLACEHOLDER not in v:
            raise ValueError(f"task_prompt_template 必须包含 {QUESTION_PLACEHOLDER}")
        return v

    @field_validator("sources")
    @classmethod
    def _v_sources(cls, v: list[dict[str, str]]) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for raw in v or []:
            if not isinstance(raw, dict):
                continue
            url = str(raw.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                continue
            rows.append({"title": str(raw.get("title") or url)[:200], "url": url[:500]})
        return rows[:8]


def _bounded_list(value: list[str], field: str, minimum: int, maximum: int) -> list[str]:
    cleaned: list[str] = []
    for raw in value or []:
        text = str(raw or "").strip()
        if text and text not in cleaned:
            cleaned.append(text)
    if not minimum <= len(cleaned) <= maximum:
        raise ValueError(f"{field} 必须 {minimum}-{maximum} 条")
    return cleaned


def _pydantic_message(exc: ValidationError) -> str:
    errors = exc.errors()
    first: dict[str, Any] = dict(errors[0]) if errors else {}
    loc = ".".join(str(part) for part in first.get("loc") or ())
    reason = str(first.get("msg") or "格式不合规").removeprefix("Value error, ")
    return f"{loc or 'spec'}：{reason}"


def validate_agent_spec(payload: Any, *, available_tools: dict[str, bool]) -> AgentSpec:
    """校验模型产出的规格；未知工具 → 422，其余结构问题 → 422（带可读原因）。"""
    try:
        spec = AgentSpec.model_validate(payload)
    except ValidationError as e:
        raise AppError(
            "VALIDATION_ERROR",
            f"智能体规格不合规：{_pydantic_message(e)}",
            http_status=422,
            details={"reason": _pydantic_message(e)},
        ) from e

    unknown = [code for code in spec.allowed_tools if code not in available_tools]
    if unknown:
        raise AppError(
            "VALIDATION_ERROR",
            f"allowed_tools 包含未注册的工具：{unknown[0]}",
            http_status=422,
            details={"unknown_tools": unknown},
        )
    unknown_step_tools = [
        step.tool for step in spec.workflow if step.tool and step.tool not in available_tools
    ]
    if unknown_step_tools:
        raise AppError(
            "VALIDATION_ERROR",
            f"workflow 引用了未注册的工具：{unknown_step_tools[0]}",
            http_status=422,
            details={"unknown_tools": unknown_step_tools},
        )
    return spec


# ---------------------------------------------------------------------------
# 提示词与兜底
# ---------------------------------------------------------------------------


def build_spec_prompt(
    utterance: str,
    available_tools: dict[str, bool],
    sources: list[dict[str, str]],
    catalog: str,
) -> tuple[str, str]:
    """(system, user)：框架硬约束 + 可用工具 + 已有技能目录（只作参考，不许写 code）。"""
    tool_hint = (
        "、".join(sorted(available_tools)) or "（当前没有可用工具，allowed_tools 必须留空数组）"
    )
    source_block = (
        "\n".join(f"- {s['title']} {s['url']}" for s in sources)
        if sources
        else "（没有可用参考来源：不得编造链接，sources 必须为空数组）"
    )
    catalog_block = catalog or "（本小院暂无可用扩展技能/插件）"
    system = (
        "你是科技小院的智能体设计师。把用户的一句话需求设计成一个可直接运行的智能体配置，"
        "只输出一个 JSON 对象，不要输出 Markdown 围栏或任何解释文字。\n"
        "固定框架（六段缺一不可，出现未知字段会被服务端拒绝）：\n"
        '{"agent_code":"gen_ 开头的英文小写标识，^[a-z][a-z0-9_]{2,99}$",'
        '"name":"中文名 2-30 字","description":"一句话职责 4-200 字","emoji":"一个 emoji",'
        '"mission":"使命：为谁解决什么问题，1 句",'
        '"audience":"服务对象的身份与场景",'
        '"in_scope":["要做的 3-6 条，必须具体到动作"],'
        '"out_of_scope":["明确不做的 2-4 条，防止越界"],'
        '"workflow":[{"action":"3-6 步，每步一个动作","tool":"工具 code 或 null"}],'
        '"guardrails":["2-5 条硬约束，如：不确定就追问、给结论必须给依据"],'
        '"output_format":"回答的结构（如：结论/依据/下一步）",'
        '"persona_prompt":"系统提示词：角色、专业口径、语气、边界，200-1500 字",'
        '"task_prompt_template":"任务模板，必须包含 {{question}} 占位",'
        '"allowed_tools":[],"knowledge_scopes":["yard"],'
        '"skill_needs":[{"need":"需要的能力名","keywords":["2-6 个检索词"],'
        '"reason":"为什么需要"}],'
        '"sources":[{"title":"资料标题","url":"https://..."}]}\n'
        "硬约束：\n"
        "1) 只能输出上述字段，agent_code 必须以 gen_ 开头；\n"
        f"2) allowed_tools 与 workflow.tool 只能取自：{tool_hint}；\n"
        '3) knowledge_scopes 只能取 "yard" 或 "crop_public"，至少一个；\n'
        "4) skill_needs 描述「需要什么能力」，不要编造技能 code；最多 8 项，"
        "平台会按 keywords 自动匹配已有技能；\n"
        "5) sources 只写上面给出的公开资料，不得编造链接；没有资料时给空数组；\n"
        "6) 不要输出风险等级——风险由服务端按工具与知识范围复算。"
    )
    user = (
        f"用户需求：{(utterance or '').strip()[:1000]}\n"
        f"可参考的公开资料：\n{source_block}\n"
        f"本小院可绑定的技能与插件（仅供理解能力边界，不要输出这些 code）：\n{catalog_block}"
    )
    return system, user


def _fallback_payload(utterance: str) -> dict[str, Any]:
    """确定性兜底：模型连续不合规时也能落一个保守可编辑的草稿。"""
    text = (utterance or "").strip()[:120] or "通用业务助手"
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    name = text[:20] or "未命名智能体"
    return {
        "agent_code": f"{GENERATED_CODE_PREFIX}{digest}",
        "name": name,
        "description": f"按一句话需求生成的草稿：{text[:60]}",
        "emoji": DEFAULT_EMOJI,
        "mission": f"围绕「{text[:40]}」为一线人员提供可执行的分析与建议。",
        "audience": "本小院业务人员",
        "in_scope": [
            "澄清用户的实际问题与已知条件",
            "结合本小院知识库给出可执行的下一步建议",
            "说明结论依据与不确定性",
        ],
        "out_of_scope": [
            "不替代专家做最终诊断与决策",
            "不在缺少依据时给出确定性结论",
        ],
        "workflow": [
            {"action": "整理用户问题中的关键条件", "tool": None},
            {"action": "检索本小院知识库的相关依据", "tool": "knowledge.search"},
            {"action": "按结论/依据/下一步组织回答", "tool": None},
        ],
        "guardrails": [
            "依据不足时明确说明并追问关键条件",
            "涉及用药、法规与金额的结论必须给出核实路径",
        ],
        "output_format": "结论 / 依据 / 下一步（markdown 列表）",
        "persona_prompt": (
            "你是科技小院的业务助手。先确认问题与条件，再依据本小院知识库作答；"
            "不确定就说明不确定并追问，不编造数据与政策条文。"
        ),
        "task_prompt_template": (
            "用户问题：{{question}}\n请先检索知识库，再按「结论 / 依据 / 下一步」作答。"
        ),
        "allowed_tools": [],
        "knowledge_scopes": ["yard"],
        "skill_needs": [],
        "sources": [],
    }


def fallback_spec(utterance: str, *, available_tools: dict[str, bool]) -> AgentSpec:
    """兜底规格（恒通过校验）：只说保守的话，提示人工复核。"""
    payload = _fallback_payload(utterance)
    if "knowledge.search" not in available_tools:
        payload["allowed_tools"] = []
        payload["workflow"] = [
            {"action": "整理用户问题中的关键条件", "tool": None},
            {"action": "说明需要补充的信息", "tool": None},
            {"action": "按结论/依据/下一步组织回答", "tool": None},
        ]
    return validate_agent_spec(payload, available_tools=available_tools)


def blueprint_payload(
    spec: AgentSpec,
    *,
    sources: list[dict[str, str]],
    offline: bool,
    model: str,
    note: str,
) -> dict[str, Any]:
    """落到 ``YardAgent.blueprint_json``：框架六段 + 生成元信息（编辑页只读展示）。"""
    return {
        "framework": "agent-framework/v3.5",
        "mission": spec.mission,
        "audience": spec.audience,
        "in_scope": list(spec.in_scope),
        "out_of_scope": list(spec.out_of_scope),
        "workflow": [step.model_dump() for step in spec.workflow],
        "guardrails": list(spec.guardrails),
        "output_format": spec.output_format,
        "skill_needs": [need.model_dump() for need in spec.skill_needs],
        "sources": list(sources),
        "offline": offline,
        "model": model,
        "note": note,
    }
