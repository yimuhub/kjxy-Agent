"""对话意图识别单测：保守优先，宁可漏判不可误判。

口径：
- create 必须同时命中"创建动词"与"能力名词"；
- 疑问词降级为提问（咨询不是指令）；
- install 走市场安装；
- 其余一律提问（零回归）。
"""

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
    got = intent.detect("生成一份本周工作总结")
    assert got.kind == intent.INTENT_QA


def test_noun_without_verb_stays_qa():
    got = intent.detect("我需要一个助手")
    assert got.kind == intent.INTENT_QA


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
