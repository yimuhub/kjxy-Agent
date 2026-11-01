"""目标可实现性判定：区分「系统能做」与「现实办不到」。

指挥官只在**现实世界不可执行**时才允许降级。判定分三档：

- ``doable``：系统具备对应能力（文档/表格/PPT/图片/视频/检索/分析…），
  必须拆任务去做，不允许以「能力不足」为由降级；
- ``partial``：能完成前置工作（查资料、比价、出清单、写话术），但最后一步
  必须由人完成（付款、签字、到场）。结果里要说清「已经做到哪、你还要做什么」；
- ``blocked``：需要身体动作或实物交付（吃掉、穿上、搬到某地、当面交付），
  系统无论怎么补能力都做不到——这是唯一合法的降级场景。

判定顺序很重要：先看有没有可交付能力，再看物理动作。否则「做一份给果树
施肥的 PPT」里的「施肥」会被误判成线下动作。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.modules.commander import deliverables

STATUS_DOABLE = "doable"
STATUS_PARTIAL = "partial"
STATUS_BLOCKED = "blocked"

#: 降级原因：现实不可执行（唯一合法的 fallback 理由）
REASON_UNREALIZABLE = "unrealizable"

#: 需要身体动作 / 到场 / 实物交付：系统无法替代人。
#: 只在出现祈使标记（「帮我/替我/你去/去把…」）时才判定，避免把
#: 「吃了这个药要注意什么」这类咨询误判成线下任务。
_PHYSICAL_RE = re.compile(
    r"(?:帮我|替我|你给我|你去|你到|麻烦你|去把|给我)"
    r"[^，。；！？\n]{0,16}?"
    r"(吃掉|喝了|喝掉|吃了|尝一?尝|穿上|戴上|搬(?:过去|过来|到)|扛(?:过去|过来)|"
    r"拿给我|送到我手上|当面(?:交给|谈|签)|签(?:个)?字|盖章|"
    r"浇[^，。；！？\n]{0,4}水|施[^，。；！？\n]{0,4}肥|打[^，。；！？\n]{0,4}药|"
    r"采[^，。；！？\n]{0,4}摘|挖[^，。；！？\n]{0,4}坑)"
    r"|(?:帮我|替我|你去|你到|亲自)[^，。；！？\n]{0,8}?(?:去)?(?:现场|地里|田里|果园|大棚)"
)

#: 外部交易：能调研、能准备、能给出下单方案，但资金动作必须由人确认
_EXTERNAL_TRANSACTIONS: tuple[str, ...] = (
    "买",
    "采购",
    "下单",
    "订购",
    "付款",
    "付钱",
    "转账",
    "汇款",
    "预订",
    "叫外卖",
    "配送",
    "快递给我",
)


@dataclass(frozen=True)
class Capability:
    """一项系统真实具备的能力。"""

    code: str
    label: str
    #: 关联的交付格式（与 deliverables.FORMAT_LABELS 对齐；纯检索类为空）
    formats: tuple[str, ...] = ()
    #: 关联的市场技能编码（用于给责任人 Agent 绑定技能）
    skill_codes: tuple[str, ...] = ()
    #: 识别词
    patterns: tuple[str, ...] = ()
    #: 交付工具码（doc.generate 等；纯检索类为空）
    tool: str = ""


#: 能力目录。新增能力只需在此登记，规划与选兵会自动跟随。
CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        code="document",
        label="文档写作与排版",
        formats=("docx", "pdf", "md"),
        skill_codes=("office_word_report", "office_doc_review"),
        patterns=("word", "docx", "文档", "报告", "方案", "申报", "公文", "合同", "纪要"),
        tool=deliverables.DELIVERABLE_TOOL,
    ),
    Capability(
        code="spreadsheet",
        label="表格与数据整理",
        formats=("xlsx",),
        skill_codes=("office_excel_table", "office_data_clean"),
        patterns=("excel", "xlsx", "表格", "台账", "统计表", "数据整理", "对账"),
        tool=deliverables.DELIVERABLE_TOOL,
    ),
    Capability(
        code="presentation",
        label="PPT 制作",
        formats=("pptx",),
        skill_codes=(
            "office_ppt_deck",
            "office_ppt_storyline",
            "office_ppt_design",
            "office_ppt_chart",
            "office_ppt_review",
            "office_ppt_template",
        ),
        patterns=("ppt", "pptx", "幻灯片", "演示文稿", "汇报材料", "宣讲", "路演"),
        tool=deliverables.DELIVERABLE_TOOL,
    ),
    Capability(
        code="image",
        label="图片生成与处理",
        skill_codes=("office_ppt_design",),
        patterns=("配图", "海报", "生成图", "画一张", "图片处理", "修图", "去水印"),
    ),
    Capability(
        code="video",
        label="短视频生成",
        patterns=("视频", "短片", "剪辑", "宣传片"),
    ),
    Capability(
        code="web",
        label="联网检索",
        patterns=("搜索", "搜一下", "查一下", "最新", "行情", "价格", "新闻", "政策", "附近"),
    ),
    Capability(
        code="kb",
        label="知识库检索",
        patterns=("知识库", "院里", "本站", "我们小院", "已有资料", "历史资料"),
    ),
    Capability(
        code="analysis",
        label="数据分析与结论",
        patterns=("分析", "对比", "趋势", "测算", "评估", "复盘", "原因"),
    ),
)


def detect_capabilities(text: str) -> list[str]:
    """识别目标需要哪些能力；按目录顺序去重。"""
    lowered = (text or "").lower()
    fmt = deliverables.detect(text)
    hits: list[str] = []
    for cap in CAPABILITIES:
        if fmt and fmt in cap.formats:
            hits.append(cap.code)
            continue
        if any(word.lower() in lowered for word in cap.patterns):
            hits.append(cap.code)
    return hits


def capability(code: str) -> Capability | None:
    for cap in CAPABILITIES:
        if cap.code == code:
            return cap
    return None


def skill_codes_for(codes: list[str]) -> list[str]:
    """把能力编码展开成要绑定的技能编码（去重保序）。"""
    out: list[str] = []
    for code in codes:
        cap = capability(code)
        if cap is None:
            continue
        for skill in cap.skill_codes:
            if skill not in out:
                out.append(skill)
    return out


def _first_hit(text: str, words: tuple[str, ...]) -> str | None:
    for word in words:
        if word in text:
            return word
    return None


@dataclass
class Assessment:
    """可实现性判定结果。"""

    status: str
    capabilities: list[str] = field(default_factory=list)
    #: 命中的物理动作 / 交易动作（用于生成说明）
    verb: str = ""
    #: 面向用户的处理建议（blocked / partial 时非空）
    guidance: str = ""

    @property
    def blocked(self) -> bool:
        return self.status == STATUS_BLOCKED

    @property
    def doable(self) -> bool:
        return self.status == STATUS_DOABLE

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "capabilities": list(self.capabilities),
            "verb": self.verb,
            "guidance": self.guidance,
        }


def _physical_hit(text: str) -> str:
    match = _PHYSICAL_RE.search(text)
    if match is None:
        return ""
    return next((group for group in match.groups() if group), match.group(0))


def assess(text: str, *, fmt: str | None = None) -> Assessment:
    """判定目标能否由系统完成。

    ``fmt`` 由调用方传入时（用户显式要文件）直接视为可达，避免被正文里的
    线下动词带偏。
    """
    content = (text or "").strip()
    caps = detect_capabilities(content)
    if fmt:
        # 显式要文件时补上对应能力，避免「做一份施肥 PPT」被后面的线下动词带偏
        required = "presentation" if fmt == "pptx" else "document"
        if fmt == "xlsx":
            required = "spreadsheet"
        if required not in caps:
            caps.insert(0, required)

    # 有可交付能力 → 一定是能做（哪怕正文里提到施肥、买、送）
    if fmt or caps:
        return Assessment(status=STATUS_DOABLE, capabilities=caps)

    physical = _physical_hit(content)
    if physical:
        return Assessment(
            status=STATUS_BLOCKED,
            verb=physical,
            guidance=(
                f"「{physical}」需要人在现实里完成，我无法代替你的身体动作。"
                "我可以把该做的事整理成一份可执行的清单/步骤，你照着做即可。"
            ),
        )

    trade = _first_hit(content, _EXTERNAL_TRANSACTIONS)
    if trade:
        return Assessment(
            status=STATUS_PARTIAL,
            capabilities=["web"],
            verb=trade,
            guidance=(
                f"「{trade}」涉及资金动作，必须由你确认后支付。"
                "我可以先查清渠道、价格与联系方式，整理成可直接照着操作的清单。"
            ),
        )

    # 纯问答/咨询：默认可达（走检索或模型直答）
    return Assessment(status=STATUS_DOABLE, capabilities=caps)


def plan_hint(assessment: Assessment) -> str:
    """给规划器的一句话约束：把能力边界说清楚，避免它自作主张降级。"""
    if assessment.blocked:
        return "该目标需要现实中的身体动作，系统无法完成；只输出说明与替代方案，不要拆执行任务。"
    if assessment.status == STATUS_PARTIAL:
        return (
            "该目标最后一步需用户亲自完成；请拆成「调研/整理 + 交付清单」的任务，"
            "并在指令中要求写清用户还需做什么。"
        )
    caps = "、".join(
        cap.label for code in assessment.capabilities if (cap := capability(code)) is not None
    )
    return f"该目标在系统能力范围内（{caps or '通用问答'}）；必须拆出可执行任务，不得以能力不足为由放弃。"


__all__ = [
    "CAPABILITIES",
    "REASON_UNREALIZABLE",
    "STATUS_BLOCKED",
    "STATUS_DOABLE",
    "STATUS_PARTIAL",
    "Assessment",
    "Capability",
    "assess",
    "capability",
    "detect_capabilities",
    "plan_hint",
    "skill_codes_for",
]
