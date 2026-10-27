"""技能模板契约与校验器（v3.2「一句话生成/安装技能」底座）。

唯一字段契约见 ``contracts/skill-template.yaml``：3.2 LLM 生成与人工创建都
以它为准。本模块只做纯数据校验与渲染，绝不执行技能内容中的任何脚本：

- ``SkillSpec``：严格 pydantic 模型——未知字段拒绝（extra=forbid）、字符串
  长度上限、steps/eval_cases 等条数上限、sources.url 仅允许 http/https；
- ``validate_skill_spec``：跨字段与运行时校验——code 保留字、工具注册表对照
  （available_tools: code → 是否只读）、引用非只读工具时 risk_level 强制升为
  high 并标记 requires_review=True、eval_cases≥2、sources 为空禁止声明
  require_citation；所有失败统一抛 ``AppError("VALIDATION_ERROR", …, 422)``；
- ``dump_markdown``：渲染带 frontmatter 的 Markdown，frontmatter 可被现有
  ``skilldoc.parse_frontmatter`` 解析（纯 ``key: value`` 标量，不嵌套）。
"""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.core.errors import AppError

# --- 字段约束常量（与 contracts/skill-template.yaml 注释保持一致） ---
CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,60}$")
SEMVER_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$")
INPUT_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
TOOL_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{1,79}$")
# code 保留前缀：与平台内置/系统/管理保留命名空间隔离
RESERVED_CODE_PREFIXES = ("builtin", "system", "admin")
MAX_INPUTS = 20
MAX_STEPS = 10
MAX_ALLOWED_TOOLS = 30
MAX_KNOWLEDGE_SCOPES = 10
MAX_EXAMPLES = 10
MAX_EVAL_CASES = 20
MAX_SOURCES = 20
MAX_TAGS = 10


class _StrictModel(BaseModel):
    """严格基类：未知字段一律拒绝。"""

    model_config = ConfigDict(extra="forbid")


class SkillInput(_StrictModel):
    """技能输入参数（模板 inputs[]）。"""

    key: str = Field(min_length=1, max_length=41, pattern=INPUT_KEY_PATTERN.pattern)
    label: str = Field(min_length=1, max_length=60)
    required: bool
    type: Literal["string", "text", "number", "integer", "boolean", "enum"]


class SkillStep(_StrictModel):
    """执行步骤（模板 steps[]）：kind=prompt|tool|llm，tool 步骤必须带 tool code。"""

    kind: Literal["prompt", "tool", "llm"]
    title: str = Field(default="", max_length=80)
    tool: str = Field(default="", max_length=80)
    text: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def _check_kind_fields(self) -> SkillStep:
        if self.kind == "tool":
            if not self.tool.strip():
                raise ValueError("kind=tool 的步骤必须携带 tool code")
            if not TOOL_CODE_PATTERN.fullmatch(self.tool.strip()):
                raise ValueError(f"非法 tool code：{self.tool}")
        elif self.tool:
            raise ValueError(f"kind={self.kind} 的步骤不允许携带 tool 字段")
        if self.kind in {"prompt", "llm"} and not self.text.strip():
            raise ValueError(f"kind={self.kind} 的步骤必须包含 text 内容")
        return self


class SkillExample(_StrictModel):
    """示例问答（模板 examples[]）。"""

    question: str = Field(min_length=1, max_length=300)
    answer_outline: str = Field(min_length=1, max_length=1000)


class SkillEvalCase(_StrictModel):
    """评测用例（模板 eval_cases[]，≥2 条）。"""

    input: str = Field(min_length=1, max_length=500)
    expect: str = Field(min_length=1, max_length=1000)


class SkillGuardrails(_StrictModel):
    """护栏（模板 guardrails）：无依据来源时禁止声明 require_citation。"""

    risk_level: Literal["low", "medium", "high"] = "low"
    max_steps: int = Field(default=5, ge=1, le=MAX_STEPS)
    require_citation: bool = True


class SkillSource(_StrictModel):
    """依据来源（模板 sources[]，可空；为空时必须 require_citation=false）。"""

    title: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=500)

    @field_validator("url")
    @classmethod
    def _check_url_scheme(cls, value: str) -> str:
        stripped = value.strip()
        if any(ch.isspace() for ch in stripped) or "\\" in stripped:
            raise ValueError("sources.url 不允许包含空白或反斜杠字符")
        if urlsplit(stripped).scheme.lower() not in {"http", "https"}:
            raise ValueError(f"sources.url 仅允许 http/https：{stripped}")
        return stripped


