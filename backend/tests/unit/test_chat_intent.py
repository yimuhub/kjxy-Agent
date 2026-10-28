"""对话意图识别单测：保守优先，宁可漏判不可误判。

口径：
- create 必须同时命中"创建动词"与"能力名词"；
- 疑问词降级为提问（咨询不是指令）；
- install 走市场安装；
- skill 必须同时命中"技能名词（技能/技能包/skill）"与"请求动词"，
  且 agent 名词（助手/智能体…）优先，不抢占既有能力链路；
- doc 必须同时命中"产出动词"与"文档名词"（能力名词优先）；
- 其余一律提问（零回归）。
"""

import pytest
from app.modules.chat import intent


def test_empty_input_is_qa():
    got = intent.detect("   ")
    assert got.kind == intent.INTENT_QA
    assert got.is_action is False


def test_plain_question_is_qa():
    got = intent.detect("今天地里需要浇水吗")
    assert got.kind == intent.INTENT_QA


def test_create_verb_and_noun_hits_create():
    got = intent.detect("创建一个「农机维修问答」助手")
    assert got.kind == intent.INTENT_CREATE
    assert got.target == "agent"
    assert got.is_action is True


def test_team_noun_targets_team():
    got = intent.detect("搭一个病虫害会诊班组")
    assert got.kind == intent.INTENT_CREATE
    assert got.target == "team"


def test_verb_without_noun_stays_qa():
    """只命中产出动词、没有能力/文档名词 → 仍是提问。"""
    got = intent.detect("生成一份天气情况")
    assert got.kind == intent.INTENT_QA


def test_summary_request_is_document():
    got = intent.detect("生成一份本周工作总结")
    assert got.kind == intent.INTENT_DOC
    assert got.target == "document"
    assert got.is_action is True


def test_document_format_request_is_doc():
    got = intent.detect("帮我生成一份设备巡检报告，用 Word 格式")
    assert got.kind == intent.INTENT_DOC


def test_write_weekly_report_is_doc():
    got = intent.detect("写个周报")
    assert got.kind == intent.INTENT_DOC


def test_document_question_downgrades_to_qa():
    got = intent.detect("这份报告怎么改")
    assert got.kind == intent.INTENT_QA


def test_capability_noun_wins_over_document():
    got = intent.detect("生成一个巡检助手")
    assert got.kind == intent.INTENT_CREATE


def test_noun_without_verb_stays_qa():
    got = intent.detect("这个助手不错")
    assert got.kind == intent.INTENT_QA


def test_request_prefix_with_agent_noun_is_create():
    """ "我需要一个助手" = 要造能力（请求前缀与创建动词同效）。"""
    got = intent.detect("我需要一个助手")
    assert got.kind == intent.INTENT_CREATE
    assert got.target == "agent"


def test_question_word_downgrades_to_qa():
    got = intent.detect("怎么创建一个助手")
    assert got.kind == intent.INTENT_QA
    assert "疑问词" in got.reason


def test_install_verb_goes_market():
    got = intent.detect("安装水稻病虫害识别助手")
    assert got.kind == intent.INTENT_INSTALL
    assert got.target == "market"


def test_install_checked_before_create():
    got = intent.detect("装载一个设备巡检技能")
    assert got.kind == intent.INTENT_INSTALL


def test_english_agent_noun_is_lowercased():
    got = intent.detect("Build an Agent for irrigation")
    assert got.kind == intent.INTENT_QA  # 无中文创建动词 → 不误判


# ---------------------------------------------------------------------------
# 技能链路（v3.2 一句话要技能）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "我需要一个病虫害防治技能",
        "帮我做一个病虫害防治技能",
        "给我来一个技能包",
        "生成一个巡检 skill",
        "创建一个技能",
        "帮我做一个 Skill",
        "我需要一个技能",
        "来一个病虫害技能",
        "给我一个技能包",
    ],
)
def test_skill_noun_with_request_verb_is_skill(text):
    got = intent.detect(text)
    assert got.kind == intent.INTENT_SKILL
    assert got.target == "skill"
    assert got.is_action is True


