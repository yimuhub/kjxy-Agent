"""枢纽/运营模式测试（v3.65）：模式选择、绑定、聚合收窄、判权。"""

import pytest
from app.core.deps import YardContext
from app.core.errors import AppError
from app.modules.commander.models import CommanderRun  # noqa: F401 - 注册模型
from app.modules.hubs import service
from app.modules.journal.models import YardJournal
from app.modules.yards.models import YardMembership
from app.modules.yards.repository import create_yard


def _ctx(user_id="u-admin", roles=("platform_admin",), yard_id="y0") -> YardContext:
    return YardContext(yard_id=yard_id, user_id=user_id, roles=list(roles), request_id="req")


@pytest.fixture()
def setup(db_session):
    y1 = create_yard(db_session, name="曲周小院A")
    y2 = create_yard(db_session, name="曲周小院B")
    y3 = create_yard(db_session, name="独立小院")
    db_session.add_all(
        [
            YardMembership(yard_id=y1.id, user_id="u-op", role="operator"),
            YardMembership(yard_id=y2.id, user_id="u-op2", role="operator"),
        ]
    )
    db_session.add_all(
        [
            YardJournal(
                id="j1",
                yard_id=y1.id,
                author_user_id="u-op",
                kind="daily",
                period_key="2026-11-04",
                entry_date=None,
                title="t",
                content_md="c",
                status="submitted",
            ),
            YardJournal(
                id="j2",
                yard_id=y1.id,
                author_user_id="u-op",
                kind="daily",
                period_key="2026-11-03",
                entry_date=None,
                title="t",
                content_md="c",
                status="draft",
            ),
        ]
    )
    db_session.commit()
    return y1, y2, y3


def test_create_hub_mode_and_auto_manager(db_session, setup):
    y1, y2, y3 = setup
    ctx = _ctx()
    object.__setattr__(ctx, "yard_id", y1.id)
    hub = service.create_hub(
        db_session,
        ctx,
        name="曲周实验站枢纽",
        mode="station",
        region="河北邯郸曲周县",
        note="一院一策",
    )
    assert hub.mode == "station"
    # 创建者自动 manager
    assert service.is_hub_manager(db_session, "u-admin", hub.id) is True
    out = service.hub_out(db_session, hub)
    assert out["mode_label"] and "实验站" in out["mode_label"]


def test_create_hub_invalid_mode_409_name_taken(db_session, setup):
    y1, _, _ = setup
    ctx = _ctx()
    object.__setattr__(ctx, "yard_id", y1.id)
    with pytest.raises(AppError) as e:
        service.create_hub(db_session, ctx, name="X", mode="yolo", region="", note="")
    assert e.value.code == "INVALID_REQUEST"
    service.create_hub(db_session, ctx, name="同名检测", mode="province", region="", note="")
    with pytest.raises(AppError) as e2:
        service.create_hub(db_session, ctx, name="同名检测", mode="province", region="", note="")
    assert e2.value.code == "HUB_NAME_TAKEN"


def test_attach_and_overview_narrowing(db_session, setup):
    """聚合只统计所辖小院：绑 A/B 两院，第三院不在聚合里。"""
    y1, y2, y3 = setup
    ctx = _ctx()
    object.__setattr__(ctx, "yard_id", y1.id)
    hub = service.create_hub(db_session, ctx, name="枢纽X", mode="station", region="", note="")
    service.attach_yard(db_session, ctx, hub.id, y1.id)
    service.attach_yard(db_session, ctx, hub.id, y2.id)
    # 重复绑定幂等
    again = service.attach_yard(db_session, ctx, hub.id, y1.id)
    assert again["moved"] is False
    # 已属他处 409
    other_ctx = _ctx()
    object.__setattr__(other_ctx, "yard_id", y3.id)
    hub3 = service.create_hub(
        db_session, other_ctx, name="枢纽Y", mode="province", region="", note=""
    )
    with pytest.raises(AppError) as e:
        service.attach_yard(db_session, other_ctx, hub3.id, y1.id)
    assert e.value.code == "YARD_ALREADY_ATTACHED"

    overview = service.hub_overview(db_session, _ctx(), hub.id)
    ids = {i["yard_id"] for i in overview["items"]}
    assert ids == {y1.id, y2.id} and y3.id not in ids
    a = next(i for i in overview["items"] if i["yard_id"] == y1.id)
    assert a["journal_count"] == 2 and a["active_authors"] == 1
    assert overview["hub"]["mode"] == "station"


def test_manager_gate(db_session, setup):
    """非 manager 看聚合/绑定 403；viewer 可看聚合不可绑定。"""
    y1, _, _ = setup
    admin_ctx = _ctx()
    object.__setattr__(admin_ctx, "yard_id", y1.id)
    hub = service.create_hub(
        db_session, admin_ctx, name="权限枢纽", mode="province", region="", note=""
    )
    viewer_ctx = _ctx("u-viewer", roles=())
    object.__setattr__(viewer_ctx, "yard_id", y1.id)
    service.add_member(db_session, admin_ctx, hub.id, "u-viewer", "viewer")
    # viewer 可看
    assert service.hub_overview(db_session, viewer_ctx, hub.id)["hub"]["id"] == hub.id
    # viewer 不能绑定
    with pytest.raises(AppError) as e:
        service.attach_yard(db_session, viewer_ctx, hub.id, y1.id)
    assert e.value.code == "FORBIDDEN"
    # 陌生人 403
    stranger_ctx = _ctx("u-stranger", roles=())
    object.__setattr__(stranger_ctx, "yard_id", y1.id)
    with pytest.raises(AppError) as e2:
        service.hub_overview(db_session, stranger_ctx, hub.id)
    assert e2.value.code == "FORBIDDEN"


def test_list_managed_hubs(db_session, setup):
    y1, _, _ = setup
    ctx = _ctx("u-manager")
    object.__setattr__(ctx, "yard_id", y1.id)
    h1 = service.create_hub(db_session, ctx, name="我的枢纽", mode="municipal", region="", note="")
    service.create_hub(
        db_session, _ctx("u-other"), name="别人枢纽", mode="station", region="", note=""
    )
    managed = service.list_managed_hubs(db_session, "u-manager")
    assert [h.id for h in managed] == [h1.id]
    assert managed[0].mode == "municipal"
