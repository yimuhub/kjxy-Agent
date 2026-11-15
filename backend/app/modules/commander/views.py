"""指挥官视图序列化、产物提取与权限检查（从 commander/service.py 拆分，保持对外重导出兼容）。"""

from __future__ import annotations

import re
from typing import Any, TypedDict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.timezone import to_iso as _iso
from app.modules.commander import deliverables
from app.modules.commander.models import (
    SUB_ERROR,
    SUB_OK,
    SUB_SKIPPED,
    CommanderRun,
    CommanderSubtask,
)
from app.modules.yards.models import YardMembership

ERR_RUN_NOT_FOUND = "COMMANDER_RUN_NOT_FOUND"

_ARTIFACT_KEYS = ("artifacts", "attachments", "documents", "files", "downloads")
_ARTIFACT_URL_RE = re.compile(
    r"https?://[^\s)\]\}\"'<>]+?\.(?:pptx|docx|xlsx|pdf|png|jpe?g|gif|webp|zip|md|csv)",
    re.I,
)


def subtask_out(st: CommanderSubtask) -> dict[str, Any]:
    return {
        "id": st.id,
        "seq": int(st.seq),
        "title": st.title,
        "instruction": st.instruction,
        "expected_output": st.expected_output,
        "agent_id": st.agent_id,
        "agent_code": st.agent_code,
        "agent_name": st.agent_name,
        "agent_source": st.agent_source,
        "status": st.status,
        "depends_on": [int(x) for x in (st.depends_on_json or [])],
        "attempts": int(st.attempts or 0),
        "remediation": list(st.remediation_json or []),
        "tokens": int(st.input_tokens or 0) + int(st.output_tokens or 0),
        "output_excerpt": st.output_excerpt,
        "artifact_json": st.artifact_json,
        "error_code": st.error_code,
        "started_at": _iso(st.started_at),
        "finished_at": _iso(st.finished_at),
    }


def _budget_view(run: CommanderRun) -> dict[str, Any]:
    budget = int(getattr(run, "token_budget", 0) or 0)
    used = int(run.tokens_total or 0)
    ratio = round(used / budget, 4) if budget > 0 else 0.0
    status = str(getattr(run, "budget_status", "") or "")
    if not status and budget > 0:
        status = "exceeded" if used >= budget else "warning" if ratio >= 0.8 else "ok"
    return {
        "token_budget": budget,
        "budget_ratio": ratio,
        "budget_status": status or "ok",
        "budget_remaining": max(0, budget - used) if budget > 0 else 0,
    }


def run_out(run: CommanderRun, subtasks: list[CommanderSubtask]) -> dict[str, Any]:
    return {
        "id": run.id,
        "session_id": run.session_id,
        "goal": run.goal,
        "status": run.status,
        "max_parallel": int(run.max_parallel or 1),
        "effective_parallel": int(run.effective_parallel or run.max_parallel or 1),
        "degraded": bool(run.degraded),
        "approval_mode": str(getattr(run, "approval_mode", None) or "ask"),
        "tokens_total": int(run.tokens_total or 0),
        **_budget_view(run),
        "latency_ms": int(run.latency_ms or 0),
        "created_at": _iso(run.created_at),
        "plan_fallback": bool((run.plan_json or {}).get("fallback")),
        "plan_fallback_reason": str((run.plan_json or {}).get("fallback_reason") or ""),
        "memory_used": bool((run.plan_json or {}).get("memory_used")),
        "clarify": list((run.plan_json or {}).get("clarify") or []),
        "subtasks": [subtask_out(st) for st in subtasks],
    }


