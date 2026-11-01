"""交付物识别与确定性文档阶段（离线）。"""

from __future__ import annotations

import pytest
from app.core.deps import YardContext
from app.core.errors import AppError
from app.db.base import Base
from app.modules.chat import document as document_module
from app.modules.commander import deliverables, planner
from app.modules.commander import service as commander_service
from app.modules.commander.models import CommanderRun, CommanderSubtask
from app.modules.yards.repository import create_yard
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker


def test_detect_explicit_formats_only():
    assert deliverables.detect("帮我做一个宣传小院的 PPT") == "pptx"
    assert deliverables.detect("整理成 Word 文档") == "docx"
    assert deliverables.detect("导出 Excel 台账") == "xlsx"
    assert deliverables.detect("生成一份 PDF") == "pdf"
    assert deliverables.detect("讲一下 PPT 怎么排版") == "pptx"


def test_subtask_format_requires_file_action():
    assert (
        deliverables.subtask_format(
            {"title": "生成 PPT 交付物", "instruction": "调用 doc.generate 生成 .pptx"}
        )
        == "pptx"
    )
    assert (
        deliverables.subtask_format(
            {
                "title": "整理幻灯片资料",
                "instruction": "整理演示文稿要点",
                "expected_output": "文本",
            }
        )
        is None
    )
    # 要大纲/文案不是要文件（避免同一轮生成两份 PPT）
    assert (
        deliverables.subtask_format(
            {
                "title": "整合 PPT 文案大纲",
                "instruction": "输出：完整的 PPT 大纲，包含封面、目录与各章节",
                "expected_output": "PPT 逐页文案大纲",
            }
        )
        is None
    )


def test_clean_requirement_strips_delivery_mechanics():
    raw = (
        "制作一份突出智能监测的小院宣传 PPT，并生成可下载的 .pptx 文件\n"
        "调用 doc.generate 生成 .pptx 并作为会话附件交付"
    )
    cleaned = deliverables.clean_requirement(raw)
    assert ".pptx" not in cleaned
    assert "doc.generate" not in cleaned
    assert "智能监测" in cleaned


def test_ensure_plan_deliverable_appends_task():
    plan = {
        "goal": "做 PPT",
        "subtasks": [
            {"seq": 1, "title": "收集资料", "instruction": "收集资料", "depends_on": []},
            {"seq": 2, "title": "写提纲", "instruction": "写提纲", "depends_on": [1]},
        ],
    }
    added = deliverables.ensure_plan_deliverable(plan, "做 PPT", "pptx", max_subtasks=6)
    assert added is True
    assert len(plan["subtasks"]) == 3
    assert plan["subtasks"][2]["title"] == "生成PPT交付物"
    assert plan["subtasks"][2]["depends_on"] == [1, 2]
    assert plan["deliverable"]["format"] == "pptx"


def test_ensure_plan_deliverable_merges_when_at_capacity():
    plan = {
        "goal": "做 PPT",
        "subtasks": [
            {"seq": 1, "title": "收集资料", "instruction": "收集资料", "depends_on": []},
        ],
    }
    assert deliverables.ensure_plan_deliverable(plan, "做 PPT", "pptx", max_subtasks=1) is True
    assert len(plan["subtasks"]) == 1
    assert "doc.generate" in plan["subtasks"][0]["instruction"]
    assert ".pptx" in plan["subtasks"][0]["expected_output"]


def test_ensure_plan_deliverable_keeps_existing_coverage():
    plan = {
        "goal": "做 PPT",
        "subtasks": [
            {
                "seq": 1,
                "title": "生成 PPT",
                "instruction": "调用 doc.generate 生成 .pptx",
                "expected_output": "PPT 文件（.pptx）",
                "depends_on": [],
            }
        ],
    }
    assert deliverables.ensure_plan_deliverable(plan, "做 PPT", "pptx", max_subtasks=6) is False
    assert len(plan["subtasks"]) == 1


def test_fallback_plan_becomes_deliverable_task():
    plan = planner.fallback_plan("做一个 PPT", deliverable="pptx")
    assert plan["fallback"] is True
    assert len(plan["subtasks"]) == 1
    task = plan["subtasks"][0]
    assert task["title"] == "生成PPT交付物"
    assert "doc.generate" in task["instruction"]
    assert ".pptx" in task["expected_output"]
    assert plan["deliverable"] == {"format": "pptx", "label": "PPT 演示文稿"}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'deliverable.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = factory()
    yard = create_yard(db, name="交付物小院")
    db.commit()
    monkeypatch.setattr(commander_service, "_session_factory", factory)
    monkeypatch.setattr(commander_service, "RETRY_BACKOFF_SECONDS", 0.0)
    try:
        yield db, factory, yard
    finally:
        db.close()
        engine.dispose()


