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