def plan_message_payload(
    run: CommanderRun, subtasks: list[CommanderSubtask] | None = None
) -> dict[str, Any]:
    """对话层「计划卡」的载荷（SSE ``commander-plan`` 直接发送）。"""
    if subtasks is None:
        state = getattr(run, "_sa_instance_state", None)
        session = state.session if state is not None else None
        if session is not None:
            subtasks = list(
                session.scalars(
                    select(CommanderSubtask)
                    .where(CommanderSubtask.run_id == run.id)
                    .order_by(CommanderSubtask.seq.asc())
                )
            )
        else:
            subtasks = []
    return {
        "type": "commander_plan",
        "run_id": run.id,
        "goal": run.goal,
        "status": run.status,
        "degraded": bool(run.degraded),
        "max_parallel": int(run.max_parallel or 1),
        "effective_parallel": int(run.effective_parallel or run.max_parallel or 1),
        "plan_fallback": bool((run.plan_json or {}).get("fallback")),
        "plan_fallback_reason": str((run.plan_json or {}).get("fallback_reason") or ""),
        "clarify": list((run.plan_json or {}).get("clarify") or []),
        "skills": [
            {"code": str(item.get("code") or ""), "name": str(item.get("name") or "")}
            for item in (run.plan_json or {}).get("skills") or []
            if isinstance(item, dict) and item.get("code")
        ],
        "token_usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": int(run.tokens_total or 0),
            "model": "commander",
        },
        "subtasks": [
            {
                "id": st.id,
                "seq": int(st.seq),
                "title": st.title,
                "instruction": st.instruction,
                "expected_output": st.expected_output,
                "agent_id": st.agent_id,
                "agent_code": st.agent_code,
                "agent_name": st.agent_name,
                "agent_source": st.agent_source,
                "status": st.status,
                "depends_on": [int(x) for x in (st.depends_on_json or [])],
            }
            for st in subtasks
        ],
    }


def get_run(db: Session, yard_id: str, run_id: str) -> tuple[CommanderRun, list[CommanderSubtask]]:
    run = db.get(CommanderRun, run_id)
    if run is None or run.yard_id != yard_id:
        raise AppError(
            ERR_RUN_NOT_FOUND,
            "指挥官运行不存在或无权访问。",
            http_status=404,
        )
    subtasks = list(
        db.scalars(
            select(CommanderSubtask)
            .where(
                CommanderSubtask.run_id == run.id,
                CommanderSubtask.yard_id == yard_id,
            )
            .order_by(CommanderSubtask.seq.asc())
        )
    )
    return run, subtasks


def can_access_run(db: Session, run: CommanderRun, *, user_id: str, roles: list[str]) -> bool:
    if run.user_id == user_id or "platform_admin" in roles:
        return True
    from app.modules.yards.authz import is_owner

    return is_owner(db, run.yard_id, user_id)


def require_run_access(db: Session, run: CommanderRun, *, user_id: str, roles: list[str]) -> None:
    if not can_access_run(db, run, user_id=user_id, roles=roles):
        raise AppError(ERR_RUN_NOT_FOUND, "指挥官运行不存在或无权访问。", http_status=404)


def _extract_artifacts(payload: Any, answer: str) -> list[dict[str, Any]]:
    """从 AgentResult 或正文中提取产物卡（文档/图片/链接）。"""
    found: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(item: Any) -> None:
        if len(found) >= 20:
            return
        artifact: dict[str, Any]
        if isinstance(item, str):
            url = item.strip()
            if not url:
                return
            artifact = {"type": "link", "title": url.rsplit("/", 1)[-1] or url, "url": url}
        elif isinstance(item, dict):
            url = str(
                item.get("url")
                or item.get("download_url")
                or item.get("href")
                or item.get("path")
                or ""
            ).strip()
            title = str(
                item.get("title") or item.get("name") or item.get("filename") or url
            ).strip()
            artifact = {
                "type": str(item.get("type") or item.get("kind") or "file"),
                "title": title,
                "url": url or None,
                "format": item.get("format") or item.get("ext"),
                "mime": item.get("mime") or item.get("content_type"),
            }
            for key in ("storage_key", "doc_format", "generated"):
                if key in item:
                    artifact[key] = item[key]
        else:
            return
        fingerprint = f"{artifact.get('url') or ''}|{artifact.get('title') or ''}"
        if fingerprint in seen:
            return
        seen.add(fingerprint)
        found.append({k: v for k, v in artifact.items() if v not in (None, "")})

    def _walk(node: Any, depth: int) -> None:
        if depth > 4 or not isinstance(node, dict):
            return
        for key in _ARTIFACT_KEYS:
            value = node.get(key)
            if isinstance(value, list):
                for item in value:
                    _add(item)
        for value in node.values():
            if isinstance(value, dict):
                _walk(value, depth + 1)

    if isinstance(payload, dict):
        _walk(payload, 0)
    for match in _ARTIFACT_URL_RE.findall(answer or ""):
        _add(match)
    return found


