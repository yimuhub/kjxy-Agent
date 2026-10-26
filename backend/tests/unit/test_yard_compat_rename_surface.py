"""tenant→yard 重命名后的对外兼容面回归测试。

覆盖三类"代码库之外仍带旧拼写"的存量：
- 已签发给浏览器/桌面端的 JWT（claim 键 tenant_id）；
- 未更新客户端 POST 的登录请求体字段 tenant_id；
- 部署切换前入队的 Celery 上下文键 tenant_id，以及迁移改写前
  缓存/序列化引用里的 trust_level 旧值 tenant_material；
- 迁移 0022 改写过用户名后，老用户手里仍记着的旧演示账号名。
"""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from app.core.config import settings
from app.core.deps import get_current_user
from app.core.errors import AppError
from app.core.security import ALGORITHM, decode_token
from app.modules.agriculture import worker_tasks
from app.modules.agriculture.qa_agent import _LEGACY_SOURCES, _detect_conflicting_values
from app.modules.auth import repository as auth_repo
from app.modules.auth import service as auth_service
from app.modules.auth.schemas import LoginRequest
from app.modules.pairing import service as pairing_service
from app.modules.yards import repository as yard_repo
from fastapi.security import HTTPAuthorizationCredentials

# ---------------------------------------------------------------- JWT 双读


def _legacy_access_token(**claims) -> str:
    payload = {
        "type": "access",
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        "iat": datetime.now(UTC),
        **claims,
    }
    return jwt.encode(payload, settings.app_secret_key, algorithm=ALGORITHM)


def test_deps_current_user_accepts_legacy_tenant_claim():
    """未更新的在手 access 令牌（tenant_id）仍能解析出小院上下文。"""
    token = _legacy_access_token(sub="u1", tenant_id="legacy_yard", roles=["yard_admin"])
    request = SimpleNamespace(state=SimpleNamespace(request_id="req_x"))
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    user = get_current_user(request, creds)
    assert user.yard_id == "legacy_yard"
    assert user.roles == ["yard_admin"]


def test_deps_current_user_new_claim_precedence():
    token = _legacy_access_token(sub="u1", yard_id="new_yard", tenant_id="legacy_yard", roles=[])
    request = SimpleNamespace(state=SimpleNamespace(request_id="req_x"))
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    user = get_current_user(request, creds)
    assert user.yard_id == "new_yard"


# ---------------------------------------------------------------- pair 令牌（第二签发方）


def _legacy_pair_token(**claims) -> str:
    payload = {
        "kind": "pair",
        "type": "pair",
        "exp": datetime.now(UTC) + timedelta(days=1),
        "iat": datetime.now(UTC),
        **claims,
    }
    return jwt.encode(payload, settings.app_secret_key, algorithm=ALGORITHM)


def test_verify_pair_token_backfills_legacy_tenant_claim():
    """重命名前发给桌面端的设备令牌：verify 后统一以 yard_id 呈现。"""
    token = _legacy_pair_token(sub="dev1", device_id="dev1", tenant_id="old_yard")
    payload = pairing_service.verify_pair_token(token)
    assert payload["yard_id"] == "old_yard"


def test_verify_pair_token_new_claim_untouched():
    token = pairing_service.create_pair_token(device_id="dev1", yard_id="new_yard")
    payload = pairing_service.verify_pair_token(token)
    assert payload["yard_id"] == "new_yard"


def test_verify_pair_token_rejects_access_type():
    with pytest.raises(AppError):
        pairing_service.verify_pair_token(_legacy_access_token(sub="u1", tenant_id="t"))


# ---------------------------------------------------------------- 登录请求体双收


def test_login_request_accepts_legacy_tenant_id_body():
    req = LoginRequest.model_validate({"username": "u", "password": "p", "tenant_id": "t1"})
    assert req.yard_id == "t1"


def test_login_request_accepts_new_yard_id_body():
    req = LoginRequest.model_validate({"username": "u", "password": "p", "yard_id": "y1"})
    assert req.yard_id == "y1"


def test_login_request_new_field_wins_when_both_present():
    req = LoginRequest.model_validate(
        {"username": "u", "password": "p", "yard_id": "y1", "tenant_id": "t1"}
    )
    assert req.yard_id == "y1"


def test_login_request_output_contract_stays_yard_id():
    req = LoginRequest.model_validate({"username": "u", "password": "p", "tenant_id": "t1"})
    dumped = req.model_dump()
    assert dumped["yard_id"] == "t1"
    assert "tenant_id" not in dumped


def test_login_request_python_kwargs_still_work():
    req = LoginRequest(username="u", password="p", yard_id="y1")
    assert req.yard_id == "y1"


def test_login_endpoint_legacy_body_selects_right_yard(client, db_session):
    """多小院用户用旧字段名登录：选中的小院即请求里指定的那个。"""
    t1 = yard_repo.create_yard(db_session, name="小院A")
    t2 = yard_repo.create_yard(db_session, name="小院B")
    alice = auth_repo.create_user(db_session, username="alice", password="pw-alice")
    yard_repo.add_membership(db_session, yard_id=t1.id, user_id=alice.id, role="yard_admin")
    yard_repo.add_membership(db_session, yard_id=t2.id, user_id=alice.id, role="viewer")
    db_session.commit()

    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "pw-alice", "tenant_id": t2.id},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    payload = decode_token(data["access_token"], expected_type="access")
    assert payload["yard_id"] == t2.id
    assert payload["roles"] == ["viewer"]


