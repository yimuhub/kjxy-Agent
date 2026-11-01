"""指挥官设置：默认值、严格校验、部分更新（离线）。"""

from __future__ import annotations

import pytest
from app.core.errors import AppError
from app.modules.commander import settings as commander_settings
from app.modules.commander.settings import DEFAULT_SETTINGS
from app.modules.yards.repository import create_yard


@pytest.fixture()
def yard(db_session):
    row = create_yard(db_session, name="指挥官设置小院")
    db_session.commit()
    return row


def test_defaults_are_complete_and_stable(db_session, yard):
    cfg = commander_settings.settings_for(db_session, yard.id)
    assert cfg == DEFAULT_SETTINGS
    assert cfg["display_name"] == "指挥官"
    assert cfg["emoji"] == "🧭"
    assert cfg["max_parallel"] == 3
    assert cfg["fallback_parallel"] == 2
    assert cfg["max_subtasks"] == 6
    assert cfg["token_budget"] == 120000
    assert cfg["supplier_id"] is None
    # v3.40：默认不问澄清、失败自主补救，一句话目标直接开跑
    assert cfg["clarify_enabled"] is True
    assert cfg["preview_plan"] is False
    assert cfg["auto_remediate"] is True
    assert cfg["remediate_rounds"] == 5
    assert cfg["daily_agent_limit"] == 10


def test_partial_update_merges_with_defaults(db_session, yard):
    updated = commander_settings.update_settings(
        db_session,
        yard_id=yard.id,
        payload={"max_parallel": 5, "display_name": "总指挥"},
        actor_id="u-owner",
        request_id="req-cfg",
    )
    assert updated["max_parallel"] == 5
    assert updated["display_name"] == "总指挥"
    # 未提交字段保持默认
    assert updated["fallback_parallel"] == 2
    db_session.commit()

    again = commander_settings.settings_for(db_session, yard.id)
    assert again["max_parallel"] == 5
    assert again["display_name"] == "总指挥"


@pytest.mark.parametrize(
    "payload",
    [
        {"max_parallel": 0},
        {"max_parallel": 11},
        {"fallback_parallel": 0},
        {"max_subtasks": 1},
        {"max_subtasks": 13},
        {"subtask_timeout_seconds": 10},
        {"run_timeout_seconds": 30},
        {"token_budget": 10},
        {"daily_agent_limit": 0},
        {"daily_agent_limit": 51},
        {"clarify_enabled": 1},
        {"enabled": "yes"},
        {"max_parallel": 3.5},
        {"display_name": ""},
    ],
)
def test_invalid_values_are_rejected(db_session, yard, payload):
    with pytest.raises(AppError) as exc:
        commander_settings.update_settings(
            db_session,
            yard_id=yard.id,
            payload=payload,
            actor_id="u-owner",
            request_id="req-cfg",
        )
    assert exc.value.code == "INVALID_REQUEST"
    assert exc.value.http_status == 400


def test_unknown_field_is_rejected(db_session, yard):
    with pytest.raises(AppError) as exc:
        commander_settings.update_settings(
            db_session,
            yard_id=yard.id,
            payload={"unknown_flag": True},
            actor_id="u-owner",
            request_id="req-cfg",
        )
    assert exc.value.code == "INVALID_REQUEST"
    assert "unknown_flag" in exc.value.message


def test_dirty_stored_value_falls_back_to_defaults(db_session, yard):
    from app.modules.yards.models import YardSettings

    db_session.add(
        YardSettings(
            yard_id=yard.id,
            key="commander",
            value_json={"max_parallel": 999, "enabled": "bad"},
        )
    )
    db_session.commit()
    cfg = commander_settings.settings_for(db_session, yard.id)
    assert cfg == DEFAULT_SETTINGS
