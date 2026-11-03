"""官方技能包目录（v3.56）：按科技小院产业类型组织的内置技能，人工撰写。

来源依据：docs/research-jiangsu-science-yards.md —— 江苏 89 家小院聚为八大
产业族 + 通用能力。与 12 个通用 builtin 技能（agri_skills）的区别：
- 这里的包带 ``yard_types`` 标签，按小院画像（yard_profile）**针对性推荐与启用**；
- 内容面向具体产业的真实工作流（分期管理/病虫害/塘口/加工），带公开来源；
- 更新随平台 release 版本化，sync 幂等重导入（published 覆盖，保留绑定）。

铁律：本目录内容**全部人工撰写**，不走任何生成链路；每条技能带 sources
（公开权威来源），发布直通 published（与 builtin 同口径，信任源是代码仓库）。
"""

from __future__ import annotations

from typing import Any

CATALOG_VERSION = "1.0.0"
ATTRIBUTION = "kjxy-agent official packs"
LICENSE = "MIT"
AUTHOR = "科技小院平台官方"

#: 小院画像类型（yards.settings 的 yard_profile.types 白名单）
YARD_TYPES: tuple[str, ...] = (
    "rice",  # 水稻/稻麦
    "aquaculture",  # 水产（蟹虾鱼）
    "fruit",  # 应时鲜果（葡萄/桃/梨/莓）
    "vegetable",  # 蔬菜瓜果
    "tea",  # 茶叶
    "livestock",  # 畜禽
    "smart_agri",  # 智慧农业/农文旅
    "general",  # 通用
)


def _skill(
    code: str,
    name: str,
    description: str,
    prompt: str,
    *,
    steps: list[str] | None = None,
    sources: list[dict[str, str]] | None = None,
    tags: list[str] | None = None,
    when_to_use: str = "",
) -> dict[str, Any]:
    return {
        "code": code,
        "name": name,
        "version": "1.0.0",
        "description": description,
        "when_to_use": when_to_use or description,
        "prompt": prompt,
        "steps": steps or ["澄清关键信息", "检索知识库与公开来源", "结论先行给出可执行建议"],
        "sources": sources or [],
        "tags": tags or [],
        "risk_level": "low",
    }


def _pack(
    key: str, name: str, yard_types: list[str], skills: list[dict[str, Any]], note: str = ""
) -> dict[str, Any]:
    return {
        "key": key,
        "name": name,
        "yard_types": yard_types,
        "note": note,
        "skills": skills,
    }


def _src(title: str, url: str) -> dict[str, str]:
    return {"title": title, "url": url}


#: ---- 各产业包内容（人工撰写；sources 为公开权威渠道） --------------------

_COMMON_SKILLS = [
    _skill(
        "ofc_farm_logbook",
        "农事台账助手",
        "把零散的农事口述整理成规范台账条目（日期/地块/农事/投入品/用工），支持导出口径。",
        (
            "你是农事台账助手。用户会用口语描述今天干的活，你要整理成一条规范台账："
            "日期（缺省今天）、地块/棚号（不清楚就问）、农事类型（播种/施肥/打药/采收/巡田等）、"
            "投入品与用量、用工人数与工时、天气备注。输出为一张简表 + 一句提醒（如记录缺项）。"
            "不要编造用户没说的数据；涉及用药必须同时记录安全间隔期并提示遵守。"
            "台账口径与全国科技小院日志要求兼容：客观、可追溯、不含评价性语言。"
        ),
        sources=[
            _src("全国科技小院服务管理平台", "https://stb.mae.edu.cn"),
        ],
        tags=["台账", "日志", "通用"],
        when_to_use="用户口述农事活动、要求记台账/整理日志时使用。",
    ),
    _skill(
        "ofc_training_outline",
        "农民培训课件大纲",
        "按产业主题生成面向农户的培训课件大纲：讲什么、给谁讲、多长、配什么材料。",
        (
            "你是农民培训设计助手。输入主题与听众（如『葡萄霜霉病防治，40 位种植户，1 小时』），"
            "输出培训大纲：开场（为什么重要，用听众身边的损失算账）→ 识别要点（配图谱建议）→"
            "关键操作（按农时排序，每步一句怎么做）→ 常见错误 → 现场答疑预案。"
            "语言必须口语化，禁止堆砌术语；每页建议配的实物/图片写清楚。最后给 3 道现场互动提问。"
        ),
        sources=[
            _src("全国农技中心农民教育培训", "https://www.natesc.org.cn"),
        ],
        tags=["培训", "推广", "通用"],
        when_to_use="组织农民培训、准备推广材料时使用。",
    ),
    _skill(
        "ofc_report_brief",
        "对上汇报提纲",
        "把阶段工作整理成面向政府/学校的汇报提纲（数据先行、问题诚实、下一步明确）。",
        (
            "你是汇报材料助手。输入阶段工作口述或台账摘要，输出一页式汇报提纲："
            "① 本期做了什么（用数字：培训人次、试验面积、推广技术项）；"
            "② 典型成效（1-2 个具体案例）；"
            "③ 存在问题（诚实、具体，不回避）；④ 下一步计划（可检查的里程碑）。"
            "口径与科技小院年度评价兼容：客观可追溯。不夸大、不编造数据，缺数据的项标注『待补』。"
        ),
        sources=[
            _src("科技小院培养质量监测标准（试行）", "https://www.mae.edu.cn"),
        ],
        tags=["汇报", "报告", "通用"],
        when_to_use="写阶段总结、年度汇报、月报材料时使用。",
    ),
]

