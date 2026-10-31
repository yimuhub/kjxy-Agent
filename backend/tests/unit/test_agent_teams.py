"""班组服务层单元测试（W2）。

覆盖（纯服务层，不起 HTTP）：
- members 规整：默认值/字段拒绝/数量 2..8 边界；
- create_team：同小院重名 409、跨小院成员 404、成员顺序与 critic 标记落库；
- update_team/delete_team 基本语义；
- 接力拼接纯函数 build_member_question（前序段格式、截断、critic 指令）；
- 答案拼接 compose_answer（末位 critic / critic 在中间并入 / 无 critic）；
- 工厂蓝图 → 班组成员：蓝图顺序、goal 摘要、末位审核角色自动 is_critic。
"""

import pytest
from app.core.deps import YardContext
from app.core.errors import AppError
from app.modules.agents import declarative
from app.modules.agents.declarative import DeclarativeAgentCreate
from app.modules.agents.teams import service
from app.modules.agents.teams.models import AgentTeam
from app.modules.yards.repository import create_yard

REQ_ID = "req-unit-teams"


def _ctx(yard_id: str, user_id: str = "u-team") -> YardContext:
    return YardContext(yard_id=yard_id, user_id=user_id, roles=["yard_admin"], request_id=REQ_ID)


def _mk_agent(db, yard_id: str, code: str, name: str = "助手") -> str:
    """服务层直建已发布声明式 Agent（绕开 HTTP；本模块测的不是发布链路）。"""
    req = DeclarativeAgentCreate(
        agent_code=code,
        name=name,
        persona_prompt="你是测试助手。",
        task_prompt_template="请回答：{{question}}",
        knowledge_scopes=["yard"],
    )
    agent = declarative.create_agent(
        db, yard_id=yard_id, actor_id="u-team", req=req, request_id=REQ_ID
    )
    db.commit()
    return agent.id


@pytest.fixture()
def yard(db_session):
    t = create_yard(db_session, name="班组单测小院")
    db_session.commit()
    return t


@pytest.fixture()
def other(db_session):
    t = create_yard(db_session, name="隔壁单测小院")
    db_session.commit()
    return t


# ---------------------------------------------------------------------------
# members 规整
# ---------------------------------------------------------------------------


def test_parse_members_defaults_and_order():
    members = service.parse_members(
        [
            {"agent_id": "a-1", "role_name": "解读员", "instruction": "读数据"},
            {"agent_id": "a-2", "role_name": "审核员", "is_critic": True},
        ]
    )
    assert [m["agent_id"] for m in members] == ["a-1", "a-2"]  # 顺序即接力顺序
    assert members[0]["instruction"] == "读数据" and members[0]["is_critic"] is False
    assert members[1]["instruction"] == "" and members[1]["is_critic"] is True


def test_parse_members_count_bounds():
    one = [{"agent_id": "a", "role_name": "r"}]
    with pytest.raises(AppError) as e1:
        service.parse_members(one)
    assert e1.value.code == "TEAM_MEMBER_COUNT" and e1.value.http_status == 400
    nine = [{"agent_id": f"a{i}", "role_name": f"r{i}"} for i in range(9)]
    with pytest.raises(AppError) as e2:
        service.parse_members(nine)
    assert e2.value.code == "TEAM_MEMBER_COUNT"


def test_parse_members_field_rejections():
    # 空白 role_name / 未知字段 → TEAM_MEMBERS_INVALID
    with pytest.raises(AppError) as e:
        service.parse_members(
            [{"agent_id": "a", "role_name": "  "}, {"agent_id": "b", "role_name": "r"}]
        )
    assert e.value.code == "TEAM_MEMBERS_INVALID"
    with pytest.raises(AppError) as e2:
        service.parse_members(
            [
                {"agent_id": "a", "role_name": "r", "yard_id": "hack"},
                {"agent_id": "b", "role_name": "r"},
            ]
        )
    assert e2.value.code == "TEAM_MEMBERS_INVALID"


# ---------------------------------------------------------------------------
# CRUD（服务层）
# ---------------------------------------------------------------------------


