"""手机配对（扫码接入局域网，Wave B）集成测试。

覆盖：
1. 建码：pair_url 格式契约（kjxy://pair?host=<ip>:<port>&code=<码>）与
   expires_in；配额上限（同小院未用码 >5 拒 429，用掉一个即可再建）；
2. 交换：成功（签发 pair 令牌 + 设备建档 + token_hash 落库）；过期拒；
   重复交换拒（原子占用）；错误码与过期/已用同文案（防枚举）；
3. 台账：列表、吊销（幂等 + revoked_at）、跨小院统一 404；
4. 权限：非管理员 403；**pair 令牌访问常规鉴权接口被 401 拒绝**
   （现有 decode_token 对 type 精确匹配 access——口径见 service 模块头）。

全部离线：局域网 IP 探测在 URL 断言用例中 monkeypatch（真实环境可能无
出网路由，两种分支都要稳定）。
"""

import os
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-min-32-chars-for-unit-tests")

import pytest  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.modules.pairing import service as pairing_service  # noqa: E402
from app.modules.pairing.models import PairCode, PairDevice  # noqa: E402
from app.modules.yards.repository import create_yard  # noqa: E402

YARD = "t-pair-a"
OTHER = "t-pair-b"


@pytest.fixture()
def yard(db_session):
    t = create_yard(db_session, name="配对小院A")
    db_session.commit()
    return t


