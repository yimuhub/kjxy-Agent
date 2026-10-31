"""Agent 工厂的角色框架库：按行业描述组合角色，而不是固定一套四角色。

设计口径：
- 每个框架覆盖一类行业职能，提供 1-3 个互补角色；角色带工作流阶段
  （分析→方案→执行→交付→审核），审核角色固定排在最后，班组运行时担任 critic。
- 选角分三步：先给「描述 + 侧重 + 联网情报」按关键词打分选框架；再跨框架
  轮转取 4 个领域角色（避免某个框架霸占全部名额）；最后补齐分析/审核等
  关键阶段，保证 3-6 个角色、阶段顺序稳定。
- 未知行业走通用框架（需求分析/方案设计/执行协调/质检审核），不伪装成行业专家。
- 纯规则、确定性、无外部依赖；联网情报只作为额外的匹配文本与提示词素材。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

STAGE_ANALYSIS = "analysis"
STAGE_PLAN = "plan"
STAGE_EXECUTION = "execution"
STAGE_DELIVERY = "delivery"
STAGE_REVIEW = "review"

STAGE_ORDER: dict[str, int] = {
    STAGE_ANALYSIS: 10,
    STAGE_PLAN: 20,
    STAGE_EXECUTION: 30,
    STAGE_DELIVERY: 40,
    STAGE_REVIEW: 90,
}
STAGE_LABELS: dict[str, str] = {
    STAGE_ANALYSIS: "分析与取证",
    STAGE_PLAN: "方案设计",
    STAGE_EXECUTION: "执行落地",
    STAGE_DELIVERY: "交付汇总",
    STAGE_REVIEW: "审核把关",
}
STAGES: tuple[str, ...] = tuple(STAGE_LABELS)

#: 角色总数硬边界（与提示词一致）
MIN_ROLES, MAX_ROLES = 3, 6
#: 跨框架轮转先取的领域角色上限（审核角色另算）
DOMAIN_ROLE_LIMIT = 4


@dataclass(frozen=True)
class RoleTemplate:
    code: str
    name: str
    emoji: str
    stage: str
    duty: str
    template: str
    questions: tuple[str, ...]
    #: 该角色建议自动绑定的技能 code（办公域用；其它角色为空）
    skills: tuple[str, ...] = ()


@dataclass(frozen=True)
class Framework:
    code: str
    label: str
    keywords: tuple[str, ...]
    roles: tuple[RoleTemplate, ...]


@dataclass(frozen=True)
class BriefProfile:
    label: str
    crops: tuple[str, ...]
    business: str
    goals: tuple[str, ...]
    matched: tuple[str, ...] = field(default=())


FRAMEWORKS: tuple[Framework, ...] = (
    Framework(
        code="crop_production",
        label="种植生产",
        keywords=(
            "种植",
            "苗情",
            "长势",
            "播种",
            "育苗",
            "育秧",
            "水肥",
            "施肥",
            "灌溉",
            "大棚",
            "温室",
            "果园",
            "蔬菜",
            "小麦",
            "水稻",
            "玉米",
            "棉花",
            "茶园",
            "果树",
            "花卉",
            "苗木",
            "土壤",
            "墒情",
            "栽培",
            "田间",
            "生育期",
        ),
        roles=(
            RoleTemplate(
                code="agronomist",
                name="种植技术顾问",
                emoji="🌱",
                stage=STAGE_ANALYSIS,
                duty="结合作物、区域、生育期与土壤墒情，判断长势与管理重点",
                template=(
                    "先拆解用户问题中的作物、区域、生育期与限制条件，再依据资料给出"
                    "技术判断、依据和下一步建议：{{question}}"
                ),
                questions=(
                    "{label}当前生育期的管理重点是什么？",
                    "怎么判断{label}长势是否正常？",
                    "{label}水肥管理有哪些常见误区？",
                    "遇到连阴天或高温，{label}该怎么调整管理？",
                    "{label}增产提质最值得先做哪几件事？",
                ),
            ),
            RoleTemplate(
                code="field_operator",
                name="田间执行官",
                emoji="🚜",
                stage=STAGE_EXECUTION,
                duty="把技术方案拆成可执行的农事动作、时间窗与验收标准",
                template=(
                    "把下面的问题转化为可执行的田间动作清单：做什么、什么时候做、"
                    "做到什么标准算完成：{{question}}"
                ),
                questions=(
                    "{label}这一周应该安排哪些农事？",
                    "{label}施肥/打药的作业顺序怎么排？",
                    "人手和机具有限时，{label}农事怎么排优先级？",
                    "{label}作业完成后怎么验收？",
                    "遇到下雨或农忙冲突，{label}计划怎么顺延？",
                ),
            ),
        ),
    ),
    Framework(
        code="plant_protection",
        label="植保诊断",
        keywords=(
            "病虫害",
            "病害",
            "虫害",
            "防治",
            "农药",
            "诊断",
            "打药",
            "赤霉",
            "白粉",
            "蚜虫",
            "红蜘蛛",
            "飞虱",
            "锈病",
            "枯萎",
            "霜霉",
            "疫病",
            "螟",
            "蓟马",
            "菌核",
            "病毒病",
            "缺素",
            "药害",
        ),
        roles=(
            RoleTemplate(
                code="pathologist",
                name="病虫害诊断师",
                emoji="🔬",
                stage=STAGE_ANALYSIS,
                duty="依据症状、环境与病史判断病虫害，给出鉴别要点与风险等级",
                template=(
                    "先根据症状、发生环境和近期天气做鉴别诊断，列出可能病因、"
                    "判断依据和需要补充的信息：{{question}}"
                ),
                questions=(
                    "{label}当前高发的病虫害有哪些？",
                    "怎么区分{label}的病害、虫害与缺素？",
                    "{label}出现病斑/卷叶时先查什么？",
                    "{label}病虫害预警要看哪些指标？",
                    "没有实验室检测时，{label}怎么快速判断病因？",
                ),
            ),
            RoleTemplate(
                code="control_planner",
                name="防治方案师",
                emoji="🧪",
                stage=STAGE_PLAN,
                duty="制定预防、监测与应急处置方案，标注安全间隔和合规边界",
                template=(
                    "基于诊断结论给出分阶段的防治方案：农业/生物/化学手段、时机、"
                    "安全间隔与风险提示；不确定的药剂剂量必须标注需专家确认：{{question}}"
                ),
                questions=(
                    "{label}病害怎么制定预防方案？",
                    "{label}打药的安全间隔和轮换要求是什么？",
                    "绿色防控下{label}虫害怎么处理？",
                    "{label}防治失败后怎么补救？",
                    "{label}防治方案怎么评估成本和效果？",
                ),
            ),
        ),
    ),
    Framework(
        code="farm_ops",
        label="农事作业",
        keywords=(
            "农事",
            "作业",
            "修剪",
            "采摘",
            "采收",
            "除草",
            "整地",
            "托管",
            "代耕",
            "劳务",
            "用工",
            "排班",
            "产后",
        ),
        roles=(
            RoleTemplate(
                code="ops_coordinator",
                name="农事调度员",
                emoji="🗂️",
                stage=STAGE_EXECUTION,
                duty="编排作业顺序、人力机具与时间窗，控制农忙冲突",
                template=(
                    "把问题拆成作业任务，排出先后顺序、资源需求和风险预案，"
                    "给出可以照着执行的调度表：{{question}}"
                ),
                questions=(
                    "{label}这周的人手和机具怎么安排？",
                    "{label}农忙季怎么避免窝工？",
                    "{label}作业质量标准怎么定？",
                    "临时缺人时{label}哪些活可以延后？",
                    "{label}托管服务怎么报价和验收？",
                ),
            ),
            RoleTemplate(
                code="harvest_manager",
                name="采收与产后管理员",
                emoji="🧺",
                stage=STAGE_DELIVERY,
                duty="制定采收标准、分级、预冷与储运安排，减少产后损耗",
                template=(
                    "围绕采收与产后环节给出可执行安排：成熟度、分级标准、"
                    "预冷/储运条件与损耗控制：{{question}}"
                ),
                questions=(
                    "{label}什么时候采收最合适？",
                    "{label}采收后怎么分级和包装？",
                    "{label}储运损耗怎么降下来？",
                    "{label}采收用工怎么安排？",
                    "行情波动时{label}是立即卖还是暂存？",
                ),
            ),
        ),
    ),
    Framework(
        code="market_sales",
        label="产销市场",
        keywords=(
            "销售",
            "电商",
            "直播",
            "渠道",
            "价格",
            "行情",
            "对接",
            "订单",
            "品牌",
            "批发",
            "零售",
            "复购",
            "社群",
            "团购",
            "产销",
            "卖货",
            "客户",
            "客单价",
            "推广",
            "获客",
        ),
        roles=(
            RoleTemplate(
                code="market_analyst",
                name="市场行情分析师",
                emoji="📈",
                stage=STAGE_ANALYSIS,
                duty="分析价格、供需、竞品与渠道结构，给出定价和入市时机",
                template=(
                    "先给出价格/供需/竞品的判断依据，再给出定价、渠道和入市时机建议；"
                    "数据不足要明确说明：{{question}}"
                ),
                questions=(
                    "{label}最近的行情和价格走势怎么样？",
                    "{label}适合走哪些销售渠道？",
                    "{label}怎么定价更有竞争力？",
                    "{label}的竞品和差异化卖点是什么？",
                    "行情下跌时{label}怎么减少损失？",
                ),
            ),
            RoleTemplate(
                code="channel_operator",
                name="渠道运营师",
                emoji="🛒",
                stage=STAGE_EXECUTION,
                duty="把销售策略落成电商/直播/社群/批发的动作与履约流程",
                template=(
                    "把销售目标拆成渠道动作：选平台、定节奏、做内容、接订单、"
                    "管履约，给出可执行清单：{{question}}"
                ),
                questions=(
                    "{label}电商店铺怎么起步？",
                    "{label}直播/短视频怎么排内容？",
                    "{label}订单和售后怎么处理？",
                    "{label}怎么做老客复购？",
                    "{label}线上线下的价格怎么协同？",
                ),
            ),
            RoleTemplate(
                code="brand_planner",
                name="品牌与内容策划",
                emoji="🎯",
                stage=STAGE_PLAN,
                duty="提炼卖点与品牌故事，规划包装、内容与活动节奏",
                template=("先提炼差异化卖点，再给出品牌表达、包装与内容节奏建议：{{question}}"),
                questions=(
                    "{label}的卖点怎么提炼？",
                    "{label}包装和视觉怎么设计？",
                    "{label}适合做什么促销活动？",
                    "{label}品牌故事怎么讲更可信？",
                    "{label}怎么做产地背书？",
                ),
            ),
        ),
    ),
    Framework(
        code="agri_machinery",
        label="农机设备",
        keywords=(
            "农机",
            "设备",
            "运维",
            "维修",
            "保养",
            "传感器",
            "物联网",
            "巡检",
            "故障",
            "喷灌",
            "滴灌",
            "无人机",
            "温控",
            "机井",
            "水泵",
            "卷帘",
        ),
        roles=(
            RoleTemplate(
                code="equipment_diagnostician",
                name="设备诊断师",
                emoji="🛠️",
                stage=STAGE_ANALYSIS,
                duty="判断设备故障、保养周期与安全风险，给出排查顺序",
                template=(
                    "按现象→可能原因→排查顺序给出设备诊断，标注安全注意事项和"
                    "需要现场确认的信息：{{question}}"
                ),
                questions=(
                    "{label}设备出现异常怎么排查？",
                    "{label}保养周期怎么定？",
                    "{label}传感器数据不准怎么办？",
                    "{label}哪些故障必须停机处理？",
                    "{label}设备老化怎么评估更换？",
                ),
            ),
            RoleTemplate(
                code="maintenance_engineer",
                name="运维工程师",
                emoji="⚙️",
                stage=STAGE_EXECUTION,
                duty="制定维保计划、备件清单与操作规范，降低停机时间",
                template=(
                    "把问题落成维保计划：频次、备件、责任人和验收标准，"
                    "并给出安全操作要求：{{question}}"
                ),
                questions=(
                    "{label}年度维保怎么排？",
                    "{label}常备哪些易损件？",
                    "{label}远程监控怎么做？",
                    "{label}作业安全规范有哪些？",
                    "{label}维修成本怎么控制？",
                ),
            ),
        ),
    ),
    Framework(
        code="policy_business",
        label="经营与政策",
        keywords=(
            "政策",
            "补贴",
            "申报",
            "合规",
            "财务",
            "成本",
            "合同",
            "经营",
            "税务",
            "保险",
            "贷款",
            "立项",
            "资质",
            "审计",
            "收益",
            "预算",
        ),
        roles=(
            RoleTemplate(
                code="policy_advisor",
                name="政策申报顾问",
                emoji="📜",
                stage=STAGE_PLAN,
                duty="梳理补贴、资质、合规与申报路径，标注材料和时间节点",
                template=(
                    "按适用条件→材料清单→办理路径→风险提示给出申报建议；"
                    "政策口径不确定时必须提示以主管部门为准：{{question}}"
                ),
                questions=(
                    "{label}可以申请哪些补贴？",
                    "{label}申报需要哪些材料？",
                    "{label}合规经营要注意什么？",
                    "{label}项目立项怎么准备？",
                    "{label}政策申报常见驳回原因有哪些？",
                ),
            ),
            RoleTemplate(
                code="business_analyst",
                name="经营分析师",
                emoji="🧮",
                stage=STAGE_ANALYSIS,
                duty="拆解成本、收益、现金流与合同风险，给出经营判断",
                template=("先列成本与收益结构，再给出盈亏平衡、风险点和改善建议：{{question}}"),
                questions=(
                    "{label}的成本结构怎么拆？",
                    "{label}怎么算盈亏平衡？",
                    "{label}合同有哪些风险点？",
                    "{label}现金流怎么安排？",
                    "{label}哪些投入最值得优先？",
                ),
            ),
        ),
    ),
    Framework(
        code="quality_trace",
        label="品控溯源",
        keywords=(
            "品控",
            "质检",
            "溯源",
            "认证",
            "绿色",
            "有机",
            "农残",
            "标准",
            "抽检",
            "合格证",
            "检测",
            "质量",
            "安全",
            "批次",
        ),
        roles=(
            RoleTemplate(
                code="quality_inspector",
                name="品控审核员",
                emoji="✅",
                stage=STAGE_REVIEW,
                duty="按标准抽检与审核前序输出，指出事实错误、依据不足与合规风险",
                template=(
                    "请以审核员身份复核前序结论：逐条核对标准与依据，指出风险与缺口，"
                    "最后给出是否放行和修订意见：{{question}}"
                ),
                questions=(
                    "{label}抽检应该查哪些项目？",
                    "{label}农残标准怎么把握？",
                    "{label}合格证怎么开？",
                    "{label}质检记录怎么留档？",
                    "{label}不合格批次怎么处置？",
                ),
            ),
            RoleTemplate(
                code="trace_archivist",
                name="溯源档案员",
                emoji="🏷️",
                stage=STAGE_DELIVERY,
                duty="建立批次档案与追溯记录，保证来源、过程、检测可回查",
                template=(
                    "把问题整理成可追溯的档案结构：批次、环节、责任人、记录与查询方式：{{question}}"
                ),
                questions=(
                    "{label}溯源档案要记哪些内容？",
                    "{label}追溯码怎么用？",
                    "{label}批次怎么划分？",
                    "{label}记录缺失怎么补？",
                    "{label}怎么应对客户溯源查询？",
                ),
            ),
        ),
    ),
    Framework(
        code="training_service",
        label="培训客服",
        keywords=(
            "培训",
            "客服",
            "售后",
            "答疑",
            "指导",
            "新手",
            "教学",
            "课程",
            "咨询",
            "投诉",
            "回访",
        ),
        roles=(
            RoleTemplate(
                code="trainer",
                name="技术培训师",
                emoji="🎓",
                stage=STAGE_PLAN,
                duty="把知识讲成新手能上手的课程与操作要领，指出常见误区",
                template=(
                    "以培训讲师口吻组织内容：先给结论，再讲步骤、演示要点和常见误区：{{question}}"
                ),
                questions=(
                    "{label}新手入门要学什么？",
                    "{label}培训课程怎么设计？",
                    "{label}操作要领怎么讲清楚？",
                    "{label}常见误区有哪些？",
                    "{label}培训效果怎么评估？",
                ),
            ),
            RoleTemplate(
                code="service_agent",
                name="客户成功专员",
                emoji="💬",
                stage=STAGE_DELIVERY,
                duty="承接咨询、投诉与回访，形成闭环记录和改进建议",
                template=("按受理→核实→处理→回访给出服务闭环，并标注需要升级的问题：{{question}}"),
                questions=(
                    "{label}客户常见问题怎么答？",
                    "{label}投诉怎么闭环？",
                    "{label}回访怎么做才有效？",
                    "{label}服务话术怎么统一？",
                    "{label}怎么把问题反馈给生产端？",
                ),
            ),
        ),
    ),
    Framework(
        code="data_monitor",
        label="数据监测",
        keywords=(
            "数据",
            "报表",
            "统计",
            "汇总",
            "监测",
            "预警",
            "看板",
            "台账",
            "记录",
            "分析",
            "指标",
            "趋势",
        ),
        roles=(
            RoleTemplate(
                code="data_analyst",
                name="数据分析师",
                emoji="📊",
                stage=STAGE_ANALYSIS,
                duty="定义指标、识别异常与趋势，给出预警阈值与数据缺口",
                template=(
                    "先定义指标与口径，再做趋势/异常判断，列出数据缺口和预警阈值：{{question}}"
                ),
                questions=(
                    "{label}应该盯哪些指标？",
                    "{label}数据异常怎么判断？",
                    "{label}台账怎么记才可用于分析？",
                    "{label}怎么做趋势对比？",
                    "{label}预警阈值怎么设？",
                ),
            ),
            RoleTemplate(
                code="report_editor",
                name="报表汇编员",
                emoji="📋",
                stage=STAGE_DELIVERY,
                duty="把零散记录整理成结构化摘要、对比表和汇报口径",
                template=(
                    "把信息整理成结构化摘要（必要时用表格口径），注明来源、口径和数据缺口：{{question}}"
                ),
                questions=(
                    "{label}周报/月报怎么写？",
                    "{label}数据怎么做对比表？",
                    "{label}汇报口径怎么统一？",
                    "{label}图表怎么选？",
                    "{label}数据缺口怎么写才诚实？",
                ),
            ),
        ),
    ),
    Framework(
        code="aquaculture",
        label="水产养殖",
        keywords=(
            "水产",
            "养殖",
            "鱼",
            "虾",
            "蟹",
            "池塘",
            "水质",
            "投喂",
            "增氧",
            "网箱",
            "稻田养鱼",
        ),
        roles=(
            RoleTemplate(
                code="aqua_technician",
                name="水质与养殖技术员",
                emoji="🐟",
                stage=STAGE_ANALYSIS,
                duty="判断水质、密度、投喂与病害风险，给出调控方向",
                template=(
                    "结合水质指标、养殖密度与天气给出风险判断和调控建议，"
                    "缺少检测数据时说明需要补测什么：{{question}}"
                ),
                questions=(
                    "{label}水质要测哪些指标？",
                    "{label}浮头/缺氧怎么应急？",
                    "{label}投喂量怎么定？",
                    "{label}常见病害怎么防？",
                    "{label}密度和产量怎么平衡？",
                ),
            ),
            RoleTemplate(
                code="aqua_operator",
                name="养殖执行官",
                emoji="🌊",
                stage=STAGE_EXECUTION,
                duty="编排巡塘、增氧、换水、投喂与用药的日常动作",
                template=(
                    "把养殖管理拆成每日/每周动作清单，写明时机、判断标准和记录要求：{{question}}"
                ),
                questions=(
                    "{label}每天巡塘看什么？",
                    "{label}增氧机什么时候开？",
                    "{label}换水和投喂怎么排？",
                    "{label}高温/暴雨天怎么应对？",
                    "{label}养殖记录怎么留？",
                ),
            ),
        ),
    ),
    Framework(
        code="processing_storage",
        label="加工仓储",
        keywords=(
            "加工",
            "烘干",
            "杀青",
            "分级",
            "包装",
            "仓储",
            "冷库",
            "物流",
            "库存",
            "配送",
            "保鲜",
        ),
        roles=(
            RoleTemplate(
                code="process_engineer",
                name="加工工艺师",
                emoji="🏭",
                stage=STAGE_PLAN,
                duty="确定工艺参数、分级标准与包装方案，控制品质波动",
                template=(
                    "给出工艺参数、关键控制点、分级与包装建议，并标注需要试验验证的部分：{{question}}"
                ),
                questions=(
                    "{label}加工工艺参数怎么定？",
                    "{label}怎么分级更合理？",
                    "{label}包装怎么选？",
                    "{label}品质波动怎么控制？",
                    "{label}加工损耗怎么降？",
                ),
            ),
            RoleTemplate(
                code="warehouse_keeper",
                name="仓储物流协调员",
                emoji="📦",
                stage=STAGE_EXECUTION,
                duty="安排入库、温湿度、库存周转与配送，降低损耗",
                template=(
                    "把问题落成仓储/配送安排：温湿度、库位、周转、配送与异常处理：{{question}}"
                ),
                questions=(
                    "{label}冷库温湿度怎么管？",
                    "{label}库存周转怎么加快？",
                    "{label}配送路线怎么排？",
                    "{label}滞销库存怎么处理？",
                    "{label}仓储损耗怎么降？",
                ),
            ),
        ),
    ),
)

#: 未知行业兜底：通用协作角色（不冒充行业专家）
GENERAL_FRAMEWORK = Framework(
    code="general",
    label="通用协作",
    keywords=(),
    roles=(
        RoleTemplate(
            code="requirement_analyst",
            name="需求分析师",
            emoji="🧭",
            stage=STAGE_ANALYSIS,
            duty="把模糊需求拆成目标、约束、交付物与待确认信息",
            template="先澄清目标、对象、约束与交付物，再给出分析结论和下一步：{{question}}",
            questions=(
                "{label}这件事的目标和边界是什么？",
                "{label}需要哪些信息和数据？",
                "{label}主要风险是什么？",
                "{label}怎么判断做成了？",
                "{label}下一步先做什么？",
            ),
        ),
        RoleTemplate(
            code="solution_designer",
            name="方案设计师",
            emoji="🧩",
            stage=STAGE_PLAN,
            duty="设计可选方案、比较取舍并给出推荐路径",
            template="给出 2-3 个可选方案，比较成本/收益/风险，并说明推荐哪一个：{{question}}",
            questions=(
                "{label}有哪几种做法？",
                "{label}各方案成本和风险如何？",
                "{label}推荐方案是什么？",
                "{label}需要哪些资源？",
                "{label}怎么分阶段落地？",
            ),
        ),
        RoleTemplate(
            code="execution_coordinator",
            name="执行协调员",
            emoji="🤝",
            stage=STAGE_EXECUTION,
            duty="把方案拆成任务、责任人与时间表，跟踪阻塞",
            template="把方案拆成任务清单：责任人、时间、依赖和验收标准：{{question}}",
            questions=(
                "{label}任务怎么拆？",
                "{label}谁负责哪一块？",
                "{label}时间表怎么排？",
                "{label}卡住了怎么处理？",
                "{label}进度怎么跟踪？",
            ),
        ),
        RoleTemplate(
            code="quality_reviewer",
            name="质检审核员",
            emoji="✅",
            stage=STAGE_REVIEW,
            duty="复核前序输出的事实、依据、可行性与合规风险并给出修订结论",
            template=(
                "以审核员身份逐条核对事实与依据，指出风险，最后给出是否通过"
                "和修订结论：{{question}}"
            ),
            questions=(
                "{label}结论的依据够不够？",
                "{label}有哪些风险没覆盖？",
                "{label}方案能不能落地？",
                "{label}还需要补什么？",
                "{label}最终结论是什么？",
            ),
        ),
    ),
)

OFFICE_FRAMEWORK = Framework(
    code="office_documents",
    label="办公文档",
    keywords=(
        "办公",
        "文档",
        "公文",
        "排版",
        "报告",
        "周报",
        "月报",
        "日报",
        "总结",
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
        "办公软件",
        "写作",
    ),
    roles=(
        RoleTemplate(
            code="spreadsheet_analyst",
            name="表格数据分析师",
            emoji="📊",
            stage=STAGE_ANALYSIS,
            duty="核对数据口径，设计 Excel 表头与统计规则，整理台账原始数据",
            template=(
                "先确认数据来源、口径与要统计的指标，再给出表头设计、"
                "清洗规则和可复核的统计结果：{{question}}"
            ),
            questions=(
                "{label}需要哪些列和指标？",
                "{label}数据从哪里来？",
                "{label}口径和单位怎么统一？",
                "{label}缺失值怎么处理？",
                "{label}怎么做汇总统计？",
            ),
            skills=("office_excel_table", "office_data_clean"),
        ),
        RoleTemplate(
            code="document_architect",
            name="文档架构师",
            emoji="🧱",
            stage=STAGE_PLAN,
            duty="规划文档章节层级、阅读顺序与交付格式，确保结构完整可读",
            template=(
                "先给文档骨架：标题、章节层级、每节结论与所需材料，"
                "再说明选择 Word/Excel/PPT 的理由：{{question}}"
            ),
            questions=(
                "{label}文档要分几章？",
                "{label}阅读对象是谁？",
                "{label}每章结论是什么？",
                "{label}用 Word 还是 PPT？",
                "{label}还缺哪些材料？",
            ),
            skills=("office_word_report", "office_doc_review"),
        ),
        RoleTemplate(
            code="content_writer",
            name="办公文档撰写师",
            emoji="✍️",
            stage=STAGE_EXECUTION,
            duty="按骨架撰写正文、周报汇报与演示要点，控制篇幅与表达",
            template=(
                "按给定骨架撰写内容：先结论后依据，控制篇幅；"
                "需要交付文件时说明目标格式与块结构：{{question}}"
            ),
            questions=(
                "{label}正文怎么写？",
                "{label}周报怎么组织？",
                "{label}PPT 每页放什么？",
                "{label}哪些内容要压缩？",
                "{label}怎么保证数字一致？",
            ),
            skills=("office_word_report", "office_ppt_deck", "office_weekly_report"),
        ),
        RoleTemplate(
            code="office_reviewer",
            name="文档审校员",
            emoji="🔍",
            stage=STAGE_REVIEW,
            duty="检查层级、编号、术语、数字单位与格式，给出定稿修改清单",
            template=(
                "按标题层级、编号、术语、数字单位、标点分组审校，"
                "逐条给出原文与修改建议，最后给出定稿结论：{{question}}"
            ),
            questions=(
                "{label}格式有哪些问题？",
                "{label}术语统一了吗？",
                "{label}数字单位对吗？",
                "{label}哪里可能引起误解？",
                "{label}可以定稿了吗？",
            ),
            skills=("office_doc_review",),
        ),
    ),
)

FRAMEWORKS = (*FRAMEWORKS, OFFICE_FRAMEWORK)

#: 正式写作框架：公文 / 申报 / 招投标 / 通用正式长文（对接 28 项正式写作技能）
FORMAL_WRITING_FRAMEWORK = Framework(
    code="formal_writing",
    label="正式写作",
    keywords=(
        "公文",
        "发文",
        "通知",
        "请示",
        "批复",
        "公函",
        "纪要",
        "会议纪要",
        "工作方案",
        "实施方案",
        "工作总结",
        "工作报告",
        "情况报告",
        "讲话稿",
        "信息简报",
        "情况专报",
        "申报",
        "申报书",
        "项目申报",
        "专项资金",
        "补贴",
        "可行性研究",
        "可研",
        "中期检查",
        "结题",
        "验收",
        "预算说明",
        "招标",
        "招标文件",
        "投标",
        "投标文件",
        "标书",
        "评分响应",
        "评分点",
        "废标",
        "澄清",
        "质疑",
        "政策解读",
        "正式长文",
        "一页纸",
        "会议材料",
        "材料包",
    ),
    roles=(
        RoleTemplate(
            code="official_writer",
            name="公文写作",
            emoji="📜",
            stage=STAGE_PLAN,
            duty="按公文文种规范起草通知、请示、报告、函、纪要、方案、总结与讲话稿",
            template=(
                "按对应公文文种的结构起草以下材料：先确认文种与主送对象，"
                "再写缘由、事项、执行要求与落款；缺信息列待核实清单，不编造数据与文号：{{question}}"
            ),
            questions=(
                "{label}这类事项应该用哪种公文文种？",
                "{label}通知的事项和时限怎么写清楚？",
                "{label}请示怎么写才容易获批？",
                "{label}会议纪要的议定事项怎么拆？",
                "{label}工作总结怎么写出问题和下一步？",
            ),
            skills=(
                "gov_notice",
                "gov_request",
                "gov_report",
                "gov_letter",
                "gov_minutes",
                "gov_work_plan",
                "gov_summary",
                "gov_reply",
                "gov_speech",
                "gov_briefing",
            ),
        ),
        RoleTemplate(
            code="grant_specialist",
            name="申报专员",
            emoji="🗂️",
            stage=STAGE_ANALYSIS,
            duty="对照申报指南拆资格条件、指标与材料，编制申报书、中期检查与结题验收材料",
            template=(
                "先对照申报指南/任务书逐条拆解资格条件、考核指标与材料清单，"
                "再给出申报书章节骨架与待补项；不虚构业绩、资质与金额：{{question}}"
            ),
            questions=(
                "{label}申报要满足哪些资格条件？",
                "{label}申报书需要哪些章节和附件？",
                "{label}专项资金预算怎么说明？",
                "{label}中期检查的偏差怎么解释？",
                "{label}结题验收要准备哪些佐证？",
            ),
            skills=(
                "grant_project_application",
                "grant_tech_program",
                "grant_subsidy",
                "grant_honor",
                "grant_midterm",
                "grant_acceptance",
                "grant_budget",
            ),
        ),
        RoleTemplate(
            code="tender_specialist",
            name="投标专员",
            emoji="📑",
            stage=STAGE_EXECUTION,
            duty="按招标文件总装投标文件、做评分响应对照与废标风险自查",
            template=(
                "把招标文件拆成资格、评分、技术与格式要求，逐条安排响应章节与证据，"
                "列出废标风险与待补材料；不虚构业绩与参数响应：{{question}}"
            ),
            questions=(
                "{label}招标文件的评分点怎么拆？",
                "{label}投标文件按什么顺序总装？",
                "{label}技术方案怎么逐条响应参数？",
                "{label}商务标要准备哪些资格材料？",
                "{label}哪些情况会导致废标？",
            ),
            skills=(
                "tender_document",
                "bid_document",
                "bid_response_matrix",
                "bid_technical",
                "bid_commercial",
                "bid_risk_review",
                "bid_clarification",
            ),
        ),
        RoleTemplate(
            code="formal_writer",
            name="正式长文",
            emoji="🖋️",
            stage=STAGE_DELIVERY,
            duty="组织调研长文、一页纸简报、政策解读与会议材料包，控制结构与篇幅",
            template=(
                "先定读者与结论，再搭结构：长文给章节骨架与论证依据，"
                "简报压缩到一页并单列决策事项；政策解读只依据原文：{{question}}"
            ),
            questions=(
                "{label}正式长文的章节怎么搭？",
                "{label}怎么压缩成一页纸简报？",
                "{label}政策解读要讲清哪些要素？",
                "{label}会议材料包要准备哪些文件？",
                "{label}哪些数据需要标注来源？",
            ),
            skills=(
                "formal_longform",
                "exec_brief",
                "policy_interpretation",
                "meeting_pack",
            ),
        ),
    ),
)

FRAMEWORKS = (*FRAMEWORKS, FORMAL_WRITING_FRAMEWORK)

ALL_FRAMEWORKS: tuple[Framework, ...] = (*FRAMEWORKS, GENERAL_FRAMEWORK)

#: 作物/品类词表（长词优先匹配，避免「稻」和「水稻」重复计）
CROPS: tuple[str, ...] = tuple(
    sorted(
        {
            "小麦",
            "水稻",
            "玉米",
            "棉花",
            "大豆",
            "油菜",
            "马铃薯",
            "甘薯",
            "花生",
            "高粱",
            "谷子",
            "荞麦",
            "燕麦",
            "大麦",
            "绿豆",
            "红豆",
            "芝麻",
            "向日葵",
            "甜菜",
            "甘蔗",
            "烟草",
            "蔬菜",
            "番茄",
            "黄瓜",
            "辣椒",
            "茄子",
            "白菜",
            "甘蓝",
            "花椰菜",
            "西兰花",
            "菠菜",
            "生菜",
            "芹菜",
            "韭菜",
            "大葱",
            "生姜",
            "大蒜",
            "洋葱",
            "萝卜",
            "胡萝卜",
            "山药",
            "芋头",
            "莲藕",
            "苹果",
            "梨",
            "桃",
            "葡萄",
            "柑橘",
            "橙",
            "柚子",
            "草莓",
            "西瓜",
            "甜瓜",
            "樱桃",
            "猕猴桃",
            "枣",
            "核桃",
            "板栗",
            "香蕉",
            "芒果",
            "荔枝",
            "龙眼",
            "菠萝",
            "茶叶",
            "茶树",
            "花卉",
            "苗木",
            "食用菌",
            "香菇",
            "木耳",
            "平菇",
            "金针菇",
            "中药材",
            "人参",
            "三七",
            "天麻",
            "石斛",
            "枸杞",
            "橡胶",
            "咖啡",
            "可可",
            "饲草",
            "青贮",
            "果蔬",
            "水果",
            "果园",
            "大棚",
            "温室",
            "稻田",
            "水产",
            "畜禽",
            "家禽",
            "生猪",
            "奶牛",
            "肉牛",
            "小龙虾",
            "龙虾",
            "河蟹",
            "对虾",
            "螃蟹",
            "鲫鱼",
            "草鱼",
            "鲈鱼",
            "黄鳝",
            "泥鳅",
            "牛蛙",
        },
        key=lambda word: (-len(word), word),
        reverse=False,
    )
)

BUSINESS_TYPES: tuple[str, ...] = (
    "合作社",
    "家庭农场",
    "农业公司",
    "服务队",
    "示范基地",
    "基地",
    "园区",
    "协会",
    "供销社",
    "联合体",
    "种植大户",
    "养殖场",
)

GOAL_WORDS: tuple[str, ...] = (
    "监测",
    "预警",
    "对接",
    "销售",
    "培训",
    "申报",
    "降本",
    "增产",
    "提质",
    "溯源",
    "诊断",
    "防治",
    "托管",
    "运维",
    "复盘",
    "汇报",
    "降损",
)


def industry_label(brief: str) -> str:
    """展示用行业短语：首行、压缩空白、遇标点截断、最多 16 字。"""
    text = (brief or "").strip()
    if not text:
        return "行业"
    first_line = re.split(r"[，。；;:,：!！?？\n]", text, maxsplit=1)[0]
    label = re.sub(r"\s+", "", first_line)[:16]
    return label.rstrip("，。；、,.;:：!！?？") or "行业"


def parse_brief(
    brief: str, focus: str | None = None
) -> tuple[BriefProfile, list[tuple[int, Framework]]]:
    """深度解析描述：抽作物/经营主体/目标，并按关键词给框架打分。"""
    raw = (brief or "").strip()
    text = f"{raw} {focus or ''}"
    lowered = text.lower()
    # 长词优先且去子串：命中「小龙虾」后不再重复计「龙虾」
    matched_crops: list[str] = []
    for crop in CROPS:
        if crop not in text:
            continue
        if any(crop in picked for picked in matched_crops):
            continue
        matched_crops.append(crop)
        if len(matched_crops) >= 2:
            break
    crops = tuple(matched_crops)
    business = next((b for b in BUSINESS_TYPES if b in text), "")
    goals = tuple([g for g in GOAL_WORDS if g in text][:4])
    label = "、".join(crops) if crops else industry_label(raw)

    scored: list[tuple[int, Framework]] = []
    for framework in FRAMEWORKS:
        score = 0
        for keyword in framework.keywords:
            if keyword in lowered:
                score += 2 if len(keyword) >= 3 else 1
        if score > 0:
            scored.append((score, framework))
    scored.sort(key=lambda item: (-item[0], item[1].code))
    if any(f.code == "aquaculture" for _, f in scored):
        # 水产描述里的“病害/防治”是水生动物病害，不拉种植植保框架
        scored = [(s, f) for s, f in scored if f.code != "plant_protection"]
    profile = BriefProfile(
        label=label,
        crops=crops,
        business=business,
        goals=goals,
        matched=tuple(f.code for _, f in scored),
    )
    return profile, scored


def _round_robin_roles(scored: list[tuple[int, Framework]]) -> list[tuple[str, RoleTemplate]]:
    """跨框架轮转取领域角色：先取每个框架的第 1 个角色，再取第 2 个……"""
    pools = [framework.roles for _, framework in scored]
    picked: list[tuple[str, RoleTemplate]] = []
    seen: set[str] = set()
    depth = 0
    while len(picked) < DOMAIN_ROLE_LIMIT:
        added = False
        for (_, framework), pool in zip(scored, pools, strict=False):
            if depth >= len(pool):
                continue
            role = pool[depth]
            if role.code in seen:
                continue
            seen.add(role.code)
            picked.append((framework.code, role))
            added = True
            if len(picked) >= DOMAIN_ROLE_LIMIT:
                break
        if not added:
            break
        depth += 1
    return picked


def compose_roles(
    brief: str,
    focus: str | None = None,
    research_text: str = "",
) -> tuple[BriefProfile, list[tuple[str, RoleTemplate]]]:
    """按描述组合 3-6 个角色：领域角色轮转 + 补齐分析/审核 + 阶段排序。"""
    profile, scored = parse_brief(brief, focus)
    if research_text:
        # 情报文本只增强匹配，不改变画像里的作物/主体
        extra_scored: list[tuple[int, Framework]] = []
        for framework in FRAMEWORKS:
            score = sum(
                (2 if len(kw) >= 3 else 1)
                for kw in framework.keywords
                if kw in research_text.lower()
            )
            if score > 0:
                extra_scored.append((score, framework))
        extra_scored.sort(key=lambda item: (-item[0], item[1].code))
        merged: dict[str, tuple[int, Framework]] = {f.code: (s, f) for s, f in scored}
        for score, framework in extra_scored:
            current = merged.get(framework.code)
            if current is None:
                merged[framework.code] = (score, framework)
            else:
                merged[framework.code] = (current[0] + score, framework)
        scored = sorted(merged.values(), key=lambda item: (-item[0], item[1].code))

    # 水产描述里的“病害/防治”属于水生动物病害，不应拉进种植植保框架
    if any(framework.code == "aquaculture" for _, framework in scored):
        scored = [(s, f) for s, f in scored if f.code != "plant_protection"]

    if not scored:
        return profile, [("general", role) for role in GENERAL_FRAMEWORK.roles]

    top_score, top_framework = scored[0]
    if top_framework.code == "office_documents" and top_score >= 4:
        # 办公需求足够明确时，优先组齐办公角色（表格/架构/撰写/审校），
        # 避免被数据监控等泛化框架稀释成半套流程。
        picked = [("office_documents", role) for role in top_framework.roles][:DOMAIN_ROLE_LIMIT]
    elif top_framework.code == "formal_writing" and top_score >= 4:
        # 正式写作需求足够明确时（公文/申报/招投标），整套给齐正式写作角色，
        # 避免只出一个写手、缺申报或投标的对照与自查环节。
        picked = [("formal_writing", role) for role in top_framework.roles][:DOMAIN_ROLE_LIMIT]
    else:
        picked = _round_robin_roles(scored)
    has = {role.code for _, role in picked}

    # 补齐关键阶段：至少要有分析或方案，且必须有审核（critic）
    if not any(role.stage in (STAGE_ANALYSIS, STAGE_PLAN) for _, role in picked):
        role = GENERAL_FRAMEWORK.roles[0]
        if role.code not in has:
            picked.append(("general", role))
            has.add(role.code)
    if not any(role.stage == STAGE_REVIEW for _, role in picked):
        quality = next((f for _, f in scored if f.code == "quality_trace"), None)
        review_role = quality.roles[0] if quality is not None else GENERAL_FRAMEWORK.roles[-1]
        if review_role.code not in has:
            picked.append((quality.code if quality is not None else "general", review_role))
            has.add(review_role.code)

    # 总数钳制：优先保留分析/方案/审核，按阶段顺序稳定排序
    picked = picked[:MAX_ROLES]
    picked.sort(key=lambda item: (STAGE_ORDER.get(item[1].stage, 50), item[0]))
    if len(picked) < MIN_ROLES:
        for role in GENERAL_FRAMEWORK.roles:
            if len(picked) >= MIN_ROLES:
                break
            if role.code in has:
                continue
            picked.append(("general", role))
            has.add(role.code)
        picked.sort(key=lambda item: (STAGE_ORDER.get(item[1].stage, 50), item[0]))
    return profile, picked


__all__ = [
    "ALL_FRAMEWORKS",
    "BriefProfile",
    "FORMAL_WRITING_FRAMEWORK",
    "FRAMEWORKS",
    "GENERAL_FRAMEWORK",
    "MAX_ROLES",
    "MIN_ROLES",
    "OFFICE_FRAMEWORK",
    "RoleTemplate",
    "STAGES",
    "STAGE_ANALYSIS",
    "STAGE_DELIVERY",
    "STAGE_EXECUTION",
    "STAGE_LABELS",
    "STAGE_ORDER",
    "STAGE_PLAN",
    "STAGE_REVIEW",
    "compose_roles",
    "industry_label",
    "parse_brief",
]
