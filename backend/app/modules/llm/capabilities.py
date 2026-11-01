"""能力清单（v3.25）：模型调用用途码 → 中文名与说明。

用途码是**代码写死的事实**：每个 ``LLMMetadata(purpose=...)`` 写入点必须在此登记，
否则「模型中心」的能力绑定页签会漏掉一条真实链路。校验端点（``/llm/traces?purpose=``）
同样以本清单为准，杜绝「清单里有、库里从不写」或「库里在写、清单查不到」的错位。
"""

from dataclasses import dataclass

from app.modules.llm.binding_models import SCOPE_AGENT, SCOPE_GLOBAL, SCOPE_PURPOSE


@dataclass(frozen=True)
class Capability:
    code: str
    label: str
    group: str
    hint: str


#: 未标注用途的调用统一归集到这个码（llm_traces/llm_calls 的落库默认值）
PLATFORM_PURPOSE = "platform"

CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        "declarative",
        "专业助手对话",
        "对话",
        "内置/自建助手在问答与任务运行时的主模型调用",
    ),
    Capability("chat_plan", "对话内方案生成", "对话", "「给我一套方案」类请求的多源合成正文"),
    Capability("chat_plan_continue", "方案续写", "对话", "长方案分段输出的后续段落"),
    Capability("chat_plan_repair", "方案补章", "对话", "方案缺章缺节时的定点补齐"),
    Capability("chat_document", "对话内文档生成", "文档", "会话中直接产出 md/docx 文档正文"),
    Capability("chat_capability", "能力卡片判定", "对话", "判断这条提问该走哪个能力/助手"),
    Capability("group_gate", "群聊应答自判", "对话", "多助手群聊里判断本轮由谁接话"),
    Capability(
        "commander_plan",
        "指挥官任务规划",
        "智能体编排",
        "把用户目标拆成带依赖的子任务 DAG（含 JSON 修复调用）",
    ),
    Capability(
        "commander_merge",
        "指挥官结果汇总",
        "智能体编排",
        "子任务产出合并为最终答复；无合适助手时也用于指挥官代执行",
    ),
    Capability(
        "commander_match",
        "指挥官智能选兵",
        "智能体编排",
        "对规则打分的前 8 个候选做复核重排（agent_code + confidence + reason）",
    ),
    Capability("supervisor", "班组总控", "班组", "协作班组的调度与收口判断"),
    Capability("team_synthesis", "班组检索融合", "班组", "班组多路检索结果的综述合成"),
    Capability("quick_action", "一句话业务解析", "业务", "台账/诊断/工单的一句话字段抽取"),
    Capability("factory", "Agent 工厂蓝图", "构建", "工厂按蓝图生成智能体的推理步"),
    Capability("agent_generation", "一句话生成助手", "构建", "自然语言描述→助手规格（AgentSpec）"),
    Capability("skill_generation", "一句话生成技能", "技能与插件", "自然语言描述→Skill 定义草稿"),
    Capability(
        "plugin_generation", "一句话生成插件", "技能与插件", "自然语言描述→Plugin 工作流草稿"
    ),
    Capability("plugin", "插件工作流推理", "技能与插件", "插件运行时 llm 步骤"),
    Capability("skill_suggest", "技能改进建议", "技能与插件", "依据失败/反馈给出的技能修订建议"),
    Capability("skill_upgrade", "技能自动升级", "技能与插件", "自升级任务产出的待审核草稿"),
    Capability(
        "tool_generation", "一句话生成工具", "技能与插件", "自然语言描述→可用工具定义草稿（v3.25）"
    ),
    Capability("tool_run", "自定义工具执行", "业务", "自定义 prompt 工具运行时的模型调用"),
    Capability("self_improve", "知识自生长", "知识", "定时任务从会话中提炼知识候选"),
    Capability("embedding", "知识向量化", "知识", "知识库切片入库的向量计算"),
    Capability("device_analysis", "设备数据解读", "设备", "设备分析结论的自然语言解释"),
    Capability(
        "image_generation",
        "图像生成",
        "视觉",
        "文生图（海报/配图）走的图像供应商；与对话模型分开绑定（v3.26）",
    ),
    Capability(
        "video_generation",
        "视频生成",
        "视觉",
        "视频生成通道预留：登记协议位与绑定位，暂无内置调用工具（v3.26）",
    ),
    Capability(
        "asr",
        "语音转写",
        "语音",
        "对话语音输入的在线转写通道（POST {base_url}/audio/transcriptions）；"
        "与对话模型分开绑定，本机离线模型不占用此能力（v3.30）",
    ),
)

CAPABILITY_CODES: tuple[str, ...] = tuple(item.code for item in CAPABILITIES)

#: 查询与校验接受的全部用途码（含未标注兜底）
VALID_PURPOSES: tuple[str, ...] = (*CAPABILITY_CODES, PLATFORM_PURPOSE)

_CAPABILITY_INDEX: dict[str, Capability] = {item.code: item for item in CAPABILITIES}

SCOPE_TYPE_LABELS: dict[str, str] = {
    SCOPE_GLOBAL: "全局默认",
    SCOPE_PURPOSE: "按能力",
    SCOPE_AGENT: "按智能体",
}


def capability_label(code: str) -> str:
    item = _CAPABILITY_INDEX.get(code)
    return item.label if item else code


def is_known_purpose(code: str) -> bool:
    return code in VALID_PURPOSES


def capability_payload() -> list[dict[str, str]]:
    """前端「能力绑定」页签的清单：码、中文名、分组与用途说明。"""
    return [
        {"code": c.code, "label": c.label, "group": c.group, "hint": c.hint} for c in CAPABILITIES
    ]