def _hdr(yard_id: str = YARD, roles: tuple[str, ...] = ("yard_admin",)) -> dict:
    token = create_access_token(user_id="u-pair", yard_id=yard_id, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


def _make_code(client, yard_id: str = YARD) -> tuple[str, str]:
    """建码并返回 (配对码明文, pair_url)。"""
    resp = client.post("/api/v1/pairing/codes", headers=_hdr(yard_id))
    assert resp.status_code == 200, resp.text
    pair_url = resp.json()["data"]["pair_url"]
    qs = parse_qs(urlparse(pair_url).query)
    return qs["code"][0], pair_url


# ---------------------------------------------------------------------------
# 建码
# ---------------------------------------------------------------------------


def test_create_code_pair_url_contract(client, yard):
    resp = client.post("/api/v1/pairing/codes", headers=_hdr())
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["expires_in"] == 300
    url = data["pair_url"]
    assert url.startswith("kjxy://pair?host=")
    parsed = urlparse(url)
    assert parsed.scheme == "kjxy" and parsed.netloc == "pair"
    qs = parse_qs(parsed.query)
    assert ":" in qs["host"][0] and len(qs["code"][0]) >= 16


def test_code_quota_limit(client, yard):
    # 默认上限 5：前 5 个成功，第 6 个 429
    codes: list[str] = []
    for _ in range(settings.pairing_max_active_codes):
        code, _url = _make_code(client)
        codes.append(code)
    resp = client.post("/api/v1/pairing/codes", headers=_hdr())
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "RATE_LIMITED"
    # 用掉一个后额度释放：交换一个再建码即可成功
    assert (
        client.post(
            "/api/v1/pairing/exchange",
            json={"code": codes[0], "device_name": "释放额度手机"},
        ).status_code
        == 200
    )
    assert client.post("/api/v1/pairing/codes", headers=_hdr()).status_code == 200


def test_code_never_stored_in_plaintext(client, yard, db_session):
    code, _ = _make_code(client)
    row = db_session.query(PairCode).one()
    assert row.code_hash != code and code not in row.code_hash


# ---------------------------------------------------------------------------
# 交换
# ---------------------------------------------------------------------------


def test_exchange_success(client, yard, db_session):
    code, _ = _make_code(client)
    resp = client.post("/api/v1/pairing/exchange", json={"code": code, "device_name": "小明的手机"})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["token"] and data["device_id"]

    # 令牌口径：kind=pair / sub=device_id / 小院正确；verify 走本模块专用校验
    payload = pairing_service.verify_pair_token(data["token"])
    assert payload["kind"] == "pair" and payload["sub"] == data["device_id"]
    assert payload["yard_id"] == YARD

    device = db_session.get(PairDevice, data["device_id"])
    assert device is not None
    assert device.name == "小明的手机"
    # 令牌只存哈希：库内拿不到令牌明文
    assert device.token_hash != data["token"]
    assert pairing_service._sha256(data["token"]) == device.token_hash
    assert device.created_by is None  # 扫码交换无登录主体
    db_session.refresh(db_session.query(PairCode).one())


def test_exchange_expired_rejected(client, yard, db_session):
    code, _ = _make_code(client)
    row = db_session.query(PairCode).one()
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    resp = client.post("/api/v1/pairing/exchange", json={"code": code, "device_name": "过期手机"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


def test_exchange_replay_rejected(client, yard):
    code, _ = _make_code(client)
    body = {"code": code, "device_name": "重复手机"}
    assert client.post("/api/v1/pairing/exchange", json=body).status_code == 200
    # 一次性：第二次（换一个名字也一样）被拒
    resp = client.post("/api/v1/pairing/exchange", json=body)
    assert resp.status_code == 401
    # 错误码/文案与"码不存在"完全一致：不给枚举者可乘之机
    bogus = client.post("/api/v1/pairing/exchange", json={"code": "x" * 22, "device_name": "猜码"})
    assert bogus.status_code == 401
    assert bogus.json()["error"]["message"] == resp.json()["error"]["message"]


# ---------------------------------------------------------------------------
# 权限与台账
# ---------------------------------------------------------------------------


def test_non_admin_forbidden(client, yard):
    for call in (
        lambda: client.post("/api/v1/pairing/codes", headers=_hdr(roles=("member",))),
        lambda: client.get("/api/v1/pairing/devices", headers=_hdr(roles=("member",))),
    ):
        resp = call()
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "FORBIDDEN"
    # 匿名同样进不了管理端点
    assert client.get("/api/v1/pairing/devices").status_code == 401


def test_list_and_revoke(client, yard, db_session):
    code, _ = _make_code(client)
    dev = client.post(
        "/api/v1/pairing/exchange", json={"code": code, "device_name": "台账手机"}
    ).json()["data"]

    rows = client.get("/api/v1/pairing/devices", headers=_hdr()).json()["data"]
    assert [d["id"] for d in rows] == [dev["device_id"]]
    assert rows[0]["name"] == "台账手机"
    assert rows[0]["revoked_at"] == "" or rows[0]["revoked_at"] is None

    resp = client.post(f"/api/v1/pairing/devices/{dev['device_id']}/revoke", headers=_hdr())
    assert resp.status_code == 200
    assert resp.json()["data"]["revoked_at"]
    # 幂等：重复吊销仍成功且不报错
    assert (
        client.post(
            f"/api/v1/pairing/devices/{dev['device_id']}/revoke", headers=_hdr()
        ).status_code
        == 200
    )
    device = db_session.get(PairDevice, dev["device_id"])
    assert device is not None and device.revoked_at is not None


def test_cross_yard_revoke_not_found(client, yard, db_session):
    # 小院 B 的管理员吊销小院 A 的设备：统一 404（防枚举）
    other_t = create_yard(db_session, name="配对小院B")
    db_session.commit()
    code, _ = _make_code(client)
    dev = client.post(
        "/api/v1/pairing/exchange", json={"code": code, "device_name": "A的手机"}
    ).json()["data"]
    resp = client.post(
        f"/api/v1/pairing/devices/{dev['device_id']}/revoke",
        headers=_hdr(other_t.id, roles=("yard_admin",)),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"
    # 小院 B 看不到小院 A 的设备
    assert client.get("/api/v1/pairing/devices", headers=_hdr(other_t.id)).json()["data"] == []


# ---------------------------------------------------------------------------
# pair_url 的 LAN host 分支
# ---------------------------------------------------------------------------


def test_lan_ip_host_and_advertise_port(client, yard, monkeypatch):
    monkeypatch.setattr(pairing_service, "detect_local_ipv4", lambda: "192.168.6.20")
    _, url = _make_code(client)
    qs = parse_qs(urlparse(url).query)
    assert qs["host"][0] == f"192.168.6.20:{settings.pairing_advertise_port}"


def test_host_header_fallback_without_route(client, yard, monkeypatch):
    # 无出网路由（探测失败）→ 回退请求 Host 头（TestClient 默认 testserver）
    monkeypatch.setattr(pairing_service, "detect_local_ipv4", lambda: None)
    _, url = _make_code(client)
    qs = parse_qs(urlparse(url).query)
    assert qs["host"][0] == f"testserver:{settings.pairing_advertise_port}"


def test_pair_token_cannot_pass_regular_auth(client, yard):
    """口径实锤：现有鉴权依赖只认 type=access，pair 令牌被常规接口 401。"""
    code, _ = _make_code(client)
    dev = client.post(
        "/api/v1/pairing/exchange", json={"code": code, "device_name": "越权尝试"}
    ).json()["data"]
    resp = client.get(
        "/api/v1/pairing/devices", headers={"Authorization": f"Bearer {dev['token']}"}
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"