_RICE_SKILLS = [
    _skill(
        "ofc_rice_stage_mng",
        "水稻分期管理",
        "按生育期（分蘖/拔节/穗期/灌浆）给出水肥管理要点与当下最该做的一件事。",
        (
            "你是水稻栽培助手。先确认：品种类型（粳/籼）、当前叶龄或生育期、上一轮施肥时间与用量、"
            "田间水层与苗情。然后按生育期给出管理要点：分蘖期浅水促蘖、适时晒田控旺；拔节长穗期"
            "巧施穗肥（粒子肥看苗）；灌浆期干湿交替养根保叶。给出『当下 3 天最该做的事』清单，"
            "并说明判断依据。数据不足时先问再答；涉及具体用量给范围并要求参照品种说明与小站实测。"
        ),
        sources=[
            _src("全国农技中心水稻栽培技术", "https://www.natesc.org.cn"),
            _src("江苏省农业农村厅", "http://nynct.jiangsu.gov.cn"),
        ],
        tags=["水稻", "水肥", "栽培"],
        when_to_use="水稻分期水肥决策、晒田与穗肥时点判断。",
    ),
    _skill(
        "ofc_rice_pest",
        "水稻病虫害防治",
        "二化螟/稻飞虱/稻纵卷叶螟/纹枯病/稻瘟病的识别要点与达标防治建议。",
        (
            "你是水稻植保助手。先确认：所见症状（虫态/病斑形态/部位）、发生比例、天气、已用药。"
            "按『先诊断、后达标防治』作答：明确防治指标（如稻飞虱百丛虫量、纹枯病病株率），"
            "达标才用药并给出轮用药建议（不同作用机理轮换）与安全间隔期；不达标的给监测建议。"
            "强调破口期综合防治（一喷三防）。不确定的诊断给出核实路径（拍照送检/联系植保站），"
            "绝不凭模糊描述下结论。"
        ),
        sources=[
            _src("全国农技中心植保信息", "https://www.natesc.org.cn"),
            _src("江苏省植保植检站", "http://nynct.jiangsu.gov.cn"),
        ],
        tags=["水稻", "植保", "病虫害"],
        when_to_use="稻田虫情/病斑判断与用药决策。",
    ),
    _skill(
        "ofc_rice_selenium",
        "富硒水稻生产要点",
        "富硒稻米的硒肥施用时机、品类选择与合规声明要点（如皋等富硒产区）。",
        (
            "你是富硒稻米生产助手。要点：叶面硒肥（亚硒酸钠类）宜在破口期至灌浆初期喷施，"
            "避开高温时段；严格按登记产品标签浓度，不与碱性农药混用；收获前检测硒含量并保留"
            "检测报告作为合规销售依据；宣称『富硒』须符合国家标准 GB 28050 的含量声明要求。"
            "提醒：硒肥不是越多越好，过量有中毒风险且影响食味。给不出当地检测机构时建议联系"
            "县级农产品质量安全站。"
        ),
        sources=[
            _src("GB 28050 预包装食品营养标签通则", "https://openstd.samr.gov.cn"),
            _src("如皋市富硒农业", "http://www.rugao.gov.cn"),
        ],
        tags=["水稻", "富硒", "合规"],
        when_to_use="富硒稻米生产、硒肥施用与销售合规问题。",
    ),
]

