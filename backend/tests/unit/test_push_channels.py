"""推送通道测试（W4）：CRUD+掩码 / SSRF / 熔断 / 限频 / 事件挂接 / 主流程隔离。

外呼一律 httpx MockTransport（devices 同款注入：替换 push_service.httpx），
全离线；URL 用 .invalid 保留域（DNS 必不可解析 → net_guard 放行给 mock）。
"""

import json
import os

os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-min-32-chars-for-unit-tests")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("SUPPLIER_KEY_SECRET", Fernet.generate_key().decode())

import httpx  # noqa: E402
import pytest  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.modules.audit.models import AuditLog  # noqa: E402
from app.modules.devices import service as devices_service  # noqa: E402
from app.modules.devices.models import Device, DeviceAlert, DeviceAlertRule  # noqa: E402
from app.modules.push import service as push_service  # noqa: E402
from app.modules.push.models import PushChannel  # noqa: E402
from app.modules.yards.repository import create_yard  # noqa: E402

YARD = "t-push"
KEY_URL = "https://push.invalid/hook?robot=SEC1a2b3c4d"


@pytest.fixture(autouse=True)
def _reset_window():
    push_service._rate_window.reset()
    yield
    push_service._rate_window.reset()


def _hdr(user_id="u-push", yard_id=YARD, roles=("yard_admin",)):
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


def _mk_channel(
    db,
    *,
    name="主通道",
    kind="wecom_robot",
    url: str | None = KEY_URL,
    events=None,
    enabled=True,
):
    ch = PushChannel(
        yard_id=YARD,
        name=name,
        kind=kind,
        url_enc=push_service.prepare_url_enc(url) if url else None,
        events_json=events if events is not None else ["device_alert"],
        enabled=enabled,
        created_by="u-push",
    )
    db.add(ch)
    db.flush()
    return ch


class _FakeHttpx:
    """替身：httpx.Client(...) 返回挂 MockTransport 的真实 Client（devices 同款）。"""

    def __init__(self, handler):
        self._handler = handler

    def Client(self, **kwargs):  # noqa: N802 —— 对齐 httpx 命名
        return httpx.Client(transport=httpx.MockTransport(self._handler))


def _ok_handler(calls):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append({"url": str(request.url), "body": json.loads(request.content)})
        return httpx.Response(200, json={"errcode": 0})

    return handler


# ---------------------------------------------------------------------------
# API：CRUD + 掩码 + 校验 + SSRF + 权限
# ---------------------------------------------------------------------------


@pytest.fixture()
def yard(db_session):
    t = create_yard(db_session, name="推送小院")
    db_session.commit()
    return t


def _payload(**kw):
    base = {"name": "运维群", "kind": "wecom_robot", "url": KEY_URL, "events": ["device_alert"]}
    base.update(kw)
    return base


def test_create_masks_url_and_never_echoes(client, yard):
    resp = client.post("/api/v1/settings/push-channels", json=_payload(), headers=_hdr())
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["url_masked"] == "https://push.invalid/***"
    assert "SEC1a2b3c4d" not in resp.text  # 全响应体无 key 片段
    assert "hook?" not in resp.text  # 连 path 都不回显

    lst = client.get("/api/v1/settings/push-channels", headers=_hdr()).json()["data"]
    assert lst[0]["url_masked"] == "https://push.invalid/***"
    assert "SEC1a2b3c4d" not in json.dumps(lst, ensure_ascii=False)


def test_audit_never_records_url(client, db_session, yard):
    resp = client.post("/api/v1/settings/push-channels", json=_payload(), headers=_hdr())
    assert resp.status_code == 200
    audit = db_session.query(AuditLog).filter(AuditLog.action == "push_channel_create").one()
    assert "SEC1a2b3c4d" not in json.dumps(audit.metadata_json, ensure_ascii=False)
    assert audit.metadata_json["kind"] == "wecom_robot"


