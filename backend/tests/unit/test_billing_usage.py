"""用量口径聚合护栏（2026-11-13 二次深审：聚合下沉 SQL 后的语义锁定）。

守住的性质：
1. 只统计 status='success' 的调用（失败调用不进用量，也不进升级触发器）；
2. input/output 任一为 NULL 的行按 0 计，不整行丢失；
3. 空数据返回零值而不是除零/None；
4. 水位与锚点换算与"剩余额度"一致。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.db.base import Base
from app.modules.billing import usage as billing_usage
from app.modules.llm.models import LLMCall
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'usage.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield session
    finally:
        session.close()


def _call(db, *, status="success", inp=1000, out=500, cost=0.01, days_ago=0):
    db.add(
        LLMCall(
            yard_id="yard-usage",
            task_id=None,
            provider="fake",
            model="m",
            input_tokens=inp,
            output_tokens=out,
            estimated_cost=cost,
            status=status,
            latency_ms=100,
            request_id=f"req-{status}-{inp}",
            prompt_sha="0" * 64,
            created_at=datetime.now(UTC) - timedelta(days=days_ago),
        )
    )


def test_recent_usage_counts_success_only_and_handles_nulls(db):
    _call(db, status="success", inp=1000, out=500, cost=0.01)
    _call(db, status="error", inp=99999, out=99999, cost=9.99)  # 必须被过滤
    db.add(
        LLMCall(  # input/output 为 NULL 的成功行：按 0 计，不能整行丢
            yard_id="yard-usage",
            task_id=None,
            provider="fake",
            model="m",
            input_tokens=None,
            output_tokens=None,
            estimated_cost=0.02,
            status="success",
            latency_ms=50,
            request_id="req-null",
            prompt_sha="0" * 64,
        )
    )
    db.commit()

    recent = billing_usage._recent_usage(db, "yard-usage")
    assert recent["calls"] == 2
    assert recent["tokens"] == 1500.0
    assert abs(recent["cost"] - 0.03) < 1e-9


def test_usage_summary_empty_yard_is_zeroed(db):
    summary = billing_usage.usage_summary(db, "yard-usage")
    assert summary["recent"] == {"tokens": 0, "calls": 0, "cost": 0}
    assert summary["anchor"]["based_on_calls"] == 0
    assert summary["anchor"]["calls_remaining"] == 0
    assert summary["needs_attention"] is False


def test_effective_budget_matches_effective_limit(db):
    """v3.82 聚合护栏：effective_budget 三元组与 effective_limit 逐字段同口径。

    守住的性质：
    - 非无限策略：limit == max(limit_value, used) + grants；
    - 无限策略：limit == inf，但 grants/used 照实返回（用量页要展示水位）；
    - 同一策略重复查询时，budget 与 limit/grant_total 的一致性不随调用顺序漂移。
    """
    import math

    from app.modules.quota import engine as quota_engine
    from app.modules.quota.models import QuotaPolicy

    def _policy(limit_value: float) -> QuotaPolicy:
        return QuotaPolicy(
            yard_id="yard-usage",
            scope_type="yard",
            scope_key="",
            metric="tokens",
            window="month",
            limit_value=limit_value,
            on_exceed="approve",
        )

    # 零余额：limit=0，被 preflight 当"已用尽"
    used, limit, grants = quota_engine.effective_budget(db, _policy(0))
    assert (used, limit, grants) == (0.0, 0.0, 0.0)
    assert quota_engine.effective_limit(db, _policy(0)) == 0.0

    # 有基线：limit == 基线（无用量无临时额度）
    used, limit, grants = quota_engine.effective_budget(db, _policy(5000))
    assert (used, limit, grants) == (0.0, 5000.0, 0.0)

    # 无限策略：limit=inf，grants 照实返回 0（本例无 grant 行）
    used, limit, grants = quota_engine.effective_budget(db, _policy(-1))
    assert used == 0.0
    assert math.isinf(limit)
    assert grants == 0.0
    assert quota_engine.effective_limit(db, _policy(-1)) == math.inf
