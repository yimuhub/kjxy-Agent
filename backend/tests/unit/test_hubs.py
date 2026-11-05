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


def test_yard_manager_sees_hub_after_attach(db_session, setup):
    """v3.65 修复：小院被绑进枢纽后，该院负责人/管理员可见枢纽（只读）。"""
    y1, _, _ = setup
    # 小院管理员（该院活跃 yard_admin）
    db_session.add(YardMembership(yard_id=y1.id, user_id="u-yard-admin", role="yard_admin"))
    db_session.commit()
    admin_ctx = _ctx()
    object.__setattr__(admin_ctx, "yard_id", y1.id)
    hub = service.create_hub(
        db_session, admin_ctx, name="可见性枢纽", mode="municipal", region="", note=""
    )
    service.attach_yard(db_session, admin_ctx, hub.id, y1.id)

    # 平台管理员可见（manager）
    visible_admin = service.list_visible_hubs(db_session, "u-admin")
    assert hub.id if False else any(h.id == hub.id and r == "manager" for h, r in visible_admin)
    # 所辖小院的负责人/管理员可见（yard_manager）
    visible_ym = service.list_visible_hubs(db_session, "u-yard-admin")
    assert any(h.id == hub.id and r == "yard_manager" for h, r in visible_ym)
    # 只读聚合可访问
    ctx_ym = _ctx("u-yard-admin", roles=("yard_admin",))
    object.__setattr__(ctx_ym, "yard_id", y1.id)
    overview = service.hub_overview(db_session, ctx_ym, hub.id)
    assert overview["hub"]["id"] == hub.id
    # 普通成员（operator）不可见
    db_session.add(YardMembership(yard_id=y1.id, user_id="u-op-plain", role="operator"))
    db_session.commit()
    visible_op = service.list_visible_hubs(db_session, "u-op-plain")
    assert not any(h.id == hub.id for h, _ in visible_op)


def test_hub_permissions_viewer_vs_manager(db_session, setup):
    """v3.66：枢纽权限包与 JWT 角色解耦——viewer 只读，manager 全包，陌生人空。"""
    y1, _, _ = setup
    ctx = _ctx()
    object.__setattr__(ctx, "yard_id", y1.id)
    hub = service.create_hub(db_session, ctx, name="权限包枢纽", mode="station", region="", note="")
    service.add_member(db_session, ctx, hub.id, "u-viewer2", "viewer")

    assert service.hub_permissions(db_session, "u-admin") == service.HUB_MANAGER_PERMISSIONS
    assert service.hub_permissions(db_session, "u-viewer2") == service.HUB_VIEW_PERMISSIONS
    assert service.hub_permissions(db_session, "u-nobody") == frozenset()

    context = service.hub_context(db_session, "u-viewer2")
    assert len(context) == 1
    assert context[0]["hub_id"] == hub.id
    assert context[0]["role"] == "viewer"
    assert context[0]["mode"] == "station"
    assert context[0]["mode_label"]


def test_mode_insights_province_groups_by_unit(db_session, setup):
    """v3.66：省级模式按培养单位分组，并给出 100 篇年锚点的月度达标率。"""
    y1, y2, _ = setup
    ctx = _ctx()
    object.__setattr__(ctx, "yard_id", y1.id)
    hub = service.create_hub(
        db_session, ctx, name="省科协枢纽", mode="province", region="江苏", note=""
    )
    y1.attributes_json = {"unit": "南京农业大学", "county": "如皋市", "yard_type": "水稻"}
    y2.attributes_json = {"unit": "南京农业大学", "county": "溧阳市", "yard_type": "水稻"}
    service.attach_yard(db_session, ctx, hub.id, y1.id)
    service.attach_yard(db_session, ctx, hub.id, y2.id)
    db_session.flush()

    data = service.hub_overview(db_session, ctx, hub.id)
    insights = data["insights"]
    assert insights["group_by"] == "unit"
    assert insights["group_label"] == "培养单位"
    assert insights["anchor_annual"] == 100 and insights["anchor_monthly"] == 9
    assert len(insights["groups"]) == 1
    group = insights["groups"][0]
    assert group["key"] == "南京农业大学" and group["yards"] == 2
    assert insights["summary"]["yards"] == 2
    # y1 本月 2 篇、y2 本月 0 篇，均低于 9 篇月度锚点
    assert insights["summary"]["compliant"] == 0
    assert insights["summary"]["compliance_rate"] == 0.0
    # 明细行带分组键与特性，前端横向对比直接用
    row = next(i for i in data["items"] if i["yard_id"] == y1.id)
    assert row["group_key"] == "南京农业大学"
    assert row["attributes"]["county"] == "如皋市"


def test_mode_insights_municipal_and_station(db_session, setup):
    """v3.66：市级按县域、实验站按小院类型分组；缺特性时回退到模型列。"""
    y1, y2, _ = setup
    ctx = _ctx()
    object.__setattr__(ctx, "yard_id", y1.id)

    # 市级：county 优先，缺失回退 city
    municipal = service.create_hub(
        db_session, ctx, name="市专班枢纽", mode="municipal", region="邯郸", note=""
    )
    y1.attributes_json = {"county": "曲周县"}
    y2.attributes_json = {}
    y2.city = "邯郸市"
    service.attach_yard(db_session, ctx, municipal.id, y1.id)
    service.attach_yard(db_session, ctx, municipal.id, y2.id)
    db_session.flush()
    m = service.hub_overview(db_session, ctx, municipal.id)["insights"]
    assert m["group_by"] == "county" and m["group_label"] == "县域"
    assert {g["key"] for g in m["groups"]} == {"曲周县", "邯郸市"}

    # 实验站：yard_type 优先，缺失回退 scene_type
    service.detach_yard(db_session, ctx, y1.id)
    service.detach_yard(db_session, ctx, y2.id)
    station = service.create_hub(
        db_session, ctx, name="实验站枢纽", mode="station", region="曲周", note=""
    )
    y1.attributes_json = {"yard_type": "水稻"}
    y2.attributes_json = {}
    y2.scene_type = "设施蔬菜"
    service.attach_yard(db_session, ctx, station.id, y1.id)
    service.attach_yard(db_session, ctx, station.id, y2.id)
    db_session.flush()
    s = service.hub_overview(db_session, ctx, station.id)["insights"]
    assert s["group_by"] == "yard_type" and s["group_label"] == "小院类型"
    assert {g["key"] for g in s["groups"]} == {"水稻", "设施蔬菜"}