def test_create_team_validates_agents_and_stores_order(db_session, yard, other):
    a1 = _mk_agent(db_session, yard.id, "t_a1", "解读员")
    a2 = _mk_agent(db_session, yard.id, "t_a2", "审核员")
    foreign = _mk_agent(db_session, other.id, "t_foreign", "外人")

    # 跨小院成员 → 404（防枚举，不泄露"存在但无权"）
    with pytest.raises(AppError) as e:
        service.create_team(
            db_session,
            yard_id=yard.id,
            actor_id="u-team",
            name="混队",
            members=[{"agent_id": a1, "role_name": "r1"}, {"agent_id": foreign, "role_name": "r2"}],
            request_id=REQ_ID,
        )
    assert e.value.code == "AGENT_NOT_FOUND" and e.value.http_status == 404
    db_session.rollback()

    team = service.create_team(
        db_session,
        yard_id=yard.id,
        actor_id="u-team",
        name="棚室管家",
        members=[
            {"agent_id": a1, "role_name": "解读员"},
            {"agent_id": a2, "role_name": "审核员", "is_critic": True},
        ],
        request_id=REQ_ID,
    )
    db_session.commit()
    assert [m["role_name"] for m in team.members_json] == ["解读员", "审核员"]
    assert team.members_json[1]["is_critic"] is True

    # 同小院重名 → 409
    with pytest.raises(AppError) as dup:
        service.create_team(
            db_session,
            yard_id=yard.id,
            actor_id="u-team",
            name="棚室管家",
            members=[{"agent_id": a1, "role_name": "x"}, {"agent_id": a2, "role_name": "y"}],
            request_id=REQ_ID,
        )
    assert dup.value.code == "TEAM_NAME_CONFLICT" and dup.value.http_status == 409

    # 他小院不可见 → 404
    with pytest.raises(AppError) as cross:
        service.get_team(db_session, yard_id=other.id, team_id=team.id)
    assert cross.value.code == "TEAM_NOT_FOUND"


def test_update_and_delete_team(db_session, yard):
    # 同小院助手名唯一（uq_yard_agents_yard_name）：两名必须不同
    a1 = _mk_agent(db_session, yard.id, "t_u1", "助手一")
    a2 = _mk_agent(db_session, yard.id, "t_u2", "助手二")
    team = service.create_team(
        db_session,
        yard_id=yard.id,
        actor_id="u-team",
        name="编辑队",
        members=[{"agent_id": a1, "role_name": "r1"}, {"agent_id": a2, "role_name": "r2"}],
        request_id=REQ_ID,
    )
    db_session.commit()
    updated = service.update_team(
        db_session,
        yard_id=yard.id,
        actor_id="u-team",
        team_id=team.id,
        fields={"enabled": False, "description": "停用演示"},
        request_id=REQ_ID,
    )
    db_session.commit()
    assert updated.enabled is False and updated.description == "停用演示"

    # 成员整单替换也要重校验
    with pytest.raises(AppError) as bad:
        service.update_team(
            db_session,
            yard_id=yard.id,
            actor_id="u-team",
            team_id=team.id,
            fields={"members": [{"agent_id": a1, "role_name": "r1"}]},
            request_id=REQ_ID,
        )
    assert bad.value.code == "TEAM_MEMBER_COUNT"

    deleted_id = team.id
    service.delete_team(
        db_session,
        yard_id=yard.id,
        actor_id="u-team",
        team_id=team.id,
        request_id=REQ_ID,
    )
    db_session.commit()
    assert db_session.get(AgentTeam, deleted_id) is None


# ---------------------------------------------------------------------------
# 接力拼接 / 答案拼接（纯函数）
# ---------------------------------------------------------------------------