class SkillSpec(_StrictModel):
    """技能规格（contracts/skill-template.yaml 的运行时形态）。"""

    code: str
    name: str = Field(min_length=1, max_length=80)
    version: str
    category: Literal[
        "plant_protection",
        "pest_control",
        "cultivation",
        "water_fertilizer",
        "machinery",
        "market",
        "other",
    ]
    summary: str = Field(min_length=1, max_length=300)
    when_to_use: str = Field(min_length=1, max_length=500)
    inputs: list[SkillInput] = Field(default_factory=list, max_length=MAX_INPUTS)
    steps: list[SkillStep] = Field(min_length=1, max_length=MAX_STEPS)
    allowed_tools: list[str] = Field(default_factory=list, max_length=MAX_ALLOWED_TOOLS)
    knowledge_scopes: list[Literal["yard", "crop_public"]] = Field(
        min_length=1, max_length=MAX_KNOWLEDGE_SCOPES
    )
    prompt: str = Field(min_length=1, max_length=4000)
    examples: list[SkillExample] = Field(default_factory=list, max_length=MAX_EXAMPLES)
    eval_cases: list[SkillEvalCase] = Field(min_length=2, max_length=MAX_EVAL_CASES)
    guardrails: SkillGuardrails
    output_format: Literal["markdown", "json", "text"] = "markdown"
    sources: list[SkillSource] = Field(default_factory=list, max_length=MAX_SOURCES)
    tags: list[str] = Field(default_factory=list, max_length=MAX_TAGS)
    license: str = Field(min_length=1, max_length=50)
    # 服务端计算字段：validate_skill_spec 引用非只读工具时置 true，生成方无需填写
    requires_review: bool = False

    @field_validator("code")
    @classmethod
    def _check_code(cls, value: str) -> str:
        if not CODE_PATTERN.fullmatch(value):
            raise ValueError("code 需匹配 ^[a-z][a-z0-9_]{2,60}$（小写开头，字母/数字/下划线）")
        return value

    @field_validator("version")
    @classmethod
    def _check_version(cls, value: str) -> str:
        if not SEMVER_PATTERN.fullmatch(value):
            raise ValueError("version 需为 semver（如 1.0.0、1.2.0-rc.1）")
        return value

    @field_validator("tags", "allowed_tools")
    @classmethod
    def _strip_strings(cls, value: list[str]) -> list[str]:
        return [v.strip() for v in value]

    @model_validator(mode="after")
    def _check_lists(self) -> SkillSpec:
        if len(set(self.tags)) != len(self.tags):
            raise ValueError("tags 存在重复项")
        if len(set(self.allowed_tools)) != len(self.allowed_tools):
            raise ValueError("allowed_tools 存在重复项")
        for tool in self.allowed_tools:
            if not TOOL_CODE_PATTERN.fullmatch(tool):
                raise ValueError(f"非法 tool code：{tool}")
        input_keys = [item.key for item in self.inputs]
        if len(set(input_keys)) != len(input_keys):
            raise ValueError("inputs.key 存在重复项")
        return self


def _format_validation_error(exc: ValidationError) -> str:
    """取 pydantic 首个错误拼成用户可读信息。"""
    errors = exc.errors()
    if not errors:  # pragma: no cover - pydantic 至少返回一条
        return "技能模板字段校验失败。"
    first = errors[0]
    loc = ".".join(str(p) for p in first.get("loc", ())) or "(root)"
    return f"技能模板字段校验失败：{loc}: {first.get('msg', '校验失败')}"


def validate_skill_spec(
    raw: dict,
    *,
    available_tools: dict[str, bool],
    max_steps: int = MAX_STEPS,
) -> SkillSpec:
    """校验原始 dict 并返回 SkillSpec；所有失败抛 AppError("VALIDATION_ERROR", …, 422)。

    available_tools：工具 code → 是否只读（与 tools 注册表对照，bool 值为只读与否）。
    max_steps：steps 条数上限（默认 10，可由调用方收紧；模型硬上限同为 10）。
    """
    if not isinstance(raw, dict):
        raise AppError("VALIDATION_ERROR", "技能模板必须是 JSON 对象。", http_status=422)

    # ① 步骤条数：先于 pydantic 检查，保证 max_steps 参数可收紧且报错信息明确
    steps = raw.get("steps")
    if isinstance(steps, list) and len(steps) > max_steps:
        raise AppError(
            "VALIDATION_ERROR",
            f"steps 数量 {len(steps)} 超过上限 {max_steps}。",
            http_status=422,
        )

    # ② 结构校验（未知字段/长度/条数/URL 协议/semver 等均由 SkillSpec 严格拒绝）
    try:
        spec = SkillSpec.model_validate(raw)
    except ValidationError as e:
        raise AppError("VALIDATION_ERROR", _format_validation_error(e), http_status=422) from e

    # ③ code 保留字：builtin/system/admin 前缀与平台保留命名空间冲突
    if spec.code.startswith(RESERVED_CODE_PREFIXES):
        raise AppError(
            "VALIDATION_ERROR",
            f"技能 code 禁用保留前缀（builtin/system/admin）：{spec.code}",
            http_status=422,
        )

    # ④ 工具对照：allowed_tools 与 tool 步骤引用的工具都必须在注册表内
    referenced: list[str] = list(spec.allowed_tools)
    for step in spec.steps:
        if step.kind == "tool" and step.tool and step.tool not in referenced:
            referenced.append(step.tool)
    for code in referenced:
        if code not in available_tools:
            raise AppError("VALIDATION_ERROR", f"技能引用了未注册的工具：{code}", http_status=422)
    for step in spec.steps:
        if step.kind == "tool" and step.tool and step.tool not in spec.allowed_tools:
            raise AppError(
                "VALIDATION_ERROR",
                f"步骤工具未列入 allowed_tools：{step.tool}",
                http_status=422,
            )

    # ⑤ 风险升级：引用任一非只读工具 → risk_level 强制 high + requires_review
    if any(not available_tools[code] for code in referenced):
        spec.guardrails.risk_level = "high"
        spec.requires_review = True

    # ⑥ 依据声明：sources 为空时禁止标记「有依据生成」（require_citation）
    if not spec.sources and spec.guardrails.require_citation:
        raise AppError(
            "VALIDATION_ERROR",
            "sources 为空时禁止声明 require_citation（无依据来源不得标记有依据生成）。",
            http_status=422,
        )
    return spec


