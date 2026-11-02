"""指挥官版本快照：创建、列表、回滚（v3.44 P1）。

快照只保存「可回滚的运行范式」，当前包含指挥官设置与人格；不包含对话内容、
密钥或产物正文。回滚前先自动补一份当前状态快照，避免误操作丢失现场。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.audit.service import log_audit
from app.modules.commander import settings as commander_settings
from app.modules.commander.governance_models import (
    SNAPSHOT_MANUAL,
    SNAPSHOT_RESTORE,
    CommanderSnapshot,
)

log = get_logger(__name__)

#: 列表默认上限
LIST_LIMIT = 20


def snapshot_out(row: CommanderSnapshot) -> dict[str, Any]:
    return {
        "id": row.id,
        "label": row.label,
        "reason": row.reason,
        "source": row.source,
        "persona": (row.payload_json or {}).get("persona"),
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def create_snapshot(
    db: Session,
    *,
    yard_id: str,
    label: str = "",
    reason: str = "",
    source: str = SNAPSHOT_MANUAL,
    created_by: str | None = None,
    settings: dict[str, Any] | None = None,
) -> CommanderSnapshot:
    """写入一份快照（不 commit，由调用方决定事务边界）。"""
    current = settings or commander_settings.settings_for(db, yard_id)
    row = CommanderSnapshot(
        yard_id=yard_id,
        label=(label or "指挥官快照")[:60],
        reason=(reason or "")[:200],
        source=source,
        payload_json={
            "settings": dict(current),
            "persona": current.get("persona", "balanced"),
        },
        created_by=created_by,
    )
    db.add(row)
    db.flush()
    return row


def list_snapshots(db: Session, yard_id: str, limit: int = LIST_LIMIT) -> list[CommanderSnapshot]:
    capped = max(1, min(int(limit or LIST_LIMIT), 100))
    return list(
        db.scalars(
            select(CommanderSnapshot)
            .where(CommanderSnapshot.yard_id == yard_id)
            .order_by(CommanderSnapshot.created_at.desc())
            .limit(capped)
        )
    )


def get_snapshot(db: Session, yard_id: str, snapshot_id: str) -> CommanderSnapshot:
    row = db.get(CommanderSnapshot, snapshot_id)
    if row is None or row.yard_id != yard_id:
        raise AppError("COMMANDER_SNAPSHOT_NOT_FOUND", "快照不存在或已删除。", http_status=404)
    return row


def restore_snapshot(
    db: Session,
    *,
    yard_id: str,
    snapshot_id: str,
    actor_id: str,
    request_id: str,
) -> tuple[CommanderSnapshot, dict[str, Any]]:
    """按快照恢复设置；恢复前自动备份当前状态。"""
    target = get_snapshot(db, yard_id, snapshot_id)
    payload = target.payload_json or {}
    settings_payload = payload.get("settings")
    if not isinstance(settings_payload, dict):
        raise AppError("INVALID_REQUEST", "快照内容不完整，无法回滚。", http_status=400)

    backup = create_snapshot(
        db,
        yard_id=yard_id,
        label="回滚前自动备份",
        reason=f"回滚到 {target.id} 前保存当前状态",
        source=SNAPSHOT_RESTORE,
        created_by=actor_id,
    )
    restored = commander_settings.update_settings(
        db,
        yard_id=yard_id,
        payload=settings_payload,
        actor_id=actor_id,
        request_id=request_id,
    )
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=actor_id,
        action="commander_snapshot_restored",
        resource_type="commander_snapshot",
        resource_id=target.id,
        result="success",
        request_id=request_id,
        metadata={
            "backup_id": backup.id,
            "persona": restored.get("persona"),
        },
    )
    db.flush()
    log.info(
        "commander snapshot restored yard=%s snapshot=%s backup=%s",
        yard_id,
        target.id,
        backup.id,
    )
    return backup, restored


__all__ = [
    "create_snapshot",
    "get_snapshot",
    "list_snapshots",
    "restore_snapshot",
    "snapshot_out",
]
