"""一句话生成的幂等键时间桶（F-3：旧任务不得永久吃掉同一句话的重新生成）。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.modules.agents import agent_generate_router as gen


def _key_at(instant: datetime, *, utterance: str = "病虫害助手", user_id: str = "user-1") -> str:
    class _Frozen:
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001, ANN202
            return instant

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(gen, "datetime", _Frozen)
        return gen._generate_idempotency_key("yard-1", user_id, utterance)


def test_same_window_same_utterance_reuses_one_key() -> None:
    assert _key_at(datetime(2026, 11, 3, 10, 0, 5, tzinfo=UTC)) == _key_at(
        datetime(2026, 11, 3, 10, 0, 25, tzinfo=UTC)
    )


def test_next_window_gets_a_fresh_key() -> None:
    """跨过窗口必须换键：上一轮任务已结束，且 (yard_id, idempotency_key) 有唯一约束，
    同键重投会把用户带回旧草稿甚至撞约束。"""
    assert _key_at(datetime(2026, 11, 3, 10, 0, 29, tzinfo=UTC)) != _key_at(
        datetime(2026, 11, 3, 10, 0, 31, tzinfo=UTC)
    )


def test_key_shape_fits_the_column() -> None:
    key = _key_at(datetime(2026, 11, 3, 10, 0, 0, tzinfo=UTC))
    assert key.startswith("agent-gen-")
    assert len(key) <= 64


def test_key_varies_with_utterance_and_user() -> None:
    moment = datetime(2026, 11, 3, 10, 0, 0, tzinfo=UTC)
    base = _key_at(moment)
    assert _key_at(moment, utterance="客服助手") != base
    assert _key_at(moment, user_id="user-2") != base
