"""内置办公技能种子：Word / Excel / PPT（含 5 项 PPT 专项）/ 周报 / 数据整理 / 审校。

与农技、增长技能同一套结构（``MarketItem`` 字段 + ``SkillSpec`` 形态的
``definition_json``），由 ``market/builtin_seed.py`` 统一落库与铺给新小院。

落点：
- 技能库/市场自动可见，助手编辑弹窗可勾选；
- 对话绑定助手时，办公技能 SOP 会随文档生成提示词一起进入模型；
- Agent 工厂与一句话生成在识别到办公需求时自动绑定对应技能。

约定：本批为平台原创方法论文档，不写外部 sources，因此
``guardrails.require_citation=False``。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.modules.market.builtin_seed import ensure_skills, seed_items

AUTHOR = "科技小院平台"
LICENSE = "内部原创"
SOURCE_LABEL = "office_skills"
GROUP_OFFICE = "office_documents"

OFFICE_SKILL_CODES: tuple[str, ...] = (
    "office_word_report",
    "office_excel_table",
    "office_ppt_deck",
    "office_weekly_report",
    "office_data_clean",
    "office_doc_review",
    "office_ppt_storyline",
    "office_ppt_design",
    "office_ppt_chart",
    "office_ppt_review",
    "office_ppt_template",
)

#: PPT 专项技能：叙事 → 设计 → 图表 → 质检 → 模板，配合 office_ppt_deck 使用
PPT_SKILL_CODES: tuple[str, ...] = (
    "office_ppt_storyline",
    "office_ppt_design",
    "office_ppt_chart",
    "office_ppt_review",
    "office_ppt_template",
)

#: 办公类助手的识别词：用于给存量 Agent 幂等补绑办公技能。
OFFICE_AGENT_KEYWORDS: tuple[str, ...] = (
    "办公",
    "文档",
    "公文",
    "排版",
    "报告",
    "周报",
    "月报",
    "日报",
    "台账",
    "表格",
    "数据整理",
    "统计",
    "汇报",
    "纪要",
    "合同",
    "清单",
    "excel",
    "xlsx",
    "ppt",
    "powerpoint",
    "幻灯片",
    "演示文稿",
    "word",
    "docx",
    "pdf",
)

#: 各技能的匹配词（长度越长的词权重越高，避免「文档」压过「Excel」）。
_SKILL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "office_word_report": ("word", "docx", "文档", "公文", "排版", "报告", "方案书", "说明书"),
    "office_excel_table": ("excel", "xlsx", "表格", "台账", "工作表", "数据表", "清单"),
    "office_ppt_deck": ("ppt", "pptx", "powerpoint", "幻灯片", "演示文稿", "汇报材料", "路演"),
    "office_weekly_report": ("周报", "月报", "日报", "工作总结", "述职", "复盘汇报"),
    "office_data_clean": ("数据整理", "清洗", "去重", "统计", "汇总", "透视", "对账"),
    "office_doc_review": ("审校", "校对", "格式检查", "润色", "定稿", "合规检查", "查错"),
    "office_ppt_storyline": (
        "叙事线",
        "故事线",
        "storyline",
        "金字塔",
        "结论先行",
        "SCR",
        "汇报逻辑",
    ),
    "office_ppt_design": ("版式", "配色", "主题色", "字号", "字体", "视觉规范", "母版设计"),
    "office_ppt_chart": ("图表", "柱状图", "折线图", "饼图", "数据可视化", "图表选型"),
    "office_ppt_review": ("PPT质检", "演示质检", "结构检查", "页面检查", "汇报评审", "预演"),
    "office_ppt_template": ("PPT模板", "母版", "模板替换", "占位符", "套模板", "公司模板"),
}

#: 格式 → 必选技能（文档链路自动注入时的第一优先级）
_FORMAT_SKILL: dict[str, str] = {
    "docx": "office_word_report",
    "xlsx": "office_excel_table",
    "pptx": "office_ppt_deck",
    "pdf": "office_word_report",
}


def _definition(
    *,
    code: str,
    name: str,
    description: str,
    prompt: str,
    steps: list[str],
    tags: list[str],
    extras: dict[str, Any],
) -> dict[str, Any]:
    return {
        # ---- 既有链路字段：字符串 steps + prompt ----
        "name": name,
        "description": description,
        "prompt": prompt,
        "steps": steps,
        "source_url": None,
        "license": LICENSE,
        "author": AUTHOR,
        "category": "office",
        "skill_group": GROUP_OFFICE,
        "tags": tags,
        # ---- SkillSpec 契约字段 ----
        "code": code,
        "version": "1.0.0",
        "summary": description,
        "when_to_use": extras["when_to_use"],
        "inputs": extras["inputs"],
        "allowed_tools": ["knowledge.search"],
        "knowledge_scopes": ["yard"],
        "eval_cases": extras["eval_cases"],
        "guardrails": {
            "risk_level": "low",
            "max_steps": 5,
            "require_citation": False,
        },
        "output_format": "markdown",
        "sources": [],
    }


_SPEC_EXTRAS: dict[str, dict[str, Any]] = {
    "office_word_report": {
        "when_to_use": "要产出正式 Word 报告、方案、说明书、合同附件或需要规范排版的公文时使用。",
        "inputs": [
            {"key": "topic", "label": "文档主题", "required": True, "type": "string"},
            {"key": "audience", "label": "阅读对象", "required": False, "type": "string"},
            {"key": "facts", "label": "已有事实与数据", "required": False, "type": "text"},
            {
                "key": "style",
                "label": "文风要求（正式/简洁/简报）",
                "required": False,
                "type": "string",
            },
        ],
        "eval_cases": [
            {
                "input": (
                    "写一份泵站年度检修报告，数据是：检修 12 台泵、"
                    "更换轴承 8 套、停机合计 36 小时"
                ),
                "expect": (
                    "按标题/概述/工作内容/发现问题/下一步计划分节，"
                    "只使用给出数据，不编造金额与人员"
                ),
            },
            {
                "input": "没有具体数据，帮我写一份设备管理方案",
                "expect": "给出方案框架与待补充项清单，明确哪些数据需要现场补录",
            },
        ],
    },
    "office_excel_table": {
        "when_to_use": "要产出 Excel 台账、统计表、对账单、名单或可继续计算的数据表时使用。",
        "inputs": [
            {"key": "purpose", "label": "表格用途", "required": True, "type": "string"},
            {"key": "columns", "label": "需要的列", "required": False, "type": "text"},
            {"key": "rows", "label": "已有数据行", "required": False, "type": "text"},
        ],
        "eval_cases": [
            {
                "input": "做一个设备巡检台账，列有日期、设备、巡检人、油位、异响、处理结果",
                "expect": "输出规范表头与示例行，数值列可计算，空值列明确留待填写",
            },
            {
                "input": "把这些数据做成 Excel：一号泵 0.6MPa、二号泵 0.55MPa",
                "expect": "压力值写成数值（不是字符串），保留单位说明列或表头注明 MPa",
            },
        ],
    },
    "office_ppt_deck": {
        "when_to_use": (
            "要做汇报 PPT、路演材料、培训课件或会议演示文稿，需要版式、主题与结构质检时使用。"
        ),
        "inputs": [
            {"key": "topic", "label": "演示主题", "required": True, "type": "string"},
            {"key": "audience", "label": "听众", "required": False, "type": "string"},
            {"key": "duration", "label": "汇报时长", "required": False, "type": "string"},
            {"key": "data", "label": "可引用的数据与来源", "required": False, "type": "text"},
        ],
        "eval_cases": [
            {
                "input": "给合作社做一个 10 分钟的智慧灌溉汇报 PPT，数据是节水 36%、培训 126 人",
                "expect": (
                    "输出 5-30 页的 DeckPlan：封面 + 目录 + 现状/问题/方案/行动 + 结尾；"
                    "每页一个结论、3-6 条要点，KPI 用 kpi 版式，趋势用 chart 且类别与数值等长"
                ),
            },
            {
                "input": "把这份 3000 字报告直接转成 PPT",
                "expect": "先提炼结论与证据再分页；说明被压缩与取舍的内容，不整段搬上幻灯片",
            },
            {
                "input": "做一版党建主题的年度工作汇报 PPT",
                "expect": "选择 gov_red 主题；页脚写数据来源；缺数据处标「待补充」而不是估算",
            },
        ],
    },
    "office_weekly_report": {
        "when_to_use": "要写周报、月报、日报、工作总结、述职或对上简报时使用。",
        "inputs": [
            {
                "key": "period",
                "label": "周期（本周/本月/某日期）",
                "required": True,
                "type": "string",
            },
            {"key": "work", "label": "已完成工作", "required": True, "type": "text"},
            {"key": "issues", "label": "问题与风险", "required": False, "type": "text"},
            {"key": "next", "label": "下周/下月计划", "required": False, "type": "text"},
        ],
        "eval_cases": [
            {
                "input": "本周完成 3 个大棚滴灌改造，下周计划做 2 个，期间缺 20 个阀门",
                "expect": "按完成情况/问题风险/下周计划三段写，数字与待办原样保留，不夸大成果",
            },
            {
                "input": "这周没干什么，帮我编点内容",
                "expect": "拒绝编造工作内容，改为帮助梳理真实事项或列出待补充清单",
            },
        ],
    },
    "office_data_clean": {
        "when_to_use": "手里有原始表格需要去重、统一格式、统计汇总或做透视分析时使用。",
        "inputs": [
            {"key": "source", "label": "原始数据", "required": True, "type": "text"},
            {"key": "rules", "label": "清洗规则", "required": False, "type": "text"},
            {"key": "metrics", "label": "要统计的指标", "required": False, "type": "text"},
        ],
        "eval_cases": [
            {
                "input": "这列日期有 2024/5/1、2024-05-01、5月1日，帮我统一",
                "expect": "统一为 YYYY-MM-DD，说明无法解析的行单独列出，不静默丢弃",
            },
            {
                "input": "帮我把缺失的产量数据补成平均值",
                "expect": "先说明补值会改变统计口径，给出补齐/标记缺失两种方案供选择",
            },
        ],
    },
    "office_doc_review": {
        "when_to_use": "文档定稿前要做格式检查、错别字校对、口径一致性审查时使用。",
        "inputs": [
            {"key": "content", "label": "待审文档", "required": True, "type": "text"},
            {"key": "standard", "label": "格式或用语标准", "required": False, "type": "string"},
        ],
        "eval_cases": [
            {
                "input": "帮我检查这份报告的格式和错别字",
                "expect": "按标题层级/编号/术语/数字单位/标点分组列问题，给出原文与修改建议",
            },
            {
                "input": "把这份材料润色得更专业",
                "expect": "保留事实与数字不变，仅调整表达；改动处逐条列出，不新增未提供的结论",
            },
        ],
    },
    "office_ppt_storyline": {
        "when_to_use": "汇报还没想清讲什么、顺序怎么排，需要先定叙事线与每页结论时使用。",
        "inputs": [
            {
                "key": "goal",
                "label": "汇报目标（要听众做什么决定）",
                "required": True,
                "type": "string",
            },
            {"key": "audience", "label": "听众与关注点", "required": False, "type": "string"},
            {"key": "facts", "label": "已有结论、证据与数据", "required": False, "type": "text"},
        ],
        "eval_cases": [
            {
                "input": "给理事会汇报滴灌改造，想争取下一期预算",
                "expect": (
                    "用 SCR（情境-冲突-答案）或金字塔给出主线：结论先行 → 三条支撑 → 预算请求；"
                    "每页写一句结论，标出哪几页是决策页"
                ),
            },
            {
                "input": "材料很杂，帮我理一条 10 分钟的叙事线",
                "expect": "压缩到 6-9 个结论句，明确取舍了哪些内容以及原因，不堆砌目录",
            },
        ],
    },
    "office_ppt_design": {
        "when_to_use": "要定主题色、版式、字号与留白规范，让整份 PPT 视觉统一时使用。",
        "inputs": [
            {
                "key": "scene",
                "label": "使用场景（党政/科技/农业/商务/学术）",
                "required": True,
                "type": "string",
            },
            {"key": "constraint", "label": "品牌或模板约束", "required": False, "type": "string"},
        ],
        "eval_cases": [
            {
                "input": "这份农业项目汇报用什么配色和版式比较稳",
                "expect": (
                    "推荐 agri_green 主题，说明主色/辅助色/中性色分工、标题与正文字号阶梯、"
                    "页边距 0.6 英寸与 12 栏栅格；避免装饰条与低对比"
                ),
            },
            {
                "input": "领导喜欢红色，但别做得太土",
                "expect": "用 gov_red 的深红做主色、金色只做点缀；给出对比度≥4.5:1 的具体色值",
            },
        ],
    },
    "office_ppt_chart": {
        "when_to_use": "手里有数据，要决定用柱/折线/饼图并给出可直接落图的图表规格时使用。",
        "inputs": [
            {
                "key": "data",
                "label": "数据（类别 + 数值 + 单位）",
                "required": True,
                "type": "text",
            },
            {"key": "point", "label": "想让图表说明的结论", "required": False, "type": "string"},
        ],
        "eval_cases": [
            {
                "input": "5-8 月用水量 320/280/240/210 吨，做个图",
                "expect": (
                    "选柱状图（类别 4 个、单系列等长），标题写结论「用水量连续下降」，"
                    "标注单位吨与数据来源，不截断纵轴夸大差异"
                ),
            },
            {
                "input": "三个片区的节水率想放一页",
                "expect": "类别≤6、单系列柱状图；若份额合计为 100% 才用饼图，否则说明不适用",
            },
        ],
    },
    "office_ppt_review": {
        "when_to_use": "PPT 定稿前要做结构质检与视觉检查（越界、重叠、字号、对比度）时使用。",
        "inputs": [
            {"key": "deck", "label": "待检查的演示计划或文件", "required": True, "type": "text"},
            {
                "key": "audience",
                "label": "讲解场景（会议室/大屏/打印）",
                "required": False,
                "type": "string",
            },
        ],
        "eval_cases": [
            {
                "input": "帮我检查这份 PPT 有没有排版问题",
                "expect": (
                    "按结构（页数/重复标题/要点数量）、版式（越界/重叠/溢出）、"
                    "可读性（字号<12pt/对比度<4.5）分组列问题，定位到页码并给修改建议"
                ),
            },
            {
                "input": "这份 PPT 能直接拿去汇报吗",
                "expect": (
                    "先给结论（可/需修），再列必须修的 error 与可选的 warn；"
                    "不把未做的视觉检查说成通过"
                ),
            },
        ],
    },
    "office_ppt_template": {
        "when_to_use": "用户提供了 .pptx 模板或公司母版，需要按既有版式与占位符出片时使用。",
        "inputs": [
            {"key": "template", "label": "模板文件或母版说明", "required": True, "type": "text"},
            {"key": "content", "label": "要放入的内容", "required": False, "type": "text"},
        ],
        "eval_cases": [
            {
                "input": "用我们公司的 PPT 模板做这份汇报",
                "expect": (
                    "先读取模板的版式清单与占位符（标题/正文/页码/图片位），"
                    "把内容映射到对应占位符；说明哪些页沿用了模板、哪些需要新建"
                ),
            },
            {
                "input": "模板里有母版动画，能保留吗",
                "expect": "说明只做「读取版式与占位符后按版式出片」，不改写母版 XML 与动画",
            },
        ],
    },
}

_SKILL_SPECS: tuple[dict[str, Any], ...] = (
    {
        "code": "office_word_report",
        "name": "Word 正式文档排版",
        "description": "把事实与需求整理成结构完整、层级清楚、符合中文公文习惯的 Word 文档。",
        "prompt": (
            "你是中文正式文档撰写与排版专家。请把用户需求整理成 Word 文档结构："
            "标题 → 主题/作者（可空）→ 一级章节（heading）→ 正文段落 → 必要时列表/表格。"
            "要求：①每节先给结论句，再展开依据；②数字、单位、时间必须来自用户提供的信息，"
            "缺失处写「待补充」而不是估算；③层级不超过三级，同级标题措辞平行；"
            "④正式报告使用书面语，避免口语与营销化夸张；⑤表格只放对比或清单类信息，"
            "不要把长段落塞进单元格。输出严格使用文档模型 JSON（heading/paragraph/bullets/"
            "numbered/table），不要输出排版说明以外的闲聊。"
        ),
        "steps": [
            "确认主题、阅读对象与已有事实，缺关键信息先列出待补项",
            "搭标题与三级以内的章节骨架，保证同级标题平行",
            "每节先写结论句，再补依据、数据与出处",
            "把对比或清单信息整理成表格，长文留在段落",
            "通读检查数字单位、时间与称谓一致性",
        ],
        "tags": ["Word", "报告", "排版", "公文"],
    },
    {
        "code": "office_excel_table",
        "name": "Excel 台账与数据表",
        "description": "把业务信息整理成表头规范、数值可计算、可直接继续维护的 Excel 表格。",
        "prompt": (
            "你是 Excel 表格设计专家。目标格式是 Excel 时，优先用 table 块组织数据："
            "第一行是表头，每行列数与表头一致。要求：①表头用名词短语，单位写进表头"
            "（如「压力(MPa)」「产量(kg)」），不要在单元格里重复单位；"
            "②纯数字写成数字（12、0.6、35%），日期写 YYYY-MM-DD，状态用统一枚举；"
            "③一列只表达一件事，不合并语义（如「姓名/电话」拆两列）；"
            "④缺数据留空并说明，不填 0、不编造；⑤需要计算时在段落里说明公式与口径。"
            "少量说明用 paragraph，不要用大段文字破坏表格结构。"
        ),
        "steps": [
            "确认用途、使用人与需要长期维护的列",
            "设计表头：名词短语 + 单位，一列一件事",
            "整理数据行，数字/日期/枚举按类型归一",
            "缺值留空并标注待补，不静默补 0",
            "在说明段落写清统计口径与计算公式",
        ],
        "tags": ["Excel", "台账", "表格", "数据"],
    },
    {
        "code": "office_ppt_deck",
        "name": "PPT 演示文稿（DeckPlan + 主题 + 结构质检）",
        "description": (
            "把材料变成每页一个结论、版式统一、可过结构质检的 PPT："
            "先定叙事线，再选版式与主题，最后按质检清单收口。"
        ),
        "prompt": (
            "你是演示文稿架构师。目标格式是 PPT 时，输出 DeckPlan JSON 而不是段落："
            "theme（gov_red/tech_blue/agri_green/business_navy/minimal_mono/warm_orange/"
            "academic/forest_green）+ title/subtitle/audience/storyline + 5-30 页 slides。"
            "要求：①第一页 cover、最后一页 closing，第二页可放 agenda；"
            "②每页只讲一个结论，正文只能是 bullets(≤6 条×≤100 字) / two_column / kpi / "
            "chart / table(≤6 列×8 行) / timeline / process / comparison / quote / image 中的一种，"
            "不要在一页里既要点又堆图表；③先提炼全局结论，再按「现状—问题—方案—行动」组织，"
            "章节页用 section 把长汇报分段；④数据只保留支撑结论的关键数字，chart 的 "
            "categories 与每个 series.values 必须等长，单位写进 unit、来源写进 source；"
            "⑤每页写 notes 作为演讲者备注，细节放备注而不是塞进页面；"
            "⑥不编造数据与效果承诺，缺数据写「待补充」并改用 statement/bullets；"
            "⑦页脚保留来源，全文用同一主题色，标题一句话且不超过 60 字。"
            "生成后按结构质检清单自检：页数、重复标题、要点数量、字号≥12pt、对比度≥4.5:1。"
        ),
        "steps": [
            "确认听众、时长、决策目标与可引用数据",
            "用 SCR 或金字塔写出叙事线与每页一句结论",
            "选主题（党政红/科技蓝/农科绿…），确定版式与字号阶梯",
            "逐页填 DeckPlan：每页一种版式，图表数据等长，备注写讲解要点",
            "过结构质检：页数、重复标题、要点数量、字号与对比度，再定稿",
        ],
        "tags": ["PPT", "演示", "汇报", "DeckPlan", "质检"],
    },
    {
        "code": "office_weekly_report",
        "name": "周报月报与工作总结",
        "description": "按完成情况、问题风险、下一步计划组织周期汇报，数字与待办不夸大不遗漏。",
        "prompt": (
            "你是工作总结撰写助手。请按「本期完成 → 问题与风险 → 下期计划」三段组织，"
            "必要时加「需要支持」。要求：①完成事项写清对象、数量、结果，数字原样保留；"
            "②问题写现象、影响与当前处置，不把问题写成成绩；③下期计划可执行、有负责人或时间"
            "（用户未给则标待定）；④不编造未发生的工作，用户说「没干什么」时帮助梳理真实事项；"
            "⑤对上简报控制在 300 字内，先结论后细节。"
        ),
        "steps": [
            "确认汇报周期、汇报对象与已发生的真实事项",
            "按完成情况列事项：对象 + 数量 + 结果",
            "单列问题与风险，写清影响和当前处置",
            "整理下期计划，缺负责人/时间标待定",
            "压缩出 300 字对上简报，结论先行",
        ],
        "tags": ["周报", "月报", "总结", "简报"],
    },
    {
        "code": "office_data_clean",
        "name": "表格数据整理与统计",
        "description": "统一日期、数值与枚举口径，去重并汇总，无法处理的行如实列出。",
        "prompt": (
            "你是数据整理助手。请先说明数据口径与清洗规则，再给结果。要求："
            "①日期统一 YYYY-MM-DD，数值去掉千分位与多余单位，百分比统一为同一口径；"
            "②去重按业务主键（如设备编号+日期），说明合并了哪些重复；"
            "③无法解析或缺失的行单独列出，不静默丢弃、不擅自补平均值；"
            "④统计结果注明分子分母与时间范围；⑤原始数据与清洗后数据分开呈现，"
            "保证可复核。输出优先用 table 块，规则说明用 paragraph/bullets。"
        ),
        "steps": [
            "确认数据来源、主键与统计口径",
            "统一日期、数值、单位与枚举写法",
            "按主键去重并记录合并明细",
            "列出无法解析/缺失行，给出处置建议",
            "输出清洗结果与统计表，注明分子分母",
        ],
        "tags": ["数据清洗", "统计", "去重", "对账"],
    },
    {
        "code": "office_doc_review",
        "name": "文档审校与格式检查",
        "description": "定稿前检查层级、编号、术语、数字单位与标点，逐条给出原文和修改建议。",
        "prompt": (
            "你是文档审校员。请分组列出问题：标题层级、编号连续性、术语一致性、"
            "数字与单位、时间格式、标点与错别字、事实与口径冲突。"
            "每条给出「原文 → 问题 → 建议修改」，不直接重写全文。"
            "要求：①只改表达不改事实，不新增用户未提供的结论与数据；"
            "②同一术语全文统一，首次出现给全称、后文用简称；"
            "③数字保留原始精度，单位统一；④无法判断对错的地方标为「待确认」并说明原因。"
        ),
        "steps": [
            "通读全文，记录标题层级与编号问题",
            "核对术语、称谓、时间与数字单位是否一致",
            "逐条列出原文、问题与修改建议",
            "标出无法判断、需要作者确认的事项",
            "给出定稿前必须处理的优先级清单",
        ],
        "tags": ["审校", "校对", "格式", "润色"],
    },
    {
        "code": "office_ppt_storyline",
        "name": "PPT 叙事线与结论提炼",
        "description": "先把「讲什么、按什么顺序讲」定下来：SCR、金字塔与每页一句结论。",
        "prompt": (
            "你是汇报叙事顾问。先给主线，再给页面清单。方法：①SCR——情境（共识背景）→"
            "冲突（变化/问题/代价）→答案（方案与请求）；或金字塔——结论先行，下面 3 条"
            "互不重叠的支撑，再往下才是证据；②每一页写成一句可被反驳的结论句，而不是"
            "「背景介绍」「工作进展」这类空标题；③标出决策页（要听众拍板/给资源的那页）；"
            "④明确取舍：哪些素材被压缩、放到备注或删掉，并说明原因；⑤不编造结论，"
            "证据不足时写「待验证」并给出验证方式。输出：叙事线一句话 + 5-9 页结论清单。"
        ),
        "steps": [
            "问清汇报目标：要听众做什么决定",
            "用 SCR 或金字塔搭主线，写出结论先行的那一句",
            "把支撑点拆成互不重叠的 3-5 条，每条配证据",
            "把每条支撑落成一页结论句，标出决策页与附录",
            "复核顺序：前一页是否为后一页的前提，删掉不支撑结论的内容",
        ],
        "tags": ["PPT", "叙事", "金字塔", "结论先行"],
    },
    {
        "code": "office_ppt_design",
        "name": "PPT 主题与版式设计",
        "description": "按场景选主题色与版式，定字号阶梯、页边距与栅格，保证整份视觉统一。",
        "prompt": (
            "你是演示视觉设计顾问。只做设计决策，不改内容。要求：①选主题——党政/党建用"
            "gov_red，科技/数据用 tech_blue，农业/合作社用 agri_green，经营/招商用 "
            "business_navy，学术答辩用 academic，内部极简用 minimal_mono；②配色分工：主色"
            "用于标题与关键数字，辅助色用于结构与强调，中性色用于正文与分割，正文与背景"
            "对比度不低于 4.5:1；③字号阶梯：封面 40-42、章节 32-34、页标题 28-30、正文 "
            "18、辅助 14、注释 12，正文不小于 12pt；④版式：页边距 0.6 英寸、12 栏栅格、"
            "单页一个视觉焦点，标题最多两行；⑤禁止装饰条、3D、阴影堆叠与低对比配色；"
            "⑥同一份稿只用一套主题，章节页可用浅色底区分节奏。输出具体主题名与色值。"
        ),
        "steps": [
            "确认使用场景、投影条件与品牌/模板约束",
            "选一套内置主题，写明主色/辅助色/中性色色值",
            "定字号阶梯与页边距，检查对比度是否≥4.5:1",
            "为每类内容指定版式（KPI/图表/双栏/时间轴），避免同页多焦点",
            "全稿复核：主题是否唯一、标题是否≤两行、是否出现装饰条与 3D",
        ],
        "tags": ["PPT", "配色", "版式", "字号"],
    },
    {
        "code": "office_ppt_chart",
        "name": "PPT 图表选型与数据规格",
        "description": "把数据变成能支撑结论的图表规格：选型、类别与系列对齐、单位与来源。",
        "prompt": (
            "你是数据可视化顾问。产出可直接落图的图表规格。要求：①选型——比较大小用柱状图，"
            "看趋势用折线图，只有「份额合计 100% 且类别≤6」才用饼图，否则改柱状；"
            "②规格：categories 与每个 series.values 必须等长，单图系列≤3、类别≤8，"
            "单位写进 unit，数据来源写进 source；③标题写结论（如「用水量连续 4 个月下降」），"
            "不要写「用水量统计图」；④不截断纵轴、不做 3D、不用渐变；⑤数据缺失不补零，"
            "缺哪期就说明缺哪期；⑥一页只放一个图表，配一句结论说明它证明了什么。"
        ),
        "steps": [
            "确认这张图要证明的结论与受众关心点",
            "按数据类型选柱/折线/饼，说明为何不选其他类型",
            "整理类别与数值，保证每个系列等长并注明单位",
            "写图表结论标题与数据来源，必要时标注口径与时间范围",
            "复核：是否夸大差异、是否缺数据、是否一页多图",
        ],
        "tags": ["PPT", "图表", "数据可视化"],
    },
    {
        "code": "office_ppt_review",
        "name": "PPT 结构与视觉质检",
        "description": "定稿前按清单检查页数、版式、字号与对比度，问题定位到页并给修改建议。",
        "prompt": (
            "你是演示质检员。按三层清单检查并定位到页码：①结构——页数是否 5-30、首尾是否"
            "封面与结尾、标题是否重复、每页是否只有一个结论、要点是否≤6 条；②版式——元素"
            "是否越界（页边距 0.6 英寸）、文本框是否重叠、文字是否溢出、表格是否超过 6×8、"
            "图表类别与系列是否等长；③可读性——正文字号是否≥12pt、前景/背景对比度是否"
            "≥4.5:1、标题是否超过两行。每条给「页码 → 问题 → 修改建议」。明确区分：结构"
            "质检是确定性检查；视觉质检依赖本机 soffice 导图，没做就写「未执行」，"
            "不得把未做的视觉检查写成通过。"
        ),
        "steps": [
            "先看结构：页数、首尾页、重复标题、每页结论数量",
            "再看版式：越界、重叠、溢出、表格密度与图表数据",
            "最后看可读性：字号下限、对比度、标题行数",
            "按 error（必须改）/ warn（建议改）分组，定位到具体页码",
            "说明视觉质检是否执行，未执行时明确标注",
        ],
        "tags": ["PPT", "质检", "评审", "排版检查"],
    },
    {
        "code": "office_ppt_template",
        "name": "PPT 模板与母版套用",
        "description": "读取用户 .pptx 的版式清单与占位符，把内容映射到既有版式出片。",
        "prompt": (
            "你是模板套用助手。只做「读取模板 → 按版式出片」，不改写母版 XML、不动动画与"
            "主题脚本。步骤：①读模板——列出 slide_layouts 名称、每页版式的占位符"
            "（标题/正文/图片/页码）与主题字体；②做映射——封面、目录、章节、内容、结尾分别"
            "对应哪个版式，内容字段落到哪个占位符；③出片——按映射写标题与要点，占位符"
            "放不下时减字而不是缩到 12pt 以下；④说明差异——哪些页沿用模板、哪些页因模板"
            "没有对应版式而新建、哪些装饰元素无法保留；⑤能力边界：不做母版 XML 编辑、"
            "不还原动画与切换效果，需要时请用户在设计器里处理。"
        ),
        "steps": [
            "读取模板文件，列出可用版式与各版式占位符",
            "确认主题字体、配色与页面尺寸（16:9 或 4:3）",
            "建立内容到版式/占位符的映射表",
            "按映射填充内容，超长时减字并把细节放备注",
            "说明沿用与新建的页面，标注无法保留的母版效果",
        ],
        "tags": ["PPT", "模板", "母版", "占位符"],
    },
)


def select_office_skill_codes(text: str, fmt: str, limit: int = 2) -> list[str]:
    """按格式与文本挑选最相关的办公技能（格式优先，再补文本命中）。"""
    picked: list[str] = []
    primary = _FORMAT_SKILL.get((fmt or "").lower())
    if primary:
        picked.append(primary)
    for code in match_office_skill_codes(text, limit=limit):
        if code not in picked:
            picked.append(code)
        if len(picked) >= limit:
            break
    return picked[:limit]


def match_office_skill_codes(text: str, limit: int = 3) -> list[str]:
    """按关键词命中打分挑选办公技能（长词权重更高）。"""
    lowered = f" {(text or '').lower()} "
    if not lowered.strip():
        return []
    scored: list[tuple[int, str]] = []
    for code, words in _SKILL_KEYWORDS.items():
        score = sum(len(word) for word in words if word in lowered)
        if score:
            scored.append((score, code))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [code for _, code in scored[:limit]]


def need_keywords(code: str) -> list[str]:
    """技能匹配用关键词（给一句话生成的 skill_needs 使用）。"""
    return list(_SKILL_KEYWORDS.get(code, ()))


def is_office_request(text: str) -> bool:
    lowered = (text or "").lower()
    return any(word in lowered for word in OFFICE_AGENT_KEYWORDS)


def builtin_items() -> list[dict[str, Any]]:
    """返回内置办公技能条目（6 项通用 + 5 项 PPT 专项，纯数据不查库）。"""
    items: list[dict[str, Any]] = []
    for spec in _SKILL_SPECS:
        definition = _definition(
            code=spec["code"],
            name=spec["name"],
            description=spec["description"],
            prompt=spec["prompt"],
            steps=list(spec["steps"]),
            tags=list(spec["tags"]),
            extras=_SPEC_EXTRAS[str(spec["code"])],
        )
        items.append(
            {
                "item_key": f"builtin:skill:{spec['code']}",
                "item_type": "skill",
                "source": "builtin",
                "code": spec["code"],
                "name": spec["name"],
                "version": "1.0.0",
                "description": spec["description"],
                "source_url": None,
                "license": LICENSE,
                "author": AUTHOR,
                "checksum": None,
                "risk_level": "low",
                "status": "available",
                "definition_json": definition,
                "manifest_json": None,
                "metadata_json": {
                    "author": AUTHOR,
                    "license": LICENSE,
                    "skill_group": GROUP_OFFICE,
                    "tags": list(spec["tags"]),
                    "language": "zh-CN",
                },
                "verified": True,
                "verified_at": None,
            }
        )
    return items


def builtin_item_map() -> dict[str, dict[str, Any]]:
    return {item["item_key"]: item for item in builtin_items()}


def seed_office_skills(db: Session) -> int:
    """幂等 upsert 内置办公技能到市场缓存；返回新增条数。"""
    return seed_items(db, builtin_items())


def ensure_office_skills(db: Session, *, yard_id: str, actor_id: str, request_id: str = "") -> int:
    """为小院铺内置办公技能（published），返回新建数量。"""
    return ensure_skills(
        db,
        builtin_items(),
        yard_id=yard_id,
        actor_id=actor_id,
        request_id=request_id,
        source_label=SOURCE_LABEL,
    )


def ensure_office_skills_for_all_yards(db: Session) -> int:
    """存量小院补铺办公技能（启动时一次性幂等补齐），返回新建数量。"""
    from app.modules.yards.models import Yard

    created = 0
    for yard_id, owner_id in db.execute(select(Yard.id, Yard.owner_user_id)).all():
        created += ensure_office_skills(
            db,
            yard_id=str(yard_id),
            actor_id=str(owner_id or "system"),
            request_id="backfill:office_skills",
        )
    return created


def office_skill_ids(db: Session, yard_id: str) -> dict[str, str]:
    """本小院已发布的平台内置办公技能：skill_code → Skill.id。

    只认 ``created_from=market_builtin`` 且定义分类为 office 的技能，
    避免同名第三方技能被误当作办公 SOP 注入文档提示词。
    """
    from app.modules.evolution.models import Skill

    rows = (
        db.query(Skill)
        .filter(
            Skill.status == "published",
            Skill.skill_code.in_(OFFICE_SKILL_CODES),
            Skill.created_from == "market_builtin",
            or_(Skill.yard_id == yard_id, Skill.yard_id.is_(None)),
        )
        .all()
    )
    out: dict[str, str] = {}
    for row in rows:
        definition = row.definition_json if isinstance(row.definition_json, dict) else {}
        if definition.get("category") != "office":
            continue
        out[str(row.skill_code)] = str(row.id)
    return out


def office_guidance(
    db: Session,
    yard_id: str,
    *,
    text: str,
    fmt: str,
    bound_skills: list[str] | None = None,
    limit: int = 2,
) -> str:
    """取办公技能 SOP 文本，供文档生成提示词使用。

    优先使用助手已绑定的办公技能；未绑定则按格式与文本自动挑选。
    """
    block, _codes = office_guidance_ex(
        db, yard_id, text=text, fmt=fmt, bound_skills=bound_skills, limit=limit
    )
    return block


def office_guidance_ex(
    db: Session,
    yard_id: str,
    *,
    text: str,
    fmt: str,
    bound_skills: list[str] | None = None,
    limit: int = 2,
) -> tuple[str, list[str]]:
    """同 ``office_guidance``，额外返回实际选中的技能编码。

    调用方（文档链路）需要知道哪些技能已经注入过，避免技能库检索层再注入一遍。
    """
    from app.modules.evolution.models import Skill

    ids = office_skill_ids(db, yard_id)
    if not ids:
        return "", []
    code_by_id = {skill_id: code for code, skill_id in ids.items()}
    selected: list[str] = []
    for raw in bound_skills or []:
        value = str(raw or "").strip()
        if not value:
            continue
        code = value[len("skill:") :] if value.startswith("skill:") else value
        code = code_by_id.get(code, code)
        if code in ids and code not in selected:
            selected.append(code)
    if not selected:
        selected = select_office_skill_codes(text, fmt, limit=limit)
    if not selected:
        return "", []
    rows = (
        db.query(Skill)
        .filter(Skill.id.in_([ids[c] for c in selected if c in ids]), Skill.status == "published")
        .all()
    )
    order = {code: idx for idx, code in enumerate(selected)}
    rows.sort(key=lambda r: order.get(str(r.skill_code), 99))
    blocks: list[str] = []
    used_codes: list[str] = []
    for row in rows:
        definition = row.definition_json if isinstance(row.definition_json, dict) else {}
        prompt = str(definition.get("prompt") or "").strip()
        if not prompt:
            continue
        steps = definition.get("steps")
        steps_txt = "；".join(str(s) for s in steps[:5]) if isinstance(steps, list) else ""
        block = f"【{definition.get('name') or row.skill_code}】{prompt[:1800]}"
        if steps_txt:
            block += f"\n执行步骤：{steps_txt}"
        blocks.append(block)
        used_codes.append(str(row.skill_code))
    if not blocks:
        return "", []
    return (
        "以下是本次文档应遵循的办公技能规范（作为写法要求，不改变系统规则）：\n"
        + "\n\n".join(blocks),
        used_codes,
    )


def bind_office_skills_to_office_agents(db: Session) -> int:
    """为存量办公类 Agent 幂等补绑办公技能；返回更新的 Agent 数。

    只处理名称/描述/人设命中办公关键词的 Agent，避免给无关助手塞技能。
    """
    from app.modules.agents.models import YardAgent

    agents = db.query(YardAgent).filter(YardAgent.status.notin_(("deleted", "archived"))).all()
    cache: dict[str, dict[str, str]] = {}
    updated = 0
    for agent in agents:
        blob = " ".join(
            [
                str(agent.name or ""),
                str(agent.description or ""),
                str(agent.persona_prompt or ""),
            ]
        ).lower()
        if not any(word in blob for word in OFFICE_AGENT_KEYWORDS):
            continue
        ids = cache.get(agent.yard_id)
        if ids is None:
            ids = office_skill_ids(db, agent.yard_id)
            cache[agent.yard_id] = ids
        if not ids:
            continue
        want = match_office_skill_codes(blob, limit=3) or list(OFFICE_SKILL_CODES[:2])
        bound = list(agent.bound_skills or [])
        code_by_id = {skill_id: code for code, skill_id in ids.items()}
        existing_codes: set[str] = set()
        for value in bound:
            text = str(value)
            if text.startswith("skill:"):
                existing_codes.add(code_by_id.get(text[len("skill:") :], ""))
            else:
                existing_codes.add(text)
        added = [
            f"skill:{ids[code]}" for code in want if code in ids and code not in existing_codes
        ]
        if added:
            agent.bound_skills = bound + added
            updated += 1
    if updated:
        db.flush()
    return updated


__all__ = [
    "GROUP_OFFICE",
    "OFFICE_AGENT_KEYWORDS",
    "OFFICE_SKILL_CODES",
    "PPT_SKILL_CODES",
    "bind_office_skills_to_office_agents",
    "builtin_item_map",
    "builtin_items",
    "ensure_office_skills",
    "ensure_office_skills_for_all_yards",
    "is_office_request",
    "match_office_skill_codes",
    "need_keywords",
    "office_guidance",
    "office_skill_ids",
    "seed_office_skills",
    "select_office_skill_codes",
]
