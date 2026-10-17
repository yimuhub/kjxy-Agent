"""seed 安全守卫测试：非开发环境拒绝执行、SEED_ALLOW_PRODUCTION 放行、随机口令开关。"""

import pytest
from app.db import seed as seed_mod


@pytest.fixture()
def _calls(monkeypatch):
    """拦截真实 seed()，只记录调用参数（避免测试触碰真实数据库）。"""
    calls: list[dict] = []
    monkeypatch.setattr(seed_mod, "seed", lambda **kw: calls.append(kw))
    return calls


def test_seed_refused_outside_development(monkeypatch, _calls):
    monkeypatch.setattr(seed_mod.settings, "app_env", "production")
    monkeypatch.delenv("SEED_ALLOW_PRODUCTION", raising=False)
    with pytest.raises(SystemExit) as exc:
        seed_mod.main()
    assert exc.value.code == 1
    assert _calls == []


def test_seed_refused_in_test_env_without_flag(monkeypatch, _calls):
    monkeypatch.setattr(seed_mod.settings, "app_env", "test")
    monkeypatch.setenv("SEED_ALLOW_PRODUCTION", "yes")  # 必须是大写 YES
    with pytest.raises(SystemExit) as exc:
        seed_mod.main()
    assert exc.value.code == 1
    assert _calls == []


def test_seed_allowed_outside_development_with_flag(monkeypatch, _calls):
    monkeypatch.setattr(seed_mod.settings, "app_env", "production")
    monkeypatch.setenv("SEED_ALLOW_PRODUCTION", "YES")
    monkeypatch.delenv("SEED_RANDOM_PASSWORDS", raising=False)
    seed_mod.main()
    assert _calls == [{"random_passwords": False}]


def test_seed_development_needs_no_flag(monkeypatch, _calls):
    monkeypatch.setattr(seed_mod.settings, "app_env", "development")
    monkeypatch.delenv("SEED_ALLOW_PRODUCTION", raising=False)
    monkeypatch.delenv("SEED_RANDOM_PASSWORDS", raising=False)
    seed_mod.main()
    assert _calls == [{"random_passwords": False}]


def test_seed_random_passwords_env_passthrough(monkeypatch, _calls):
    monkeypatch.setattr(seed_mod.settings, "app_env", "development")
    monkeypatch.setenv("SEED_RANDOM_PASSWORDS", "YES")
    seed_mod.main()
    assert _calls == [{"random_passwords": True}]


def test_random_password_is_random_and_long_enough():
    generated = {seed_mod._random_password() for _ in range(20)}
    assert len(generated) == 20
    assert all(len(p) >= 12 for p in generated)