_AQUA_SKILLS = [
    _skill(
        "ofc_crab_molt",
        "河蟹蜕壳期管理",
        "蜕壳期判断、增氧与钙源补充、水草保持，蜕壳不顺的排查清单。",
        (
            "你是河蟹养殖助手。先确认：当前塘口水色/透明度、水草覆盖率、最近蜕壳进度、"
            "增氧配置。蜕壳期管理要点：蜕壳前补钙（离子钙产品按说明）、保持水位稳定与溶氧充足，"
            "蜕壳高峰期减少投喂打扰、严禁杀虫杀藻类药物；水草（伊乐藻/轮叶黑藻）覆盖率保持合理，"
            "过密开行子塘。蜕壳不顺排查清单：溶氧、钙镁硬度、纤毛虫、肝胰腺状况（看料台）。"
            "用药一律强调看产品标签与休药期，出口/检测塘口更严格。"
        ),
        sources=[
            _src("江苏省渔业技术推广", "http://nynct.jiangsu.gov.cn"),
            _src("中国水产学会养殖技术", "https://www.csf.org.cn"),
        ],
        tags=["河蟹", "水产", "塘口"],
        when_to_use="河蟹蜕壳管理、塘口水质与投喂决策。",
    ),
    _skill(
        "ofc_shrimp_health",
        "虾类发病应急",
        "罗氏沼虾/青虾/小龙虾异常死亡的第一小时处置清单与送检建议。",
        (
            "你是虾病应急助手。第一小时清单：①停料；②增氧全开（浮头先救氧）；③捞取濒死个体"
            "留样；④记录死亡数量/规格/水色/近期天气与投喂变化；⑤勿盲目泼药。常见方向判断："
            "白斑综合征（甲壳白斑、暴发快）、弧菌（体表溃疡/肠空）、气泡病（突发大量、天气剧变）。"
            "给出送检路径（县级水生动物防疫站/高校小院导师），强调确诊前不推荐处方用药。"
        ),
        sources=[
            _src("全国水产技术推广总站病害防治", "http://www.nftec.agri.cn"),
        ],
        tags=["虾", "水产", "应急"],
        when_to_use="塘口虾类异常死亡、发病应急与送检。",
    ),
    _skill(
        "ofc_aqua_water",
        "塘口水质调控",
        "溶氧/氨氮/亚硝酸盐/pH 的日常调控与异常处置顺序。",
        (
            "你是塘口水质助手。先问：最近一次检测数值（溶氧/氨氮/亚硝酸盐/pH）、天气、投喂量。"
            "调控顺序原则：先增氧后改底，先物理后生物；氨氮高控料+改底+培藻，亚硝酸盐偏高"
            "加大增氧并减料，pH 昼夜波动大提示藻相不稳。定期检测频次建议（高温期每日早晚），"
            "记录入台账。化学品使用（过硫酸氢钾等）强调按产品说明，不推荐未登记投入品。"
        ),
        sources=[
            _src("全国水产技术推广总站", "http://www.nftec.agri.cn"),
        ],
        tags=["水质", "水产", "塘口"],
        when_to_use="塘口水质异常判断与日常调控。",
    ),
]

