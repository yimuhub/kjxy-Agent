"""agent_builder 单测：匹配打分、每日配额、可见性判权、预览。"""

import pytest
from app.core.deps import YardContext
from app.core.errors import AppError
from app.modules.agents import agent_builder
from app.modules.tasks.service import TaskService
from app.modules.yards.repository import create_yard
from app.modules.yards.settings_service import update_setting


def _ctx(roles=("member",), yard_id="t1", user_id="u1") -> YardContext:
    return YardContext(yard_id=yard_id, user_id=user_id, roles=list(roles), request_id="req_t")


# ---------------------------------------------------------------------------
# 匹配
# ---------------------------------------------------------------------------


def _pool():
    return {
        "skills": [
            {"code": "skill:1", "name": "病虫害防治", "summary": "识别与防治", "source": "builtin"},
            {"code": "skill:2", "name": "水肥管理", "summary": "灌溉施肥", "source": "builtin"},
        ],
        "plugins": [
            {"code": "plugin:wf1", "name": "灌溉调度", "summary": "自动排程", "source": "local"},
        ],
    }


def test_match_skills_hits_best_entry():
    needs = [{"need": "病虫害识别", "keywords": ["病虫害", "防治"], "reason": "核心能力"}]
    matched, missing = agent_builder.match_skills(_pool(), needs)
    assert len(matched) == 1 and missing == []
    assert matched[0]["code"] == "skill:1" and matched[0]["kind"] == "skill"


def test_match_skills_below_threshold_goes_to_requirements():
    needs = [{"need": "遥感影像解析", "keywords": ["遥感", "卫星"], "reason": "平台没有"}]
    matched, missing = agent_builder.match_skills(_pool(), needs)
    assert matched == []
    assert missing[0]["status"] == "missing" and missing[0]["need"] == "遥感影像解析"


def test_match_skills_caps_and_dedups():
    needs = [
        {"need": "病虫害防治", "keywords": ["病虫害"], "reason": "r1"},
        {"need": "病虫害复诊", "keywords": ["病虫害"], "reason": "r2"},
        {"need": "水肥调度", "keywords": ["水肥管理"], "reason": "r3"},
    ]
    matched, missing = agent_builder.match_skills(_pool(), needs)
    assert [m["code"] for m in matched] == ["skill:1", "skill:2"]
    assert [m["need"] for m in missing] == ["病虫害复诊"]


def test_match_skills_max_five():
    pool = {
        "skills": [
            {"code": f"skill:{i}", "name": f"能力{i}", "summary": "", "source": "builtin"}
            for i in range(8)
        ]
    }
    needs = [{"need": f"需要{i}", "keywords": [f"能力{i}"], "reason": ""} for i in range(8)]
    matched, missing = agent_builder.match_skills(pool, needs)
    assert len(matched) == agent_builder.MAX_MATCHES == 5
    assert len(missing) == 3


# ---------------------------------------------------------------------------
# 配额与判权
# ---------------------------------------------------------------------------


def _set_limit(db, yard_id, **cfg):
    update_setting(
        db, yard_id=yard_id, key="agent_generate", value=cfg, actor_id="u1", request_id="req"
    )


def test_quota_counts_only_agent_generate_tasks(db_session):
    yard = create_yard(db_session, name="配额小院")
    db_session.commit()
    ctx = _ctx(yard_id=yard.id)
    for task_type in ("agent_generate", "knowledge_qa"):
        TaskService.create(db_session, ctx, task_type=task_type, input={}, request_id="req")
    db_session.commit()
    used = agent_builder.used_today(db_session, yard.id, "u1")
    assert used == 1
    snapshot = agent_builder.quota_snapshot(db_session, yard.id, "u1")
    assert snapshot["enabled"] is True and snapshot["limit"] == agent_builder._daily_limit(
        agent_builder._settings(db_session, yard.id)
    )


def test_ensure_daily_quota_over_limit_429(db_session):
    yard = create_yard(db_session, name="限额小院")
    db_session.commit()
    _set_limit(db_session, yard.id, daily_limit=1)
    db_session.commit()
    ctx = _ctx(yard_id=yard.id)
    TaskService.create(db_session, ctx, task_type="agent_generate", input={}, request_id="req")
    db_session.commit()
    with pytest.raises(AppError) as e:
        agent_builder.ensure_daily_quota(db_session, yard.id, "u1")
    assert e.value.code == "AGENT_GENERATE_QUOTA" and e.value.http_status == 429


def test_ensure_daily_quota_disabled_403(db_session):
    yard = create_yard(db_session, name="停用小院")
    db_session.commit()
    _set_limit(db_session, yard.id, enabled=False)
    db_session.commit()
    with pytest.raises(AppError) as e:
        agent_builder.ensure_daily_quota(db_session, yard.id, "u1")
    assert e.value.code == "AGENT_GENERATE_DISABLED" and e.value.http_status == 403


def test_visibility_permission_matrix():
    assert (
        agent_builder.ensure_visibility_allowed(visibility="private", roles=["member"]) == "private"
    )
    assert (
        agent_builder.ensure_visibility_allowed(visibility="public", roles=["yard_admin"])
        == "public"
    )
    assert (
        agent_builder.ensure_visibility_allowed(visibility="public", roles=["platform_admin"])
        == "public"
    )
    with pytest.raises(AppError) as e:
        agent_builder.ensure_visibility_allowed(visibility="public", roles=["member"])
    assert e.value.code == "FORBIDDEN" and e.value.http_status == 403
    # 未知/缺省值：成员一律落 private
    assert (
        agent_builder.ensure_visibility_allowed(visibility="weird", roles=["member"]) == "private"
    )


# ---------------------------------------------------------------------------
# 预览
# ---------------------------------------------------------------------------


def test_request_agent_requires_substantive_text(db_session):
    yard = create_yard(db_session, name="预览小院")
    db_session.commit()
    with pytest.raises(AppError) as e:
        agent_builder.request_agent(db_session, ctx=_ctx(yard_id=yard.id), utterance="短")
    assert e.value.code == "INVALID_REQUEST"


def test_request_agent_returns_policy_and_candidates(db_session):
    yard = create_yard(db_session, name="预览小院二")
    db_session.commit()
    result = agent_builder.request_agent(
        db_session, ctx=_ctx(yard_id=yard.id), utterance="病虫害防治"
    )
    assert result["policy"]["enabled"] is True
    assert isinstance(result["matches"], list) and isinstance(result["keywords"], list)
    # keywords[:12] 截断内应含核心词（中文 2/3-gram）
    assert "病虫害" in result["keywords"]