def _run_with_doc_task(db, yard) -> CommanderRun:
    run = CommanderRun(
        yard_id=yard.id,
        session_id="s-doc",
        user_id="u-doc",
        goal="做一份宣传小院的 PPT",
        plan_json={"fallback": False, "subtasks": []},
        status="queued",
        max_parallel=1,
        effective_parallel=1,
        degraded=False,
        tokens_total=0,
        roles_json=["yard_admin"],
    )
    db.add(run)
    db.flush()
    db.add(
        CommanderSubtask(
            run_id=run.id,
            yard_id=yard.id,
            seq=1,
            title="生成PPT交付物",
            instruction="调用 doc.generate 生成 .pptx 并作为会话附件交付",
            expected_output="PPT 文件（.pptx）",
            agent_source="commander",
            agent_name="指挥官",
            status="pending",
            depends_on_json=[],
            attempts=0,
        )
    )
    db.commit()
    return run


def _ctx(yard) -> YardContext:
    return YardContext(
        yard_id=yard.id,
        user_id="u-doc",
        roles=["yard_admin"],
        request_id="req-doc",
        session_id="s-doc",
    )


@pytest.mark.asyncio
async def test_deliver_document_returns_artifact(env, monkeypatch):
    db, _factory, yard = env
    run = _run_with_doc_task(db, yard)
    calls: dict[str, object] = {}

    async def fake_doc(_db, **kwargs):
        calls.update(kwargs)
        return (
            {
                "type": "document",
                "attachment_id": "att-1",
                "session_id": run.session_id,
                "filename": "宣传小院.pptx",
                "format": "pptx",
                "size_bytes": 2048,
            },
            "已生成 PPT。",
            {"input_tokens": 2, "output_tokens": 3, "model": "fake"},
        )

    monkeypatch.setattr(document_module, "generate_document_from_chat", fake_doc)
    subtask = db.scalars(select(CommanderSubtask)).first()
    result = await commander_service._deliver_document(
        db,
        run=run,
        subtask=subtask,
        ctx=_ctx(yard),
        fmt="pptx",
        context="上游资料",
    )
    assert calls["fmt"] == "pptx"
    assert calls["session_id"] == "s-doc"
    assert result["artifacts"][0]["format"] == "pptx"
    assert result["artifacts"][0]["attachment_id"] == "att-1"
    assert result["artifacts"][0]["url"].endswith("/download")
    assert result["input_tokens"] == 2
    assert result["output_tokens"] == 3


@pytest.mark.asyncio
async def test_deliver_document_rejects_other_format(env, monkeypatch):
    db, _factory, yard = env
    run = _run_with_doc_task(db, yard)

    async def fake_doc(_db, **_kwargs):
        return (
            {
                "type": "document",
                "attachment_id": "att-1",
                "session_id": run.session_id,
                "filename": "降级.md",
                "format": "md",
                "size_bytes": 10,
            },
            "已降级。",
            {},
        )

    monkeypatch.setattr(document_module, "generate_document_from_chat", fake_doc)
    subtask = db.scalars(select(CommanderSubtask)).first()
    with pytest.raises(AppError) as exc:
        await commander_service._deliver_document(
            db,
            run=run,
            subtask=subtask,
            ctx=_ctx(yard),
            fmt="pptx",
            context="",
        )
    assert exc.value.code == "COMMANDER_DELIVERABLE_MISSING"


@pytest.mark.asyncio
async def test_execute_run_records_document_artifact(env, monkeypatch):
    db, _factory, yard = env
    run = _run_with_doc_task(db, yard)

    async def fake_doc(_db, **_kwargs):
        return (
            {
                "type": "document",
                "attachment_id": "att-9",
                "session_id": run.session_id,
                "filename": "宣传小院.pptx",
                "format": "pptx",
                "size_bytes": 4096,
            },
            "已生成 PPT。",
            {"input_tokens": 1, "output_tokens": 1, "model": "fake"},
        )

    async def fake_summary(_db, _request):
        from app.modules.llm.schemas import LLMResponse

        return LLMResponse(content="已交付 PPT。", input_tokens=1, output_tokens=1, model="fake")

    monkeypatch.setattr(document_module, "generate_document_from_chat", fake_doc)
    monkeypatch.setattr(commander_service, "complete_with_logging", fake_summary)
    summary = await commander_service.execute_run(run.id)

    db.expire_all()
    row = db.scalars(select(CommanderSubtask)).first()
    assert row.status == "ok"
    assert row.artifact_json[0]["attachment_id"] == "att-9"
    assert summary["artifacts"][0]["format"] == "pptx"
    assert summary["failed"] == []


def test_insufficient_excerpt_detects_refusals():
    refusal = "无法确认。检索资料为空，缺乏关于智能监测和节水灌溉的具体信息，因此无法提供条目列表。"
    assert commander_service._insufficient_excerpt(refusal) is True
    assert commander_service._insufficient_excerpt("   ") is True
    outline = "## 智能监测\n- 传感器网络实时采集\n## 节水灌溉\n- 滴灌节水 30%"
    assert commander_service._insufficient_excerpt(outline) is False
    # 长回答即使开头有保留措辞，也按有效素材处理
    long_body = "无法确认全部细节，但可按通用做法整理：\n" + "- 要点\n" * 120
    assert commander_service._insufficient_excerpt(long_body) is False
    receipt = "已由「指挥官」生成PPT 演示文稿《宣传小院》（3 页，37 KB）。"
    assert commander_service._insufficient_excerpt(receipt) is True


