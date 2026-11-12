"""知识库解析看门狗与 CAS 抢占的回归测试（审查 D-03 残留场景）。

覆盖三条此前无人兜底的路径：

1. **CAS 抢占**：看门狗兜底与真 worker 同时盯上同一份文档时，只有一方能继续，
   不产生重复 chunk、不重复计费；
2. **看门狗兜底**：broker 可达但无 worker 消费（消息进了队列却没人推进）时，
   文档由 API 进程本机兜底解析，不再永久卡在 queued；
3. **僵尸回收**：解析进程被 kill / OOM 导致卡在 processing 的文档，会被重置回
   queued 并重新排队。
"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta

import pytest
from app.core.config import settings
from app.core.deps import YardContext
from app.modules.knowledge import service
from app.modules.knowledge.models import KnowledgeChunk, KnowledgeDocument
from app.modules.knowledge.parse_watchdog import sweep_once
from app.modules.knowledge.worker_tasks import claim_document, parse_document_sync
from fastapi import UploadFile

TEXT = "水稻分蘖期田间管理：浅水勤灌，促进低位分蘖发生；群体茎蘖数达到预期穗数 80% 时晒田。"


@pytest.fixture()
def storage_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "local_storage_path", str(tmp_path))
    return tmp_path


def _ctx(yard_id: str = "yard-wd") -> YardContext:
    return YardContext(yard_id=yard_id, user_id="u-wd", roles=["yard_admin"], request_id="req-wd")


def _upload(db, ctx, *, title: str = "水稻资料"):
    return service.upload_document(
        db,
        ctx,
        UploadFile(file=io.BytesIO(TEXT.encode("utf-8")), filename="shuidao.txt"),
        title=title,
        request_id="req-wd",
    )


def _age(db, document_id: str, *, seconds: int) -> None:
    """把文档的 updated_at 往前推，模拟"排队很久没人推进"。"""
    row = db.get(KnowledgeDocument, document_id)
    assert row is not None
    row.updated_at = datetime.now(UTC) - timedelta(seconds=seconds)
    db.commit()


# --------------------------------------------------------------- CAS 抢占


def test_claim_document_is_exclusive(db_session, storage_tmp):
    """同一份文档只能被一个执行者抢到解析权；每次抢占代数 +1。"""
    doc = _upload(db_session, _ctx())

    first = claim_document(db_session, doc.id)
    assert first == 1  # 首次抢占：代数 0 → 1
    # 第二次抢占失败：状态已是 processing
    assert claim_document(db_session, doc.id) is None


def test_parse_document_sync_skips_when_claimed(db_session, storage_tmp):
    """已被抢占的文档再次解析直接跳过，不产生 chunk（不重复计费）。"""
    doc = _upload(db_session, _ctx())
    assert claim_document(db_session, doc.id) == 1

    parse_document_sync(db_session, doc.id)

    chunks = db_session.query(KnowledgeChunk).filter(KnowledgeChunk.document_id == doc.id).all()
    assert chunks == []
    row = db_session.get(KnowledgeDocument, doc.id)
    assert row.status == "processing"


def test_parse_document_sync_runs_after_claim(db_session, storage_tmp):
    """正常路径：CAS 抢占成功后照常解析入库。"""
    doc = _upload(db_session, _ctx())
    parse_document_sync(db_session, doc.id)

    row = db_session.get(KnowledgeDocument, doc.id)
    assert row.status == "ready"
    assert row.parse_attempt == 1
    chunks = db_session.query(KnowledgeChunk).filter(KnowledgeChunk.document_id == doc.id).all()
    assert chunks


def test_superseded_parser_yields_at_finalize(db_session, storage_tmp, monkeypatch):
    """代数闸门回归（2026-11-13 二次深审）：僵尸回收/重解析产生新一代解析后，
    **旧的慢执行者**晚到的收尾必须让位，绝不覆盖新一代解析的结果。

    场景重放：新执行者已 claim（代数 1）并完成解析（ready + 切块）；旧执行者
    持有已失效的代数认知（ monkeypatch claim 返回旧代数，模拟"抢占早于
    僵尸回收、收尾晚于新一代完成"）走到收尾闸门。旧实现会无条件覆盖
    状态与切块；现在它在闸门处让位，新一代成果原样保留。
    """
    from app.modules.knowledge import worker_tasks as kb_worker

    doc = _upload(db_session, _ctx())

    # 新一代执行者完整解析：代数 1 → ready + 切块
    parse_document_sync(db_session, doc.id)
    row = db_session.get(KnowledgeDocument, doc.id)
    assert row.status == "ready"
    assert row.parse_attempt == 1
    fresh_chunks = (
        db_session.query(KnowledgeChunk).filter(KnowledgeChunk.document_id == doc.id).all()
    )
    assert fresh_chunks

    # 旧执行者此刻才跑到收尾：claim 直接"返回"它当年抢到的旧代数
    monkeypatch.setattr(kb_worker, "claim_document", lambda db, document_id: 1)
    parse_document_sync(db_session, doc.id)

    row = db_session.get(KnowledgeDocument, doc.id)
    assert row.status == "ready"
    assert row.parse_attempt == 1
    chunks_after = (
        db_session.query(KnowledgeChunk).filter(KnowledgeChunk.document_id == doc.id).all()
    )
    assert len(chunks_after) == len(fresh_chunks)


# --------------------------------------------------------------- 看门狗兜底


def test_watchdog_rescues_stalled_queued_document(db_session, storage_tmp, monkeypatch):
    """broker 可达但无 worker 消费 → 文档排队超时后由看门狗本机兜底解析。

    这是 D-03 残留场景的直接回归：修复前文档永远停在 queued。
    """
    from app.modules.knowledge import parse_watchdog

    doc = _upload(db_session, _ctx())
    # 排队 1 小时没人推进
    _age(db_session, doc.id, seconds=3600)

    parsed: list[str] = []
    monkeypatch.setattr(
        parse_watchdog, "_run_in_thread", lambda document_id: parsed.append(document_id)
    )
    # 心跳不可用 → 按"无活跃 worker"处理，走较短的那档超时
    monkeypatch.setattr(parse_watchdog, "_worker_alive", lambda: False)

    stats = sweep_once(db_session)

    assert stats["rescued"] == 1
    assert parsed == [doc.id]


def test_watchdog_skips_recent_queued_document(db_session, storage_tmp, monkeypatch):
    """刚入队不久的文档不看门狗（避免与正常 worker 抢活）。"""
    from app.modules.knowledge import parse_watchdog

    doc = _upload(db_session, _ctx())
    _age(db_session, doc.id, seconds=10)

    parsed: list[str] = []
    monkeypatch.setattr(
        parse_watchdog, "_run_in_thread", lambda document_id: parsed.append(document_id)
    )
    monkeypatch.setattr(parse_watchdog, "_worker_alive", lambda: False)

    stats = sweep_once(db_session)

    assert stats["rescued"] == 0
    assert parsed == []


def test_watchdog_rescues_when_worker_alive_but_message_dropped(
    db_session, storage_tmp, monkeypatch
):
    """worker 活着但消息被取走丢弃（worker 连错库）→ 长时间无进展也兜底。

    这是走查环境实测到的第二个成因：celery worker 连 SQLite 库、API 连 PostgreSQL 库，
    worker 取走消息后在自己的库里找不到文档直接丢弃。
    """
    from app.modules.knowledge import parse_watchdog

    doc = _upload(db_session, _ctx())
    _age(db_session, doc.id, seconds=3600)

    parsed: list[str] = []
    monkeypatch.setattr(
        parse_watchdog, "_run_in_thread", lambda document_id: parsed.append(document_id)
    )
    monkeypatch.setattr(parse_watchdog, "_worker_alive", lambda: True)

    stats = sweep_once(db_session)

    assert stats["rescued"] == 1
    assert parsed == [doc.id]


def test_watchdog_does_not_rescue_when_worker_alive_and_recent(
    db_session, storage_tmp, monkeypatch
):
    """有活跃 worker 且文档刚入队 → 不兜底，留给 worker 正常消费。"""
    from app.modules.knowledge import parse_watchdog

    doc = _upload(db_session, _ctx())
    _age(db_session, doc.id, seconds=120)

    parsed: list[str] = []
    monkeypatch.setattr(
        parse_watchdog, "_run_in_thread", lambda document_id: parsed.append(document_id)
    )
    monkeypatch.setattr(parse_watchdog, "_worker_alive", lambda: True)

    stats = sweep_once(db_session)

    assert stats["rescued"] == 0
    assert parsed == []


# --------------------------------------------------------------- 僵尸回收


def test_watchdog_resets_zombie_processing(db_session, storage_tmp, monkeypatch):
    """卡在 processing 的僵尸文档被重置回 queued（解析进程被 kill / OOM）。"""
    from app.modules.knowledge import parse_watchdog

    doc = _upload(db_session, _ctx())
    row = db_session.get(KnowledgeDocument, doc.id)
    row.status = "processing"
    db_session.commit()
    _age(db_session, doc.id, seconds=3600)

    monkeypatch.setattr(parse_watchdog, "_worker_alive", lambda: True)

    stats = sweep_once(db_session)

    assert stats["zombie_reset"] == 1
    refreshed = db_session.get(KnowledgeDocument, doc.id)
    assert refreshed.status == "queued"
    assert "看门狗" in (refreshed.error_message or "")


def test_watchdog_ignores_healthy_processing(db_session, storage_tmp, monkeypatch):
    """正在正常解析（processing 未超时）的文档不被回收。"""
    from app.modules.knowledge import parse_watchdog

    doc = _upload(db_session, _ctx())
    row = db_session.get(KnowledgeDocument, doc.id)
    row.status = "processing"
    db_session.commit()

    monkeypatch.setattr(parse_watchdog, "_worker_alive", lambda: True)

    stats = sweep_once(db_session)

    assert stats["zombie_reset"] == 0
    assert db_session.get(KnowledgeDocument, doc.id).status == "processing"


def test_watchdog_ignores_deleted_documents(db_session, storage_tmp, monkeypatch):
    """软删除的文档不参与兜底。"""
    from app.modules.knowledge import parse_watchdog

    doc = _upload(db_session, _ctx())
    row = db_session.get(KnowledgeDocument, doc.id)
    row.deleted_at = datetime.now(UTC)
    db_session.commit()
    _age(db_session, doc.id, seconds=3600)

    parsed: list[str] = []
    monkeypatch.setattr(
        parse_watchdog, "_run_in_thread", lambda document_id: parsed.append(document_id)
    )
    monkeypatch.setattr(parse_watchdog, "_worker_alive", lambda: False)

    stats = sweep_once(db_session)

    assert stats["rescued"] == 0
    assert parsed == []
