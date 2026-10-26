"""内置农业提示词技能种子（12 项）。

来源：farmfriend-labs/agent-skills-farming（MIT）。中文提示词为该仓库技能
思路的改写，仅保留可执行的流程性指导，不复制任何实测数据或第三方脚本。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.modules.market.models import MarketItem

SOURCE_REPO = "farmfriend-labs/agent-skills-farming"
SOURCE_REPO_URL = f"https://github.com/{SOURCE_REPO}"
LICENSE = "MIT"
AUTHOR = "FarmFriend Terminal Labs"
ATTRIBUTION = (
    "改写自 farmfriend-labs/agent-skills-farming（MIT License, "
    "Copyright (c) 2026 FarmFriend Terminal Labs）的农业技能思路；"
    "中文提示词与步骤为本项目重写，未复制其脚本或数据。"
)


def _source_url(upstream: str) -> str:
    return f"{SOURCE_REPO_URL}/blob/main/skills/{upstream}/SKILL.md"


def _definition(
    *,
    code: str,
    name: str,
    description: str,
    prompt: str,
    steps: list[str],
    upstream: str,
    tags: list[str],
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "prompt": prompt,
        "steps": steps,
        "source_url": _source_url(upstream),
        "license": LICENSE,
        "attribution": ATTRIBUTION,
        # 扩展字段：便于前端展示分类与溯源，不参与执行
        "category": "agriculture",
        "upstream_skill": f"{SOURCE_REPO}/skills/{upstream}",
        "tags": tags,
    }


_SKILL_SPECS: tuple[dict[str, Any], ...] = (
    {
        "code": "agri_soil_health",
        "name": "土壤健康管理",
        "description": "依据土壤检测与耕作记录判断限制因子，给出改良、轮作与复测方案。",
        "prompt": (
            "你是土壤健康管理助手。请先向用户收集地块位置、土壤检测报告"
            "（pH、有机质、氮磷钾、容重或紧实度）以及近年耕作与施肥记录。"
            "基于用户提供的数据判定限制因子，按“改良目标—措施—用量区间—复测时点”给出建议。"
            "缺少检测数据时只列出需要补测的项目与采样方法，不编造任何数值。"
        ),
        "steps": [
            "核对地块基本信息、检测报告与近三年耕作施肥记录",
            "按 pH、有机质、养分平衡、物理结构逐项判定限制因子",
            "给出有机肥、轮作、覆盖或深松等改良措施及实施顺序",
            "明确复测指标、采样深度与建议复测时间",
            "把措施与观察结果写入田间记录，便于下一季复盘",
        ],
        "upstream": "soil-health-builder",
        "tags": ["土壤", "有机质", "改良"],
    },
    {
        "code": "agri_irrigation",
        "name": "灌溉决策",
        "description": "结合作物生育期、土壤墒情与天气预报，给出灌水量、时段与淋洗建议。",
        "prompt": (
            "你是灌溉决策助手。请先确认作物与生育期、土壤质地、根层深度、"
            "近期降水与未来天气预报、灌溉方式和水源条件。"
            "根据用户提供的墒情或传感数据判断是否需要灌溉，并给出建议灌水量区间、时段与淋洗需求。"
            "数据不足时明确列出需要补充的观测项，不得虚构墒情数值。"
        ),
        "steps": [
            "确认作物、生育期、土壤质地与根层深度",
            "汇总墒情、降水实况与短期天气预报",
            "判断是否达到灌溉触发条件并说明依据",
            "给出灌水量区间、灌溉时段与分区轮灌顺序",
            "提示盐分淋洗需求并安排灌后复查",
        ],
        "upstream": "water-wisdom-helper",
        "tags": ["灌溉", "墒情", "节水"],
    },
    {
        "code": "agri_pest_diagnosis",
        "name": "病虫害识别流程",
        "description": "按排除法梳理症状与环境信息，给出最可能方向和田间验证方法。",
        "prompt": (
            "你是病虫害识别流程助手。请让用户描述作物、受害部位、症状出现时间与扩散速度，"
            "并尽量提供清晰照片和环境条件。"
            "先按“非侵染性因素—真菌—细菌—病毒—虫害”的顺序做排除，"
            "列出最可能的 2-3 个方向及田间验证方法。"
            "缺少图像或症状信息时只给出观察清单，不确诊、不编造用药剂量。"
        ),
        "steps": [
            "记录作物品种、生育期、受害部位与症状演变",
            "先排除药害、缺素、渍涝、低温等非侵染性因素",
            "按真菌、细菌、病毒、虫害顺序提出候选方向",
            "给出田间取样、镜检或诱捕等验证方法",
            "验证后再进入用药或综合防治环节",
        ],
        "upstream": "pest-protector-organic",
        "tags": ["病虫害", "诊断", "绿色防控"],
    },
    {
        "code": "agri_pesticide_safety",
        "name": "农药安全使用",
        "description": "核对标签与登记信息，给出安全间隔期、防护、施药与废弃物处理要点。",
        "prompt": (
            "你是农药安全使用顾问。请根据用户提供的作物、防治对象、农药标签与当地登记信息核对适用性。"
            "给出安全间隔期、施药时段、个人防护、器械清洗与废弃物处理步骤。"
            "标签或登记信息不全时必须提示查阅实物标签，绝不臆造用量、浓度和禁用范围。"
        ),
        "steps": [
            "核对作物、防治对象与农药登记范围是否匹配",
            "阅读实物标签，确认剂量、浓度、安全间隔期与禁用限用条款",
            "确认施药天气、周边敏感区域与蜂蚕保护要求",
            "列出个人防护、施药顺序与器械清洗步骤",
            "记录用药信息并处理空包装等废弃物",
        ],
        "upstream": "regulatory-compliance-autopilot",
        "tags": ["农药", "安全间隔期", "合规"],
    },
    {
        "code": "agri_harvest_storage",
        "name": "采收与保鲜",
        "description": "制定适期采收、预冷、分级包装与贮运温湿度控制流程。",
        "prompt": (
            "你是采收与保鲜助手。请先确认作物品种、成熟度指标、采收方式、"
            "采后处理条件与销售半径。"
            "按“适期采收—预冷—分级包装—贮运温湿度”给出可执行流程和关键控制点。"
            "没有实测温湿度或成熟度数据时，只说明测定方法与目标区间，不编造批次数据。"
        ),
        "steps": [
            "确认品种、成熟度判断指标与采收窗口",
            "安排采收时段、采收工具与避免机械损伤的做法",
            "给出预冷方式、预冷终温与时间要求",
            "制定分级、包装与码垛规范",
            "明确贮运温湿度、通风与到货验收要点",
        ],
        "upstream": "harvest-preservation-guide",
        "tags": ["采收", "保鲜", "贮运"],
    },
    {
        "code": "agri_machinery",
        "name": "农机保养",
        "description": "按班次、定期与封存三档安排农机检查、润滑与易损件更换。",
        "prompt": (
            "你是农机保养助手。请让用户提供机具型号、作业时长、作业环境与近期故障表现。"
            "按班次保养、定期保养和季节性封存三档给出检查项目、润滑与易损件更换建议。"
            "涉及拆装、高压或旋转部件时先提示断电熄火与安全防护，"
            "型号不明时不得臆造扭矩和油品规格。"
        ),
        "steps": [
            "记录机具型号、累计作业时长与故障现象",
            "执行班次保养：清洁、紧固、润滑与安全检查",
            "按周期检查滤芯、皮带、刀片、轮胎与液压系统",
            "给出易损件更换判断标准与备件清单",
            "安排季末清洗、防锈与封存，并登记保养台账",
        ],
        "upstream": "repair-decision-assistant",
        "tags": ["农机", "保养", "安全"],
    },
    {
        "code": "agri_field_log",
        "name": "田间记录",
        "description": "把口述农事整理成结构化记录，缺项标注待补充，不替用户填写数据。",
        "prompt": (
            "你是田间记录助手。请把用户口述的农事活动整理成结构化记录："
            "日期、地块、作物与生育期、操作内容、投入品与用量、天气、操作人和备注。"
            "缺项用“待补充”标注，不替用户填写未经确认的数据。"
            "输出可直接粘贴到记录表或知识库的清单格式。"
        ),
        "steps": [
            "复述事件要点，确认日期、地块与作物",
            "补齐操作内容、投入品名称与用量单位",
            "记录天气、操作人与机具信息",
            "把缺失字段标为“待补充”并列出追问清单",
            "按统一字段输出记录，提示归档位置",
        ],
        "upstream": "invisible-data-logger",
        "tags": ["记录", "台账", "可追溯"],
    },
    {
        "code": "agri_farm_calendar",
        "name": "农事日历",
        "description": "按地区、作物与播栽期生成逐周农事安排与判断标准。",
        "prompt": (
            "你是农事日历助手。请根据用户所在地区、作物品种、播栽日期和生育期安排，"
            "生成按周排列的农事计划。每项任务写明时间窗口、操作要点、判断标准和所需物资。"
            "气候与品种信息不足时给出可调整的模板，并提示以当地农技部门指导为准，不虚构具体日期。"
        ),
        "steps": [
            "确认地区、品种、播栽期与目标茬口",
            "按生育期拆分整地、播种、水肥、植保与采收任务",
            "为每项任务标注时间窗口、判断标准与物资需求",
            "标出与天气强相关的关键窗口和备选方案",
            "输出周历并在执行后回填实际完成情况",
        ],
        "upstream": "grow-timing-calendar",
        "tags": ["农事", "日历", "计划"],
    },
    {
        "code": "agri_market_price",
        "name": "市场询价",
        "description": "生成品种规格明确的询价清单与比价口径，不编造实时价格。",
        "prompt": (
            "你是农产品市场询价助手。请让用户提供品种、规格、等级、产地、数量和期望销售时间。"
            "输出询价清单：目标批发市场或收购商、比价口径、运输与损耗成本、结算方式。"
            "没有实时行情数据时明确说明需现场或电话询价，不编造价格和涨跌幅度。"
        ),
        "steps": [
            "确认品种、规格等级、产地、数量与销售时间",
            "列出目标市场、收购商与联系方式获取途径",
            "统一比价口径：含税、运费、包装与损耗",
            "记录报价、结算方式与交货条件",
            "汇总询价结果并提示价格波动的核实方式",
        ],
        "upstream": "local-market-connector",
        "tags": ["市场", "询价", "销售"],
    },
    {
        "code": "agri_policy_search",
        "name": "政策检索",
        "description": "按地区与经营主体梳理政策检索路径、申报条件与材料清单。",
        "prompt": (
            "你是农业政策检索助手。请先确认用户所在地区、经营主体类型、产业方向和想申报的支持事项。"
            "按“政策名称—发布部门—适用范围—申报条件—材料清单—截止时间”整理检索框架。"
            "未检索到原文时只给出检索路径和关键词，不臆测补贴金额、资格或申报时间。"
        ),
        "steps": [
            "确认地区、主体类型、产业方向与支持事项",
            "给出政府门户、农业农村部门等检索入口与关键词",
            "按政策名称、发布部门、适用范围建立对照表",
            "逐条核对申报条件、材料清单与截止时间",
            "提示以政策原文和主管部门答复为准",
        ],
        "upstream": "grant-money-finder",
        "tags": ["政策", "申报", "检索"],
    },
    {
        "code": "agri_crop_nutrition",
        "name": "作物营养诊断",
        "description": "结合症状、土壤检测与施肥史排查缺素，给出采样与复测建议。",
        "prompt": (
            "你是作物营养诊断助手。请收集作物种类、生育期、症状部位与表现、"
            "土壤检测和近期施肥记录。"
            "按大量元素、中微量元素和根系或土壤环境逐项排查，给出叶面诊断或土壤复测建议。"
            "信息不足时列出观察与采样清单，不凭单张照片断定缺素，不编造施肥量。"
        ),
        "steps": [
            "记录作物、生育期、症状部位与出现顺序",
            "核对土壤检测、灌溉水质与近期施肥记录",
            "按大量元素、中微量元素、根系环境逐项排查",
            "给出叶片或土壤采样方法与送检项目",
            "待检测结果确认后再制定施肥调整方案",
        ],
        "upstream": "plant-whisperer-assistant",
        "tags": ["营养", "缺素", "土壤检测"],
    },
    {
        "code": "agri_climate_risk",
        "name": "气候风险提示",
        "description": "围绕低温、高温、干旱、渍涝、大风冰雹给出风险判断与防护措施。",
        "prompt": (
            "你是农业气候风险提示助手。请确认地块位置、作物与生育期、设施类型和当前天气实况。"
            "围绕低温冻害、高温热害、干旱、渍涝、大风和冰雹给出风险判断、预警阈值与可执行的防护措施。"
            "未接入实时气象数据时明确提示以当地气象台预报为准，不编造预警等级和发生概率。"
        ),
        "steps": [
            "确认地块位置、作物生育期与设施条件",
            "对照天气实况与预报识别主要气象风险",
            "给出可观察的预警阈值与检查时点",
            "列出保温、遮阴、排水、加固等防护措施",
            "提示灾后巡查、记录与保险报案要点",
        ],
        "upstream": "actionable-weather-alerts",
        "tags": ["气候", "灾害", "预警"],
    },
)

AGRI_SKILL_SPECS: tuple[dict[str, Any], ...] = _SKILL_SPECS


def builtin_items() -> list[dict[str, Any]]:
    """返回 12 个内置技能条目（纯数据，不查库）。"""
    items: list[dict[str, Any]] = []
    for spec in _SKILL_SPECS:
        definition = _definition(
            code=spec["code"],
            name=spec["name"],
            description=spec["description"],
            prompt=spec["prompt"],
            steps=list(spec["steps"]),
            upstream=spec["upstream"],
            tags=list(spec["tags"]),
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
                "source_url": definition["source_url"],
                "license": LICENSE,
                "author": AUTHOR,
                "checksum": None,
                "risk_level": "low",
                "status": "available",
                "definition_json": definition,
                "manifest_json": None,
                "metadata_json": {
                    "attribution": ATTRIBUTION,
                    "upstream_skill": definition["upstream_skill"],
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


def seed_builtin(db: Session) -> int:
    """幂等 upsert 内置技能；返回本次新增条数（已存在的只刷新内容，不改状态）。"""
    created = 0
    for data in builtin_items():
        row = db.query(MarketItem).filter(MarketItem.item_key == data["item_key"]).first()
        if row is None:
            row = MarketItem(**data)
            db.add(row)
            created += 1
            continue
        # 只刷新内容字段；status 保留安装态，避免种子覆盖用户/安装结果
        row.name = data["name"]
        row.version = data["version"]
        row.description = data["description"]
        row.source_url = data["source_url"]
        row.license = data["license"]
        row.author = data["author"]
        row.risk_level = data["risk_level"]
        row.definition_json = data["definition_json"]
        row.metadata_json = data["metadata_json"]
        row.verified = True
        row.verified_at = row.verified_at or datetime.now(UTC)
    db.flush()
    return created


def ensure_builtin_skills(
    db: Session, *, yard_id: str, actor_id: str, request_id: str = ""
) -> int:
    """为新小院铺设内置农业技能（published），使其直接出现在专家审核 → Skill 管理。

    幂等：按 yard + skill_code 判重，已存在（任意版本）则跳过。返回新建数量。
    """
    from sqlalchemy import select

    from app.modules.audit.service import log_audit
    from app.modules.evolution.models import Skill

    existing = {
        str(code)
        for code in db.scalars(select(Skill.skill_code).where(Skill.yard_id == yard_id)).all()
    }
    created = 0
    for item in builtin_items():
        code = str(item["code"])
        if code in existing:
            continue
        definition = dict(item["definition_json"] or {})
        definition["market_item_key"] = item["item_key"]
        db.add(
            Skill(
                yard_id=yard_id,
                skill_code=code,
                version=str(item["version"]),
                definition_json=definition,
                status="published",
                created_from="market_builtin",
                approved_by=actor_id,
            )
        )
        existing.add(code)
        created += 1
    if created:
        db.flush()
        log_audit(
            db,
            yard_id=yard_id,
            actor_id=actor_id,
            action="builtin_skills_provisioned",
            resource_type="skill",
            resource_id=yard_id,
            result="success",
            request_id=request_id or f"provision:{yard_id}",
            metadata={"count": created, "source": SOURCE_REPO},
        )
    return created


__all__ = [
    "AGRI_SKILL_SPECS",
    "ATTRIBUTION",
    "LICENSE",
    "SOURCE_REPO",
    "SOURCE_REPO_URL",
    "builtin_item_map",
    "builtin_items",
    "ensure_builtin_skills",
    "seed_builtin",
]
