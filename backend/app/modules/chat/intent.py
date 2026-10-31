"""对话意图识别：区分"提问"与"下达指令"。

口径（保守优先，宁可漏判不可误判）：
- create 类意图必须**同时**命中"创建动词/带量词的请求式"与"能力名词"；
  "帮我""给我"单独出现不算指令——"「X」能帮我做什么""给我看看你的能力"
  都是咨询，不能据此建能力；
- 出现疑问词（吗/呢/怎么/为什么/什么/啥/哪/…）时降级为提问——"怎么做个助手"
  是咨询，不是指令，不能据此建能力；
- install 类意图命中"装载动词 + 能力名词"（走市场安装，不建草稿）；
- skill 类意图命中"技能名词（技能/技能包/skill）+ 请求动词"（一句话要技能：
  先检索匹配、无匹配再生成草稿）；**只有命中技能名词才走 skill**，
  "助手/智能体"等 agent 名词仍走 create，不抢占既有能力链路；
- doc 类意图命中"产出动词 + 文档名词"（Word/Excel/PPT/PDF/Markdown/报告/方案…），
  走文档生成；疑问词同样降级，能力名词优先级更高（"生成一个助手"仍是造能力）；
- plan 类意图命中"方案名词（方案/规划/技术路线…）"且**没有**点名文件格式或文件动作
  （v3.14：「写一份防治方案」要的是当场看得见的完整方案，不是附件里的一句话；
  「写一份方案，用 Word」这类仍按 doc 链路产出文件）；
- 未命中一律是提问 → 走既有问答链路（**零回归**）。

纯规则实现：不调模型、不写库、无外部依赖，判定结果可复现。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: 创建动词（命中其一即视为"下达指令"的可能）
CREATE_VERBS: tuple[str, ...] = (
    "生成",
    "创建",
    "新建",
    "做一个",
    "做个",
    "建一个",
    "建个",
    "搭一个",
    "搭个",
    "设计一个",
    "设计个",
    "写一个",
    "写个",
    "制作",
    "开发",
    "来一个",
    "弄一个",
    "弄个",
    "整一个",
    "搞一个",
    "搞个",
)

#: 能力名词（命中其一才算"要造的东西"）
CAPABILITY_NOUNS: tuple[str, ...] = (
    "助手",
    "智能体",
    "agent",
    "能力",
    "工具",
    "技能",
    "班组",
    "专家",
    "机器人",
)

#: 装载动词（走市场安装）
INSTALL_VERBS: tuple[str, ...] = ("安装", "装上", "装一个", "装个", "装载", "引入", "接入")

#: 请求前缀：**必须带量词**才算"要造一个东西"（"我需要一个助手/给我来一个技能"）；
#: 裸"帮我/给我"不算——否则"「X」能帮我做什么""给我看看你的能力"会被误判成建能力。
REQUEST_PREFIXES: tuple[str, ...] = ("我需要", "帮我", "给我", "要一个", "要个", "来个")
REQUEST_MEASURE = re.compile(
    r"(?:我需要|我要|给我|帮我|要|来)\s*(?:一?个|一种|一套|一些|一?款|一?项)"
)

#: 技能名词（大小写不敏感；"技能包"含"技能"但语义更明确，一并列出）
SKILL_NOUNS: tuple[str, ...] = ("技能", "技能包", "skill")

#: 插件名词（v3.3 一句话生成插件：workflow manifest → 待审核）；"工作流"口语中
#: 常指既有业务流程，误判代价高，不纳入
PLUGIN_NOUNS: tuple[str, ...] = ("插件", "plugin")

#: agent 侧能力名词：与技能名词分流，命中这些仍走既有 create 链路
AGENT_NOUNS: tuple[str, ...] = (
    "助手",
    "智能体",
    "agent",
    "能力",
    "工具",
    "班组",
    "专家",
    "机器人",
)

#: 文档名词（命中其一才算"要产出的文档"；格式词本身也算）
DOC_NOUNS: tuple[str, ...] = (
    "文档",
    "报告",
    "周报",
    "日报",
    "月报",
    "年报",
    "方案",
    "表格",
    "台账",
    "清单",
    "合同",
    "纪要",
    "汇报",
    "说明书",
    "总结",
    "报表",
    "记录表",
    "数据表",
    "简报",
    "名单",
    "花名册",
    "工作日志",
    "演示文稿",
    "幻灯片",
    "ppt",
    "excel",
    "word",
    "pdf",
    "markdown",
    "md文件",
)

#: 产出动词（与文档名词同时命中才判为文档生成；"写个周报"也算）
DOC_VERBS: tuple[str, ...] = (
    *CREATE_VERBS,
    "写一份",
    "写份",
    "写一篇",
    "写篇",
    "做一份",
    "做份",
    "制作一份",
    "起草",
    "撰写",
    "整理成",
    "汇总成",
    "导出",
    "输出",
    "形成",
    "列出",
)

#: 方案类名词：要的是一份可直接执行的完整方案，而不是把内容塞进文件（v3.14）
PLAN_NOUNS: tuple[str, ...] = (
    "方案",
    "规划",
    "技术路线",
    "实施计划",
    "工作计划",
    "防治计划",
    "栽培计划",
)

#: 显式点名文件格式或文件动作时仍走文档链路（"写一份防治方案，用 Word"）
FILE_HINTS: tuple[str, ...] = (
    "word",
    "docx",
    "excel",
    "xlsx",
    "表格",
    "ppt",
    "幻灯片",
    "演示文稿",
    "pdf",
    "markdown",
    "md文件",
    "文档",
    "文件",
    "导出",
    "下载",
    "打印",
)

#: 疑问词（命中即降级为提问）。口语里的"什么/啥/哪/咋"必须算疑问——
#: 否则"你能帮我做什么""你都能干啥"会被当成建能力指令。
QUESTION_WORDS: tuple[str, ...] = (
    "吗",
    "呢",
    "怎么",
    "如何",
    "为什么",
    "多少",
    "是否",
    "能不能",
    "可不可以",
    "请问",
    "是什么",
    "哪些",
    "有没有",
    "什么",
    "啥",
    "哪",
    "咋",
    "怎样",
    "咋样",
    "干嘛",
    "有什么",
    "会不会",
)

#: 意图类型
INTENT_QA = "qa"
INTENT_CREATE = "create"
INTENT_INSTALL = "install"
INTENT_SKILL = "skill"
INTENT_DOC = "doc"
INTENT_PLAN = "plan"
INTENT_PLUGIN = "plugin"

_CODE_SAFE = re.compile(r"[^a-z0-9_]+")


@dataclass(frozen=True)
class Intent:
    """意图判定结果。"""

    kind: str
    target: str
    reason: str

    @property
    def is_action(self) -> bool:
        return self.kind in (
            INTENT_CREATE,
            INTENT_INSTALL,
            INTENT_SKILL,
            INTENT_DOC,
            INTENT_PLAN,
            INTENT_PLUGIN,
        )


def _has(text: str, words: tuple[str, ...]) -> bool:
    return any(w in text for w in words)


def detect(text: str) -> Intent:
    """判定一条用户输入的意图。"""
    raw = (text or "").strip()
    if not raw:
        return Intent(INTENT_QA, "agent", "空输入，按提问处理")
    lowered = raw.lower()
    # 能力/技能/插件名词优先：命中才进入动作判定（含"生成一个助手文档"这类混合句）
    if _has(lowered, CAPABILITY_NOUNS) or _has(lowered, SKILL_NOUNS) or _has(lowered, PLUGIN_NOUNS):
        if _has(raw, QUESTION_WORDS):
            return Intent(INTENT_QA, "agent", "含疑问词，按提问处理")
        if _has(raw, INSTALL_VERBS):
            return Intent(INTENT_INSTALL, "market", "命中装载动词，走市场安装")
        requested = _has(raw, CREATE_VERBS) or REQUEST_MEASURE.search(raw) is not None
        # agent 名词（助手/智能体/班组…）优先，保持既有能力链路不被技能抢占
        if _has(lowered, AGENT_NOUNS) and requested:
            target = "team" if "班组" in lowered else "agent"
            return Intent(INTENT_CREATE, target, "命中创建动词与能力名词，按生成能力处理")
        # 只有命中技能名词（技能/技能包/skill）才走技能检索/生成
        if _has(lowered, SKILL_NOUNS) and requested:
            return Intent(INTENT_SKILL, "skill", "命中技能名词与请求动词，按技能检索/生成处理")
        # 只有命中插件名词（插件/plugin）才走插件生成（v3.3：一律待审）
        if _has(lowered, PLUGIN_NOUNS) and requested:
            return Intent(INTENT_PLUGIN, "plugin", "命中插件名词与请求动词，按插件生成处理")
        if _has(lowered, CAPABILITY_NOUNS) and requested:
            target = "team" if "班组" in lowered else "agent"
            return Intent(INTENT_CREATE, target, "命中创建动词与能力名词，按生成能力处理")
        return Intent(INTENT_QA, "agent", "仅命中能力名词，按提问处理")
    # 方案链路：要一份可执行的完整方案（显式点名文件格式/文件动作时仍按文档产出）
    if (
        _has(lowered, PLAN_NOUNS)
        and not _has(lowered, FILE_HINTS)
        and not _has(raw, QUESTION_WORDS)
    ):
        return Intent(INTENT_PLAN, "plan", "命中方案名词，按多源合成完整方案处理")
    # 文档链路：产出动词 + 文档名词；疑问句仍按提问（"这份报告怎么改"）
    if _has(lowered, DOC_NOUNS) and _has(raw, DOC_VERBS) and not _has(raw, QUESTION_WORDS):
        return Intent(INTENT_DOC, "document", "命中产出动词与文档名词，按生成文档处理")
    return Intent(INTENT_QA, "agent", "未命中动作意图，按提问处理")


__all__ = [
    "AGENT_NOUNS",
    "CAPABILITY_NOUNS",
    "CREATE_VERBS",
    "DOC_NOUNS",
    "DOC_VERBS",
    "FILE_HINTS",
    "INSTALL_VERBS",
    "INTENT_CREATE",
    "INTENT_DOC",
    "INTENT_INSTALL",
    "INTENT_PLAN",
    "INTENT_PLUGIN",
    "INTENT_QA",
    "INTENT_SKILL",
    "Intent",
    "PLAN_NOUNS",
    "PLUGIN_NOUNS",
    "QUESTION_WORDS",
    "REQUEST_PREFIXES",
    "SKILL_NOUNS",
    "detect",
]
