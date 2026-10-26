"""v2.0 W2 助手归属与首图：成员自建/自管 + 私有助手的取图边界。

覆盖：
1. 写权限：管理员可改任意助手；普通成员只能改/停/删自己的私有助手，
   碰别人的（含公开）助手一律 403，跨小院仍是 404（不泄露存在性）；
2. 成员自助发布：低风险私有助手可自行 published（"@ 得到自己的小助手"），
   高风险必须管理员发布；
3. 首图：上传→出参带回 cover_image_url→取图字节与 MIME→清除；伪装扩展名
   与超尺寸分别 415/413；私有助手的图对他人 404。
"""

from __future__ import annotations

import pytest
from app.core.security import create_access_token
from app.modules.yards.repository import create_yard

ADMIN = "u-own-admin"
OWNER = "u-own-member"
OTHER = "u-own-other"

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

_BASE = {
    "agent_code": "own_low",
    "name": "成员自建助手",
    "persona_prompt": "你是测试助手。",
    "task_prompt_template": "请回答：{{question}}",
    "allowed_tools": ["knowledge.search"],
    "knowledge_scopes": ["yard"],
}


def _hdr(user_id: str, yard_id: str, roles: tuple[str, ...] = ("viewer",)):
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def yard(db_session):
    t = create_yard(db_session, name="归属与首图院")
    db_session.commit()
    return t


def _create(client, yard, *, code, name, user=OWNER, roles=("viewer",), **extra):
    body = {
        **_BASE,
        "agent_code": code,
        "name": name,
        "task_prompt_template": "请回答：{{question}}",
        **extra,
    }
    resp = client.post("/api/v1/agents/declarative", json=body, headers=_hdr(user, yard.id, roles))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


# ---------------------------------------------------------------- 写权限


