"""演示账号闸门（S-6）：脚本不许在生产写弱口令，存量库要在启动时被看见。"""

from __future__ import annotations

import pytest
from app.core.config import settings
from app.core.security import hash_password
from app.db.seed import (
    DEMO_MEMBER_PASSWORD,
    DEMO_ROSTER,
    SEED_USERS,
    active_demo_usernames,
    guard_demo_data_writes,
)
from app.modules.auth.repository import create_user, get_by_username


def test_guard_blocks_in_production_without_override(monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.delenv("SEED_ALLOW_PRODUCTION", raising=False)
    with pytest.raises(SystemExit) as caught:
        guard_demo_data_writes("python -m app.db.seed")
    assert caught.value.code == 1


def test_guard_allows_production_with_explicit_yes(monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setenv("SEED_ALLOW_PRODUCTION", "YES")
    guard_demo_data_writes("python -m app.db.seed")  # 不抛出即放行


def test_guard_allows_development(monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.delenv("SEED_ALLOW_PRODUCTION", raising=False)
    guard_demo_data_writes("python -m app.db.seed")


def test_repair_script_shares_the_same_guard() -> None:
    """repair_demo_passwords 也写文档弱口令，必须走同一道闸而不是各写一套。"""
    from app.db import repair_demo_passwords

    assert repair_demo_passwords.guard_demo_data_writes is guard_demo_data_writes


def test_active_demo_usernames_flags_documented_passwords(db_session) -> None:
    username, password, display_name, _role = SEED_USERS[0]
    create_user(db_session, username=username, password=password, display_name=display_name)
    db_session.flush()
    assert active_demo_usernames(db_session) == [username]


def test_active_demo_usernames_ignores_hardened_accounts(db_session) -> None:
    username, password, display_name, _role = SEED_USERS[0]
    roster_name, roster_display, _member_role = DEMO_ROSTER[0]
    user = create_user(db_session, username=username, password=password, display_name=display_name)
    user.password_hash = hash_password("Zz9-secure-passphrase-2026")
    create_user(
        db_session,
        username=roster_name,
        password=DEMO_MEMBER_PASSWORD,
        display_name=roster_display,
    )
    db_session.flush()
    hits = active_demo_usernames(db_session)
    assert username not in hits
    assert roster_name in hits
    assert get_by_username(db_session, username) is not None
