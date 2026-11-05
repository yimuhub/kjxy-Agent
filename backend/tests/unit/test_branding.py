"""品牌与门户测试（v3.67）：公开读、校验、logo 生命周期、平台级授权。"""

import io

import pytest
from app.core.errors import AppError
from app.modules.auth.models import User
from app.modules.auth.service import get_me
from app.modules.branding import service
from app.modules.yards.permissions import GRANTABLE_PERMISSIONS, PLATFORM_PERMISSIONS
from app.modules.yards.repository import create_yard
from starlette.datastructures import UploadFile

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _upload(filename: str, data: bytes) -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=filename)


def _user(db, username: str) -> User:
    user = User(
        username=username,
        password_hash="x",
        display_name=username,
        status="active",
    )
    db.add(user)
    db.flush()
    return user


def test_public_defaults_then_update(db_session):
    """未配置时公开读返回默认文案；改完后公开读同步、版本号递增。"""
    out = service.public_payload(db_session)
    assert out["app_name"] == service.DEFAULT_APP_NAME
    assert out["logo_url"] is None
    assert out["brand_version"] == 0

    updated = service.update_settings(
        db_session,
        actor_id="u1",
        request_id="req",
        payload={
            "app_name": "曲周科技小院",
            "short_name": "曲周小院",
            "login_tagline": "县域农业协同平台",
            "primary_color": "#abc",
            "domains": ["https://Portal.Example.EDU.cn/portal"],
        },
    )
    assert updated["app_name"] == "曲周科技小院"
    assert updated["short_name"] == "曲周小院"
    assert updated["primary_color"] == "#AABBCC"  # #RGB 展开成 #RRGGBB
    assert updated["domains"] == ["portal.example.edu.cn"]

    pub = service.public_payload(db_session)
    assert pub["app_name"] == "曲周科技小院"
    assert pub["brand_version"] > 0
    # 公开读不含域名清单与操作者
    assert "domains" not in pub and "updated_by" not in pub


def test_validation_limits(db_session):
    with pytest.raises(AppError):
        service.update_settings(
            db_session, actor_id="u1", request_id="req", payload={"app_name": "名" * 41}
        )
    with pytest.raises(AppError):
        service.update_settings(
            db_session, actor_id="u1", request_id="req", payload={"primary_color": "red"}
        )
    with pytest.raises(AppError):
        service.update_settings(
            db_session,
            actor_id="u1",
            request_id="req",
            payload={"domains": [f"a{i}.example.com" for i in range(service.MAX_DOMAINS + 1)]},
        )
    with pytest.raises(AppError):
        service.update_settings(db_session, actor_id="u1", request_id="req", payload={})


def test_domain_normalization():
    assert service.normalize_domain("HTTP://Portal.Example.com:8443/x?y=1") == "portal.example.com"
    assert service.normalize_domain("localhost:5173") == "localhost"
    assert service.normalize_domain("127.0.0.1") == "127.0.0.1"
    assert service.normalize_domain("   ") == ""
    with pytest.raises(AppError):
        service.normalize_domain("not_a_domain")
    # 去重保序
    assert service.normalize_domains(["a.example.com", "A.example.com", ""]) == ["a.example.com"]


def test_logo_upload_read_clear(db_session):
    out = service.set_logo(
        db_session, actor_id="u1", request_id="req", file=_upload("logo.png", PNG_BYTES)
    )
    assert out["logo_url"] == service.LOGO_PATH

    data, mime, version = service.read_logo(db_session)
    assert data == PNG_BYTES and mime == "image/png" and version > 0

    # SVG 与非图片直接拒收；扩展名伪装按文件头识破
    for filename, payload in (("logo.svg", b"<svg/>"), ("logo.png", b"hello world")):
        with pytest.raises(AppError) as err:
            service.set_logo(
                db_session, actor_id="u1", request_id="req", file=_upload(filename, payload)
            )
        assert err.value.code == "UNSUPPORTED_FILE_TYPE"

    service.clear_logo(db_session, actor_id="u1", request_id="req")
    with pytest.raises(AppError) as err:
        service.read_logo(db_session)
    assert err.value.code == "BRANDING_LOGO_MISSING"


def test_branding_permission_is_platform_scoped(db_session):
    """核心回归：小院负责人＝小院全集，但**不能**因此拿到平台品牌权限。"""
    yard = create_yard(db_session, name="权限小院")
    owner = _user(db_session, "owner1")
    yard.owner_user_id = owner.id
    admin = _user(db_session, "admin1")
    db_session.flush()

    me_owner = get_me(db_session, owner.id, roles=["yard_owner"], yard_id=yard.id)
    assert me_owner.is_owner is True
    assert "branding:manage" not in me_owner.permissions
    # 其余小院权限仍在，说明不是把整个全集删掉了
    assert {"settings:manage", "members:manage"}.issubset(set(me_owner.permissions))

    me_admin = get_me(db_session, admin.id, roles=["platform_admin"], yard_id=yard.id)
    assert "branding:manage" in me_admin.permissions

    # 超管逐人授权 → 小院负责人拿到；撤销后立刻失效
    service.set_user_grants(
        db_session,
        actor_id=admin.id,
        request_id="req",
        user_id=owner.id,
        grants=["branding:manage"],
    )
    assert (
        "branding:manage"
        in get_me(db_session, owner.id, roles=["yard_owner"], yard_id=yard.id).permissions
    )

    service.set_user_grants(
        db_session, actor_id=admin.id, request_id="req", user_id=owner.id, grants=[]
    )
    assert (
        "branding:manage"
        not in get_me(db_session, owner.id, roles=["yard_owner"], yard_id=yard.id).permissions
    )


def test_platform_grants_validation_and_targets(db_session):
    admin = _user(db_session, "admin2")
    target = _user(db_session, "ops2")

    with pytest.raises(AppError) as err:
        service.set_user_grants(
            db_session,
            actor_id=admin.id,
            request_id="req",
            user_id=target.id,
            grants=["owner:transfer"],
        )
    assert err.value.code == "INVALID_REQUEST"

    service.set_user_grants(
        db_session,
        actor_id=admin.id,
        request_id="req",
        user_id=target.id,
        grants=["branding:manage"],
    )
    targets = {row["user_id"]: row for row in service.list_grant_targets(db_session)}
    assert targets[target.id]["grants"] == ["branding:manage"]
    assert targets[admin.id]["grants"] == []


def test_platform_permission_not_grantable_from_yard():
    assert PLATFORM_PERMISSIONS == frozenset({"branding:manage"})
    assert "branding:manage" not in GRANTABLE_PERMISSIONS


def test_branding_http_surface(client):
    """路由挂载与匿名读：/branding 不带令牌可读，写接口必须登录。"""
    res = client.get("/api/v1/branding")
    assert res.status_code == 200
    body = res.json()["data"]
    assert body["app_name"] == service.DEFAULT_APP_NAME
    assert body["logo_url"] is None
    assert "domains" not in body and "updated_by" not in body

    # 未登录写 → 401；未登录读管理视图 → 401
    assert client.put("/api/v1/branding/settings", json={"app_name": "x"}).status_code == 401
    assert client.get("/api/v1/branding/settings").status_code == 401
    assert client.get("/api/v1/platform/grants").status_code == 401