def test_skill_equals_install_priority():
    """装载动词优先：要"装"就走市场安装，不生成草稿。"""
    got = intent.detect("帮我安装一个病虫害技能")
    assert got.kind == intent.INTENT_INSTALL
    assert got.target == "market"
    got = intent.detect("装一个病虫害 skill")
    assert got.kind == intent.INTENT_INSTALL


def test_skill_question_downgrades_to_qa():
    got = intent.detect("怎么创建一个技能")
    assert got.kind == intent.INTENT_QA
    assert "疑问词" in got.reason


def test_agent_noun_wins_over_skill_noun():
    """混合句里出现助手/智能体 → 仍走既有 create 链路。"""
    got = intent.detect("帮我做一个病虫害技能助手")
    assert got.kind == intent.INTENT_CREATE
    assert got.target == "agent"


def test_plugin_noun_with_request_verb_is_plugin():
    """v3.3：命中插件名词 + 请求动词 → 插件生成（一律待审）。"""
    for text in (
        "帮我做一个浇灌决策插件",
        "我需要一个设备巡检插件",
        "做一个病虫害防治plugin",
    ):
        got = intent.detect(text)
        assert got.kind == intent.INTENT_PLUGIN, text


def test_workflow_word_does_not_trigger_plugin():
    """口语里的"工作流"常指既有业务流程，误判代价高，不纳入插件名词。"""
    got = intent.detect("帮我梳理一下灌溉审批的工作流")
    assert got.kind != intent.INTENT_PLUGIN


def test_plugin_question_downgrades_to_qa():
    got = intent.detect("插件是什么")
    assert got.kind == intent.INTENT_QA


def test_plugin_install_verb_goes_market():
    got = intent.detect("帮我安装一个病虫害插件")
    assert got.kind == intent.INTENT_INSTALL


def test_skill_noun_wins_over_plugin_noun():
    """同时出现技能与插件名词时，技能链路优先（含插件检索的混合句）。"""
    got = intent.detect("我需要一个病虫害防治技能插件")
    assert got.kind == intent.INTENT_SKILL


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("这道题怎么做", intent.INTENT_QA),
        ("帮我安装一个病虫害技能", intent.INTENT_INSTALL),
        ("我需要一个助手", intent.INTENT_CREATE),
        ("装载一个设备巡检技能", intent.INTENT_INSTALL),
        ("这个技能不错", intent.INTENT_QA),
        ("生成一个巡检助手", intent.INTENT_CREATE),
        ("生成一份本周工作总结", intent.INTENT_DOC),
    ],
)
def test_skill_negative_controls(text, expected):
    assert intent.detect(text).kind == expected


def test_plan_request_is_plan():
    """「写一份…方案」要的是当场可见的完整方案，不再进文档链路（v3.14）。"""
    got = intent.detect("写一份小麦赤霉病的防治方案")
    assert got.kind == intent.INTENT_PLAN
    assert got.target == "plan"
    assert got.is_action is True


@pytest.mark.parametrize(
    "text",
    [
        "制定明年基地扩建规划",
        "给出水稻水分管理的技术路线",
        "写一份秋收实施计划",
    ],
)
def test_plan_nouns_route_to_plan(text):
    assert intent.detect(text).kind == intent.INTENT_PLAN


def test_plan_with_format_hint_stays_document():
    """显式点名文件格式时仍按文档产出（方案名词让位给格式词）。"""
    assert intent.detect("写一份防治方案，用 Word 格式").kind == intent.INTENT_DOC
    assert intent.detect("把这份方案导出成文件").kind == intent.INTENT_DOC


def test_plan_question_downgrades_to_qa():
    assert intent.detect("防治方案怎么写").kind == intent.INTENT_QA


def test_capability_noun_wins_over_plan_noun():
    assert intent.detect("我需要一个病虫害防治方案助手").kind == intent.INTENT_CREATE


def test_unrelated_request_is_not_plan():
    assert intent.detect("地里今天需要做什么").kind == intent.INTENT_QA
