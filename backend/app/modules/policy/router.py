"""政策路由：采集 / 列表 / 详情 / 规则匹配。

第三期实现，不再是 P1 桩。
- 全部接口先检查 settings.policy_module_enabled，未启用时抛
  AppError("POLICY_MODULE_DISABLED", 404)；
- 全部接口经 get_tenant_context 做租户隔离。
- 采集去重冲突返回 409 POLICY_DUPLICATE。
"""

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.deps import TenantContext, get_db_session, get_tenant_context
from app.core.errors import AppError
from app.core.pagination import pagination_params
from app.core.responses import ok, paged
from app.modules.policy import service
from app.modules.policy.schemas import (
    ApplicationDraftCreate,
    ApplicationDraftOut,
    PolicyIngestRequest,
    PolicyOut,
    PolicyProfile,
)


def _draft_out(draft) -> dict:
    """组装草案响应：模型字段 + draft_id + 校验提醒。"""
    return {
        **ApplicationDraftOut.model_validate(draft).model_dump(mode="json"),
        "draft_id": draft.id,
        "verification_reminder": service.VERIFICATION_REMINDER,
    }


router = APIRouter(prefix="/policies", tags=["policy"])


def _require_enabled() -> None:
    if not settings.policy_module_enabled:
        raise AppError("POLICY_MODULE_DISABLED", "政策模块未启用。", http_status=404)


@router.get("")
def list_policies(
    keyword: str | None = Query(default=None, description="政策名称关键词"),
    province: str | None = Query(default=None),
    policy_type: str | None = Query(default=None),
    status: str | None = Query(default=None, description="active|expired|archived"),
    page_page: tuple[int, int] = Depends(pagination_params),
    ctx: TenantContext = Depends(get_tenant_context),
    db=Depends(get_db_session),
):
    _require_enabled()
    page, page_size = page_page
    items, total = service.list_policies(
        db,
        ctx,
        page,
        page_size,
        keyword=keyword,
        province=province,
        policy_type=policy_type,
        status=status,
    )
    return paged(
        [PolicyOut.model_validate(i).model_dump(mode="json") for i in items],
        page,
        page_size,
        total,
    )


@router.post("")
def ingest_policy(
    req: PolicyIngestRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    db=Depends(get_db_session),
):
    _require_enabled()
    policy = service.ingest_policy(db, ctx, req)
    return JSONResponse(
        content=ok(PolicyOut.model_validate(policy).model_dump(mode="json")),
        status_code=201,
    )


@router.post("/match")
def match_policies(
    profile: PolicyProfile,
    ctx: TenantContext = Depends(get_tenant_context),
    db=Depends(get_db_session),
):
    _require_enabled()
    results = service.match_policies(db, ctx, profile)
    return ok([r.model_dump(mode="json") for r in results])


@router.get("/{policy_id}")
def get_policy(
    policy_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
    db=Depends(get_db_session),
):
    _require_enabled()
    policy = service.get_policy(db, ctx, policy_id)
    return ok(PolicyOut.model_validate(policy).model_dump(mode="json"))


# ---------- 申报材料草案（第三期 Worker B） ----------


@router.post("/{policy_id}/application-drafts")
def create_application_draft(
    policy_id: str,
    payload: ApplicationDraftCreate,
    ctx: TenantContext = Depends(get_tenant_context),
    db=Depends(get_db_session),
):
    _require_enabled()
    draft = service.create_application_draft(db, ctx.tenant_id, ctx.user_id, policy_id, payload)
    return ok(_draft_out(draft))


@router.get("/application-drafts/{draft_id}")
def get_application_draft(
    draft_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
    db=Depends(get_db_session),
):
    _require_enabled()
    draft = service.get_application_draft(db, ctx.tenant_id, draft_id)
    return ok(_draft_out(draft))
