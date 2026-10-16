"""工具元数据接口：只读列表，含 risk_level 与所需权限，不含实现细节。"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_db_session
from app.core.responses import ok
from app.modules.tools.models import ToolDefinition
from app.modules.tools.schemas import ToolOut

router = APIRouter(prefix="/tools", tags=["tools"])


def _to_out(row: ToolDefinition) -> ToolOut:
    return ToolOut(
        code=row.code,
        version=row.version,
        name=row.name,
        description=row.description,
        input_schema=row.input_schema_json,
        output_schema=row.output_schema_json,
        required_permissions=row.required_permissions_json or [],
        risk_level=row.risk_level,
        side_effect=row.side_effect,
        timeout_seconds=row.timeout_seconds,
        enabled=row.enabled,
    )


@router.get("")
def list_tools(db: Session = Depends(get_db_session)):  # noqa: B008
    rows = (
        db.query(ToolDefinition)
        .filter(ToolDefinition.enabled.is_(True))
        .order_by(ToolDefinition.code)
        .all()
    )
    return ok([_to_out(r).model_dump() for r in rows])