def test_member_can_manage_own_private_agent(client, yard):
    agent = _create(client, yard, code="own_priv_a", name="成员甲助手")
    assert agent["visibility"] == "private"

    patched = client.patch(
        f"/api/v1/agents/declarative/{agent['id']}",
        json={"description": "改过了"},
        headers=_hdr(OWNER, yard.id),
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["data"]["description"] == "改过了"

    deleted = client.delete(
        f"/api/v1/agents/declarative/{agent['id']}", headers=_hdr(OWNER, yard.id)
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["data"]["status"] == "deleted"
    # 软删除后列表不再出现（读路径一律隐藏）
    listed = client.get("/api/v1/agents/declarative", headers=_hdr(OWNER, yard.id))
    assert agent["id"] not in {a["id"] for a in listed.json()["data"]["agents"]}


def test_member_cannot_touch_others_agents(client, yard):
    mine = _create(client, yard, code="own_priv_b", name="成员乙助手", user=OWNER)
    for verb, call in (
        (
            "PATCH",
            lambda: client.patch(
                f"/api/v1/agents/declarative/{mine['id']}",
                json={"description": "串改"},
                headers=_hdr(OTHER, yard.id),
            ),
        ),
        (
            "DELETE",
            lambda: client.delete(
                f"/api/v1/agents/declarative/{mine['id']}", headers=_hdr(OTHER, yard.id)
            ),
        ),
        (
            "POST duplicate",
            lambda: client.post(
                f"/api/v1/agents/declarative/{mine['id']}/duplicate", headers=_hdr(OTHER, yard.id)
            ),
        ),
    ):
        resp = call()
        assert resp.status_code == 403, f"{verb}: {resp.status_code} {resp.text}"
        assert resp.json()["error"]["code"] == "FORBIDDEN"


def test_member_cannot_manage_public_admin_agent(client, yard):
    pub = _create(
        client, yard, code="own_pub_c", name="公开助手", user=ADMIN, roles=("yard_admin",)
    )
    assert pub["visibility"] == "public"
    resp = client.patch(
        f"/api/v1/agents/declarative/{pub['id']}",
        json={"description": "成员改公开"},
        headers=_hdr(OTHER, yard.id),
    )
    assert resp.status_code == 403


def test_admin_can_manage_any_agent_in_yard(client, yard):
    mine = _create(client, yard, code="own_priv_d", name="成员丁助手")
    resp = client.delete(
        f"/api/v1/agents/declarative/{mine['id']}", headers=_hdr(ADMIN, yard.id, ("yard_admin",))
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "deleted"


def test_cross_yard_management_is_404(client, yard, db_session):
    other_yard = create_yard(db_session, name="隔壁院")
    db_session.commit()
    mine = _create(client, yard, code="own_priv_e", name="成员戊助手")
    resp = client.delete(
        f"/api/v1/agents/declarative/{mine['id']}",
        headers=_hdr(ADMIN, other_yard.id, ("yard_admin",)),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "AGENT_NOT_FOUND"


# ---------------------------------------------------------------- 成员自助发布


def test_member_publishes_own_low_risk_private_agent(client, yard):
    agent = _create(client, yard, code="own_pub_low", name="可发布私助")
    resp = client.post(
        f"/api/v1/agents/declarative/{agent['id']}/publish", headers=_hdr(OWNER, yard.id)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "published"


def test_member_high_risk_private_needs_admin(client, yard):
    agent = _create(
        client,
        yard,
        code="own_pub_high",
        name="高风险私助",
        allowed_tools=["device.control_valve"],
    )
    assert agent["risk_level"] == "high"
    resp = client.post(
        f"/api/v1/agents/declarative/{agent['id']}/publish", headers=_hdr(OWNER, yard.id)
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "AGENT_REVIEW_REQUIRED"


# ---------------------------------------------------------------- 首图


def test_cover_upload_read_and_clear(client, yard):
    agent = _create(client, yard, code="own_cover_a", name="有图助手")
    assert agent["cover_image_url"] is None

    up = client.post(
        f"/api/v1/agents/declarative/{agent['id']}/cover",
        files={"file": ("leaf.png", PNG_BYTES, "image/png")},
        headers=_hdr(OWNER, yard.id),
    )
    assert up.status_code == 200, up.text
    url = up.json()["data"]["cover_image_url"]
    assert url and url.endswith(f"/{agent['id']}/cover")

    got = client.get(url, headers=_hdr(OWNER, yard.id))
    assert got.status_code == 200
    assert got.headers["content-type"] == "image/png"
    assert got.content == PNG_BYTES

    cleared = client.delete(url, headers=_hdr(OWNER, yard.id))
    assert cleared.status_code == 200
    assert cleared.json()["data"]["cover_image_url"] is None
    assert client.get(url, headers=_hdr(OWNER, yard.id)).status_code == 404


def test_cover_rejects_non_image_and_spoofed_ext(client, yard):
    agent = _create(client, yard, code="own_cover_b", name="拒图助手")
    exe = client.post(
        f"/api/v1/agents/declarative/{agent['id']}/cover",
        files={"file": ("payload.exe", b"MZ\x90\x00", "application/octet-stream")},
        headers=_hdr(OWNER, yard.id),
    )
    assert exe.status_code == 415
    assert exe.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"

    fake = client.post(
        f"/api/v1/agents/declarative/{agent['id']}/cover",
        files={"file": ("evil.png", b"MZ\x90\x00\x03", "image/png")},
        headers=_hdr(OWNER, yard.id),
    )
    assert fake.status_code == 415

    empty = client.post(
        f"/api/v1/agents/declarative/{agent['id']}/cover",
        files={"file": ("none.png", b"", "image/png")},
        headers=_hdr(OWNER, yard.id),
    )
    assert empty.status_code == 400


def test_private_cover_not_readable_by_other_member(client, yard):
    agent = _create(client, yard, code="own_cover_c", name="私有图助手")
    client.post(
        f"/api/v1/agents/declarative/{agent['id']}/cover",
        files={"file": ("leaf.png", PNG_BYTES, "image/png")},
        headers=_hdr(OWNER, yard.id),
    )
    url = f"/api/v1/agents/declarative/{agent['id']}/cover"
    # 他人：连"有没有这张图"都不告知
    assert client.get(url, headers=_hdr(OTHER, yard.id)).status_code == 404
    # 管理员拿得到（管理页要预览）
    assert client.get(url, headers=_hdr(ADMIN, yard.id, ("yard_admin",))).status_code == 200
    # 成员不能给别人换图
    assert (
        client.post(
            url,
            files={"file": ("x.png", PNG_BYTES, "image/png")},
            headers=_hdr(OTHER, yard.id),
        ).status_code
        == 403
    )
