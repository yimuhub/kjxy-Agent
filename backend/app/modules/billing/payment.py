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

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import parse_qs, urlencode

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

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


def _compact_order_id(order_id: str) -> str:
    """渠道单号上限 32：UUID 去连字符正好 32 位。"""
    return str(order_id).replace("-", "")[:32]


def _expand_order_id(value: str) -> str:
    raw = str(value or "").strip()
    if len(raw) == 32 and "-" not in raw:
        return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"
    return raw


def _pem_bytes(value: str, kind: str = "PUBLIC KEY") -> bytes:
    text = (value or "").strip()
    if not text:
        return b""
    # 环境变量里常见把换行写成 \n；还原后才可被 cryptography 解析
    text = text.replace("\\n", "\n")
    # 开放平台常给“无头无尾”的单行 base64，这里补成 PEM
    if "BEGIN" not in text:
        body = "".join(text.split())
        lines = [body[i : i + 64] for i in range(0, len(body), 64)]
        text = f"-----BEGIN {kind}-----\n" + "\n".join(lines) + f"\n-----END {kind}-----"
    return text.encode("utf-8")


def _load_private_key(pem: str):
    key = serialization.load_pem_private_key(_pem_bytes(pem, "PRIVATE KEY"), password=None)
    if not hasattr(key, "sign"):
        raise ValueError("不是 RSA 私钥")
    return key


def _load_public_key(pem: str):
    data = _pem_bytes(pem, "PUBLIC KEY")
    if b"CERTIFICATE" in data:
        return x509.load_pem_x509_certificate(data).public_key()
    return serialization.load_pem_public_key(data)


def _rsa_sign(pem: str, content: str) -> str:
    signature = _load_private_key(pem).sign(
        content.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256()
    )
    return base64.b64encode(signature).decode("ascii")


