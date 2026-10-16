"""审计写入助手。供各模块调用；永不抛异常影响主流程。"""

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.modules.audit.models import AuditLog

log = get_logger(__name__)


def log_audit(
    db: Session,
    *,
    tenant_id: str | None,
    actor_id: str | None,
    action: str,
    resource_type: str,
    resource_id: str | None,
    result: str,
    request_id: str,
    metadata: dict | None = None,
) -> None:
    try:
        db.add(
            AuditLog(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                result=result,
                request_id=request_id,
                metadata_json=metadata,
            )
        )
        db.flush()
    except Exception as e:  # 审计失败不得影响业务
        log.warning("audit log failed: %s", e)