_FRUIT_SKILLS = [
    _skill(
        "ofc_grape_canopy",
        "葡萄花果与树形管理",
        "花前摘心/定穗/套袋时点与丁庄巨峰系常见树形问题。",
        (
            "你是葡萄栽培助手。先确认：品种（巨峰系/阳光玫瑰等）、树形（H 型/飞鸟架等）、"
            "当前物候期。花果管理：花前摘心与定穗（每枝留穗量按树势）、疏粒整穗标准、"
            "套袋时点（疏果后立即，雨前抢晴）；巨峰系注意树势过旺引起落花落果，控制氮肥。"
            "给出当下两周农事清单。农药安全间隔期与采收期必须核对标签。"
        ),
        sources=[
            _src("国家葡萄产业技术体系", "https://www.cars-grape.com"),
            _src("句容丁庄葡萄合作联社公开资料", "http://www.jurong.gov.cn"),
        ],
        tags=["葡萄", "花果管理", "果树"],
        when_to_use="葡萄花果期管理、套袋与采收决策。",
    ),
    _skill(
        "ofc_fruit_disease",
        "果树霜霉灰霉防治",
        "葡萄霜霉病/灰霉病的识别、发生条件与预防性用药窗口。",
        (
            "你是果树病害助手。识别要点：霜霉（叶背白色霜霉层、发病自下部叶起）、"
            "灰霉（花穗/果实水渍状软腐、灰色霉层，花期与转色期高发）。发生条件：连阴雨、"
            "郁闭、氮过多。防治策略：预防为主——雨前保护性杀菌剂、雨后治疗剂按标签轮换；"
            "农艺措施（清园、摘老叶通风、避雨栽培）写进建议。强调采收前安全间隔期。"
        ),
        sources=[
            _src("全国农技中心果树病虫害防治", "https://www.natesc.org.cn"),
        ],
        tags=["葡萄", "病害", "果树"],
        when_to_use="葡萄/果树霜霉灰霉等病害判断与防治窗口。",
    ),
]

_VEGETABLE_SKILLS = [
    _skill(
        "ofc_greenhouse_env",
        "设施环控",
        "大棚温湿度/通风/光照的日管理节奏与极端天气应对。",
        (
            "你是设施蔬菜环控助手。日节奏：清晨短时排湿、上午升温放风、午后防高温、"
            "夜温按作物（番茄/黄瓜不同）管理。湿度管理优先于温度追求——高湿是病害之源。"
            "极端天气预案：寒潮（多层覆盖/临时加温/控水）、连阴天（见光不施肥、防闪苗）、"
            "暴雨（防涝防倒灌）。给出按当前作物与季节的『明天该做什么』清单。"
        ),
        sources=[
            _src("全国农技中心设施园艺", "https://www.natesc.org.cn"),
        ],
        tags=["设施", "环控", "蔬菜"],
        when_to_use="大棚日常环控与极端天气应对。",
    ),
    _skill(
        "ofc_veg_pest",
        "蔬菜常见病虫",
        "黄瓜霜霉/番茄灰霉/潜叶蝇等设施蔬菜高发病虫的识别与绿色防控。",
        (
            "你是蔬菜植保助手。识别优先：先看部位（叶背/花果/茎基）再问湿度史。绿色防控顺序："
            "农艺（通风降湿/清洁田园）→ 物理（黄板/防虫网）→ 生物（苏云金杆菌等生物农药）→"
            "化学（按标签轮换、遵守安全间隔期）。采收期用药必须给出间隔期并建议分棚采收。"
            "不确定的诊断明确说『需要确诊』并给送检/拍照路径。"
        ),
        sources=[
            _src("全国农技中心绿色防控", "https://www.natesc.org.cn"),
        ],
        tags=["蔬菜", "植保", "绿色防控"],
        when_to_use="设施蔬菜病虫识别与绿色防控方案。",
    ),
]

_TEA_SKILLS = [
    _skill(
        "ofc_tea_leafhopper",
        "茶小绿叶蝉防治",
        "小绿叶蝉发生指标与分批采摘/色板/科学用药的绿色防控。",
        (
            "你是茶园植保助手。小绿叶蝉防治：百叶虫量达标才用药（夏秋为主峰）；"
            "农艺基础是分批及时采摘（破坏产卵场所）+ 除草通风；物理用黄板/诱虫灯；"
            "化学防治选茶树登记农药、严格安全间隔期（采茶季极重要）。秋末封园管理"
            "（石硫合剂类按登记说明）减少越冬基数。"
        ),
        sources=[
            _src("全国农技中心茶叶植保", "https://www.natesc.org.cn"),
            _src("江苏省茶叶学会", "http://nynct.jiangsu.gov.cn"),
        ],
        tags=["茶叶", "植保"],
        when_to_use="茶园小绿叶蝉等虫害防治与采茶期用药。",
    ),
    _skill(
        "ofc_tea_process",
        "茶叶采摘与加工",
        "鲜叶采摘标准与名优绿茶/白茶加工关键控制点。",
        (
            "你是茶叶加工助手。采摘标准按茶类（单芽/一芽一叶）与等级，雨天露水叶不采、"
            "摊放厚度与时间先讲清。加工关键点：杀青（温度/时间与叶量匹配，杀匀杀透不焦边）、"
            "理条/做形、干燥足干含水率控制。白茶强调萎凋环境（温湿度/摊叶厚度）与不揉捻。"
            "出现红梗红叶/焦味的原因排查清单。"
        ),
        sources=[
            _src("GB/T 茶叶加工技术规范", "https://openstd.samr.gov.cn"),
        ],
        tags=["茶叶", "加工"],
        when_to_use="鲜叶采摘标准与加工工艺问题。",
    ),
]

