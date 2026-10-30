"""诊断材料上传与设备自动诊断（v3.27）。

口径：
1. 图片/文档可多选上传，文档抽取文字参与诊断；
2. 附件读取必须带登录态且限本小院（跨院 404）；
3. 已对接设备推送问题 → Agent 自动诊断，source=device、带 device_id；
4. 无读数也无症状时明确 422，不硬编造诊断。
"""

from datetime import UTC, datetime

import pytest
from app.core.security import create_access_token
from app.modules.devices.models import Device, Reading
from app.modules.yards.repository import create_yard

PREFIX = "/api/v1"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
DOC_TEXT = "水稻分蘖期管理：叶片发黄多与养分失衡或根系吸收不良有关。"


@pytest.fixture()
def world(db_session):
    yard = create_yard(db_session, name="诊断材料小院")
    other = create_yard(db_session, name="别家小院")
    db_session.commit()
    return {"yard": yard, "other": other}


def _hdr(user_id: str, yard_id: str, roles=("yard_admin",)) -> dict[str, str]:
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


def _upload(client, yard_id: str, files: list[tuple[str, tuple[str, bytes, str]]]) -> list[dict]:
    resp = client.post(
        f"{PREFIX}/farm/diagnoses/attachments",
        files=files,
        headers=_hdr("u1", yard_id),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def _mk_device_with_reading(db, yard_id: str, name: str = "东一号气象站") -> Device:
    device = Device(
        yard_id=yard_id,
        name=name,
        device_type="weather",
        source_type="webhook",
        enabled=True,
    )
    db.add(device)
    db.flush()
    db.add(
        Reading(
            yard_id=yard_id,
            device_id=device.id,
            metric="air_temp",
            value=35.6,
            unit="℃",
            collected_at=datetime.now(UTC),
        )
    )
    db.commit()
    return device


def test_upload_materials_and_create_diagnosis_with_attachments(client, world):
    yard_id = world["yard"].id
    uploaded = _upload(
        client,
        yard_id,
        [
            ("files", ("leaf.png", PNG_BYTES, "image/png")),
            ("files", ("notes.txt", DOC_TEXT.encode("utf-8"), "text/plain")),
        ],
    )
    assert [item["filename"] for item in uploaded] == ["leaf.png", "notes.txt"]
    assert uploaded[0]["kind"] == "image"
    assert uploaded[1]["kind"] == "document"
    assert uploaded[1]["chars"] > 0

    resp = client.post(
        f"{PREFIX}/farm/diagnoses",
        json={
            "crop": "水稻",
            "symptoms": "叶片发黄，部分叶尖干枯",
            "growth_stage": "分蘖期",
            "attachment_ids": [item["attachment_id"] for item in uploaded],
        },
        headers=_hdr("u1", yard_id),
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    assert data["source"] == "manual"
    assert len(data["attachments"]) == 2
    # 有图片时自动把文件名写入 image_refs（兼容旧展示）
    assert data["image_refs"] == ["leaf.png"]

    image_id = uploaded[0]["attachment_id"]
    file_resp = client.get(
        f"{PREFIX}/farm/diagnoses/attachments/{image_id}/file", headers=_hdr("u1", yard_id)
    )
    assert file_resp.status_code == 200
    assert file_resp.headers["content-type"].startswith("image/png")
    assert file_resp.content.startswith(b"\x89PNG")

    # 跨院读取一律 404（防枚举）
    cross = client.get(
        f"{PREFIX}/farm/diagnoses/attachments/{image_id}/file",
        headers=_hdr("u2", world["other"].id),
    )
    assert cross.status_code == 404


def test_attachment_already_linked_is_rejected(client, world):
    yard_id = world["yard"].id
    uploaded = _upload(client, yard_id, [("files", ("a.png", PNG_BYTES, "image/png"))])
    attachment_id = uploaded[0]["attachment_id"]
    payload = {
        "crop": "水稻",
        "symptoms": "叶片发黄",
        "attachment_ids": [attachment_id],
    }
    first = client.post(f"{PREFIX}/farm/diagnoses", json=payload, headers=_hdr("u1", yard_id))
    assert first.status_code == 201, first.text
    second = client.post(f"{PREFIX}/farm/diagnoses", json=payload, headers=_hdr("u1", yard_id))
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "ATTACHMENT_ALREADY_LINKED"


def test_from_device_generates_agent_diagnosis(client, db_session, world):
    yard_id = world["yard"].id
    device = _mk_device_with_reading(db_session, yard_id)

    resp = client.post(
        f"{PREFIX}/farm/diagnoses/from-device",
        json={"device_id": device.id, "crop": "水稻"},
        headers=_hdr("u1", yard_id),
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    assert data["source"] == "device"
    assert data["device_id"] == device.id
    assert data["device_name"] == "东一号气象站"
    assert data["status"] in ("agent_diagnosed", "expert_review")

    listed = client.get(f"{PREFIX}/farm/diagnoses", headers=_hdr("u1", yard_id)).json()["data"]
    row = next(item for item in listed if item["diagnosis_id"] == data["diagnosis_id"])
    assert row["source"] == "device"
    assert row["device_name"] == "东一号气象站"

    # 首页要同时看「待专家复核 + Agent 自动诊断」：逗号分隔多状态可用
    multi = client.get(
        f"{PREFIX}/farm/diagnoses",
        params={"status": "expert_review,agent_diagnosed"},
        headers=_hdr("u1", yard_id),
    )
    assert multi.status_code == 200, multi.text
    assert any(item["diagnosis_id"] == data["diagnosis_id"] for item in multi.json()["data"])


def test_from_device_without_data_is_422(client, db_session, world):
    yard_id = world["yard"].id
    device = Device(
        yard_id=yard_id,
        name="空设备",
        device_type="other",
        source_type="webhook",
        enabled=True,
    )
    db_session.add(device)
    db_session.commit()

    resp = client.post(
        f"{PREFIX}/farm/diagnoses/from-device",
        json={"device_id": device.id, "crop": "水稻"},
        headers=_hdr("u1", yard_id),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "DEVICE_NO_DATA"