def test_patch_url_replaces_and_resets_failures(client, db_session, yard):
    cid = client.post("/api/v1/settings/push-channels", json=_payload(), headers=_hdr()).json()[
        "data"
    ]["id"]
    ch = db_session.get(PushChannel, cid)
    ch.consecutive_failures = 3
    db_session.commit()

    resp = client.patch(
        f"/api/v1/settings/push-channels/{cid}",
        json={"url": "https://ding.invalid/robot?access_token=***"},
        headers=_hdr(),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["url_masked"] == "https://ding.invalid/***"
    assert data["consecutive_failures"] == 0  # 重填即换 = 重新开始
    assert "newtoken" not in resp.text


def test_duplicate_name_and_validation(client, yard):
    resp = client.post("/api/v1/settings/push-channels", json=_payload(), headers=_hdr())
    assert resp.status_code == 200
    dup = client.post("/api/v1/settings/push-channels", json=_payload(), headers=_hdr())
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "DUPLICATE_NAME"

    bad_kind = client.post(
        "/api/v1/settings/push-channels", json=_payload(name="k", kind="sms"), headers=_hdr()
    )
    assert bad_kind.status_code == 400
    bad_event = client.post(
        "/api/v1/settings/push-channels",
        json=_payload(name="e", events=["device_alert", "everything"]),
        headers=_hdr(),
    )
    assert bad_event.status_code == 400


@pytest.mark.parametrize("bad_url", ["http://127.0.0.1:8080/hook", "http://localhost:8080/hook"])
def test_ssrf_localhost_rejected(client, yard, bad_url):
    resp = client.post(
        "/api/v1/settings/push-channels", json=_payload(name="bad", url=bad_url), headers=_hdr()
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "SSRF_BLOCKED"


def test_permissions_and_cross_yard(client, yard, db_session):
    # operator 连列表都不可见
    forbidden = client.get("/api/v1/settings/push-channels", headers=_hdr(roles=("operator",)))
    assert forbidden.status_code == 403
    created = client.post("/api/v1/settings/push-channels", json=_payload(), headers=_hdr())
    cid = created.json()["data"]["id"]

    # 别的小院管理员：不存在口径（404，不泄露存在性）
    other = _hdr("u-x", "t-other-yard")
    resp = client.patch(
        f"/api/v1/settings/push-channels/{cid}", json={"enabled": False}, headers=other
    )
    assert resp.status_code == 404
    assert client.delete(f"/api/v1/settings/push-channels/{cid}", headers=other).status_code == 404

    ok_del = client.delete(f"/api/v1/settings/push-channels/{cid}", headers=_hdr())
    assert ok_del.status_code == 200
    assert client.delete(f"/api/v1/settings/push-channels/{cid}", headers=_hdr()).status_code == 404


# ---------------------------------------------------------------------------
# service.dispatch：报文 / 过滤 / 熔断 / 限频
# ---------------------------------------------------------------------------


def test_dispatch_wecom_payload_chinese(db_session):
    calls: list = []
    ch = _mk_channel(db_session)
    push_service.httpx = _FakeHttpx(_ok_handler(calls))
    try:
        stats = push_service.dispatch(
            db_session,
            yard_id=YARD,
            event_type="device_alert",
            payload={
                "title": "【设备告警】1 号棚室：温度 当前 41.0℃高于阈值 35.0℃",
                "metric": "temp",
            },
        )
    finally:
        push_service.httpx = httpx
    assert stats["sent"] == 1
    body = calls[0]["body"]
    assert body["msgtype"] == "markdown"
    content = body["markdown"]["content"]
    assert "设备告警" in content and "1 号棚室" in content and "温度" in content
    assert ch.consecutive_failures == 0


def test_dispatch_dingtalk_and_generic_shapes(db_session):
    calls: list = []
    _mk_channel(db_session, name="钉钉", kind="dingtalk")
    _mk_channel(db_session, name="通用", kind="generic_webhook")
    push_service.httpx = _FakeHttpx(_ok_handler(calls))
    try:
        push_service.dispatch(
            db_session,
            yard_id=YARD,
            event_type="device_alert",
            payload={"title": "t", "value": 1},
        )
    finally:
        push_service.httpx = httpx
    bodies = [c["body"] for c in calls]
    ding = next(b for b in bodies if b["msgtype"] == "markdown" and "title" in b["markdown"])
    assert ding["markdown"]["title"] == "设备告警" and "text" in ding["markdown"]
    generic = next(b for b in bodies if "event" in b)
    assert generic["event"] == "device_alert" and generic["payload"]["value"] == 1
    assert "ts" in generic


def test_dispatch_filters_unsubscribed_disabled_and_missing_url(db_session):
    calls: list = []
    _mk_channel(db_session, name="只订告警")  # 只订阅 device_alert
    _mk_channel(db_session, name="停用", enabled=False)
    _mk_channel(db_session, name="无URL", url=None)
    push_service.httpx = _FakeHttpx(_ok_handler(calls))
    try:
        stats = push_service.dispatch(
            db_session, yard_id=YARD, event_type="workorder_created", payload={"title": "x"}
        )
    finally:
        push_service.httpx = httpx
    assert calls == [] and stats["sent"] == 0

    # 换订 workorder_created 后通道可发；停用/无 URL 的仍不发
    # （phase-1 的 finally 已还原真实 httpx，第二阶段须重新挂桩）
    ch = db_session.query(PushChannel).filter(PushChannel.name == "只订告警").one()
    ch.events_json = ["workorder_created"]
    push_service._rate_window.reset()
    push_service.httpx = _FakeHttpx(_ok_handler(calls))
    try:
        push_service.dispatch(
            db_session, yard_id=YARD, event_type="workorder_created", payload={"title": "x"}
        )
    finally:
        push_service.httpx = httpx
    assert len(calls) == 1


def test_dispatch_circuit_breaker_after_5_failures(db_session):
    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    ch = _mk_channel(db_session)
    push_service.httpx = _FakeHttpx(failing)
    try:
        for _ in range(push_service.PUSH_MAX_FAILURES):
            push_service.dispatch(
                db_session, yard_id=YARD, event_type="device_alert", payload={"title": "x"}
            )
        db_session.commit()
        stats = push_service.dispatch(  # 已熔断：不再外呼
            db_session, yard_id=YARD, event_type="device_alert", payload={"title": "x"}
        )
    finally:
        push_service.httpx = httpx
    db_session.refresh(ch)
    assert ch.enabled is False
    assert ch.consecutive_failures == push_service.PUSH_MAX_FAILURES
    assert stats["sent"] == 0 and stats["failed"] == 0  # 停用后连失败都不再产生
    assert (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "push_channel_disabled", AuditLog.yard_id == YARD)
        .count()
        == 1
    )


def test_success_resets_and_decrypt_failure_counted(db_session):
    calls: list = []
    ch = _mk_channel(db_session)
    ch.consecutive_failures = 4
    db_session.flush()
    push_service.httpx = _FakeHttpx(_ok_handler(calls))
    try:
        push_service.dispatch(
            db_session, yard_id=YARD, event_type="device_alert", payload={"title": "x"}
        )
    finally:
        push_service.httpx = httpx
    assert ch.consecutive_failures == 0

    # URL 密文不可解（主密钥轮换）：记失败、不抛出、不熔断轰炸
    # 先停用健康通道——本阶段只验解密失败路径（真实 httpx 已在上面还原，
    # 若健康通道仍订阅会走真实网络发送产生额外失败计数）
    ch.enabled = False
    broken = _mk_channel(db_session, name="坏密文")
    broken.url_enc = "not-a-fernet-token"
    db_session.flush()
    stats = push_service.dispatch(
        db_session, yard_id=YARD, event_type="device_alert", payload={"title": "x"}
    )
    assert stats["failed"] == 1 and broken.enabled is True  # 单次解密失败远未到阈值


def test_rate_limit_10_per_60s_window(db_session):
    calls: list = []
    ch = _mk_channel(db_session)
    push_service.httpx = _FakeHttpx(_ok_handler(calls))
    try:
        # 连续 12 次事件：滑窗只放行前 10 条，其余静默跳过（不发不炸）
        stats_last = None
        for _ in range(push_service.PUSH_MAX_MESSAGES_PER_WINDOW + 2):
            stats_last = push_service.dispatch(
                db_session, yard_id=YARD, event_type="device_alert", payload={"title": "x"}
            )
    finally:
        push_service.httpx = httpx
    assert len(calls) == push_service.PUSH_MAX_MESSAGES_PER_WINDOW
    assert stats_last is not None and stats_last["sent"] == 0 and stats_last["skipped"] == 1
    assert ch.consecutive_failures == 0


# ---------------------------------------------------------------------------
# 事件挂接：DeviceAlert → dispatch 被调；发送异常不影响告警主事务
# ---------------------------------------------------------------------------


def _device_with_rule(db):
    dev = Device(yard_id=YARD, name="高温棚传感器", device_type="greenhouse", source_type="webhook")
    db.add(dev)
    db.flush()
    rule = DeviceAlertRule(yard_id=YARD, metric="temp", op="gt", threshold=35.0, enabled=True)
    db.add(rule)
    db.flush()
    return dev, rule


def test_device_alert_triggers_push_with_chinese_payload(db_session):
    dev, _rule = _device_with_rule(db_session)
    _mk_channel(db_session)
    calls: list = []
    push_service.httpx = _FakeHttpx(_ok_handler(calls))
    try:
        result = devices_service.ingest_values(db_session, dev, {"temp": 41.0})
        db_session.commit()
    finally:
        push_service.httpx = httpx
    assert result["alerts_triggered"] == 1
    assert len(calls) == 1
    content = calls[0]["body"]["markdown"]["content"]
    assert "【设备告警】" in content and "高温棚传感器" in content  # 标题=告警文案


def test_push_failure_never_breaks_alert_transaction(db_session):
    """发送异常（DNS/连接失败）：告警照常入库、ingest 正常返回，只记通道失败。"""

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns down", request=request)

    dev, _rule = _device_with_rule(db_session)
    ch = _mk_channel(db_session)
    push_service.httpx = _FakeHttpx(boom)
    try:
        result = devices_service.ingest_values(db_session, dev, {"temp": 42.0})
        db_session.commit()
    finally:
        push_service.httpx = httpx
    assert result["alerts_triggered"] == 1
    assert db_session.query(DeviceAlert).count() == 1  # 主事务完好
    db_session.refresh(ch)
    assert ch.consecutive_failures == 1 and ch.enabled is True
