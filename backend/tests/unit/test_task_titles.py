"""任务可读名称（v3.29）：类型中文名 + 输入主体，历史与新建任务同口径。"""

from datetime import UTC, datetime

from app.modules.tasks.titles import TASK_TYPE_LABELS, task_subject, task_title


def _wrap(data: dict) -> dict:
    return {"data": data, "meta": {}}


def test_title_combines_label_and_subject():
    assert (
        task_title("knowledge_qa", _wrap({"question": "水稻分蘖期怎么控旺？"}))
        == "知识问答：水稻分蘖期怎么控旺？"
    )
    assert (
        task_title("agent_generate", _wrap({"utterance": "我需要一个农机维修专家"}))
        == "一句话生成智能体：我需要一个农机维修专家"
    )
    assert task_title("workorder.dispatch", _wrap({"title": "3 号棚土壤湿度过低"})) == (
        "工单派发：3 号棚土壤湿度过低"
    )


def test_subject_prefers_utterance_over_other_keys():
    got = task_subject(_wrap({"title": "标题", "question": "问题", "utterance": "需求"}))
    assert got == "需求"


def test_subject_truncates_long_text():
    subject = task_subject(_wrap({"utterance": "需" * 40}))
    assert subject.endswith("…")
    assert len(subject) == 25  # 24 字 + 省略号


def test_subject_collapses_whitespace():
    assert task_subject(_wrap({"question": "  分蘖   期\n管理  "})) == "分蘖 期 管理"


def test_subject_reads_display_meta_when_data_has_no_subject():
    """班组接力把展示信息放 meta.display：data 只有 run_id（身份校验用），标题仍可读。"""
    payload = {
        "data": {"run_id": "run-1"},
        "meta": {"display": {"team_name": "植保组", "question": "稻瘟病怎么处理"}},
    }
    assert task_subject(payload) == "稻瘟病怎么处理"
    assert task_title("team_run", payload) == "班组接力：稻瘟病怎么处理"


def test_unknown_type_keeps_raw_code():
    assert task_title("custom.task", _wrap({})) == "custom.task"
    assert "custom.task" in TASK_TYPE_LABELS.get("custom.task", "custom.task")


def test_missing_or_malformed_input_falls_back_to_label():
    assert task_title("mock.echo", None) == "回声测试"
    assert task_title("mock.echo", {}) == "回声测试"
    assert task_title("mock.echo", {"data": "not-a-dict"}) == "回声测试"


def test_task_out_exposes_title_and_type_label():
    from app.modules.tasks.schemas import TaskOut

    now = datetime.now(UTC)
    out = TaskOut(
        id="t1",
        yard_id="y1",
        created_by="u1",
        task_type="team_run",
        status="queued",
        priority="normal",
        input_json=_wrap({"team_name": "植保组", "question": "稻瘟病怎么处理"}),
        created_at=now,
        updated_at=now,
    )
    assert out.task_type_label == "班组接力"
    # 问题比班组名更能说清「这条任务在做什么」，优先级更高
    assert out.title == "班组接力：稻瘟病怎么处理"
    dumped = out.model_dump()
    assert dumped["title"] == "班组接力：稻瘟病怎么处理"
    assert dumped["task_type_label"] == "班组接力"
