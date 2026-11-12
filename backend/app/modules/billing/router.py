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

from app.core.config import settings
from app.core.deps import (
    CurrentUser,
    get_current_user,
    get_db_session,
    require_platform_admin,
    require_yard_permission,
)
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.auth.models import User
from app.modules.auth.service import is_platform_admin
from app.modules.billing import catalog, payment, service, usage
from app.modules.billing.models import BillingOrder

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


class PaymentIntentBody(BaseModel):
    provider: str | None = Field(default=None, max_length=24)


class VoucherBody(BaseModel):
    voucher_no: str = Field(min_length=4, max_length=64)
    amount_yuan: float | None = Field(default=None, ge=0)


class SandboxPrepareBody(BaseModel):
    order_id: str = Field(min_length=8, max_length=64)


class PlanUpdateBody(BaseModel):
    """运营改套餐（v3.80，审查 D-18）：改价不用发版。"""

    price_yuan: float | None = Field(default=None, ge=0, le=1_000_000)
    tagline: str | None = Field(default=None, max_length=200)
    highlights: list[str] | None = None
    items: list[dict] | None = None
    sort_order: int | None = Field(default=None, ge=0, le=10_000)
    active: bool | None = None
    note: str | None = Field(default=None, max_length=200)


def _yard_id(user: CurrentUser) -> str:
    # require_yard_permission 已保证 yard_id 存在；防御性兜底与 farm 路由同口径
    if user.yard_id is None:
        raise AppError("YARD_REQUIRED", "该操作需要小院上下文。", http_status=400)
    return user.yard_id


