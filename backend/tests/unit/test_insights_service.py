"""负责人驾驶舱统计服务单测：口径、Token 回退、小院边界。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.core.errors import AppError
from app.modules.agents.models import STATUS_DELETED, STATUS_PUBLISHED, YardAgent
from app.modules.auth.models import User
from app.modules.chat.models import ChatMessage, ChatSession
from app.modules.evolution.models import KnowledgeCandidate, Skill
from app.modules.insights import service as insights
from app.modules.knowledge.chunking import estimate_token_count
from app.modules.plugins.models import PluginManifest
from app.modules.yards.models import Yard
from app.modules.yards.repository import add_membership, create_yard


def _user(db, username: str, **kwargs) -> User:
    user = User(
        username=username,
        email=kwargs.pop("email", f"{username}@example.com"),
        phone=kwargs.pop("phone", "13800138000"),
        password_hash="x",
        display_name=kwargs.pop("display_name", username),
        status="active",
        token_version=0,
        **kwargs,
    )
    db.add(user)
    db.flush()
    return user


def _yard(db, name: str = "驾驶舱小院", owner: User | None = None) -> Yard:
    yard = create_yard(db, name=name)
    if owner is not None:
        yard.owner_user_id = owner.id
    db.flush()
    return yard


def _session(db, yard, creator, *, title="会话", created_at=None) -> ChatSession:
    session = ChatSession(
        yard_id=yard.id,
        created_by=creator.id,
        title=title,
        target="auto",
    )
    if created_at is not None:
        session.created_at = created_at
        session.updated_at = created_at
    db.add(session)
    db.flush()
    return session


def _message(
    db,
    yard,
    session,
    *,
    seq,
    role="assistant",
    content="回答",
    status="ok",
    usage=None,
    choice=None,
    created_at=None,
) -> ChatMessage:
    message = ChatMessage(
        session_id=session.id,
        yard_id=yard.id,
        seq=seq,
        role=role,
        content=content,
        status=status,
        token_usage_json=usage,
        choice_json=choice,
    )
    if created_at is not None:
        message.created_at = created_at
        message.updated_at = created_at
    db.add(message)
    db.flush()
    return message


@pytest.fixture()
def seeded(db_session):
    owner = _user(db_session, "owner")
    admin = _user(db_session, "admin", email="admin@example.com", phone="13800138001")
    member = _user(db_session, "member", email="member@example.com", phone="13900139000")
    yard = _yard(db_session, owner=owner)
    add_membership(db_session, yard_id=yard.id, user_id=owner.id, role="yard_owner")
    admin_membership = add_membership(
        db_session, yard_id=yard.id, user_id=admin.id, role="yard_admin"
    )
    member_membership = add_membership(
        db_session, yard_id=yard.id, user_id=member.id, role="operator"
    )
    # 管理员今天活跃；普通成员仅昨天活跃
    today = datetime.now(UTC)
    admin.last_login_at = today
    member.last_login_at = today - timedelta(days=1)
    member_membership.updated_at = today - timedelta(days=1)
    db_session.flush()

    now = today
    session_today = _session(db_session, yard, member, title="今日会话", created_at=now)
    session_old = _session(
        db_session,
        yard,
        member,
        title="旧会话",
        created_at=now - timedelta(days=10),
    )
    _message(
        db_session,
        yard,
        session_today,
        seq=1,
        role="user",
        content="今天浇多少水",
        usage={"input_tokens": 10, "output_tokens": 0, "total_tokens": 10},
        created_at=now,
    )
    _message(
        db_session,
        yard,
        session_today,
        seq=2,
        content="建议滴灌 2 小时",
        usage={"input_tokens": 0, "output_tokens": 20, "total_tokens": 20},
        choice={"kind": "agent", "id": "a1", "code": "irrig", "name": "灌溉顾问"},
        created_at=now,
    )
    failed = _message(
        db_session,
        yard,
        session_today,
        seq=3,
        content="模型超时",
        status="failed",
        choice={"kind": "agent", "id": "a1", "code": "irrig", "name": "灌溉顾问"},
        created_at=now,
    )
    fallback_in = estimate_token_count(failed.content)
    _message(
        db_session,
        yard,
        session_old,
        seq=1,
        content="旧数据不计入 7 天",
        usage={"input_tokens": 999, "output_tokens": 999, "total_tokens": 1998},
        created_at=now - timedelta(days=10),
    )

    db_session.add(
        YardAgent(
            yard_id=yard.id,
            agent_code="irrig",
            name="灌溉顾问",
            persona_prompt="你是灌溉顾问",
            task_prompt_template="{{question}}",
            status=STATUS_PUBLISHED,
        )
    )
    db_session.add(
        YardAgent(
            yard_id=yard.id,
            agent_code="gone",
            name="已删除助手",
            persona_prompt="x",
            task_prompt_template="{{question}}",
            status=STATUS_DELETED,
        )
    )
    db_session.add(
        KnowledgeCandidate(
            yard_id=yard.id,
            title="候选",
            content="内容",
            status="pending_review",
        )
    )
    db_session.add(
        Skill(
            yard_id=yard.id,
            skill_code="sk",
            version="1.0.0",
            definition_json={},
            status="pending_review",
        )
    )
    db_session.add(
        PluginManifest(
            plugin_id="wf",
            yard_id=yard.id,
            version="1.0.0",
            type="workflow",
            status="pending_review",
            manifest_json={},
            checksum="x",
        )
    )
    db_session.commit()
    return {
        "owner": owner,
        "admin": admin,
        "member": member,
        "yard": yard,
        "session_today": session_today,
        "session_old": session_old,
        "failed": failed,
        "fallback_in": fallback_in,
        "admin_membership": admin_membership,
    }


def test_overview_counts_and_token_fallback(db_session, seeded):
    data = insights.overview(db_session, seeded["yard"].id)

    assert data["members_total"] == 3
    assert data["active_today"] == 2  # owner/admin 的 membership 今天更新；成员昨天
    assert data["agents_count"] == 1
    assert data["sessions_today"] == 1
    assert data["messages_today"] == 3
    assert data["tokens_today"] == 10 + 20 + seeded["fallback_in"]
    assert data["tokens_today_input"] == 10
    assert data["tokens_today_output"] == 20 + seeded["fallback_in"]
    assert data["tokens_7d"] == data["tokens_today"]  # 旧会话在窗口外
    assert data["failed_messages_today"] == 1
    assert data["pending_reviews"] == {
        "total": 3,
        "knowledge_candidates": 1,
        "skills": 1,
        "plugins": 1,
    }
    assert data["network"] is None


def test_member_usage_aggregates_tokens_and_agents(db_session, seeded):
    data = insights.member_usage(db_session, seeded["yard"].id, days=7)
    by_name = {item["username"]: item for item in data["items"]}

    member = by_name["member"]
    assert member["sessions"] == 1
    assert member["messages"] == 3
    assert member["input_tokens"] == 10
    assert member["output_tokens"] == 20 + seeded["fallback_in"]
    assert member["total_tokens"] == member["input_tokens"] + member["output_tokens"]
    assert member["agents_used"] == ["灌溉顾问"]
    assert member["agents_used_count"] == 1
    assert member["last_active"] is not None
    # 无用量成员仍出现在列表
    assert by_name["admin"]["sessions"] == 0
    assert data["summary"]["members_total"] == 3
    assert data["summary"]["messages"] == 3


def test_daily_usage_has_all_days_and_totals(db_session, seeded):
    data = insights.daily_usage(db_session, seeded["yard"].id, days=7)
    assert len(data["items"]) == 7
    assert data["totals"]["messages"] == 3
    today = data["items"][-1]
    assert today["messages"] == 3
    assert today["active_members"] == 1
    assert today["by_member"][0]["username"] == "member"


def test_agent_usage_counts_failures(db_session, seeded):
    data = insights.agent_usage(db_session, seeded["yard"].id, days=7)
    assert data["totals"]["calls"] == 2
    assert data["totals"]["failed"] == 1
    assert data["items"][0]["code"] == "irrig"
    assert data["items"][0]["name"] == "灌溉顾问"


def test_list_sessions_ignores_creator_boundary(db_session, seeded):
    items, total = insights.list_sessions(db_session, seeded["yard"].id, page=1, page_size=10)
    assert total == 2
    assert {item["title"] for item in items} == {"今日会话", "旧会话"}
    assert all(item["created_by_username"] == "member" for item in items)

    filtered, filtered_total = insights.list_sessions(db_session, seeded["yard"].id, keyword="今日")
    assert filtered_total == 1
    assert filtered[0]["title"] == "今日会话"

    by_admin, zero = insights.list_sessions(
        db_session, seeded["yard"].id, user_id=seeded["admin"].id
    )
    assert by_admin == []
    assert zero == 0


def test_session_messages_enforces_yard_boundary(db_session, seeded):
    data = insights.session_messages(db_session, seeded["yard"].id, seeded["session_today"].id)
    assert [item["seq"] for item in data["items"]] == [1, 2, 3]
    assert data["session"]["created_by"] == seeded["member"].id

    other = _yard(db_session, name="隔壁小院")
    with pytest.raises(AppError) as exc:
        insights.session_messages(db_session, other.id, seeded["session_today"].id)
    assert exc.value.code == "CHAT_SESSION_NOT_FOUND"


def test_contact_masking():
    assert insights.mask_email("member@example.com") == "me***@example.com"
    assert insights.mask_email(None) is None
    assert insights.mask_phone("13800138000") == "138****8000"
    assert insights.mask_phone(None) is None