def test_build_member_question_relay_format_and_truncation():
    q = service.build_member_question(
        "棚室温度怎么样",
        [("解读员", "温度 18 度"), ("诊断员", "偏冷")],
        "给出建议",
        is_critic=False,
    )
    assert q.startswith("棚室温度怎么样")
    assert "【解读员】：温度 18 度" in q and "【诊断员】：偏冷" in q  # 规定的段格式
    assert "你的班组任务：给出建议" in q
    assert "【审核要求】" not in q

    long_out = "数" * (service.MEMBER_OUTPUT_CAP + 50)
    clipped = service.build_member_question("问题", [("长", long_out)], "", False)
    assert "…（内容过长，已截断）" in clipped  # 截断诚实标注
    assert len(clipped) < len(long_out) + 200


def test_build_member_question_critic_directive():
    q = service.build_member_question("问题", [("甲", "输出甲")], "", is_critic=True)
    assert "审核员（critic）" in q and "修订结论" in q  # critic 附加指令
    assert "【甲】：输出甲" in q  # 同样能看到前序全部输出


def test_compose_answer_strategies():
    # 末位是 critic → 答案=其输出
    assert service.compose_answer([("甲", False, "甲输出"), ("审", True, "修订结论")]) == "修订结论"
    # 末位非 critic、队列中有 critic → 末位输出 + 审核意见并入
    merged = service.compose_answer([("审", True, "修订结论"), ("甲", False, "最终输出")])
    assert merged.startswith("最终输出")
    assert "【审核意见·审】\n修订结论" in merged and "---" in merged
    # 无 critic → 纯末位输出
    assert service.compose_answer([("甲", False, "A"), ("乙", False, "B")]) == "B"


# ---------------------------------------------------------------------------
# 工厂蓝图 → 班组成员（as_team 纯逻辑）
# ---------------------------------------------------------------------------


def test_members_from_blueprint_order_and_auto_critic():
    items = [
        {
            "agent_code": "b1",
            "name": "问答助手",
            "description": "回答实操问题",
            "persona_prompt": "你是问答助手……",
            "agent_id": "id-1",
        },
        {
            "agent_code": "b2",
            "name": "数据助理",
            "description": "",
            "persona_prompt": " backstory " * 50,
            "agent_id": "id-2",
        },
        {
            "agent_code": "b3",
            "name": "安全审核员",
            "description": "把关合规",
            "persona_prompt": "你是审核者",
            "agent_id": "id-3",
        },
        {"agent_code": "b4", "name": "未采纳角色", "agent_id": None},
    ]
    members = service.members_from_blueprint(items, ok_codes={"b2", "b1", "b3"})
    assert [m["role_name"] for m in members] == ["问答助手", "数据助理", "安全审核员"]
    assert members[0]["instruction"] == "回答实操问题"  # goal（description）摘要
    assert members[1]["instruction"].startswith("backstory")  # 缺 goal 回落 backstory
    assert members[0]["is_critic"] is False
    assert members[-1]["is_critic"] is True  # 末位命中审核关键词自动 critic


def test_members_from_blueprint_last_not_reviewer_no_critic():
    items = [
        {"agent_code": "b1", "name": "问答", "description": "g1", "agent_id": "i1"},
        {"agent_code": "b2", "name": "汇总", "description": "g2", "agent_id": "i2"},
    ]
    members = service.members_from_blueprint(items, ok_codes={"b1", "b2"})
    assert all(m["is_critic"] is False for m in members)


def test_members_from_blueprint_orders_by_stage_and_marks_review_critic():
    # 故意打乱：审核在前、执行在后；即使名称不含“审核”，stage=review 也判 critic
    items = [
        {
            "agent_code": "r",
            "name": "质检把关",
            "description": "复核",
            "stage": "review",
            "agent_id": "i3",
        },
        {
            "agent_code": "e",
            "name": "田间执行",
            "description": "落地",
            "stage": "execution",
            "agent_id": "i2",
        },
        {
            "agent_code": "a",
            "name": "需求分析",
            "description": "取证",
            "stage": "analysis",
            "agent_id": "i1",
        },
    ]
    members = service.members_from_blueprint(items, ok_codes={"r", "e", "a"})
    assert [m["agent_id"] for m in members] == ["i1", "i2", "i3"]
    assert members[-1]["is_critic"] is True
    assert all(m["is_critic"] is False for m in members[:-1])
