"""支付渠道抽象：生成支付意图 → 渠道回调 → 自动到账。

为什么需要（审查缺陷 **D-17**）：
原实现里"付费"完全靠人——平台管理员发兑换码、管理员点"确认到账"。每一个付费
小院都要过平台运营的手，付费转化被运营人力锁死，规模化付费不可能。

本模块把"支付成功"变成**系统事件**：渠道回调一进来，订单自动 CAS 到账并发放
额度。三条硬约束：

1. **幂等**：同一笔回调重复到达只能到账一次（复用 ``service._mark_paid_and_grant``
   的 CAS 与 ``granted_at IS NULL`` 双闸）。
2. **验签**：回调端点不鉴权（网关无法携带 JWT），签名是唯一凭据，未验签一律拒绝。
3. **金额校验**：回调金额必须与订单金额一致，否则拒绝入账并留审计。

渠道通过 ``BILLING_PAYMENT_PROVIDER`` 切换：

- ``manual``（默认）：对公转账 + 用户自助提交回单。**无需任何网关即可打通
  自助闭环**——用户不必先找运营要码；但回单真实性只有平台对账能判定，
  因此提交后订单保持待核验，由平台「确认到账」入账（2026-11-13 二次深审
  修正：不再"金额匹配即自动到账"，那是可被伪造回单利用的荣誉制）。
- ``sandbox``：内置沙箱渠道（HMAC-SHA256 签名），用于开发/演示/联调，可在没有
  任何商户资质的情况下跑通"下单 → 支付 → 回调 → 自动到账"全链路。
- ``wechat_pay`` / ``alipay``：标准第三方网关适配器，需配置商户参数后启用。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import get_logger

log = get_logger(__name__)

PROVIDER_MANUAL = "manual"
PROVIDER_SANDBOX = "sandbox"
PROVIDER_WECHAT = "wechat_pay"
PROVIDER_ALIPAY = "alipay"

#: 支付意图过期时间（分钟）：超时后需要重新发起，避免拿着过期单据去付款
INTENT_TTL_MINUTES = 30


@dataclass(frozen=True)
class PaymentIntent:
    """一次支付意图：告诉用户"去哪里付、付多少、怎么证明付了"。"""

    provider: str
    #: qr=扫码支付；transfer=对公转账；redirect=跳转收银台
    mode: str
    order_id: str
    order_code: str
    plan_code: str
    plan_name: str
    amount_yuan: float
    pay_url: str | None = None
    qr_payload: str | None = None
    expires_at: str | None = None
    instructions: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "mode": self.mode,
            "order_id": self.order_id,
            "order_code": self.order_code,
            "plan_code": self.plan_code,
            "plan_name": self.plan_name,
            "amount_yuan": self.amount_yuan,
            "pay_url": self.pay_url,
            "qr_payload": self.qr_payload,
            "expires_at": self.expires_at,
            "instructions": list(self.instructions),
            **self.extra,
        }


@dataclass(frozen=True)
class PaymentEvent:
    """渠道回调解析出的"这笔真的付了"事件。"""

    order_id: str
    trade_no: str
    amount_yuan: float
    paid_at: datetime
    raw: dict[str, Any] = field(default_factory=dict)


class PaymentProvider(Protocol):
    """支付渠道协议。实现方必须保证 :meth:`verify_event` 验签失败即抛错。"""

    code: str
    label: str

    def create_intent(
        self,
        *,
        order_id: str,
        order_code: str,
        plan_code: str,
        plan_name: str,
        amount_yuan: float,
        notify_url: str,
        return_url: str,
    ) -> PaymentIntent: ...

    def verify_event(
        self, *, raw_body: bytes, headers: dict[str, str], query: dict[str, str]
    ) -> PaymentEvent: ...


# ---------------------------------------------------------------------------
# 对公转账（无需网关）
# ---------------------------------------------------------------------------


class ManualTransferProvider:
    """对公转账 + 用户自助提交回单。

    这是**默认**渠道：不依赖任何支付网关资质，把"人工发码"的人工瓶颈去掉——
    用户付完款自己提交回单，平台核对真伪后确认到账。金额比对只是预检提示，
    **不构成入账依据**（伪造回单号的成本是零，金额数字也一样）。
    """

    code = PROVIDER_MANUAL
    label = "对公转账"

    def create_intent(
        self,
        *,
        order_id: str,
        order_code: str,
        plan_code: str,
        plan_name: str,
        amount_yuan: float,
        notify_url: str,
        return_url: str,
    ) -> PaymentIntent:
        account = settings.billing_transfer_account.strip()
        lines = [
            f"请按订单号 {order_code} 备注用途，付款 {amount_yuan:,.2f} 元。",
            "付款完成后回到订单页提交银行回单号，平台核对通过后额度自动到账。",
        ]
        if account:
            lines.insert(1, f"收款账户：{account}")
        expires = datetime.now(UTC) + timedelta(minutes=INTENT_TTL_MINUTES)
        return PaymentIntent(
            provider=self.code,
            mode="transfer",
            order_id=order_id,
            order_code=order_code,
            plan_code=plan_code,
            plan_name=plan_name,
            amount_yuan=amount_yuan,
            expires_at=expires.isoformat(),
            instructions=tuple(lines),
            extra={"transfer_account": account},
        )

    def verify_event(
        self, *, raw_body: bytes, headers: dict[str, str], query: dict[str, str]
    ) -> PaymentEvent:
        raise AppError(
            "BILLING_PAYMENT_UNSUPPORTED",
            "对公转账渠道没有异步回调，请提交银行回单完成核销。",
            http_status=400,
        )


# ---------------------------------------------------------------------------
# 内置沙箱（开发/演示/联调）
# ---------------------------------------------------------------------------


def _sandbox_secret() -> str:
    """沙箱签名密钥：优先用配置，否则由 app_secret_key 派生（域分离）。"""
    configured = settings.billing_payment_secret.strip()
    if configured:
        return configured
    return hashlib.sha256(f"billing-sandbox:{settings.app_secret_key}".encode()).hexdigest()


def sandbox_sign(payload: dict[str, Any]) -> str:
    """对沙箱回调载荷做 HMAC-SHA256 签名。"""
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hmac.new(_sandbox_secret().encode(), raw.encode(), hashlib.sha256).hexdigest()


class SandboxProvider:
    """内置沙箱渠道：无需商户资质即可跑通"支付 → 回调 → 到账"全链路。

    签名口径与真实网关一致（HMAC-SHA256 over 规范化 JSON），因此把本渠道换成
    真实网关时，回调端点、幂等结算、金额校验这套代码**一行都不用改**。
    """

    code = PROVIDER_SANDBOX
    label = "沙箱支付（联调）"

    def create_intent(
        self,
        *,
        order_id: str,
        order_code: str,
        plan_code: str,
        plan_name: str,
        amount_yuan: float,
        notify_url: str,
        return_url: str,
    ) -> PaymentIntent:
        expires = datetime.now(UTC) + timedelta(minutes=INTENT_TTL_MINUTES)
        # 沙箱收银台就是平台自己的一个回调入口：前端"我已支付"即触发它。
        qr_payload = f"{return_url.rstrip('/')}/billing/sandbox/checkout?order={order_id}"
        return PaymentIntent(
            provider=self.code,
            mode="qr",
            order_id=order_id,
            order_code=order_code,
            plan_code=plan_code,
            plan_name=plan_name,
            amount_yuan=amount_yuan,
            pay_url=qr_payload,
            qr_payload=qr_payload,
            expires_at=expires.isoformat(),
            instructions=("扫码或点击链接完成沙箱支付；支付后系统自动回调并到账。",),
            extra={"notify_url": notify_url},
        )

    def verify_event(
        self, *, raw_body: bytes, headers: dict[str, str], query: dict[str, str]
    ) -> PaymentEvent:
        try:
            data = json.loads(raw_body.decode("utf-8"))
        except Exception as e:  # noqa: BLE001 - 非法载荷直接拒
            raise AppError(
                "BILLING_PAYMENT_SIGNATURE", "支付回调载荷无法解析。", http_status=400
            ) from e
        signature = str(data.get("signature") or headers.get("x-payment-signature") or "")
        body = {k: v for k, v in data.items() if k != "signature"}
        expected = sandbox_sign(body)
        if not signature or not hmac.compare_digest(signature, expected):
            raise AppError("BILLING_PAYMENT_SIGNATURE", "支付回调签名校验失败。", http_status=400)
        try:
            amount = float(data.get("amount_yuan"))
        except (TypeError, ValueError) as e:
            raise AppError("BILLING_PAYMENT_AMOUNT", "支付回调金额无效。", http_status=400) from e
        paid_at = datetime.now(UTC)
        if data.get("paid_at"):
            try:
                paid_at = datetime.fromisoformat(str(data["paid_at"]))
            except ValueError:
                paid_at = datetime.now(UTC)
        return PaymentEvent(
            order_id=str(data.get("order_id") or ""),
            trade_no=str(data.get("trade_no") or secrets.token_hex(8)),
            amount_yuan=amount,
            paid_at=paid_at,
            raw=data,
        )


# ---------------------------------------------------------------------------
# 第三方网关适配器
# ---------------------------------------------------------------------------


class WeChatPayProvider:
    """微信支付 v3 适配器。

    需要配置 ``BILLING_WECHAT_APPID / MCHID / API_V3_KEY`` 与商户证书；
    未配置时 :meth:`create_intent` 会明确报错而不是静默降级。
    """

    code = PROVIDER_WECHAT
    label = "微信支付"

    def __init__(self) -> None:
        self.appid = settings.billing_wechat_appid.strip()
        self.mchid = settings.billing_wechat_mchid.strip()
        self.api_v3_key = settings.billing_wechat_api_v3_key.strip()

    @property
    def configured(self) -> bool:
        return bool(self.appid and self.mchid and self.api_v3_key)

    def create_intent(
        self,
        *,
        order_id: str,
        order_code: str,
        plan_code: str,
        plan_name: str,
        amount_yuan: float,
        notify_url: str,
        return_url: str,
    ) -> PaymentIntent:
        if not self.configured:
            raise AppError(
                "BILLING_PAYMENT_NOT_CONFIGURED",
                "微信支付尚未配置商户参数，请联系平台管理员。",
                http_status=503,
            )
        raise AppError(
            "BILLING_PAYMENT_NOT_CONFIGURED",
            "微信支付下单需要商户证书与已备案域名，请在网关适配器中补齐后启用。",
            http_status=503,
        )

    def verify_event(
        self, *, raw_body: bytes, headers: dict[str, str], query: dict[str, str]
    ) -> PaymentEvent:
        if not self.configured:
            raise AppError(
                "BILLING_PAYMENT_NOT_CONFIGURED",
                "微信支付尚未配置商户参数。",
                http_status=503,
            )
        raise AppError(
            "BILLING_PAYMENT_NOT_CONFIGURED",
            "微信支付回调验签需要商户证书与平台证书，请补齐配置后启用。",
            http_status=503,
        )


class AlipayProvider:
    """支付宝适配器（占位）：需要 ``BILLING_ALIPAY_APP_ID / PRIVATE_KEY / PUBLIC_KEY``。"""

    code = PROVIDER_ALIPAY
    label = "支付宝"

    def __init__(self) -> None:
        self.app_id = settings.billing_alipay_app_id.strip()
        self.private_key = settings.billing_alipay_private_key.strip()
        self.public_key = settings.billing_alipay_public_key.strip()

    @property
    def configured(self) -> bool:
        return bool(self.app_id and self.private_key and self.public_key)

    def create_intent(
        self,
        *,
        order_id: str,
        order_code: str,
        plan_code: str,
        plan_name: str,
        amount_yuan: float,
        notify_url: str,
        return_url: str,
    ) -> PaymentIntent:
        if not self.configured:
            raise AppError(
                "BILLING_PAYMENT_NOT_CONFIGURED",
                "支付宝尚未配置商户参数，请联系平台管理员。",
                http_status=503,
            )
        raise AppError(
            "BILLING_PAYMENT_NOT_CONFIGURED",
            "支付宝下单需要商户私钥，请在网关适配器中补齐后启用。",
            http_status=503,
        )

    def verify_event(
        self, *, raw_body: bytes, headers: dict[str, str], query: dict[str, str]
    ) -> PaymentEvent:
        if not self.configured:
            raise AppError(
                "BILLING_PAYMENT_NOT_CONFIGURED",
                "支付宝尚未配置商户参数。",
                http_status=503,
            )
        raise AppError(
            "BILLING_PAYMENT_NOT_CONFIGURED",
            "支付宝回调验签需要商户公钥，请补齐配置后启用。",
            http_status=503,
        )


_PROVIDERS: dict[str, type] = {
    PROVIDER_MANUAL: ManualTransferProvider,
    PROVIDER_SANDBOX: SandboxProvider,
    PROVIDER_WECHAT: WeChatPayProvider,
    PROVIDER_ALIPAY: AlipayProvider,
}


def get_payment_provider(code: str | None = None) -> PaymentProvider:
    """按配置取支付渠道；未知渠道直接回落到对公转账（保证付费路径永远不断）。"""
    key = (code or settings.billing_payment_provider or PROVIDER_MANUAL).strip().lower()
    cls = _PROVIDERS.get(key, ManualTransferProvider)
    return cls()


def list_providers() -> list[dict[str, Any]]:
    """可用渠道清单（前端据此展示"去支付"的方式）。

    ``available`` 的语义是"**现在这个部署真的能用它支付**"：
    - 当前配置的渠道永远可用；
    - 对公转账无需任何资质，恒可用（兜底路径）；
    - 其余渠道需要商户参数真正配齐（``configured``）才算可用——
      沙箱不是生产可选项，不在配置渠道时不应被标为 available
      （``create_payment_intent`` 已按配置渠道校验，列表口径必须一致）。
    """
    current = (settings.billing_payment_provider or PROVIDER_MANUAL).strip().lower()
    out: list[dict[str, Any]] = []
    for code, cls in _PROVIDERS.items():
        provider = cls()
        available = (
            code == current
            or code == PROVIDER_MANUAL
            or bool(getattr(provider, "configured", False))
        )
        out.append(
            {
                "code": code,
                "label": provider.label,
                "current": code == current,
                "available": available,
            }
        )
    return out


def build_sandbox_event(
    *, order_id: str, amount_yuan: float, trade_no: str | None = None
) -> dict[str, Any]:
    """构造沙箱回调载荷（供沙箱收银台调用真实回调端点）。"""
    body = {
        "order_id": order_id,
        "amount_yuan": round(float(amount_yuan), 2),
        "trade_no": trade_no or f"SBX-{int(time.time())}-{secrets.token_hex(4)}",
        "paid_at": datetime.now(UTC).isoformat(),
    }
    body["signature"] = sandbox_sign(body)
    return body
