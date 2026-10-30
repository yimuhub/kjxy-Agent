"""演示成员真人化（v3.29）：只补/改真人成员，四个固定账号的展示名不动。"""

from app.db.seed import DEMO_ROSTER, ensure_demo_roster
from app.modules.auth.repository import get_by_username
from app.modules.yards.models import YardMembership
from app.modules.yards.repository import create_yard, get_membership


def test_ensure_demo_roster_is_idempotent(db_session):
    yard = create_yard(db_session, name="真人成员小院")
    db_session.flush()

    assert ensure_demo_roster(db_session, yard) == len(DEMO_ROSTER)
    assert ensure_demo_roster(db_session, yard) == 0

    rows = db_session.query(YardMembership).filter_by(yard_id=yard.id).all()
    assert len(rows) == len(DEMO_ROSTER)
    assert {row.role for row in rows} == {"operator", "expert", "viewer"}
    assert all(row.status == "active" for row in rows)


def test_ensure_demo_roster_restores_display_name_and_role(db_session):
    yard = create_yard(db_session, name="真人成员小院二")
    db_session.flush()
    ensure_demo_roster(db_session, yard)

    username, display_name, role = DEMO_ROSTER[0]
    user = get_by_username(db_session, username)
    assert user is not None and user.display_name == display_name
    user.display_name = "E2E 新成员"
    membership = get_membership(db_session, yard.id, user.id)
    membership.role = "viewer"
    db_session.flush()

    assert ensure_demo_roster(db_session, yard) == 0
    assert user.display_name == display_name
    assert get_membership(db_session, yard.id, user.id).role == role


def test_roster_uses_human_names_not_role_aliases():
    aliases = {"小院负责人", "小院管理员", "农业专家", "科研人员"}
    names = [display_name for _, display_name, _ in DEMO_ROSTER]
    assert len(names) == len(set(names))
    assert not aliases & set(names)
    assert all(2 <= len(name) <= 4 for name in names)
