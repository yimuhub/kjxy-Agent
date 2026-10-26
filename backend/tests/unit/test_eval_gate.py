"""Skill 评测门禁单元测试（第四期 4.2；规范 V3.0 §11.2 / §11.4）。

runner 是同步长耗时的（51 用例逐个答题），本文件用
unittest.mock.patch("app.modules.evolution.service.run_eval_set") 统一 mock 掉，
不触碰真实评估集与问答 Agent。
"""

from unittest.mock import patch

import pytest
from app.core.deps import YardContext
from app.core.errors import AppError
from app.eval.runner import CaseResult, EvalReport
from app.modules.audit.models import AuditLog
from app.modules.evolution import service
from app.modules.evolution.models import EvalRun

_PATCH_TARGET = "app.modules.evolution.service.run_eval_set"


def _member_ctx() -> YardContext:
    return YardContext(yard_id="t1", user_id="u1", roles=["member"], request_id="req_gate")


def _expert_ctx() -> YardContext:
    return YardContext(yard_id="t1", user_id="u1", roles=["expert"], request_id="req_gate")


def _definition(evaluation: dict | None) -> dict:
    d = {"name": "gate_skill"}
    if evaluation is not None:
        d["evaluation"] = evaluation
    return d


def _make_pending_review(db, code="gate_skill", version="1.0.0", evaluation=None):
    s = service.create_skill(
        db, _member_ctx(), skill_code=code, version=version, definition=_definition(evaluation)
    )
    service.review_skill(db, _member_ctx(), s.id, "submit", None)  # draft→testing
    service.review_skill(db, _member_ctx(), s.id, "submit", None)  # testing→pending_review
    db.refresh(s)
    return s


def _report(pass_rate: float, total: int = 4, failed_ids: tuple[str, ...] = ()) -> EvalReport:
    results = [
        CaseResult(
            case_id=f"c{i}",
            category="common",
            passed=(f"c{i}" not in failed_ids),
            reason="mock",
        )
        for i in range(1, total + 1)
    ]
    passed = sum(1 for r in results if r.passed)
    return EvalReport(
        test_set_id="qa_eval_v1",
        version="1.0.0",
        minimum_pass_rate=0.8,
        total=total,
        passed=passed,
        failed=total - passed,
        pass_rate=pass_rate,
        gate_passed=pass_rate >= 0.8,
        case_results=results,
    )


def _runs(db, code="gate_skill", version="1.0.0") -> list[EvalRun]:
    return (
        db.query(EvalRun)
        .filter(EvalRun.skill_code == code, EvalRun.skill_version == version)
        .order_by(EvalRun.created_at.desc())
        .all()
    )


def _eval_definition(**overrides) -> dict:
    evaluation = {"test_set_id": "qa_eval_v1", "minimum_pass_rate": 0.8}
    evaluation.update(overrides)
    return evaluation


# ---------------------------------------------------------------- 门禁拦截


def test_gate_blocks_low_pass_rate(db_session):
    s = _make_pending_review(db_session, evaluation=_eval_definition())
    with patch(_PATCH_TARGET, return_value=_report(0.5, failed_ids=("c1", "c2"))) as m:
        with pytest.raises(AppError) as e:
            service.review_skill(db_session, _expert_ctx(), s.id, "approve", None)
    assert e.value.code == "EVAL_GATE_BLOCKED"
    assert e.value.http_status == 409
    assert e.value.details["pass_rate"] == 0.5
    assert e.value.details["minimum_pass_rate"] == 0.8
    assert e.value.details["failed_case_ids"] == ["c1", "c2"]
    assert e.value.details["total"] == 4
    m.assert_called_once_with("qa_eval_v1", m.call_args.args[1])

    # 未转移到 published，且拦截那一次也留痕了
    db_session.refresh(s)
    assert s.status == "pending_review"
    assert s.approved_by is None
    runs = _runs(db_session)
    assert len(runs) == 1
    assert runs[0].pass_rate == 0.5
    assert (runs[0].passed, runs[0].failed, runs[0].total) == (2, 2, 4)
    assert runs[0].test_set_id == "qa_eval_v1"
    assert runs[0].created_by == "u1"


def test_gate_missing_test_set_id_blocked(db_session):
    # evaluation 存在但没配 test_set_id → 无法执行评估 → 409 拦截，runner 未被调用
    s = _make_pending_review(db_session, evaluation={"minimum_pass_rate": 0.8})
    with patch(_PATCH_TARGET) as m:
        with pytest.raises(AppError) as e:
            service.review_skill(db_session, _expert_ctx(), s.id, "approve", None)
    assert e.value.code == "EVAL_GATE_BLOCKED"
    assert e.value.http_status == 409
    m.assert_not_called()
    assert _runs(db_session) == []


