"""过程可见性：对话与执行器共享的步骤词汇。

前端按统一阶段名渲染"正在思考 / 正在阅读"的步骤条，因此阶段名是**稳定契约**：
只追加新阶段，不改已有阶段含义。

阶段：
- reading    读取附件（照片/文档）
- routing    选择助手或班组（supervisor 三级路由）
- retrieving 检索知识库
- thinking   模型推理
- tool_call  调用工具
- generating 组织最终回答
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

PHASE_READING = "reading"
PHASE_ROUTING = "routing"
PHASE_RETRIEVING = "retrieving"
PHASE_THINKING = "thinking"
PHASE_TOOL_CALL = "tool_call"
PHASE_GENERATING = "generating"

PHASE_ORDER: tuple[str, ...] = (
    PHASE_READING,
    PHASE_ROUTING,
    PHASE_RETRIEVING,
    PHASE_THINKING,
    PHASE_TOOL_CALL,
    PHASE_GENERATING,
)

PHASE_TITLES: dict[str, str] = {
    PHASE_READING: "正在阅读附件",
    PHASE_ROUTING: "正在选择助手",
    PHASE_RETRIEVING: "正在检索知识库",
    PHASE_THINKING: "正在思考",
    PHASE_TOOL_CALL: "正在调用工具",
    PHASE_GENERATING: "正在组织回答",
}

#: 步骤状态
STEP_RUNNING = "running"
STEP_DONE = "done"
STEP_FAILED = "failed"

#: 步骤回调签名：(阶段, 细节, 状态)——标题由阶段词表统一给出
StepCallback = Callable[[str, str, str], None]
#: 步骤快照回调签名：(累计步骤列表)
StepNotify = Callable[[list[dict[str, Any]]], None]


def _now() -> str:
    return datetime.now(UTC).isoformat()


class StepRecorder:
    """步骤记录器：累计步骤并在每次变化后推送快照（SSE）。

    同阶段重复上报时：``running`` 会把上一条未完结的同阶段步骤收尾为 done，
    ``done``/``failed`` 直接收尾对应步骤——因此同一阶段可以先"进行中"再
    "已完成"，前端只需渲染累计列表。
    """

    def __init__(self, notify: StepNotify | None = None) -> None:
        self.steps: list[dict[str, Any]] = []
        self._notify_cb = notify

    def emit(self, phase: str, detail: str = "", status: str = STEP_RUNNING) -> None:
        title = PHASE_TITLES.get(phase, phase)
        if status == STEP_RUNNING:
            self._close(phase, STEP_DONE)
            self.steps.append(
                {
                    "phase": phase,
                    "title": title,
                    "detail": detail,
                    "status": STEP_RUNNING,
                    "ts": _now(),
                }
            )
        else:
            if not self._close(phase, status):
                self.steps.append(
                    {
                        "phase": phase,
                        "title": title,
                        "detail": detail,
                        "status": status,
                        "ts": _now(),
                    }
                )
        self._push()

    def fail(self, phase: str, detail: str = "") -> None:
        self.emit(phase, detail, STEP_FAILED)

    def finish(self) -> None:
        """收尾：所有仍在进行中的步骤标为完成。"""
        changed = False
        for step in self.steps:
            if step["status"] == STEP_RUNNING:
                step["status"] = STEP_DONE
                changed = True
        if changed:
            self._push()

    def _close(self, phase: str, status: str) -> bool:
        for step in reversed(self.steps):
            if step["phase"] == phase and step["status"] == STEP_RUNNING:
                step["status"] = status
                if status == STEP_DONE and not step.get("detail"):
                    step["detail"] = "完成"
                return True
        return False

    def _push(self) -> None:
        if self._notify_cb is None:
            return
        try:
            self._notify_cb(list(self.steps))
        except Exception:  # noqa: BLE001 —— 推送失败不得影响主流程
            pass


__all__ = [
    "PHASE_GENERATING",
    "PHASE_ORDER",
    "PHASE_READING",
    "PHASE_RETRIEVING",
    "PHASE_ROUTING",
    "PHASE_TITLES",
    "PHASE_THINKING",
    "PHASE_TOOL_CALL",
    "STEP_DONE",
    "STEP_FAILED",
    "STEP_RUNNING",
    "StepCallback",
    "StepNotify",
    "StepRecorder",
]
