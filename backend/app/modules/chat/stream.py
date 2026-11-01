"""对话流式执行：POST /chat/sessions/{id}/messages/stream（SSE）。

帧协议（前端按 event 名分发）：

- ``chat-init`` ：{session_id, user_message} —— 用户发言已落库；
- ``step``      ：{steps:[…]} —— 累计步骤快照（正在阅读/正在选择助手/正在思考…）；
- ``message``   ：{…} —— 最终助手消息（结构与 REST 的 assistant_message 同构，
  刷新后历史一致）；
- ``card``      ：{payload} —— 结构化载荷（能力卡片/市场卡片）；
- ``error``     ：{code, message} —— 执行失败；
- ``chat-end``  ：{status} —— ok｜failed｜aborted。

群聊流（``/messages/group/stream``）在同一帧协议上**只增加一种**
``member-event`` 事件（形状借自 ``agents/teams/stream.py``，不新造协议）：

- ``member-event``：{turn_id, member_index, role_name, agent_id,
  status(running|ok|error), error_text} —— 每位参与成员状态变化各一帧；
- 随后每位**应答者**一条 ``message`` 帧（落库后的真实消息行），有未回答者时
  再补一条"谁没答、为什么"的说明消息帧；``chat-end`` 仍只有 ok|failed。

**会话生命周期（关键约束）**：FastAPI 在端点函数**返回 Response 对象时**就执行
``yield`` 依赖的收尾（``get_db`` 的 ``db.close()``），而响应体是之后才由
Starlette 迭代发送的——所以流执行期间**不能再用请求级 Session 的 ORM 对象**
（会 DetachedInstanceError）。因此：

- 端点只做"发言先落库"，然后把**纯数据**（session_id / 附件 id / 消息字典）
  交给流；
- 执行任务自开短连接（``_session_factory``，与 worker 同一注入点，测试可替换），
  用完即关；
- 生成器只读队列，全程不碰 DB。

客户端断开：取消执行任务并把助手消息以 ``aborted`` 状态落库（诚实记录
"这条被中止了"，历史可核对）。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.progress import StepRecorder
from app.modules.chat import group, service
from app.modules.chat.models import MSG_ABORTED, ROLE_ASSISTANT, ChatMessage

log = get_logger(__name__)

#: 心跳间隔（秒）——模型推理期间没有业务帧，靠心跳保活防代理断连
KEEPALIVE_SECONDS = 15

#: 会话工厂注入点（测试可替换为内存库会话工厂）
_session_factory: Callable[[], Session] | None = None


def _get_session_factory() -> Callable[[], Session]:
    if _session_factory is not None:
        return _session_factory
    from app.db.session import SessionLocal

    return SessionLocal


def _frame(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


async def _run_chat_stream(
    *,
    ctx: YardContext,
    session_id: str,
    content: str,
    attachment_ids: list[str],
    user_message: dict[str, Any],
    target_code: str | None = None,
    execution_mode: str | None = None,
) -> AsyncIterator[str]:
    """执行一轮对话并按帧协议下发。"""
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

    def _notify(steps: list[dict[str, Any]]) -> None:
        queue.put_nowait(("step", {"steps": steps}))

    def _on_commander(event: str, payload: dict[str, Any]) -> None:
        # 指挥官三帧直接复用事件名：commander-plan / subtask-status / commander-summary
        queue.put_nowait((event, payload))

    recorder = StepRecorder(notify=_notify)

    async def _runner() -> None:
        db: Session = _get_session_factory()()
        try:
            session = service.get_session(
                db, yard_id=ctx.yard_id, created_by=ctx.user_id, session_id=session_id
            )
            attachments = service.resolve_attachments(
                db, ctx=ctx, session_id=session_id, attachment_ids=attachment_ids
            )
            outcome = await service.run_and_persist(
                db,
                ctx=ctx,
                session=session,
                content=content,
                attachments=attachments,
                recorder=recorder,
                target_code=target_code,
                execution_mode=execution_mode,
                on_commander_event=_on_commander,
            )
            db.flush()  # 落库并回填主键（message_out 需要 id）
            # 在会话关闭前把 ORM 对象转成普通字典：会话一关，ORM 对象即脱管
            message = service.message_out(outcome["assistant_message"])
            db.commit()
            queue.put_nowait(("result", {"message": message, "payload": outcome.get("payload")}))
        except AppError as e:
            db.rollback()
            queue.put_nowait(("error", {"code": e.code, "message": e.message}))
        except Exception as e:  # noqa: BLE001 —— 兜底：任何异常都要有明确收尾帧
            log.warning("chat stream execution failed: %s", e)
            db.rollback()
            queue.put_nowait(("error", {"code": "INTERNAL_ERROR", "message": "服务暂时不可用。"}))
        finally:
            db.close()
            queue.put_nowait(("end", None))

    task = asyncio.create_task(_runner())
    status = "ok"
    try:
        yield _frame(
            "chat-init",
            {"session_id": session_id, "user_message": user_message},
        )
        while True:
            try:
                kind, payload = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            if kind == "step":
                yield _frame("step", payload)
            elif kind in ("commander-plan", "subtask-status", "commander-summary"):
                yield _frame(kind, payload)
            elif kind == "result":
                card = payload.get("payload")
                if card:
                    yield _frame("card", {"payload": card})
                yield _frame("message", payload["message"])
                break
            elif kind == "error":
                status = "failed"
                yield _frame("error", payload)
                break
            elif kind == "end":
                break
        yield _frame("chat-end", {"status": status})
    except asyncio.CancelledError:
        # 客户端断开：中止执行并诚实记录 aborted
        status = "aborted"
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        _persist_aborted(ctx=ctx, session_id=session_id, user_seq=int(user_message.get("seq") or 0))
        raise
    except Exception:  # noqa: BLE001 —— 流内异常也要有明确收尾帧，不能静默断流
        log.exception("chat stream framing failed session=%s", session_id[:8])
        yield _frame("error", {"code": "INTERNAL_ERROR", "message": "对话流中断，请重试。"})
        yield _frame("chat-end", {"status": "failed"})
    finally:
        if not task.done():
            task.cancel()


def _persist_aborted(*, ctx: YardContext, session_id: str, user_seq: int) -> None:
    """中止落库：助手消息以 aborted 状态入库（历史可核对"被中止"）。

    若助手消息已落库（执行已完成、只是客户端先断开），不再重复补写。
    """
    db: Session = _get_session_factory()()
    try:
        last_seq = db.scalar(
            select(func.max(ChatMessage.seq)).where(
                ChatMessage.session_id == session_id,
                ChatMessage.role == ROLE_ASSISTANT,
            )
        )
        if last_seq is not None and int(last_seq) > user_seq:
            return
        session = service.get_session(
            db, yard_id=ctx.yard_id, created_by=ctx.user_id, session_id=session_id
        )
        service.persist_assistant_message(
            db,
            ctx=ctx,
            session=session,
            content="已停止生成。",
            status=MSG_ABORTED,
            error_code="ABORTED",
        )
        db.commit()
    except Exception as e:  # noqa: BLE001 —— 中止记录失败不应抛出
        log.warning("chat aborted 落库失败 session=%s: %s", session_id[:8], e)
        db.rollback()
    finally:
        db.close()


def build_stream_response(
    *,
    ctx: YardContext,
    session_id: str,
    content: str,
    attachment_ids: list[str],
    user_message: dict[str, Any],
    target_code: str | None = None,
    execution_mode: str | None = None,
) -> StreamingResponse:
    """构建对话 SSE 响应（只传纯数据：请求级 Session 此刻已不可用）。"""
    return StreamingResponse(
        _run_chat_stream(
            ctx=ctx,
            session_id=session_id,
            content=content,
            attachment_ids=attachment_ids,
            user_message=user_message,
            target_code=target_code,
            execution_mode=execution_mode,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _run_group_stream(
    *,
    ctx: YardContext,
    session_id: str,
    content: str,
    attachment_ids: list[str],
    policy: str,
    target: str | None,
    participants: list[dict[str, Any]],
    user_message: dict[str, Any],
) -> AsyncIterator[str]:
    """群聊一轮的执行与下发：成员事件实时、回答消息按成员顺序收口。

    会话生命周期与单聊流完全一致——执行任务自开短连接（每位成员再自开一条），
    生成器只读队列、全程不碰 DB，序列化后再提交。
    """
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

    def _notify(steps: list[dict[str, Any]]) -> None:
        queue.put_nowait(("step", {"steps": steps}))

    def _on_event(kind: str, payload: dict[str, Any]) -> None:
        queue.put_nowait((kind, payload))

    recorder = StepRecorder(notify=_notify)

    async def _runner() -> None:
        db: Session = _get_session_factory()()
        try:
            await group.run_group_turn(
                db,
                ctx=ctx,
                session_id=session_id,
                content=content,
                attachment_ids=attachment_ids,
                policy=policy,
                target=target,
                participants=participants,
                recorder=recorder,
                on_event=_on_event,
            )
        except AppError as e:
            db.rollback()
            queue.put_nowait(("error", {"code": e.code, "message": e.message}))
        except Exception as e:  # noqa: BLE001 —— 兜底：任何异常都要有明确收尾帧
            log.warning("group chat stream execution failed: %s", e)
            db.rollback()
            queue.put_nowait(("error", {"code": "INTERNAL_ERROR", "message": "服务暂时不可用。"}))
        finally:
            db.close()
            queue.put_nowait(("end", None))

    task = asyncio.create_task(_runner())
    status = "ok"
    saw_message = False
    try:
        yield _frame("chat-init", {"session_id": session_id, "user_message": user_message})
        while True:
            try:
                kind, payload = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            if kind == "step":
                yield _frame("step", payload)
            elif kind == "member_event":
                yield _frame("member-event", payload)
            elif kind == "message":
                saw_message = True
                yield _frame("message", payload)
            elif kind == "error":
                # 整轮失败（名单为空、会话无权访问等）；单个成员挂掉走
                # member-event(error) + 说明消息，不进这里
                status = "failed"
                yield _frame("error", payload)
            elif kind == "end":
                break
        if not saw_message:
            status = "failed"
        yield _frame("chat-end", {"status": status})
    except asyncio.CancelledError:
        # 客户端断开：中止执行并诚实记录 aborted（断流不重试，§12.2 第 4 条）
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        _persist_aborted(ctx=ctx, session_id=session_id, user_seq=int(user_message.get("seq") or 0))
        raise
    except Exception:  # noqa: BLE001 —— 流内异常也要有明确收尾帧，不能静默断流
        log.exception("group chat stream framing failed session=%s", session_id[:8])
        yield _frame("error", {"code": "INTERNAL_ERROR", "message": "对话流中断，请重试。"})
        yield _frame("chat-end", {"status": "failed"})
    finally:
        if not task.done():
            task.cancel()


def build_group_stream_response(
    *,
    ctx: YardContext,
    session_id: str,
    content: str,
    attachment_ids: list[str],
    policy: str,
    target: str | None,
    participants: list[dict[str, Any]],
    user_message: dict[str, Any],
) -> StreamingResponse:
    """构建群聊 SSE 响应（只传纯数据：请求级 Session 此刻已不可用）。"""
    return StreamingResponse(
        _run_group_stream(
            ctx=ctx,
            session_id=session_id,
            content=content,
            attachment_ids=attachment_ids,
            policy=policy,
            target=target,
            participants=participants,
            user_message=user_message,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


__all__ = ["build_group_stream_response", "build_stream_response"]