def test_gate_invalid_minimum_pass_rate(db_session):
    s = _make_pending_review(db_session, evaluation=_eval_definition(minimum_pass_rate=1.5))
    with pytest.raises(AppError) as e:
        service.review_skill(db_session, _expert_ctx(), s.id, "approve", None)
    assert e.value.code == "INVALID_REQUEST"
    assert e.value.http_status == 400


# ---------------------------------------------------------------- 门禁放行


def test_gate_passes_high_pass_rate(db_session):
    s = _make_pending_review(db_session, evaluation=_eval_definition())
    with patch(_PATCH_TARGET, return_value=_report(0.9)) as m:
        s = service.review_skill(db_session, _expert_ctx(), s.id, "approve", None)
    assert s.status == "published"
    assert s.approved_by == "u1"
    m.assert_called_once()
    runs = _runs(db_session)
    assert len(runs) == 1
    assert runs[0].pass_rate == 0.9
    # 审计留痕：skill.eval_gate_run
    audit = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "skill.eval_gate_run", AuditLog.resource_id == s.id)
        .all()
    )
    assert len(audit) == 1
    assert audit[0].metadata_json["pass_rate"] == 0.9


def test_gate_pass_rate_from_skill_config(db_session):
    # Skill 自己的 minimum_pass_rate 更严：0.9 时 0.8 通过率应被拦截
    s = _make_pending_review(db_session, evaluation=_eval_definition(minimum_pass_rate=0.9))
    with patch(_PATCH_TARGET, return_value=_report(0.8)):
        with pytest.raises(AppError) as e:
            service.review_skill(db_session, _expert_ctx(), s.id, "approve", None)
    assert e.value.code == "EVAL_GATE_BLOCKED"
    assert e.value.details["minimum_pass_rate"] == 0.9


def test_gate_default_minimum_pass_rate(db_session):
    # 没配 minimum_pass_rate → 缺省 0.8
    s = _make_pending_review(db_session, evaluation={"test_set_id": "qa_eval_v1"})
    with patch(_PATCH_TARGET, return_value=_report(0.85)):
        s = service.review_skill(db_session, _expert_ctx(), s.id, "approve", None)
    assert s.status == "published"


# ---------------------------------------------------------------- skip_gate


def test_gate_skip_gate_allows_publish(db_session):
    s = _make_pending_review(db_session, evaluation=_eval_definition(skip_gate=True))
    with patch(_PATCH_TARGET) as m:
        s = service.review_skill(db_session, _expert_ctx(), s.id, "approve", None)
    m.assert_not_called()
    assert s.status == "published"
    # 不跑评估集就不插 eval_runs
    assert _runs(db_session) == []
    # 但审计日志记一笔
    audit = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "skill.eval_gate_skipped", AuditLog.resource_id == s.id)
        .all()
    )
    assert len(audit) == 1


# ---------------------------------------------------------------- 向后兼容


def test_no_evaluation_config_skips_gate(db_session):
    # 存量 Skill 未声明 evaluation → 门禁不触发，原有流程不受影响
    s = _make_pending_review(db_session, evaluation=None)
    with patch(_PATCH_TARGET) as m:
        s = service.review_skill(db_session, _expert_ctx(), s.id, "approve", None)
    m.assert_not_called()
    assert s.status == "published"
    assert _runs(db_session) == []


def test_list_eval_runs_scoped_by_skill(db_session):
    a = _make_pending_review(db_session, code="skill_a", evaluation=_eval_definition())
    b = _make_pending_review(db_session, code="skill_b", evaluation=_eval_definition())
    with patch(_PATCH_TARGET, return_value=_report(0.9)):
        service.review_skill(db_session, _expert_ctx(), a.id, "approve", None)
    with patch(_PATCH_TARGET, return_value=_report(0.75, failed_ids=("c1",))):
        with pytest.raises(AppError):
            service.review_skill(db_session, _expert_ctx(), b.id, "approve", None)

    items_a, total_a = service.list_eval_runs(db_session, _expert_ctx(), a.id, 1, 20)
    assert total_a == 1
    assert items_a[0].skill_code == "skill_a"
    assert items_a[0].pass_rate == 0.9

    items_b, total_b = service.list_eval_runs(db_session, _expert_ctx(), b.id, 1, 20)
    assert total_b == 1
    assert items_b[0].skill_code == "skill_b"
    assert items_b[0].pass_rate == 0.75

    # 分页
    items_p1, total = service.list_eval_runs(db_session, _expert_ctx(), a.id, 1, 10)
    assert total == 1 and len(items_p1) == 1

    # 不存在的 skill → 404
    with pytest.raises(AppError) as e:
        service.list_eval_runs(db_session, _expert_ctx(), "no-such-id", 1, 20)
    assert e.value.code == "SKILL_NOT_FOUND"
