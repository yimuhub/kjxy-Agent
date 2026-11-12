"""向导第 4 步的技能目录与「按产业画像推荐」（审查 D-13，P1）。

背景
----
向导第 4 步一次性列出全部 35 个内置技能并**默认全选**。其中 11 个属于通用
办公技能（6 个还是 PPT 专项），对一个县级葡萄/水稻科技小院是纯噪声——小白
看到 35 个「不认识的名字」，会直接怀疑「这产品跟我没关系」（D-13 实测）。

本模块对应审查报告给出的三条建议
--------------------------------
1. **分场景推荐**：每个技能声明自己适用的小院画像（``scenes``），再按用户
   已选画像算出「推荐预选集合」，只把真正相关的默认勾上；
2. **搜索 + 分类**：目录按 ``group``（农技 / 增长与服务 / 通用办公）分组，
   并带 ``keywords``（取自技能自身标签）供前端检索；
3. **画像提示**：返回 ``profile_labels`` 与 ``recommended_count``，前端直接
   显示「已按你的产业画像（应时鲜果）预选 17 个」。

两条硬约束
----------
* **「通用（general）」不参与自动推荐。**  ``initialize()`` 会给画像补上
  ``general``，若把它算进推荐，所有用户都会被预选一批通用办公技能——正是
  D-13 要消灭的行为。因此推荐只看用户在向导第 3 步**显式勾选**的画像。
* **PPT 专项技能永不自动预选。** 它们是「通用办公」里的可选项，不是小院
  的核心能力；默认勾上只会让第一屏变成一片陌生名词。

纯数据 + 纯函数，不查库、不写库。
"""

from __future__ import annotations

from typing import Any

#: 产业画像白名单（与 app.modules.market.official_catalog.YARD_TYPES 对齐）
AG_PROFILE_TYPES: tuple[str, ...] = (
    "rice",
    "aquaculture",
    "fruit",
    "vegetable",
    "tea",
    "livestock",
    "smart_agri",
)

GENERAL_PROFILE = "general"

#: 画像中文标签（前端展示用）
PROFILE_LABELS: dict[str, str] = {
    "rice": "水稻 / 稻麦",
    "aquaculture": "水产蟹虾",
    "fruit": "应时鲜果",
    "vegetable": "蔬菜瓜果",
    "tea": "茶叶",
    "livestock": "畜禽",
    "smart_agri": "智慧农业 / 农文旅",
    "general": "通用",
}

#: 目录分组（前端分类筛选的顺序）
GROUP_ORDER: tuple[str, ...] = ("agri", "growth", "office")
GROUP_LABELS: dict[str, str] = {
    "agri": "农技",
    "growth": "增长与服务",
    "office": "通用办公",
}

#: 自动预选总量上限。画像再宽也不超过这个数——避免「勾了几个画像就又回到
#: 一片不认识的名字」。超出时按优先级（农技 → 增长 → 办公）截断。
RECOMMEND_CAP = 24

_AG = AG_PROFILE_TYPES
#: 生鲜/茶叶类画像：适合「品牌-卖点-定价-渠道-复购」这套经营技能
_COMMERCE = ("fruit", "vegetable", "tea", "smart_agri", GENERAL_PROFILE)
#: 活动/内容类技能：主要服务农文旅与综合型小院
_EVENT = ("smart_agri",)
#: 通用办公（报告/台账）：只在用户显式选择「通用」或农文旅时才有意义
_OFFICE_CORE = (GENERAL_PROFILE, "smart_agri")
#: PPT 专项：仅展示，永不自动预选
_OFFICE_PPT = (GENERAL_PROFILE, "smart_agri")

#: 技能推荐元数据。
#:
#: - ``scenes``：该技能适用的小院画像（展示 + 匹配用）
#: - ``auto``：是否参与自动预选
#: - ``priority``：推荐优先级，1 农技 → 2 增长 → 3 办公（数值小的先保留）
SKILL_META: dict[str, dict[str, Any]] = {
    # ---- 农技（12）：所有农业画像的基本功，优先级 1 ----
    "agri_soil_health": {"scenes": _AG, "auto": True, "priority": 1},
    "agri_irrigation": {"scenes": _AG, "auto": True, "priority": 1},
    "agri_pest_diagnosis": {"scenes": _AG, "auto": True, "priority": 1},
    "agri_pesticide_safety": {"scenes": _AG, "auto": True, "priority": 1},
    "agri_harvest_storage": {"scenes": _AG, "auto": True, "priority": 1},
    "agri_machinery": {
        "scenes": ("rice", "vegetable", "fruit", "smart_agri"),
        "auto": True,
        "priority": 1,
    },
    "agri_field_log": {"scenes": _AG, "auto": True, "priority": 1},
    "agri_farm_calendar": {"scenes": _AG, "auto": True, "priority": 1},
    "agri_market_price": {"scenes": _AG, "auto": True, "priority": 1},
    "agri_policy_search": {"scenes": _AG, "auto": True, "priority": 1},
    "agri_crop_nutrition": {
        "scenes": ("rice", "vegetable", "fruit", "tea", "smart_agri"),
        "auto": True,
        "priority": 1,
    },
    "agri_climate_risk": {"scenes": _AG, "auto": True, "priority": 1},
    # ---- 增长与服务（12）：经营/活动类，优先级 2 ----
    "growth_brand_story": {"scenes": _COMMERCE, "auto": True, "priority": 2},
    "growth_selling_points": {"scenes": _COMMERCE, "auto": True, "priority": 2},
    "growth_pricing": {"scenes": _COMMERCE, "auto": True, "priority": 2},
    "growth_channel_plan": {"scenes": _COMMERCE, "auto": True, "priority": 2},
    "growth_repurchase_plan": {"scenes": _COMMERCE, "auto": True, "priority": 2},
    "growth_content_calendar": {"scenes": _EVENT, "auto": True, "priority": 2},
    "growth_event_plan": {"scenes": _EVENT, "auto": True, "priority": 2},
    "growth_invitation": {"scenes": _EVENT, "auto": True, "priority": 2},
    "growth_live_script": {"scenes": _EVENT, "auto": True, "priority": 2},
    "growth_event_review": {"scenes": _EVENT, "auto": True, "priority": 2},
    "growth_poster_prompt": {"scenes": _EVENT, "auto": True, "priority": 2},
    "growth_poster_render": {"scenes": _EVENT, "auto": True, "priority": 2},
    # ---- 通用办公（11）：优先级 3；PPT 专项 auto=False（永不自动预选）----
    "office_word_report": {"scenes": _OFFICE_CORE, "auto": True, "priority": 3},
    "office_excel_table": {"scenes": _OFFICE_CORE, "auto": True, "priority": 3},
    "office_weekly_report": {"scenes": _OFFICE_CORE, "auto": True, "priority": 3},
    "office_data_clean": {"scenes": _OFFICE_CORE, "auto": True, "priority": 3},
    "office_doc_review": {"scenes": _OFFICE_CORE, "auto": True, "priority": 3},
    "office_ppt_deck": {"scenes": _OFFICE_PPT, "auto": False, "priority": 4},
    "office_ppt_storyline": {"scenes": _OFFICE_PPT, "auto": False, "priority": 4},
    "office_ppt_design": {"scenes": _OFFICE_PPT, "auto": False, "priority": 4},
    "office_ppt_chart": {"scenes": _OFFICE_PPT, "auto": False, "priority": 4},
    "office_ppt_review": {"scenes": _OFFICE_PPT, "auto": False, "priority": 4},
    "office_ppt_template": {"scenes": _OFFICE_PPT, "auto": False, "priority": 4},
}