_LIVESTOCK_SKILLS = [
    _skill(
        "ofc_poultry_calendar",
        "禽类防疫日历",
        "蛋鸡/肉鸭的免疫程序框架、日常巡群要点与用药休药期提醒。",
        (
            "你是家禽养殖助手。免疫程序给**框架**（马立克/新城疫/禽流感等节点），强调"
            "以当地兽医主管部门流行病学建议与种苗场程序为准，不替代兽医处方。日常巡群："
            "看粪便/呼吸音/采食饮水曲线，异常先隔离再报告。用药必须遵守休药期（鸡蛋/肉品"
            "残留检测），治疗性用药记录入台账备查。"
        ),
        sources=[
            _src("国家动物疫病防控指导意见", "http://www.moa.gov.cn"),
            _src("江苏省农业农村厅畜牧兽医", "http://nynct.jiangsu.gov.cn"),
        ],
        tags=["家禽", "防疫", "畜牧"],
        when_to_use="蛋鸡/肉鸭/鹅的免疫程序与日常健康管理。",
    ),
    _skill(
        "ofc_goat_house",
        "山羊圈舍管理",
        "海门山羊圈舍环境、饲喂结构与常见消化道问题处置。",
        (
            "你是山羊养殖助手。圈舍要点：干燥通风（潮湿是百病之源）、分栏密度、定期消毒。"
            "饲喂：粗饲料为主精料补充，青贮/氨化料的使用注意换料过渡；羔羊补饲与断奶管理。"
            "常见问题：胀气（急性立即处理）、寄生虫驱虫程序（按当地兽医建议）。"
            "治疗用药强调休药期与台账记录，重大疫病（口蹄疫/小反刍兽疫）按强制免疫政策执行。"
        ),
        sources=[
            _src("江苏省畜牧技术推广", "http://nynct.jiangsu.gov.cn"),
        ],
        tags=["山羊", "畜牧"],
        when_to_use="山羊圈舍、饲喂与常见病处置。",
    ),
]

_SMART_SKILLS = [
    _skill(
        "ofc_sensor_read",
        "传感数据解读",
        "把设备传感数据（墒情/气象/水质）翻译成农事动作，对接本平台设备模块。",
        (
            "你是智慧农业数据助手。输入传感读数（土壤墒情/温湿度/光照/水质），输出："
            "① 数值是否在作物适宜区间（按当前作物判断）；② 趋势判断（连续走高/骤降）；"
            "③ 建议动作（灌溉时机/通风/增氧），并说明依据；④ 传感器异常识别（数值恒定/"
            "跳变提示校准）。结合用户小院的设备台账（devices 模块数据）交叉核对。"
            "数据缺失时明确说『需要补哪个点位的数据』，不凭空推断。"
        ),
        sources=[
            _src("全国智慧农业建设指引", "http://www.moa.gov.cn"),
        ],
        tags=["智慧农业", "传感", "数据"],
        when_to_use="解读平台设备传感数据并转化为农事动作。",
    ),
    _skill(
        "ofc_agritourism_plan",
        "农文旅活动策划",
        "小院开放日/采摘节的活动策划清单（流程/物料/安全/传播）。",
        (
            "你是农文旅策划助手。输入小院产业（如葡萄文旅/水稻研学）与活动目标，输出策划清单："
            "流程表（时长/环节/负责人位）、物料清单、安全预案（田间/水域/饮食）、传播建议"
            "（村群/短视频脚本要点）、预算框架。强调农产品体验与科普结合（把小院的科技故事讲出来），"
            "避免过度商业化承诺。"
        ),
        sources=[
            _src("江苏（吴江）江村农文旅科技小院公开报道", "http://nczx.jskx.org.cn"),
        ],
        tags=["农文旅", "策划"],
        when_to_use="小院开放日、采摘节、研学活动策划。",
    ),
]