def test_delivery_contract_is_appended_once():
    question = commander_service._with_delivery_contract("写三页宣传大纲")
    assert question.startswith("写三页宣传大纲")
    assert "指挥官交付要求" in question
    assert "待补充" in question


def test_dependency_context_skips_empty_refusals(env):
    db, _factory, yard = env
    run = _run_with_doc_task(db, yard)
    db.add_all(
        [
            CommanderSubtask(
                run_id=run.id,
                yard_id=yard.id,
                seq=2,
                title="空手而归",
                instruction="整理资料",
                agent_source="existing",
                status="ok",
                output_excerpt="无法确认。检索资料为空，缺乏具体信息。",
            ),
            CommanderSubtask(
                run_id=run.id,
                yard_id=yard.id,
                seq=3,
                title="可用大纲",
                instruction="写大纲",
                agent_source="existing",
                status="ok",
                output_excerpt="## 智能监测\n- 传感器网络实时采集\n## 节水灌溉\n- 滴灌节水三成",
            ),
        ]
    )
    db.commit()
    context = commander_service._dependency_context(db, run.id, [1, 2, 3])
    assert "无法确认" not in context
    assert "智能监测" in context
    assert "空手而归" not in context


def test_self_executor_hides_doc_tool_for_non_deliverable():
    from app.modules.docs.tool import register_doc_generate_tool

    register_doc_generate_tool()
    ctx = YardContext(yard_id="yard-x", user_id="u-x", roles=[], request_id="r-x")
    assert "doc.generate" in commander_service._self_executor_agent(ctx).allowed_tools
    assert (
        "doc.generate"
        not in commander_service._self_executor_agent(ctx, allow_doc_tool=False).allowed_tools
    )


@pytest.mark.asyncio
async def test_deliver_document_uses_all_prior_material(env, monkeypatch):
    db, _factory, yard = env
    run = CommanderRun(
        yard_id=yard.id,
        session_id="s-doc",
        user_id="u-doc",
        goal="做一份宣传小院的 PPT",
        plan_json={"fallback": False, "subtasks": []},
        status="queued",
        max_parallel=1,
        effective_parallel=1,
        degraded=False,
        tokens_total=0,
        roles_json=["yard_admin"],
    )
    db.add(run)
    db.flush()
    db.add_all(
        [
            CommanderSubtask(
                run_id=run.id,
                yard_id=yard.id,
                seq=1,
                title="技术要点",
                instruction="整理要点",
                agent_source="existing",
                status="ok",
                output_excerpt="## 智能监测\n- 传感器实时采集土壤墒情\n## 节水灌溉\n- 滴灌节水三成",
            ),
            CommanderSubtask(
                run_id=run.id,
                yard_id=yard.id,
                seq=2,
                title="逐页文案",
                instruction="写文案",
                agent_source="existing",
                status="ok",
                output_excerpt='{"slides":[{"title":"封面：智慧小院","content":["智能监测","节水灌溉"]}]}',
            ),
            CommanderSubtask(
                run_id=run.id,
                yard_id=yard.id,
                seq=3,
                title="生成 PPT 文件",
                instruction="调用 doc.generate 生成 .pptx",
                expected_output=".pptx 文件",
                agent_source="commander",
                agent_name="指挥官",
                status="pending",
                depends_on_json=[2],
            ),
        ]
    )
    db.commit()
    calls: dict[str, object] = {}

    async def fake_doc(_db, **kwargs):
        calls.update(kwargs)
        return (
            {
                "type": "document",
                "attachment_id": "att-3",
                "session_id": run.session_id,
                "filename": "宣传小院.pptx",
                "format": "pptx",
                "size_bytes": 4096,
            },
            "已生成 PPT。",
            {},
        )

    monkeypatch.setattr(document_module, "generate_document_from_chat", fake_doc)
    subtask = db.scalars(
        select(CommanderSubtask).where(CommanderSubtask.run_id == run.id, CommanderSubtask.seq == 3)
    ).one()
    await commander_service._deliver_document(
        db, run=run, subtask=subtask, ctx=_ctx(yard), fmt="pptx", context="仅直接依赖"
    )
    material = str(calls["context"])
    assert "传感器实时采集" in material
    assert "智能监测" in material


def test_subtask_format_ignores_copy_task_mentioning_file():
    copy_task = {
        "title": "撰写 PPT 核心文案与结构",
        "instruction": (
            "基于 t1 编写一份宣传小院的 PPT 文稿，按页组织。【验收标准】"
            "成功生成可下载的 .pptx 文件"
        ),
        "expected_output": ".pptx 文件",
    }
    assert deliverables.subtask_format(copy_task) is None
    build_task = {
        "title": "生成 PPT 文件",
        "instruction": "根据大纲生成 .pptx 演示文稿并交付",
        "expected_output": ".pptx 文件",
    }
    assert deliverables.subtask_format(build_task) == "pptx"