@router.get("/plans")
def list_plans(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """套餐目录：含按本小院额度环境算出的**总额度**（与配额页同源）。

    v3.80 起目录以数据库为准，运营改价不用发版（审查 D-18/D-21）。
    """
    yard_id = user.yard_id
    return ok(service.list_plans(db, yard_id=yard_id), request)


@router.get("/usage")
def usage_summary(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """用量/额度/水位：用与套餐卖的东西完全一致的口径（审查 D-21）。"""
    return ok(usage.usage_summary(db, _yard_id(user)), request)


@router.get("/recommendation")
def recommendation(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """升级引导：按近 30 天真实消耗给出该升到哪一档、为什么（审查 D-19/D-20）。"""
    return ok(usage.recommendation(db, _yard_id(user)), request)


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
    # scope=all 仅供平台管理员拉全平台订单；JWT 预检 + 数据库实时复核，
    # 与发码/确认闸门一致，撤权即时生效（审查 B6）；其余按本小院
    if scope == "all":
        if "platform_admin" not in user.roles:
            raise AppError("FORBIDDEN", "仅平台管理员可查看全部订单。", http_status=403)
        row = db.get(User, user.user_id)
        if row is None or row.status != "active" or not is_platform_admin(row):
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


# ---------------------------------------------------------------------------
# 套餐运营（v3.80，审查 D-18）：改价/改额度/上下架，不用发版
# ---------------------------------------------------------------------------


@router.get("/admin/plans")
def admin_list_plans(
    request: Request,
    user: CurrentUser = Depends(require_platform_admin()),
    db: Session = Depends(get_db_session),
):
    """运营视角的套餐目录（含已下架的）。"""
    plans = catalog.load_plans(db, include_inactive=True)
    return ok([catalog.plan_payload(plan) for plan in plans], request)


@router.patch("/admin/plans/{plan_code}")
def admin_update_plan(
    plan_code: str,
    payload: PlanUpdateBody,
    request: Request,
    user: CurrentUser = Depends(require_platform_admin()),
    db: Session = Depends(get_db_session),
):
    """改套餐：价格、卖点、额度项、排序、上下架。

    改价立即对新订单生效；**已成交订单的价格快照不变**（订单存的是下单时的
    price_yuan），不会出现"改价把老订单也算改了"。
    """
    data = catalog.update_plan(
        db,
        code=plan_code,
        actor_id=user.user_id,
        price_yuan=payload.price_yuan,
        tagline=payload.tagline,
        highlights=payload.highlights,
        items=payload.items,
        sort_order=payload.sort_order,
        active=payload.active,
        note=payload.note,
    )
    return ok(data, request)


# ---------------------------------------------------------------------------
# 自助付费闭环（v3.79，审查 D-17）
# ---------------------------------------------------------------------------


@router.get("/payment/providers")
def list_payment_providers(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """可用支付渠道清单（前端据此渲染"去支付"入口）。"""
    return ok(payment.list_providers(), request)


@router.post("/orders/{order_id}/pay")
def create_payment(
    order_id: str,
    payload: PaymentIntentBody,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("quota:manage")),
    db: Session = Depends(get_db_session),
):
    """为待支付订单生成支付意图——用户自助付费入口。"""
    data = service.create_payment_intent(
        db,
        yard_id=_yard_id(user),
        order_id=order_id,
        user_id=user.user_id,
        base_url=str(request.base_url),
        provider_code=payload.provider,
    )
    return ok(data, request)


@router.post("/orders/{order_id}/voucher")
def submit_voucher(
    order_id: str,
    payload: VoucherBody,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("quota:manage")),
    db: Session = Depends(get_db_session),
):
    """对公转账回单自助提交：金额匹配即自动到账，不匹配转人工。"""
    data = service.submit_voucher(
        db,
        yard_id=_yard_id(user),
        order_id=order_id,
        user_id=user.user_id,
        voucher_no=payload.voucher_no,
        amount_yuan=payload.amount_yuan,
    )
    return ok(data, request)


@router.post("/payments/sandbox/prepare")
def sandbox_prepare(
    payload: SandboxPrepareBody,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("quota:manage")),
    db: Session = Depends(get_db_session),
):
    """沙箱收银台：生成一份已签名的回调载荷（仅沙箱渠道可用）。

    生产环境禁止沙箱渠道（见 Settings.validate_for_runtime），此端点随之失效。
    """
    provider = payment.get_payment_provider(payment.PROVIDER_SANDBOX)
    order = db.get(BillingOrder, payload.order_id)
    if order is None or order.yard_id != _yard_id(user):
        raise AppError("BILLING_ORDER_NOT_FOUND", "订单不存在或无权访问。", http_status=404)
    if provider.code != (settings.billing_payment_provider or "").strip().lower():
        raise AppError(
            "BILLING_PAYMENT_NOT_CONFIGURED",
            "当前未启用沙箱支付渠道。",
            http_status=400,
        )
    body = payment.build_sandbox_event(order_id=order.id, amount_yuan=order.price_yuan)
    return ok(
        {
            "payload": body,
            "notify_url": str(request.base_url).rstrip("/")
            + f"/api/v1/billing/payments/{provider.code}/notify",
        },
        request,
    )


@router.post("/payments/{provider}/notify")
async def payment_notify(
    provider: str,
    request: Request,
    db: Session = Depends(get_db_session),
):
    """支付渠道回调（**公开端点**，靠签名鉴权而非 JWT）。

    网关无法携带本平台的 JWT，因此签名是唯一凭据：验签失败一律拒绝，
    验签通过后按"订单号 + 金额"核对，金额不符拒绝入账。
    同一笔支付的重复回调是幂等的，不会重复发放额度。
    """
    raw_body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    query = dict(request.query_params)
    impl = payment.get_payment_provider(provider)
    if impl.code != provider.strip().lower():
        raise AppError("BILLING_PAYMENT_UNKNOWN", "未知支付渠道。", http_status=404)
    event = impl.verify_event(raw_body=raw_body, headers=headers, query=query)
    data = service.settle_payment_event(db, event=event, provider_code=impl.code)
    # 网关通常期望固定应答体，失败时抛 4xx 让它重试或告警
    return {"code": "SUCCESS", "message": "OK", "data": data}