def _fm_scalar(value: str) -> str:
    """frontmatter 标量净化：折叠空白（parse_frontmatter 不支持多行值）并去双引号。"""
    return " ".join(value.split()).replace('"', "'")


def dump_markdown(spec: SkillSpec) -> str:
    """渲染带 frontmatter 的技能 Markdown（唯一模板产物）。

    frontmatter 仅含 ``key: value`` 标量（skilldoc.parse_frontmatter 可解析）；
    列表与长文本放正文分节，标题刻意对齐 GitHub SKILL.md 惯例
    （Purpose/Instructions/…Tools…），便于市场导入链路复用。
    """
    guard = spec.guardrails
    fm: list[str] = [
        "---",
        f'code: "{_fm_scalar(spec.code)}"',
        f'name: "{_fm_scalar(spec.name)}"',
        f'version: "{_fm_scalar(spec.version)}"',
        f'category: "{_fm_scalar(spec.category)}"',
        f'output_format: "{_fm_scalar(spec.output_format)}"',
        f'license: "{_fm_scalar(spec.license)}"',
        f'summary: "{_fm_scalar(spec.summary)}"',
    ]
    if spec.tags:
        fm.append(f'tags: "{_fm_scalar(", ".join(spec.tags))}"')
    fm += [
        f"risk_level: {guard.risk_level}",
        f"max_steps: {guard.max_steps}",
        f"require_citation: {'true' if guard.require_citation else 'false'}",
        f"requires_review: {'true' if spec.requires_review else 'false'}",
        "---",
    ]

    body: list[str] = ["", f"# {spec.name}", ""]

    body += ["## Purpose", "", spec.summary, "", spec.when_to_use, ""]

    body += ["## Inputs", ""]
    if spec.inputs:
        for item in spec.inputs:
            required = "必填" if item.required else "可选"
            body.append(f"- `{item.key}`（{item.label}，{required}，{item.type}）")
    else:
        body.append("（无输入参数）")
    body.append("")

    body += ["## Instructions", ""]
    for idx, step in enumerate(spec.steps, start=1):
        title = step.title or ("调用工具" if step.kind == "tool" else f"{step.kind} 步骤")
        if step.kind == "tool":
            detail = f"：调用工具 `{step.tool}`" + (f"，{step.text}" if step.text else "")
            body.append(f"{idx}. {title}{detail}")
        else:
            body.append(f"{idx}. {title}：{step.text}")
    body.append("")

    body += ["## Allowed tools", ""]
    body += [f"- `{tool}`" for tool in spec.allowed_tools] or ["（不使用工具）"]
    body.append("")

    body += ["## Knowledge scopes", ""]
    body += [f"- {scope}" for scope in spec.knowledge_scopes]
    body.append("")

    body += ["## Prompt", "", spec.prompt, ""]

    body += ["## Examples", ""]
    if spec.examples:
        for example in spec.examples:
            body += [f"- 问：{example.question}", f"  答案要点：{example.answer_outline}"]
    else:
        body.append("（暂无示例）")
    body.append("")

    body += ["## Eval cases", ""]
    for idx, case in enumerate(spec.eval_cases, start=1):
        body += [f"{idx}. 输入：{case.input}", f"   期望：{case.expect}"]
    body.append("")

    body += ["## Sources", ""]
    if spec.sources:
        body += [f"- [{source.title}]({source.url})" for source in spec.sources]
    else:
        body.append("（无外部来源）")
    body.append("")

    return "\n".join(fm + body)


__all__ = [
    "SkillSpec",
    "dump_markdown",
    "validate_skill_spec",
]