#: 兜底元数据：不在表里的技能只展示、不预选
_DEFAULT_META: dict[str, Any] = {"scenes": (), "auto": False, "priority": 9}


def normalize_types(profile_types: list[str] | None) -> list[str]:
    """去重 + 去空白的画像列表（保持用户选择顺序）。"""
    out: list[str] = []
    for raw in profile_types or []:
        key = str(raw).strip()
        if key and key not in out:
            out.append(key)
    return out


def profile_labels(profile_types: list[str]) -> list[str]:
    """画像 key → 中文标签（未知 key 原样返回，避免前端出现空白）。"""
    return [PROFILE_LABELS.get(key, key) for key in normalize_types(profile_types)]


def meta_for(code: str) -> dict[str, Any]:
    """取技能推荐元数据（未登记的技能返回兜底值）。"""
    return SKILL_META.get(str(code), _DEFAULT_META)


def recommend_codes(catalog: list[dict[str, Any]], profile_types: list[str]) -> list[str]:
    """按已选画像算出「推荐预选」的技能 code 列表。

    规则：场景（``scenes``）与已选画像有交集、且允许自动预选（``auto``）的
    技能进入候选；按优先级（农技 → 增长 → 办公）与目录顺序排序后，
    截断到 :data:`RECOMMEND_CAP`。
    """
    selected = set(normalize_types(profile_types))
    if not selected:
        return []
    scored: list[tuple[int, int, str]] = []
    for index, item in enumerate(catalog):
        code = str(item.get("code") or "")
        meta = meta_for(code)
        if not meta.get("auto"):
            continue
        scenes = {str(s) for s in (meta.get("scenes") or ())}
        if not (scenes & selected):
            continue
        scored.append((int(meta.get("priority", 9)), index, code))
    scored.sort()
    return [code for _, _, code in scored[:RECOMMEND_CAP]]


def build_payload(
    catalog: list[dict[str, Any]],
    profile_types: list[str],
) -> dict[str, Any]:
    """组装向导第 4 步的技能清单载荷。

    返回：``items``（含分组/关键词/推荐标记）、``groups``（分类统计）、
    ``recommended_codes`` / ``recommended_count``（预选集合）、
    ``profile_types`` / ``profile_labels``（画像回显）。
    """
    types = normalize_types(profile_types)
    recommended = set(recommend_codes(catalog, types))

    items: list[dict[str, Any]] = []
    for item in catalog:
        code = str(item.get("code") or "")
        meta = meta_for(code)
        items.append(
            {
                "code": code,
                "name": str(item.get("name") or ""),
                "description": str(item.get("description") or ""),
                "pack": str(item.get("pack") or ""),
                "group": str(item.get("group") or "office"),
                "keywords": [str(k) for k in (item.get("keywords") or [])],
                "scenes": [str(s) for s in (meta.get("scenes") or ())],
                "recommended": code in recommended,
            }
        )

    groups: list[dict[str, Any]] = []
    for key in GROUP_ORDER:
        group_items = [i for i in items if i["group"] == key]
        if not group_items:
            continue
        groups.append(
            {
                "key": key,
                "label": GROUP_LABELS.get(key, key),
                "total": len(group_items),
                "recommended": sum(1 for i in group_items if i["recommended"]),
            }
        )

    return {
        "items": items,
        "total": len(items),
        "groups": groups,
        "recommended_codes": sorted(recommended),
        "recommended_count": len(recommended),
        "profile_types": types,
        "profile_labels": profile_labels(types),
    }


__all__ = [
    "AG_PROFILE_TYPES",
    "GENERAL_PROFILE",
    "GROUP_LABELS",
    "GROUP_ORDER",
    "PROFILE_LABELS",
    "RECOMMEND_CAP",
    "SKILL_META",
    "build_payload",
    "meta_for",
    "normalize_types",
    "profile_labels",
    "recommend_codes",
]