# ---------------------------------------------------------------- 旧演示用户名登录

LEGACY = "tenant_admin"
RENAMED = "yard_admin"


def test_authenticate_falls_back_to_renamed_demo_user(db_session):
    """改名迁移把演示账号换成了 yard_admin，用户仍输旧名时不得被锁在门外。"""
    auth_repo.create_user(
        db_session, username=RENAMED, password="pw-demo", display_name="小院管理员"
    )
    db_session.commit()

    user = auth_service.authenticate(db_session, LEGACY, "pw-demo")
    assert user.username == RENAMED


def test_authenticate_legacy_name_still_checks_password(db_session):
    auth_repo.create_user(db_session, username=RENAMED, password="pw-demo")
    db_session.commit()

    with pytest.raises(AppError) as exc:
        auth_service.authenticate(db_session, LEGACY, "wrong-password")
    assert exc.value.code == "INVALID_CREDENTIALS"


def test_authenticate_prefers_real_legacy_named_user(db_session):
    """库里真有一个叫 tenant_admin 的用户（迁移冲突保护保留了旧名）时，
    他的凭据只属于他自己，不能被改名后的账号顶替。"""
    real = auth_repo.create_user(db_session, username=LEGACY, password="pw-real")
    auth_repo.create_user(db_session, username=RENAMED, password="pw-other")
    db_session.commit()

    assert auth_service.authenticate(db_session, LEGACY, "pw-real").id == real.id
    with pytest.raises(AppError):
        auth_service.authenticate(db_session, LEGACY, "pw-other")


def test_authenticate_unknown_legacy_name_still_rejected(db_session):
    auth_repo.create_user(db_session, username="someone", password="pw-demo")
    db_session.commit()

    with pytest.raises(AppError) as exc:
        auth_service.authenticate(db_session, LEGACY, "pw-demo")
    assert exc.value.code == "INVALID_CREDENTIALS"


def test_login_endpoint_accepts_legacy_demo_username(client, db_session):
    demo_yard = yard_repo.create_yard(db_session, name="演示科技小院")
    user = auth_repo.create_user(db_session, username=RENAMED, password="pw-demo")
    yard_repo.add_membership(db_session, yard_id=demo_yard.id, user_id=user.id, role="yard_admin")
    db_session.commit()

    resp = client.post("/api/v1/auth/login", json={"username": LEGACY, "password": "pw-demo"})
    assert resp.status_code == 200, resp.text
    payload = decode_token(resp.json()["data"]["access_token"], expected_type="access")
    assert payload["sub"] == user.id
    assert payload["yard_id"] == demo_yard.id


# ---------------------------------------------------------------- Celery 在途载荷


def test_context_yard_id_new_key_first():
    assert worker_tasks._context_yard_id({"yard_id": "y1", "tenant_id": "t1"}) == "y1"
    assert worker_tasks._context_yard_id({"tenant_id": "t1"}) == "t1"
    assert worker_tasks._context_yard_id({}) == ""


def test_run_knowledge_qa_inflight_legacy_context(monkeypatch):
    """部署前入队的 qa.answer 载荷只有 tenant_id：上下文不得拿到空小院。"""
    captured: dict = {}

    class _StubAgent:
        async def run(self, inputs, ctx):
            captured["ctx"] = ctx
            return SimpleNamespace(status="succeeded", output={"answer": "ok"})

    monkeypatch.setattr("app.modules.agriculture.qa_agent.AgricultureQAAgent", _StubAgent)
    ctx = {"tenant_id": "old_y"}
    result = asyncio.run(worker_tasks.run_knowledge_qa({"question": "q"}, ctx))
    assert captured["ctx"].yard_id == "old_y"
    assert result["status"] == "succeeded"


def test_run_knowledge_qa_new_context_key(monkeypatch):
    captured: dict = {}

    class _StubAgent:
        async def run(self, inputs, ctx):
            captured["ctx"] = ctx
            return SimpleNamespace(status="succeeded", output={"answer": "ok"})

    monkeypatch.setattr("app.modules.agriculture.qa_agent.AgricultureQAAgent", _StubAgent)
    asyncio.run(worker_tasks.run_knowledge_qa({}, {"yard_id": "new_y"}))
    assert captured["ctx"].yard_id == "new_y"


# ---------------------------------------------------------------- 旧持久化信任级值


def test_legacy_sources_covers_both_spellings():
    """冲突检测必须同时认识迁移后的 yard_material 与缓存里的 tenant_material。"""
    assert "yard_material" in _LEGACY_SOURCES
    assert "tenant_material" in _LEGACY_SOURCES
    assert "upload" in _LEGACY_SOURCES


@pytest.mark.parametrize("legacy_source", ["yard_material", "tenant_material", "upload"])
def test_conflict_detection_fires_for_old_and_new_spelling(legacy_source):
    citations = [
        {"source": "expert_correction", "quote": "稀释 1:50 使用"},
        {"source": legacy_source, "quote": "按 1:100 稀释"},
    ]
    warning = _detect_conflicting_values("建议稀释倍数 1:50。", citations)
    assert warning is not None  # 旧拼写不得让新旧数值冲突漏报
