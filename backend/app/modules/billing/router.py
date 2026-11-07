"""订阅计费路由（v3.71）：``/billing/*``。

权限口径（不新增权限点，复用既有体系）：
- 套餐目录：登录即可读（含小院上下文）；
- 兑换 / 下单 / 取消：``quota:manage``（小院管理员与负责人默认具备）；
- 发码 / 码列表 / 作废 / 订单确认：平台管理员（数据库复核，core.deps）；
- 订单列表：默认本小院；``scope=all`` 需平台管理员角色（只读列表）。
"""

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import (
    CurrentUser,
    get_current_user,
    get_db_session,
    require_platform_admin,
    require_yard_permission,
)
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.billing import service

router = APIRouter(prefix="/billing", tags=["billing"])


class RedeemBody(BaseModel):
    code: str = Field(min_length=4, max_length=24)


class OrderCreateBody(BaseModel):
    plan_code: str = Field(min_length=2, max_length=32)
    voucher_note: str | None = Field(default=None, max_length=500)


class OrderConfirmBody(BaseModel):
    note: str = Field(default="对公转账已到账", max_length=500)


class CodeCreateBody(BaseModel):
    plan_code: str = Field(min_length=2, max_length=32)
    count: int = Field(default=1, ge=1, le=200)
    note: str | None = Field(default=None, max_length=200)
    ttl_days: int = Field(default=90, ge=1, le=365)


def _yard_id(user: CurrentUser) -> str:
    # require_yard_permission 已保证 yard_id 存在；防御性兜底与 farm 路由同口径
    if user.yard_id is None:
        raise AppError("YARD_REQUIRED", "该操作需要小院上下文。", http_status=400)
    return user.yard_id


@router.get("/plans")
def list_plans(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    return ok(service.list_plans(), request)


@router.post("/redeem")
def redeem(
    payload: RedeemBody,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("quota:manage")),
    db: Session = Depends(get_db_session),
):
    data = service.redeem(
        db,
        yard_id=_yard_id(user),
        code=payload.code,
        user_id=user.user_id,
        user_name=None,
    )
    return ok(data, request)


@router.post("/orders")
def create_order(
    payload: OrderCreateBody,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("quota:manage")),
    db: Session = Depends(get_db_session),
):
    data = service.create_order(
        db,
        yard_id=_yard_id(user),
        plan_code=payload.plan_code,
        user_id=user.user_id,
        user_name=None,
        voucher_note=payload.voucher_note,
    )
    return ok(data, request)


@router.get("/orders")
def list_orders(
    request: Request,
    scope: str = Query(default="yard", max_length=8),
    limit: int = Query(default=100, ge=1, le=500),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    # scope=all 仅供平台管理员拉全平台订单；其余按本小院
    if scope == "all":
        if "platform_admin" not in user.roles:
            raise AppError("FORBIDDEN", "仅平台管理员可查看全部订单。", http_status=403)
        return ok(service.list_orders(db, yard_id=None, limit=limit), request)
    return ok(service.list_orders(db, yard_id=_yard_id(user), limit=limit), request)


@router.post("/orders/{order_id}/cancel")
def cancel_order(
    order_id: str,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("quota:manage")),
    db: Session = Depends(get_db_session),
):
    data = service.cancel_order(db, yard_id=_yard_id(user), order_id=order_id, user_id=user.user_id)
    return ok(data, request)


@router.post("/orders/{order_id}/confirm")
def confirm_order(
    order_id: str,
    payload: OrderConfirmBody,
    request: Request,
    user: CurrentUser = Depends(require_platform_admin()),
    db: Session = Depends(get_db_session),
):
    data = service.confirm_order(db, order_id=order_id, actor_id=user.user_id, note=payload.note)
    return ok(data, request)


@router.post("/codes")
def create_codes(
    payload: CodeCreateBody,
    request: Request,
    user: CurrentUser = Depends(require_platform_admin()),
    db: Session = Depends(get_db_session),
):
    rows = service.create_codes(
        db,
        plan_code=payload.plan_code,
        count=payload.count,
        actor_id=user.user_id,
        note=payload.note,
        ttl_days=payload.ttl_days,
    )
    return ok([service.code_payload(row) for row in rows], request)


@router.get("/codes")
def list_codes(
    request: Request,
    status: str | None = Query(default=None, max_length=12),
    limit: int = Query(default=100, ge=1, le=500),
    user: CurrentUser = Depends(require_platform_admin()),
    db: Session = Depends(get_db_session),
):
    return ok(service.list_codes(db, status=status, limit=limit), request)


@router.post("/codes/{code_id}/disable")
def disable_code(
    code_id: str,
    request: Request,
    user: CurrentUser = Depends(require_platform_admin()),
    db: Session = Depends(get_db_session),
):
    return ok(service.disable_code(db, code_id=code_id, actor_id=user.user_id), request)
