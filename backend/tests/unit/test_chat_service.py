"""对话模式服务层纯函数单测：标题继承、序号递增、输出形状。"""

from app.modules.chat import service
from app.modules.chat.models import MSG_OK, ROLE_ASSISTANT, ROLE_USER, ChatMessage
from app.modules.yards.repository import create_yard


def test_derive_title_takes_first_nonblank_line():
    assert service.derive_title("  \n棚里今天需要浇水吗\n第二行") == "棚里今天需要浇水吗"


def test_derive_title_caps_and_marks():
    title = service.derive_title("长" * 100)
    assert title.startswith("长" * 60)
    assert title.endswith("…")
    assert len(title) == 61  # 60 字 + 省略号


def test_derive_title_blank_returns_empty():
    assert service.derive_title("   \n  \n") == ""


def test_next_seq_starts_at_one(db_session):
    assert service.next_seq(db_session, "sess-none") == 1


def test_next_seq_increments_and_message_out_shape(db_session):
    t = create_yard(db_session, name="对话单测小院")
    db_session.commit()
    sess = service.create_session(db_session, yard_id=t.id, created_by="u1")
    db_session.commit()
    assert service.next_seq(db_session, sess.id) == 1
    m = ChatMessage(
        session_id=sess.id,
        yard_id=t.id,
        seq=1,
        role=ROLE_USER,
        content="你好",
        status=MSG_OK,
    )
    db_session.add(m)
    db_session.commit()
    assert service.next_seq(db_session, sess.id) == 2
    out = service.message_out(m)
    assert out["role"] == ROLE_USER
    assert out["status"] == "ok"
    assert out["routed_by"] is None and out["choice"] is None and out["error_code"] is None
    # 空会话的 session_out 计数为 1（刚插入的消息），且不抛错
    s_out = service.session_out(db_session, sess)
    assert s_out["message_count"] == 1 and s_out["created_by"] == "u1"


def test_message_out_assistant_carries_routing_fields(db_session):
    t = create_yard(db_session, name="对话单测小院")
    db_session.commit()
    sess = service.create_session(db_session, yard_id=t.id, created_by="u1")
    m = ChatMessage(
        session_id=sess.id,
        yard_id=t.id,
        seq=1,
        role=ROLE_ASSISTANT,
        content="答案",
        status=MSG_OK,
        routed_by="rules",
        choice_json={"kind": "agent", "name": "灌溉顾问"},
        reason="",
    )
    db_session.add(m)
    db_session.commit()
    out = service.message_out(m)
    assert out["routed_by"] == "rules"
    assert out["choice"]["name"] == "灌溉顾问"


def test_sessions_out_bulk_matches_per_row_session_out(db_session):
    """列表的批量序列化必须与逐行 session_out 一字不差。

    批量版把「每行三张表」压成四条查询，字段或口径一旦漂移，
    列表与详情就会显示两套数字——这条等价性断言就是那根围栏。
    覆盖三种混合形态：带计量的、缺计量要按正文估的、群聊+锁定助手名的。
    """
    from app.modules.agents.models import YardAgent
    from app.modules.chat.models import ChatParticipant

    t = create_yard(db_session, name="批量序列化小院")
    db_session.commit()
    db_session.add(
        YardAgent(
            yard_id=t.id,
            agent_code="bulk_c",
            name="批量顾问",
            description="d",
            persona_prompt="你是批量顾问。",
            task_prompt_template="回答：{{question}}",
            status="published",
            created_by="u1",
        )
    )

    plain = service.create_session(db_session, yard_id=t.id, created_by="u1", title="普通")
    locked = service.create_session(
        db_session,
        yard_id=t.id,
        created_by="u1",
        title="锁定",
        target="agent",
        target_code="bulk_c",
    )
    group = service.create_session(db_session, yard_id=t.id, created_by="u1", title="群聊")
    db_session.flush()

    db_session.add_all(
        [
            # 带计量的助手消息
            ChatMessage(
                session_id=plain.id,
                yard_id=t.id,
                seq=1,
                role=ROLE_ASSISTANT,
                content="回答正文",
                status=MSG_OK,
                token_usage_json={"input_tokens": 11, "output_tokens": 22, "total_tokens": 33},
            ),
            # 早期没计量的用户消息：要走「按正文估」那条回落分支
            ChatMessage(
                session_id=locked.id,
                yard_id=t.id,
                seq=1,
                role=ROLE_USER,
                content="这是用户当初问的一句话",
                status=MSG_OK,
            ),
        ]
    )
    db_session.add(
        ChatParticipant(
            session_id=group.id,
            yard_id=t.id,
            created_by="u1",
            agent_id="a1",
            agent_name="甲",
            position=1,
        )
    )
    db_session.commit()

    rows = [plain, locked, group]
    bulk = service.sessions_out_bulk(db_session, rows)
    assert bulk == [service.session_out(db_session, r) for r in rows]
    assert bulk[0]["total_tokens"] == 33
    assert bulk[1]["target_name"] == "批量顾问"
    assert bulk[2]["participant_count"] == 1
    # 空列表不报错也不发查询之外的开销
    assert service.sessions_out_bulk(db_session, []) == []