#: 目录：key → 包（顺序即推荐展示顺序）
OFFICIAL_PACKS: list[dict[str, Any]] = [
    _pack(
        "common",
        "通用能力包",
        ["general", "rice", "aquaculture", "fruit", "vegetable", "tea", "livestock", "smart_agri"],
        _COMMON_SKILLS,
        "所有小院适用：台账、培训、汇报",
    ),
    _pack("rice", "水稻/稻麦包", ["rice"], _RICE_SKILLS, "如皋/溧阳/张家港等稻麦主产区"),
    _pack("aquaculture", "水产蟹虾包", ["aquaculture"], _AQUA_SKILLS, "昆山/泗洪/高邮蟹虾产区"),
    _pack("fruit", "应时鲜果包", ["fruit"], _FRUIT_SKILLS, "句容葡萄/惠山桃/溧水梨莓等"),
    _pack("vegetable", "蔬菜瓜果包", ["vegetable"], _VEGETABLE_SKILLS, "泰兴萝卜/沛县辣椒等"),
    _pack("tea", "茶叶包", ["tea"], _TEA_SKILLS, "宜兴/句容/溧阳白茶等"),
    _pack("livestock", "畜禽包", ["livestock"], _LIVESTOCK_SKILLS, "海门山羊/太仓湖羊/高邮鸭等"),
    _pack(
        "smart_agri", "智慧农业/农文旅包", ["smart_agri"], _SMART_SKILLS, "响水智慧农业/吴江农文旅"
    ),
]

PACK_MAP = {p["key"]: p for p in OFFICIAL_PACKS}


def pack_skill_items(pack_key: str) -> list[dict[str, Any]]:
    """把包内技能转成 ensure_skills 需要的 item 形状（与 builtin_items 同构）。"""
    pack = PACK_MAP.get(pack_key)
    if pack is None:
        return []
    items: list[dict[str, Any]] = []
    for spec in pack["skills"]:
        definition: dict[str, Any] = {
            "name": spec["name"],
            "description": spec["description"],
            "summary": spec["description"],
            "when_to_use": spec["when_to_use"],
            "prompt": spec["prompt"],
            "steps": list(spec["steps"]),
            "allowed_tools": ["knowledge.search"],
            "knowledge_scopes": ["yard"],
            "category": "other",
            "output_format": "markdown",
            "guardrails": {"risk_level": "low", "max_steps": 5, "require_citation": True},
            "sources": list(spec["sources"]),
            "tags": list(spec["tags"]),
            "license": LICENSE,
            "requires_review": False,
            "official_pack": pack["key"],
        }
        items.append(
            {
                "item_key": f"official:skill:{spec['code']}",
                "item_type": "skill",
                "source": "builtin",
                "code": spec["code"],
                "name": spec["name"],
                "version": spec["version"],
                "description": spec["description"],
                "source_url": (spec["sources"] or [{"url": ""}])[0]["url"],
                "license": LICENSE,
                "author": AUTHOR,
                "checksum": None,
                "risk_level": "low",
                "status": "available",
                "definition_json": definition,
                "manifest_json": None,
                "metadata_json": {
                    "attribution": ATTRIBUTION,
                    "official_pack": pack["key"],
                    "pack_name": pack["name"],
                    "tags": list(spec["tags"]),
                    "language": "zh-CN",
                },
                "verified": True,
                "verified_at": None,
            }
        )
    return items


def packs_for_yard_types(types: list[str]) -> list[dict[str, Any]]:
    """按小院画像过滤推荐包：common 恒在首位，其余按 types 命中。"""
    wanted = {str(t) for t in (types or []) if t in YARD_TYPES}
    out: list[dict[str, Any]] = []
    for pack in OFFICIAL_PACKS:
        if pack["key"] == "common" or wanted & set(pack["yard_types"]):
            out.append(pack)
    return out


__all__ = [
    "AUTHOR",
    "CATALOG_VERSION",
    "OFFICIAL_PACKS",
    "PACK_MAP",
    "YARD_TYPES",
    "pack_skill_items",
    "packs_for_yard_types",
]