def _rsa_verify(pem: str, content: str, signature: str) -> bool:
    try:
        _load_public_key(pem).verify(
            base64.b64decode(signature),
            content.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return True
    except Exception:  # noqa: BLE001 —— 验签失败一律 false
        return False


class WeChatPayProvider:
    """微信支付 v3 Native 扫码（真实网关）。

    需要：AppID、商户号、APIv3 密钥、商户证书序列号、商户私钥 PEM、
    微信支付平台证书/公钥 PEM。缺一项即 ``configured=False``，下单/回调都
    明确报 ``BILLING_PAYMENT_NOT_CONFIGURED``，不静默降级。
    """

    code = PROVIDER_WECHAT
    label = "微信支付"
    api_path = "/v3/pay/transactions/native"

    def __init__(self) -> None:
        self.appid = settings.billing_wechat_appid.strip()
        self.mchid = settings.billing_wechat_mchid.strip()
        self.api_v3_key = settings.billing_wechat_api_v3_key.strip()
        self.serial_no = settings.billing_wechat_serial_no.strip()
        self.private_key = settings.billing_wechat_private_key.strip()
        self.platform_public_key = settings.billing_wechat_platform_public_key.strip()
        self.api_base = settings.billing_wechat_api_base.strip().rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(
            self.appid
            and self.mchid
            and self.api_v3_key
            and self.serial_no
            and self.private_key
            and self.platform_public_key
        )

    def _ensure(self) -> None:
        if not self.configured:
            raise AppError(
                "BILLING_PAYMENT_NOT_CONFIGURED",
                "微信支付尚未配置完整（AppID/商户号/APIv3 密钥/证书序列号/商户私钥/平台公钥）。",
                http_status=503,
            )
        if len(self.api_v3_key.encode("utf-8")) != 32:
            raise AppError(
                "BILLING_PAYMENT_NOT_CONFIGURED",
                "微信支付 APIv3 密钥必须是 32 字节。",
                http_status=503,
            )

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
        self._ensure()
        body = json.dumps(
            {
                "appid": self.appid,
                "mchid": self.mchid,
                "description": (plan_name or "科技小院套餐")[:127],
                "out_trade_no": _compact_order_id(order_id),
                "notify_url": notify_url,
                "amount": {"total": int(round(float(amount_yuan) * 100)), "currency": "CNY"},
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)
        sign_content = f"POST\n{self.api_path}\n{timestamp}\n{nonce}\n{body}\n"
        try:
            signature = _rsa_sign(self.private_key, sign_content)
        except Exception as e:  # noqa: BLE001
            raise AppError(
                "BILLING_PAYMENT_NOT_CONFIGURED", "微信支付商户私钥无法解析。", http_status=503
            ) from e
        authorization = (
            "WECHATPAY2-SHA256-RSA2048 "
            f'mchid="{self.mchid}",nonce_str="{nonce}",signature="{signature}",'
            f'timestamp="{timestamp}",serial_no="{self.serial_no}"'
        )
        try:
            with httpx.Client(timeout=15) as client:
                resp = client.post(
                    f"{self.api_base}{self.api_path}",
                    content=body.encode("utf-8"),
                    headers={
                        "Authorization": authorization,
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "User-Agent": "kjxy-billing/3.82",
                    },
                )
        except httpx.HTTPError as e:
            raise AppError(
                "BILLING_PAYMENT_GATEWAY_ERROR", f"微信支付下单请求失败：{e}", http_status=502
            ) from e
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400 or not data.get("code_url"):
            raise AppError(
                "BILLING_PAYMENT_GATEWAY_ERROR",
                (
                    f"微信支付下单失败：{data.get('code') or resp.status_code} "
                    f"{data.get('message') or ''}"
                ).strip(),
                http_status=502,
            )
        expires = datetime.now(UTC) + timedelta(minutes=INTENT_TTL_MINUTES)
        code_url = str(data["code_url"])
        return PaymentIntent(
            provider=self.code,
            mode="qr",
            order_id=order_id,
            order_code=order_code,
            plan_code=plan_code,
            plan_name=plan_name,
            amount_yuan=amount_yuan,
            pay_url=code_url,
            qr_payload=code_url,
            expires_at=expires.isoformat(),
            instructions=("请使用微信扫描二维码完成支付；支付成功后订单自动到账。",),
            extra={"code_url": code_url},
        )

    def verify_event(
        self, *, raw_body: bytes, headers: dict[str, str], query: dict[str, str]
    ) -> PaymentEvent:
        self._ensure()
        timestamp = headers.get("wechatpay-timestamp", "")
        nonce = headers.get("wechatpay-nonce", "")
        signature = headers.get("wechatpay-signature", "")
        if not timestamp or not nonce or not signature:
            raise AppError("BILLING_PAYMENT_SIGNATURE", "微信支付回调缺少签名头。", http_status=400)
        # 防重放：超过 5 分钟的回调拒绝
        try:
            if abs(int(time.time()) - int(timestamp)) > 300:
                raise AppError(
                    "BILLING_PAYMENT_SIGNATURE", "微信支付回调时间戳过期。", http_status=400
                )
        except ValueError as e:
            raise AppError(
                "BILLING_PAYMENT_SIGNATURE", "微信支付回调时间戳非法。", http_status=400
            ) from e
        sign_content = f"{timestamp}\n{nonce}\n{raw_body.decode('utf-8')}\n"
        if not _rsa_verify(self.platform_public_key, sign_content, signature):
            raise AppError(
                "BILLING_PAYMENT_SIGNATURE", "微信支付回调签名校验失败。", http_status=400
            )
        try:
            envelope = json.loads(raw_body.decode("utf-8"))
            resource = envelope["resource"]
            plain = AESGCM(self.api_v3_key.encode("utf-8")).decrypt(
                str(resource["nonce"]).encode("utf-8"),
                base64.b64decode(resource["ciphertext"]),
                str(resource.get("associated_data") or "").encode("utf-8"),
            )
            data = json.loads(plain.decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            raise AppError(
                "BILLING_PAYMENT_SIGNATURE", "微信支付回调解密失败。", http_status=400
            ) from e
        if data.get("trade_state") not in (None, "SUCCESS"):
            raise AppError("BILLING_PAYMENT_STATE", "微信支付回调不是成功状态。", http_status=400)
        try:
            amount = float(data["amount"]["total"]) / 100.0
        except (KeyError, TypeError, ValueError) as e:
            raise AppError(
                "BILLING_PAYMENT_AMOUNT", "微信支付回调金额无效。", http_status=400
            ) from e
        paid_at = datetime.now(UTC)
        if data.get("success_time"):
            try:
                paid_at = datetime.fromisoformat(str(data["success_time"]))
            except ValueError:
                paid_at = datetime.now(UTC)
        return PaymentEvent(
            order_id=_expand_order_id(str(data.get("out_trade_no") or "")),
            trade_no=str(data.get("transaction_id") or ""),
            amount_yuan=amount,
            paid_at=paid_at,
            raw=data,
        )


class AlipayProvider:
    """支付宝电脑网站支付（RSA2 真实网关）。

    需要：应用 ID、应用私钥（PKCS8）、支付宝公钥。回调用 form 表单，
    验签通过且 trade_status 为 TRADE_SUCCESS/TRADE_FINISHED 才入账。
    """

    code = PROVIDER_ALIPAY
    label = "支付宝"

    def __init__(self) -> None:
        self.app_id = settings.billing_alipay_app_id.strip()
        self.private_key = settings.billing_alipay_private_key.strip()
        self.public_key = settings.billing_alipay_public_key.strip()
        self.gateway = settings.billing_alipay_gateway.strip().rstrip("?")

    @property
    def configured(self) -> bool:
        return bool(self.app_id and self.private_key and self.public_key)

    def _ensure(self) -> None:
        if not self.configured:
            raise AppError(
                "BILLING_PAYMENT_NOT_CONFIGURED",
                "支付宝尚未配置完整（应用 ID/应用私钥/支付宝公钥）。",
                http_status=503,
            )

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
        self._ensure()
        biz = json.dumps(
            {
                "out_trade_no": _compact_order_id(order_id),
                "product_code": "FAST_INSTANT_TRADE_PAY",
                "total_amount": f"{float(amount_yuan):.2f}",
                "subject": (plan_name or "科技小院套餐")[:256],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        params = {
            "app_id": self.app_id,
            "method": "alipay.trade.page.pay",
            "format": "JSON",
            "charset": "utf-8",
            "sign_type": "RSA2",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "version": "1.0",
            "notify_url": notify_url,
            "return_url": return_url,
            "biz_content": biz,
        }
        # 支付宝签名口径：剔除 sign / sign_type，其余参数按 key 排序拼接
        sign_content = "&".join(
            f"{key}={params[key]}"
            for key in sorted(params)
            if key not in ("sign", "sign_type") and params[key] not in (None, "")
        )
        try:
            params["sign"] = _rsa_sign(self.private_key, sign_content)
        except Exception as e:  # noqa: BLE001
            raise AppError(
                "BILLING_PAYMENT_NOT_CONFIGURED", "支付宝应用私钥无法解析。", http_status=503
            ) from e
        pay_url = f"{self.gateway}?{urlencode(params)}"
        expires = datetime.now(UTC) + timedelta(minutes=INTENT_TTL_MINUTES)
        return PaymentIntent(
            provider=self.code,
            mode="redirect",
            order_id=order_id,
            order_code=order_code,
            plan_code=plan_code,
            plan_name=plan_name,
            amount_yuan=amount_yuan,
            pay_url=pay_url,
            expires_at=expires.isoformat(),
            instructions=("点击链接跳转支付宝收银台完成支付；支付成功后订单自动到账。",),
            extra={"gateway": self.gateway},
        )

    def verify_event(
        self, *, raw_body: bytes, headers: dict[str, str], query: dict[str, str]
    ) -> PaymentEvent:
        self._ensure()
        try:
            data = {key: values[0] for key, values in parse_qs(raw_body.decode("utf-8")).items()}
        except Exception as e:  # noqa: BLE001
            raise AppError(
                "BILLING_PAYMENT_SIGNATURE", "支付宝回调载荷无法解析。", http_status=400
            ) from e
        signature = data.pop("sign", "")
        data.pop("sign_type", None)
        if not signature:
            raise AppError("BILLING_PAYMENT_SIGNATURE", "支付宝回调缺少签名。", http_status=400)
        sign_content = "&".join(
            f"{key}={data[key]}" for key in sorted(data) if data[key] not in (None, "")
        )
        if not _rsa_verify(self.public_key, sign_content, signature):
            raise AppError("BILLING_PAYMENT_SIGNATURE", "支付宝回调签名校验失败。", http_status=400)
        if data.get("app_id") and data["app_id"] != self.app_id:
            raise AppError(
                "BILLING_PAYMENT_SIGNATURE", "支付宝回调 app_id 不匹配。", http_status=400
            )
        if data.get("trade_status") not in ("TRADE_SUCCESS", "TRADE_FINISHED"):
            raise AppError("BILLING_PAYMENT_STATE", "支付宝回调不是成功状态。", http_status=400)
        try:
            amount = float(data["total_amount"])
        except (KeyError, TypeError, ValueError) as e:
            raise AppError("BILLING_PAYMENT_AMOUNT", "支付宝回调金额无效。", http_status=400) from e
        paid_at = datetime.now(UTC)
        for key in ("gmt_payment", "notify_time"):
            if data.get(key):
                try:
                    paid_at = datetime.strptime(data[key], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
                    break
                except ValueError:
                    continue
        return PaymentEvent(
            order_id=_expand_order_id(data.get("out_trade_no") or ""),
            trade_no=str(data.get("trade_no") or ""),
            amount_yuan=amount,
            paid_at=paid_at,
            raw=data,
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

    ``available`` 的语义是"**当前部署可以直接用它下单**"：只有
    ``BILLING_PAYMENT_PROVIDER`` 选中的渠道为 true；其余渠道即使商户参数
    齐全（``configured=true``）也只是"已配置、未启用"，不能直接下单。
    """
    current = (settings.billing_payment_provider or PROVIDER_MANUAL).strip().lower()
    out: list[dict[str, Any]] = []
    for code, cls in _PROVIDERS.items():
        provider = cls()
        # 下单服务只接受“当前配置的那一个渠道”，列表口径必须一致：
        # 否则前端会把已配好但未启用的渠道当成可选，点下去必然 400。
        configured = bool(getattr(provider, "configured", code == PROVIDER_MANUAL))
        out.append(
            {
                "code": code,
                "label": provider.label,
                "current": code == current,
                "available": code == current,
                "configured": configured,
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
