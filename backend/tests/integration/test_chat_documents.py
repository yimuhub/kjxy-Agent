"""对话文档生成集成测试（HTTP 端到端，fake provider 全离线）。

覆盖：
1. 五种格式生成 → 附件落库（generated/doc_format）→ 预览 HTML → 下载文件头；
2. 校验：非法 DocModel（400 DOC_MODEL_INVALID）、未知格式（400）；
3. 边界：他人会话 / 他人附件统一 404，上传附件无 DocModel → 415；
4. 对话链路：意图判为文档 → 模型产出 DocModel → 文档卡片 + 说明；
5. 预览安全：CSP sandbox、无脚本、内容全量转义。
"""

import json

import pytest
from app.core.security import create_access_token
from app.modules.llm import providers_fake
from app.modules.yards.repository import create_yard

CHAT = "/api/v1/chat/sessions"

DOC_MODEL = {
    "title": "设备巡检报告",
    "subject": "一号泵站",
    "author": "巡检班组",
    "blocks": [
        {"type": "heading", "level": 1, "text": "巡检概况"},
        {"type": "paragraph", "text": "本次巡检覆盖一号泵站全部机组。"},
        {"type": "bullets", "items": ["油位正常", "皮带张紧度正常"]},
        {"type": "table", "header": ["项目", "结论"], "rows": [["油位", "正常"]]},
        {"type": "pagebreak"},
        {"type": "paragraph", "text": "下周复检轴承温度。"},
    ],
}

MAGIC = {
    "md": b"# ",
    "docx": b"PK\x03\x04",
    "xlsx": b"PK\x03\x04",
    "pptx": b"PK\x03\x04",
    "pdf": b"%PDF",
}


def _hdr(user_id: str = "u-doc", yard_id: str | None = None):
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=["yard_admin"])
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _script_isolation():
    providers_fake.reset_scripted_tool_responses()
    yield
    providers_fake.reset_scripted_tool_responses()


@pytest.fixture()
def yard(db_session):
    t = create_yard(db_session, name="文档小院")
    db_session.commit()
    return t


def _mk_session(client, yard) -> str:
    resp = client.post(CHAT, json={}, headers=_hdr(yard_id=yard.id))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def _create_doc(client, yard, sid: str, fmt: str = "docx", model: dict | None = None) -> dict:
    resp = client.post(
        f"{CHAT}/{sid}/documents",
        json={"format": fmt, "model": model or DOC_MODEL},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


@pytest.mark.parametrize("fmt", ["md", "docx", "xlsx", "pptx", "pdf"])
def test_generate_preview_and_download_each_format(client, yard, fmt):
    sid = _mk_session(client, yard)
    data = _create_doc(client, yard, sid, fmt)
    doc = data["document"]
    assert doc["type"] == "document"
    assert doc["format"] == fmt
    assert doc["filename"].endswith(f".{fmt}")
    assert doc["previewable"] is True
    assert {a["key"] for a in doc["actions"]} == {"preview", "download"}

    prev = client.get(f"/api/v1{data['preview_url']}", headers=_hdr(yard_id=yard.id))
    assert prev.status_code == 200, prev.text
    assert prev.headers["content-type"].startswith("text/html")
    assert "sandbox" in prev.headers["content-security-policy"]
    assert "设备巡检报告" in prev.text
    assert "<script" not in prev.text

    dl = client.get(f"/api/v1{data['download_url']}", headers=_hdr(yard_id=yard.id))
    assert dl.status_code == 200, dl.text
    assert dl.content.startswith(MAGIC[fmt])
    assert "filename*=UTF-8''" in dl.headers["content-disposition"]
    assert dl.headers["x-content-type-options"] == "nosniff"


def test_generated_attachment_listed_with_metadata(client, yard):
    sid = _mk_session(client, yard)
    _create_doc(client, yard, sid, "xlsx")
    resp = client.get(f"{CHAT}/{sid}/attachments", headers=_hdr(yard_id=yard.id))
    items = resp.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["generated"] is True
    assert items[0]["doc_format"] == "xlsx"
    assert "storage_key" not in items[0]


def test_invalid_model_rejected(client, yard):
    sid = _mk_session(client, yard)
    resp = client.post(
        f"{CHAT}/{sid}/documents",
        json={"format": "docx", "model": {"title": "x", "blocks": []}},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "DOC_MODEL_INVALID"


def test_unsupported_format_rejected(client, yard):
    sid = _mk_session(client, yard)
    resp = client.post(
        f"{CHAT}/{sid}/documents",
        json={"format": "doc", "model": DOC_MODEL},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "DOC_FORMAT_UNSUPPORTED"


def test_other_user_cannot_read_document(client, yard):
    sid = _mk_session(client, yard)
    data = _create_doc(client, yard, sid)
    preview = client.get(f"/api/v1{data['preview_url']}", headers=_hdr("u-other", yard.id))
    assert preview.status_code == 404
    download = client.get(f"/api/v1{data['download_url']}", headers=_hdr("u-other", yard.id))
    assert download.status_code == 404
    assert download.json()["error"]["code"] == "DOC_NOT_FOUND"


def test_attachment_of_other_session_not_readable(client, yard):
    sid = _mk_session(client, yard)
    other = _mk_session(client, yard)
    data = _create_doc(client, yard, sid)
    resp = client.get(
        f"{CHAT}/{other}/attachments/{data['document']['attachment_id']}/download",
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 404


def test_uploaded_attachment_has_no_preview(client, yard):
    sid = _mk_session(client, yard)
    up = client.post(
        f"{CHAT}/{sid}/attachments",
        files={"file": ("note.txt", "巡检要点".encode(), "text/plain")},
        headers=_hdr(yard_id=yard.id),
    )
    assert up.status_code == 201, up.text
    aid = up.json()["data"]["id"]
    prev = client.get(f"{CHAT}/{sid}/attachments/{aid}/preview", headers=_hdr(yard_id=yard.id))
    assert prev.status_code == 415
    assert prev.json()["error"]["code"] == "DOC_PREVIEW_UNSUPPORTED"
    dl = client.get(f"{CHAT}/{sid}/attachments/{aid}/download", headers=_hdr(yard_id=yard.id))
    assert dl.status_code == 200
    assert dl.content == "巡检要点".encode()


def test_chat_intent_generates_document_card(client, yard):
    providers_fake.set_scripted_tool_responses(
        [{"content": json.dumps(DOC_MODEL, ensure_ascii=False)}]
    )
    sid = _mk_session(client, yard)
    resp = client.post(
        f"{CHAT}/{sid}/messages",
        json={"content": "写一份设备巡检报告，用 Word 格式"},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 200, resp.text
    a = resp.json()["data"]["assistant_message"]
    assert a["payload"]["type"] == "document"
    assert a["payload"]["format"] == "docx"
    assert "已生成Word 文档" in a["content"]
    assert a["routed_by"] == "local"
    assert a["token_usage"]["total_tokens"] > 0

    prev = client.get(
        f"{CHAT}/{sid}/attachments/{a['payload']['attachment_id']}/preview",
        headers=_hdr(yard_id=yard.id),
    )
    assert prev.status_code == 200
    assert "巡检概况" in prev.text
