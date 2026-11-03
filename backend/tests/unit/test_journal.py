"""工作日志/月报测试（v3.58，角色工作台 P0）。

单测：证据聚合、草稿幂等、状态机（提交/审核/驳回）、统计、越权口径。
集成：operator 建→交→expert 审→导出 全流程 + 监管聚合权限。
"""

from datetime import UTC, datetime, timedelta

import pytest
from app.core.deps import YardContext
from app.core.errors import AppError
from app.modules.audit.models import AuditLog
from app.modules.journal import service
from app.modules.yards.repository import create_yard


def _ctx(user_id="u-op", roles=("operator",)) -> YardContext:
    return YardContext(yard_id="", user_id=user_id, roles=list(roles), request_id="req")


@pytest.fixture()
def yard(db_session):
    y = create_yard(db_session, name="日志小院")
    db_session.commit()
    return y


def _audit(db, yard_id, user_id, action="farm.record", days_ago=0):
    db.add(
        AuditLog(
            yard_id=yard_id,
            actor_id=user_id,
            action=action,
            resource_type="test",
            resource_id="x",
            result="success",
            request_id="req",
            created_at=datetime.now(UTC) - timedelta(days=days_ago),
            updated_at=datetime.now(UTC) - timedelta(days=days_ago),
        )
    )


def test_collect_evidence_counts(db_session, yard):
    _audit(db_session, yard.id, "u-op", "farm.record")
    _audit(db_session, yard.id, "u-op", "farm.record")
    _audit(db_session, yard.id, "u-op", "device.action")
    db_session.commit()
    ev = service.collect_evidence(db_session, yard.id, "u-op", datetime.now(UTC).date())
    assert ev["audit_actions"]["farm.record"] == 2
    assert ev["audit_actions"]["device.action"] == 1
    assert ev["sessions"] == 0


def test_build_draft_idempotent_and_renders(db_session, yard):
    _audit(db_session, yard.id, "u-op")
    db_session.commit()
    ctx = _ctx()
    object.__setattr__(ctx, "yard_id", yard.id)
    row = service.build_draft(db_session, ctx, "daily", datetime.now(UTC).date())
    again = service.build_draft(db_session, ctx, "daily", datetime.now(UTC).date())
    assert row.id == again.id  # 幂等
    assert "工作日志" in row.content_md and "农事台账记录" in row.content_md
    assert row.status == "draft"


def test_submit_review_state_machine(db_session, yard):
    ctx_op = _ctx()
    object.__setattr__(ctx_op, "yard_id", yard.id)
    row = service.build_draft(db_session, ctx_op, "daily", datetime.now(UTC).date())
    service.save_content(db_session, ctx_op, row.id, "# 工作日志\n- 巡塘两次", "日志 11-04")
    submitted = service.submit(db_session, ctx_op, row.id)
    assert submitted.status == "submitted"

    # 提交后不能编辑
    with pytest.raises(AppError) as e:
        service.save_content(db_session, ctx_op, row.id, "x")
    assert e.value.code == "JOURNAL_NOT_EDITABLE"

    # 驳回回 draft（带意见），再提交再通过
    ctx_expert = _ctx("u-expert", ("expert",))
    object.__setattr__(ctx_expert, "yard_id", yard.id)
    rejected = service.review(db_session, ctx_expert, row.id, "reject", "补充培训人数")
    assert rejected.status == "draft" and rejected.review_note == "补充培训人数"
    service.submit(db_session, ctx_op, row.id)
    approved = service.review(db_session, ctx_expert, row.id, "approve", None)
    assert approved.status == "approved"


def test_submit_only_owner(db_session, yard):
    ctx_a = _ctx("u-a")
    object.__setattr__(ctx_a, "yard_id", yard.id)
    row = service.build_draft(db_session, ctx_a, "daily", datetime.now(UTC).date())
    ctx_b = _ctx("u-b")
    object.__setattr__(ctx_b, "yard_id", yard.id)
    with pytest.raises(AppError) as e:
        service.submit(db_session, ctx_b, row.id)
    assert e.value.code == "FORBIDDEN"


def test_stats_counts(db_session, yard):
    ctx = _ctx()
    object.__setattr__(ctx, "yard_id", yard.id)
    month = datetime.now(UTC).strftime("%Y-%m")
    row = service.build_draft(db_session, ctx, "daily", datetime.now(UTC).date())
    service.submit(db_session, ctx, row.id)
    stat = service.stats(db_session, ctx, month)
    assert stat["daily_total"] >= 1 and stat["daily_submitted"] >= 1
    assert stat["active_days"] >= 1


def test_monthly_draft_aggregates_active_days(db_session, yard):
    # 两天有活动 → 月报草稿列出活跃天
    _audit(db_session, yard.id, "u-op", action="farm.record", days_ago=0)
    _audit(db_session, yard.id, "u-op", action="farm.record", days_ago=1)
    db_session.commit()
    ctx = _ctx()
    object.__setattr__(ctx, "yard_id", yard.id)
    row = service.build_draft(db_session, ctx, "monthly", datetime.now(UTC).date())
    assert "工作月报" in row.content_md
    assert row.sources_json["active_days"], "两天有活动应至少聚合出活跃天"


def test_export_monthly_requires_rows(db_session, yard):
    ctx = _ctx()
    object.__setattr__(ctx, "yard_id", yard.id)
    assert service.export_monthly_docx_raw(db_session, ctx, "1999-01") is None
    service.build_draft(db_session, ctx, "daily", datetime.now(UTC).date())
    raw = service.export_monthly_docx_raw(db_session, ctx, datetime.now(UTC).strftime("%Y-%m"))
    assert raw is not None and raw["blocks"]


def test_aggregate_overview_counts_and_stale(db_session, yard):
    _audit(db_session, yard.id, "u-op")
    db_session.commit()
    ctx = _ctx()
    object.__setattr__(ctx, "yard_id", yard.id)
    service.build_draft(db_session, ctx, "daily", datetime.now(UTC).date())
    month = datetime.now(UTC).strftime("%Y-%m")
    data = service.aggregate_overview(db_session, month=month)
    mine = next(i for i in data["items"] if i["yard_id"] == yard.id)
    assert mine["journal_count"] == 1 and mine["active_authors"] == 1
    assert mine["stale"] is False


def test_journal_rows_are_yard_scoped(db_session, yard):
    other = create_yard(db_session, name="隔壁小院")
    ctx_a = _ctx()
    object.__setattr__(ctx_a, "yard_id", yard.id)
    row = service.build_draft(db_session, ctx_a, "daily", datetime.now(UTC).date())
    ctx_other = _ctx()
    object.__setattr__(ctx_other, "yard_id", other.id)
    assert service.get_or_none(db_session, other.id, row.id) is None
    with pytest.raises(AppError):
        service.submit(db_session, ctx_other, row.id)
