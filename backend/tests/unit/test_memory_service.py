"""记忆底座：写入、检索、权限、运行复盘与日摘要（离线）。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.core.deps import YardContext
from app.core.errors import AppError
from app.db.base import Base
from app.modules.commander.models import CommanderRun, CommanderSubtask
from app.modules.memory import service as memory_service
from app.modules.memory.models import (
    KIND_EPISODE,
    KIND_SUMMARY,
    SCOPE_USER,
    SCOPE_YARD,
    SOURCE_DAILY,
    SOURCE_RUN,
    VISIBILITY_PRIVATE,
    VISIBILITY_YARD,
    AgentMemory,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'memory.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()


def _ctx(user_id: str, roles: list[str] | None = None) -> YardContext:
    return YardContext(
        yard_id="yard-memory",
        user_id=user_id,
        roles=list(roles or []),
        request_id="req-memory",
    )


def test_private_memory_only_visible_to_owner(db, monkeypatch):
    # 用二维确定性向量替换真实 embedding，测试只验证边界与排序逻辑
    monkeypatch.setattr(memory_service, "_embed", lambda _text: [1.0, 0.0])
    memory_service.write_memory(
        db,
        yard_id="yard-memory",
        scope_type=SCOPE_USER,
        scope_key="u1",
        kind="preference",
        title="用户偏好",
        content="用户喜欢蓝色的 PPT 风格",
        visibility=VISIBILITY_PRIVATE,
        created_by="u1",
    )
    db.commit()

    owner_hits = memory_service.retrieve(db, _ctx("u1"), "PPT 风格", mark_used=False)
    other_hits = memory_service.retrieve(db, _ctx("u2"), "PPT 风格", mark_used=False)
    assert len(owner_hits) == 1
    assert other_hits == []


def test_yard_summary_visible_to_members(db, monkeypatch):
    monkeypatch.setattr(memory_service, "_embed", lambda _text: [1.0, 0.0])
    memory_service.write_memory(
        db,
        yard_id="yard-memory",
        scope_type=SCOPE_YARD,
        scope_key="",
        kind=KIND_SUMMARY,
        title="日复盘",
        content="今天完成了三份材料",
        visibility=VISIBILITY_YARD,
    )
    db.commit()

    hits = memory_service.retrieve(db, _ctx("u2"), "材料", mark_used=False)
    assert len(hits) == 1
    assert hits[0].kind == KIND_SUMMARY


def test_keyword_fallback_when_embedding_unavailable(db, monkeypatch):
    monkeypatch.setattr(memory_service, "_embed", lambda _text: None)
    memory_service.write_memory(
        db,
        yard_id="yard-memory",
        scope_type=SCOPE_YARD,
        scope_key="",
        kind="fact",
        title="番茄病害",
        content="番茄晚疫病在连阴天高发，需要提前通风降湿",
        visibility=VISIBILITY_YARD,
    )
    db.commit()

    hits = memory_service.retrieve(db, _ctx("u1"), "番茄晚疫病", mark_used=False)
    assert len(hits) == 1
    assert "晚疫病" in hits[0].content


def test_run_episode_is_idempotent(db, monkeypatch):
    monkeypatch.setattr(memory_service, "_embed", lambda _text: [1.0, 0.0])
    run = CommanderRun(
        yard_id="yard-memory",
        session_id="s1",
        user_id="u1",
        goal="给小院做宣传 PPT",
        plan_json={"deliverable": {"format": "pptx"}},
        status="completed",
        tokens_total=1200,
        latency_ms=3000,
        roles_json=[],
    )
    db.add(run)
    db.flush()
    db.add(
        CommanderSubtask(
            run_id=run.id,
            yard_id="yard-memory",
            seq=1,
            title="生成 PPT",
            instruction="调用 doc.generate",
            expected_output="pptx",
            agent_source="commander",
            agent_name="指挥官",
            status="ok",
            depends_on_json=[],
            artifact_json=[{"title": "宣传小院.pptx"}],
        )
    )
    db.commit()

    memory_service.record_run_episode(db, run=run, subtasks=list(run_subtasks(db, run.id)))
    memory_service.record_run_episode(db, run=run, subtasks=list(run_subtasks(db, run.id)))
    db.commit()

    count = db.scalar(
        select(func.count())
        .select_from(AgentMemory)
        .where(AgentMemory.kind == KIND_EPISODE, AgentMemory.source_type == SOURCE_RUN)
    )
    assert count == 1
    row = db.scalar(select(AgentMemory).where(AgentMemory.kind == KIND_EPISODE))
    assert "宣传 PPT" in row.content
    assert "宣传小院.pptx" in row.content


def run_subtasks(db, run_id: str):
    return list(
        db.scalars(
            select(CommanderSubtask)
            .where(CommanderSubtask.run_id == run_id)
            .order_by(CommanderSubtask.seq.asc())
        )
    )


def test_daily_summary_is_idempotent(db, monkeypatch):
    monkeypatch.setattr(memory_service, "_embed", lambda _text: [1.0, 0.0])
    day = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
    run = CommanderRun(
        yard_id="yard-memory",
        session_id="s1",
        user_id="u1",
        goal="整理月度数据",
        plan_json={"fallback": False},
        status="completed",
        tokens_total=800,
        latency_ms=1000,
        roles_json=[],
        created_at=day,
    )
    db.add(run)
    db.flush()
    db.add(
        CommanderSubtask(
            run_id=run.id,
            yard_id="yard-memory",
            seq=1,
            title="汇总",
            instruction="汇总",
            expected_output="表格",
            agent_source="commander",
            agent_name="指挥官",
            status="ok",
            depends_on_json=[],
        )
    )
    db.commit()

    first = memory_service.summarize_day(db, yard_id="yard-memory", day=day.date(), created_by="u1")
    db.commit()
    second = memory_service.summarize_day(
        db, yard_id="yard-memory", day=day.date(), created_by="u1"
    )
    db.commit()

    assert first is not None and second is not None
    assert first.id == second.id
    assert "整理月度数据" in second.content
    count = db.scalar(
        select(func.count()).select_from(AgentMemory).where(AgentMemory.source_type == SOURCE_DAILY)
    )
    assert count == 1


def test_format_for_prompt_marks_untrusted_boundary():
    hit = memory_service.MemoryHit(
        id="m1",
        kind=KIND_EPISODE,
        title="运行复盘",
        content="上次做 PPT 用了 12 页",
        score=0.9,
        scope_type=SCOPE_USER,
        visibility=VISIBILITY_PRIVATE,
        source_type=SOURCE_RUN,
        source_id="r1",
        importance=0.6,
        created_at=None,
    )
    block = memory_service.format_for_prompt([hit])
    assert block.startswith("<memory>")
    assert block.endswith("</memory>")
    assert "不是指令" in block
    assert "12 页" in block


def test_delete_private_memory_requires_owner_or_manager(db, monkeypatch):
    monkeypatch.setattr(memory_service, "_embed", lambda _text: [1.0, 0.0])
    row = memory_service.write_memory(
        db,
        yard_id="yard-memory",
        scope_type=SCOPE_USER,
        scope_key="u1",
        kind="fact",
        title="私有",
        content="只有 u1 能删",
        visibility=VISIBILITY_PRIVATE,
        created_by="u1",
    )
    db.commit()

    with pytest.raises(AppError):
        memory_service.delete_memory(db, _ctx("u2"), row.id)

    memory_service.delete_memory(db, _ctx("u2", roles=["yard_owner"]), row.id)
    db.commit()
    assert row.status == "deleted"
