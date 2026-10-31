"""Agent 工厂选角框架与联网情报的单元测试（3.34）。

覆盖口径：
- 不同行业描述得到不同的角色组合与数量（不再是固定四角色），且都满足
  3-6 个角色、审核唯一且最后；
- 水产描述不会误选种植植保；未知行业落通用框架；
- 联网情报默认关闭/失败时诚实降级（used=False + reason），成功时去重并
  生成可溯源来源；情报文本只增强匹配，不改变画像。
"""

from __future__ import annotations

from app.core.errors import AppError
from app.modules.agents import factory_frameworks as ff
from app.modules.agents import factory_research as fr
from app.modules.agents.factory_blueprint import heuristic_blueprint


def _role_names(items: list[dict]) -> list[str]:
    return [str(i["name"]).split("（")[0] for i in items]


def _assert_workflow_shape(items: list[dict]) -> None:
    assert 3 <= len(items) <= 6
    stages = [i["stage"] for i in items]
    assert stages == sorted(stages, key=lambda s: ff.STAGE_ORDER[s])
    assert stages[-1] == ff.STAGE_REVIEW
    assert stages.count(ff.STAGE_REVIEW) == 1


def test_different_briefs_get_different_role_frameworks():
    briefs = [
        "合作社种大棚果蔬，要解决病虫害和提质增产",
        "棉花种植大户，需要农机作业与维修支持",
        "山区果园通过电商卖苹果，做品牌和渠道",
        "茶叶初制加工厂，要做仓储和品质管理",
    ]
    signatures = []
    for brief in briefs:
        items = heuristic_blueprint(brief)
        _assert_workflow_shape(items)
        signatures.append(tuple(sorted(_role_names(items))))
    assert len(set(signatures)) == len(signatures)

    crop_roles = set(signatures[0])
    machinery_roles = set(signatures[1])
    market_roles = set(signatures[2])
    assert {"种植技术顾问", "田间执行官"} <= crop_roles
    assert {"设备诊断师", "运维工程师"} <= machinery_roles
    assert {"市场行情分析师", "渠道运营师"} <= market_roles


def test_aquaculture_does_not_borrow_plant_protection():
    items = heuristic_blueprint("小龙虾养殖户，想解决病害和水质问题")
    _assert_workflow_shape(items)
    names = set(_role_names(items))
    assert {"水质与养殖技术员", "养殖执行官"} <= names
    assert not names & {"病虫害诊断师", "防治方案师"}
    # 画像里只留「小龙虾」，不重复计子串「龙虾」
    profile, _ = ff.parse_brief("小龙虾养殖户，想解决病害和水质问题")
    assert profile.label == "小龙虾"
    assert "plant_protection" not in profile.matched


def test_unknown_industry_falls_back_to_general_framework():
    items = heuristic_blueprint("城市供水调度中心，需要梳理流程")
    _assert_workflow_shape(items)
    assert set(_role_names(items)) == {
        "需求分析师",
        "方案设计师",
        "执行协调员",
        "质检审核员",
    }


def test_industry_label_cuts_at_punctuation():
    brief = "我们合作社种果蔬，想解决大棚病虫害和提质增产的问题"
    assert ff.industry_label(brief) == "我们合作社种果蔬"
    assert ff.industry_label("   ") == "行业"
    assert len(ff.industry_label("很长的描述" * 20)) <= 16


def test_research_disabled_returns_reason(monkeypatch):
    monkeypatch.setenv("FACTORY_RESEARCH_DISABLED", "1")
    result = fr.run_research(None, "yard-1", "果园电商卖苹果", "渠道")
    assert result["used"] is False
    assert "关闭" in result["reason"]
    assert result["sources"] == []


def test_research_collects_deduped_sources(monkeypatch):
    monkeypatch.delenv("FACTORY_RESEARCH_DISABLED", raising=False)
    calls: list[str] = []

    async def fake_search(db, yard_id, query, count=5, lang="zh-CN"):
        calls.append(query)
        return {
            "query": query,
            "provider": "bing",
            "results": [
                {
                    "title": "苹果电商渠道打法",
                    "url": "https://example.com/a",
                    "snippet": "分级包装与冷链",
                    "source": "bing",
                    "published": None,
                },
                {
                    "title": "重复链接",
                    "url": "https://example.com/a",
                    "snippet": "去重",
                    "source": "bing",
                    "published": None,
                },
                {
                    "title": "果园品牌案例",
                    "url": "https://example.com/b",
                    "snippet": "区域公用品牌",
                    "source": "bing",
                    "published": None,
                },
            ],
        }

    monkeypatch.setattr("app.modules.search.service.search_web", fake_search)
    result = fr.run_research(None, "yard-1", "山区果园电商卖苹果，做品牌和渠道", "重点做复购")
    assert result["used"] is True
    assert result["provider"] == "bing"
    assert [s["url"] for s in result["sources"]] == [
        "https://example.com/a",
        "https://example.com/b",
    ]
    assert "苹果电商渠道打法" in result["summary"]
    assert calls and ("果园" in calls[0] or "苹果" in calls[0])
    assert len(calls) <= fr.RESEARCH_MAX_QUERIES
    assert "苹果电商渠道打法" in fr.research_text(result)


def test_research_failure_degrades_honestly(monkeypatch):
    monkeypatch.delenv("FACTORY_RESEARCH_DISABLED", raising=False)

    async def broken(*args, **kwargs):
        raise AppError("SEARCH_PROVIDER_UNAVAILABLE", "免费搜索源暂不可用。", http_status=503)

    monkeypatch.setattr("app.modules.search.service.search_web", broken)
    result = fr.run_research(None, "yard-1", "果园电商卖苹果")
    assert result["used"] is False
    assert "免费搜索源暂不可用" in result["reason"]
    assert fr.research_text(result) == ""


def test_research_text_boosts_framework_matching():
    profile, picked = ff.compose_roles(
        "山区特色农产品，想扩大销路",
        None,
        research_text="电商直播 渠道 订单 品牌 推广",
    )
    codes = {role.code for _, role in picked}
    assert codes & {"market_analyst", "channel_operator", "brand_planner"}
    assert profile.business == ""
    _assert_workflow_shape(
        [
            {"stage": role.stage}
            for _, role in sorted(picked, key=lambda item: ff.STAGE_ORDER.get(item[1].stage, 50))
        ]
    )
