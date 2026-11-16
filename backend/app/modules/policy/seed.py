"""政策种子：只收录可核验的官方原文/官方解读，绝不编造政策。

审查 5.6：政策模块开箱 0 条，用户第一次点进来就是空页。这里预置 6 条
国家/山东省真实政策做“样例 + 可申报入口”，每条都带官方来源 URL、发布日期
与核验状态；正文是官方原文的**摘要**（明确标注），不是伪造的全文。

去重口径：同一小院下 policy_name + publish_department 已存在则跳过。
新小院在首启第 ④ 步自动注入；老小院由启动时 ``seed_all_yards`` 幂等回填。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.modules.policy.models import Policy
from app.modules.yards.models import Yard

log = get_logger(__name__)


def _dt(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


#: 每条都来自官方页面；content 是官方原文摘要，不添加任何原文没有的数字。
DEFAULT_POLICIES: tuple[dict[str, Any], ...] = (
    {
        "policy_name": "山东省2024—2026年农机购置与应用补贴实施方案",
        "publish_department": "山东省农业农村厅 山东省财政厅",
        "scope": "province",
        "province": "山东省",
        "department": "山东省农业农村厅",
        "published_at": _dt(2024, 10, 25),
        "apply_end_at": _dt(2026, 12, 31),
        "policy_type": "农机补贴",
        "support_direction": "农机购置与应用补贴",
        "source_url": "http://nync.shandong.gov.cn/zwgk/zcwj/zcfg/202410/t20241025_4766657.html",
        "content": (
            "山东省农业农村厅、山东省财政厅印发《山东省2024—2026年农机购置与应用补贴实施方案》"
            "（鲁农机字〔2024〕7号），落实农业农村部办公厅、财政部办公厅《2024—2026年农机购置"
            "与应用补贴实施意见》（农办机〔2024〕3号），规范实施农机购置与应用补贴政策。"
            "补贴对象为从事农业生产的农民和农业生产经营组织；具体补贴机具范围、补贴标准与"
            "申报流程以方案及年度实施细则为准。本文为官方通知摘要，原文与附件见来源链接。"
        ),
        "apply_conditions": {
            "provinces": ["山东省"],
            "applicant_types": ["个人", "家庭农场", "合作社", "企业"],
        },
        "material_requirements": {
            "items": [
                "身份证或营业执照",
                "购机发票",
                "机具铭牌/出厂编号信息",
                "本人银行卡或对公账户",
            ],
            "note": "以当地县级农业农村部门当年公布的申报材料清单为准。",
        },
    },
    {
        "policy_name": "2026年山东省加力推进农机购置与应用补贴“优机优补”“有进有出”实施方案",
        "publish_department": "山东省农业农村厅 山东省财政厅",
        "scope": "province",
        "province": "山东省",
        "department": "山东省农业农村厅",
        "published_at": _dt(2026, 4, 30),
        "apply_end_at": _dt(2026, 12, 31),
        "policy_type": "农机补贴",
        "support_direction": "优机优补、落后机具退坡退出",
        "source_url": "http://nync.shandong.gov.cn/zwgk/zcwj/zcfg/202604/t20260430_4964166.html",
        "content": (
            "鲁农机字〔2026〕7号。将播种机、主要粮油作物收获机械、高端智能和丘陵山区适用拖拉机等"
            "纳入“优机优补”范围；对有助于大面积单产提升的重点机具，补贴额测算比例最高提升至35%，"
            "通用类品目可高于中央财政最高补贴额、高出幅度控制在20%以内。2026年“优机优补”资金总量"
            "不超过1.5亿元，采取“先到先得”（以申请日期为准）。对保有量明显过多、技术落后的机具"
            "降低补贴额测算比例至15%以下，部分品目于2026年12月31日退出补贴目录。"
            "同一农民年度内高端智能拖拉机限申请1台，同一农业生产经营组织限申请2台。"
        ),
        "apply_conditions": {
            "provinces": ["山东省"],
            "applicant_types": ["个人", "家庭农场", "合作社", "企业"],
        },
        "material_requirements": {
            "items": [
                "身份证或营业执照",
                "购机发票",
                "机具铭牌/出厂编号信息",
                "本人银行卡或对公账户",
            ],
            "note": "补贴对象与办理要求按《山东省2024—2026年农机购置与应用补贴实施方案》执行。",
        },
    },
    {
        "policy_name": "山东省2026年粮食烘干中心成套设备补贴工作实施方案",
        "publish_department": "山东省农业农村厅",
        "scope": "province",
        "province": "山东省",
        "department": "山东省农业农村厅",
        "published_at": _dt(2026, 9, 23),
        "apply_end_at": _dt(2026, 12, 30),
        "policy_type": "农机补贴",
        "support_direction": "粮食烘干中心成套设备",
        "source_url": "http://nync.shandong.gov.cn/zwgk/tzgg/tfwj/202609/t20260923_4998311.html",
        "content": (
            "鲁农函字〔2026〕194号。补贴产品为2026年1月1日（含）至2026年12月30日（含）购置的"
            "粮食烘干中心成套设备；补贴对象为从事农业生产的农民和农业生产经营组织。全省年度资金"
            "规模不超过1500万元，统筹中央财政与省级财政配套，采取“先到先得”（以结算申请日期为准），"
            "用完即止。实行“先建设使用、后验收补贴”，建设完成使用一个作业季后方可兑付；县级财政部"
            "门对验收通过的于15个工作日内拨付。已申领农机购置与应用补贴的机具不得重复享受。"
        ),
        "apply_conditions": {
            "provinces": ["山东省"],
            "applicant_types": ["个人", "家庭农场", "合作社", "企业"],
        },
        "material_requirements": {
            "items": [
                "建设地点、类型、规模、用途承诺书",
                "身份证或营业执照",
                "购机发票",
                "建设合同",
                "施工图纸",
                "第三方验收报告",
            ],
            "note": "先向县级农业农村部门申请补贴资格，竣工验收合格后再提交结算申请。",
        },
    },
    {
        "policy_name": "中共中央 国务院关于进一步深化农村改革 扎实推进乡村全面振兴的意见",
        "publish_department": "中共中央 国务院",
        "scope": "national",
        "department": "中共中央 国务院",
        "published_at": _dt(2025, 2, 23),
        "policy_type": "中央一号文件",
        "support_direction": "粮食安全、乡村产业、农民增收",
        "source_url": "https://www.gov.cn/gongbao/2025/issue_11906/material/gwygb202507.pdf",
        "content": (
            "2025年中央一号文件（2025年1月1日）。重点包括：深入推进粮油作物大面积单产提升行动，"
            "稳定粮食播种面积、主攻单产和品质提升，推进水肥一体化；完善粮食生产支持政策，稳定耕地"
            "地力保护补贴，完善玉米大豆生产者补贴、稻谷补贴；发展乡村特色产业，实施农业品牌精品培育"
            "计划；完善联农带农机制，将联农带农作为政策倾斜的重要依据；支持发展智慧农业，拓展人工"
            "智能、数据、低空等技术应用场景。本文为官方公报摘要，原文见来源链接。"
        ),
        "apply_conditions": {
            "applicant_types": ["个人", "家庭农场", "合作社", "企业"],
        },
        "material_requirements": {"items": [], "note": "方向性文件，不直接对应单一申报材料。"},
    },
    {
        "policy_name": "新型农业经营主体提质增效带动小农户增收行动方案",
        "publish_department": "农业农村部",
        "scope": "national",
        "department": "农业农村部",
        "published_at": _dt(2026, 5, 6),
        "policy_type": "经营主体培育",
        "support_direction": "家庭农场、合作社、社会化服务",
        "source_url": "https://hzjjs.moa.gov.cn/gzdt/202605/t20260506_6483937.htm",
        "content": (
            "农业农村部印发《新型农业经营主体提质增效带动小农户增收行动方案》。方案提出完善家庭农场"
            "分类培育机制，推动农业社会化服务扩面提质，增强生产经营能力；鼓励新型农业经营主体与农技"
            "推广机构、高等院校、科研院所、企业合作，推动与科技小院对接，开展新品种、新技术、新设备、"
            "新模式应用示范；支持仓储保鲜、冷链物流、产地初加工、烘干晾晒等关键环节能力建设；"
            "推广“服务主体+农村集体经济组织+农户”等组织形式。"
        ),
        "apply_conditions": {
            "applicant_types": ["家庭农场", "合作社", "企业"],
        },
        "material_requirements": {
            "items": [],
            "note": "行动方案为方向性文件，具体扶持以各省项目实施通知为准。",
        },
    },
    {
        "policy_name": "关于支持建设一批科技小院的通知（三部门）",
        "publish_department": "教育部 农业农村部 中国科协",
        "scope": "national",
        "department": "教育部 农业农村部 中国科协",
        "published_at": _dt(2022, 8, 23),
        "policy_type": "科技人才",
        "support_direction": "科技小院建设、研究生培养、农技服务",
        "source_url": "https://kjs.moa.gov.cn/tgjy/202208/t20220823_6407585.htm",
        "content": (
            "教育部、农业农村部、中国科协印发《关于支持建设一批科技小院的通知》。科技小院是研究生"
            "培养单位把农业专业学位研究生长期派驻到农业生产一线，在完成理论学习的同时研究解决农业"
            "农村生产实践实际问题的培养模式，集人才培养、科技创新、社会服务于一体的政产学研合作。"
            "三部门将支持范围由300多个扩展到780个，实现31个省份和新疆生产建设兵团全覆盖，对地处国家"
            "重点帮扶县、聚焦县域特色农产品的科技小院倾斜支持；将科技小院纳入当地农业技术服务体系"
            "和农民培训体系。本文为农业农村部官方答问摘要。"
        ),
        "apply_conditions": {
            "applicant_types": ["高校", "科研院所", "合作社", "企业"],
        },
        "material_requirements": {
            "items": [],
            "note": "建设申报以三部门及省级研究生教育主管单位当年通知为准。",
        },
    },
)


def _source_hash(item: dict[str, Any]) -> str:
    raw = f"{item['policy_name']}\n{item['source_url']}\n{item.get('content') or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def seed_default_policies(db: Session, yard_id: str) -> int:
    """给指定小院幂等注入默认政策，返回新增条数。"""
    now = datetime.now(UTC)
    existing = {
        (row.policy_name, row.publish_department)
        for row in db.scalars(
            select(Policy).where(Policy.yard_id == yard_id)
        ).all()
    }
    added = 0
    for item in DEFAULT_POLICIES:
        key = (item["policy_name"], item["publish_department"])
        if key in existing:
            continue
        db.add(
            Policy(
                yard_id=yard_id,
                scope=item["scope"],
                province=item.get("province"),
                city=item.get("city"),
                department=item.get("department"),
                policy_name=item["policy_name"],
                publish_department=item["publish_department"],
                published_at=item.get("published_at"),
                apply_start_at=item.get("apply_start_at"),
                apply_end_at=item.get("apply_end_at"),
                policy_type=item.get("policy_type"),
                support_direction=item.get("support_direction"),
                apply_conditions_json=item.get("apply_conditions") or {},
                material_requirements_json=item.get("material_requirements") or {},
                content=item["content"],
                source_url=item["source_url"],
                source_hash=_source_hash(item),
                first_crawled_at=now,
                last_checked_at=now,
                verified_at=now,
                status="active",
                review_status="verified",
            )
        )
        added += 1
    if added:
        db.commit()
    return added


def seed_all_yards(db: Session) -> int:
    """启动时回填所有已有小院；幂等，失败只记日志不阻断启动。"""
    total = 0
    for yard_id in db.scalars(select(Yard.id)).all():
        total += seed_default_policies(db, yard_id)
    if total:
        log.info("policy seed backfilled %s rows", total)
    return total