def _artifact_matches_format(artifacts: Any, fmt: str) -> bool:
    """产物里是否有指定格式的文件（按 format/文件名/下载地址判断）。"""
    ext = deliverables.FORMAT_EXT.get(fmt, f".{fmt}").lower()
    want = fmt.lower().lstrip(".")
    for item in artifacts or []:
        if not isinstance(item, dict):
            continue
        declared = str(item.get("format") or "").strip().lower().lstrip(".")
        if declared == want:
            return True
        for key in ("title", "filename", "name", "url", "download_url", "path"):
            value = str(item.get(key) or "").strip().lower()
            if value and value.endswith(ext):
                return True
    return False


def _failed_items(subtasks: list[CommanderSubtask]) -> list[dict[str, Any]]:
    """失败/跳过原因：优先给人能读懂的结果说明，错误码只做兜底。"""
    return [
        {
            "seq": int(st.seq),
            "title": st.title,
            "reason": ((st.output_excerpt or "").strip() or st.error_code or "未完成")[:200],
        }
        for st in subtasks
        if st.status in (SUB_ERROR, SUB_SKIPPED)
    ]


def _collect_artifacts(subtasks: list[CommanderSubtask]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for st in subtasks:
        for item in st.artifact_json or []:
            if not isinstance(item, dict):
                continue
            out.append(
                {
                    **item,
                    "subtask_seq": int(st.seq),
                    "subtask_title": st.title,
                    "agent_name": st.agent_name,
                }
            )
    return out[:50]


def _deterministic_answer(
    run: CommanderRun, subtasks: list[CommanderSubtask], *, failed: list[dict[str, Any]]
) -> str:
    lines = [f"目标：{run.goal}"]
    ok = [st for st in subtasks if st.status == SUB_OK]
    if ok:
        lines.append("已完成：")
        for st in ok:
            excerpt = (st.output_excerpt or "").strip()
            lines.append(f"- {st.title}（{st.agent_name or '指挥官'}）")
            if excerpt:
                lines.append(f"  {excerpt[:400]}")
    if failed:
        lines.append("未完成：")
        for item in failed:
            lines.append(f"- {item['title']}：{item['reason']}")
    if not ok and not failed:
        lines.append("本次没有可交付的子任务结果。")
    return "\n".join(lines)


class CommanderSummary(TypedDict):
    """``execute_run`` 的汇总返回（同时兼容对话层的 content/payload 字段）。"""

    run_id: str
    answer: str
    artifacts: list[dict[str, Any]]
    failed: list[dict[str, Any]]
    tokens_total: int
    degraded: bool
    content: str
    payload: dict[str, Any]
    citations: list[Any]
    token_usage: dict[str, Any]


def _summary_payload(
    run: CommanderRun,
    subtasks: list[CommanderSubtask],
    *,
    answer: str,
    artifacts: list[dict[str, Any]],
    failed: list[dict[str, Any]],
) -> CommanderSummary:
    tokens = int(run.tokens_total or 0)
    return {
        "run_id": run.id,
        "answer": answer,
        "artifacts": artifacts,
        "failed": failed,
        "tokens_total": tokens,
        "degraded": bool(run.degraded),
        "content": answer,
        "payload": {
            "type": "commander_summary",
            "run_id": run.id,
            "answer": answer,
            "artifacts": artifacts,
            "failed": failed,
            "tokens_total": tokens,
            "degraded": bool(run.degraded),
            "subtasks": [subtask_out(st) for st in subtasks],
        },
        "citations": [],
        "token_usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": tokens,
            "model": "commander",
        },
    }


def run_member_revoked(db: Session, run: CommanderRun) -> bool:
    """成员身份重验（v3.56，审计 C16）。"""
    yard_has_members = (
        db.scalar(
            select(func.count())
            .select_from(YardMembership)
            .where(YardMembership.yard_id == run.yard_id)
        )
        or 0
    ) > 0
    if not yard_has_members:
        return False
    return (
        db.scalar(
            select(YardMembership.user_id).where(
                YardMembership.yard_id == run.yard_id,
                YardMembership.user_id == run.user_id,
            )
        )
    ) is None


__all__ = [
    "CommanderSummary",
    "ERR_RUN_NOT_FOUND",
    "_ARTIFACT_KEYS",
    "_ARTIFACT_URL_RE",
    "_artifact_matches_format",
    "_budget_view",
    "_collect_artifacts",
    "_deterministic_answer",
    "_extract_artifacts",
    "_failed_items",
    "_summary_payload",
    "can_access_run",
    "get_run",
    "plan_message_payload",
    "require_run_access",
    "run_member_revoked",
    "run_out",
    "subtask_out",
]
