"""支付网关适配器单测：真实签名/验签/解密形状，不依赖外网或商户账号。
"""

from __future__ import annotations

import base64
import json
import time
from urllib.parse import urlencode

import pytest
from app.core.config import settings
from app.core.errors import AppError
from app.modules.billing.payment import (
    AlipayProvider,
    WeChatPayProvider,
    _compact_order_id,
)
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return key, private_pem, public_pem


def test_wechat_verify_event_decrypts_and_expands_order_id(monkeypatch):
    merchant_key, merchant_priv, _ = _keypair()
    platform_key, _, platform_pub = _keypair()
    api_key = "0123456789abcdef0123456789abcdef"
    order_id = "6c72815b-2059-4351-90be-ec1cbdb92895"

    monkeypatch.setattr(settings, "billing_wechat_appid", "wx-test")
    monkeypatch.setattr(settings, "billing_wechat_mchid", "1900000001")
    monkeypatch.setattr(settings, "billing_wechat_api_v3_key", api_key)
    monkeypatch.setattr(settings, "billing_wechat_serial_no", "SERIAL")
    monkeypatch.setattr(settings, "billing_wechat_private_key", merchant_priv)
    monkeypatch.setattr(settings, "billing_wechat_platform_public_key", platform_pub)

    payload = {
        "out_trade_no": _compact_order_id(order_id),
        "transaction_id": "4200000000202611160000000001",
        "trade_state": "SUCCESS",
        "success_time": "2026-11-16T18:00:00+08:00",
        "amount": {"total": 199900, "currency": "CNY"},
    }
    nonce = "abcdefghijkl"
    ciphertext = AESGCM(api_key.encode()).encrypt(
        nonce.encode(), json.dumps(payload).encode(), b"transaction"
    )
    body = json.dumps(
        {
            "id": "evt-1",
            "event_type": "TRANSACTION.SUCCESS",
            "resource": {
                "algorithm": "AEAD_AES_256_GCM",
                "ciphertext": base64.b64encode(ciphertext).decode(),
                "nonce": nonce,
                "associated_data": "transaction",
            },
        }
    ).encode()
    timestamp = str(int(time.time()))
    header_nonce = "header-nonce"
    sign_content = f"{timestamp}\n{header_nonce}\n{body.decode()}\n"
    signature = base64.b64encode(
        platform_key.sign(sign_content.encode(), padding.PKCS1v15(), hashes.SHA256())
    ).decode()

    event = WeChatPayProvider().verify_event(
        raw_body=body,
        headers={
            "wechatpay-timestamp": timestamp,
            "wechatpay-nonce": header_nonce,
            "wechatpay-signature": signature,
        },
        query={},
    )
    assert event.order_id == order_id
    assert event.trade_no == "4200000000202611160000000001"
    assert event.amount_yuan == pytest.approx(1999.0)


def test_wechat_rejects_bad_signature(monkeypatch):
    merchant_key, merchant_priv, _ = _keypair()
    _, _, platform_pub = _keypair()
    monkeypatch.setattr(settings, "billing_wechat_appid", "wx-test")
    monkeypatch.setattr(settings, "billing_wechat_mchid", "1900000001")
    monkeypatch.setattr(settings, "billing_wechat_api_v3_key", "0123456789abcdef0123456789abcdef")
    monkeypatch.setattr(settings, "billing_wechat_serial_no", "SERIAL")
    monkeypatch.setattr(settings, "billing_wechat_private_key", merchant_priv)
    monkeypatch.setattr(settings, "billing_wechat_platform_public_key", platform_pub)
    with pytest.raises(AppError) as exc:
        WeChatPayProvider().verify_event(
            raw_body=b"{}",
            headers={
                "wechatpay-timestamp": str(int(time.time())),
                "wechatpay-nonce": "n",
                "wechatpay-signature": "bad",
            },
            query={},
        )
    assert exc.value.code == "BILLING_PAYMENT_SIGNATURE"


def test_alipay_verify_event(monkeypatch):
    key, private_pem, public_pem = _keypair()
    monkeypatch.setattr(settings, "billing_alipay_app_id", "2021000000000001")
    monkeypatch.setattr(settings, "billing_alipay_private_key", private_pem)
    monkeypatch.setattr(settings, "billing_alipay_public_key", public_pem)

    order_id = "6c72815b-2059-4351-90be-ec1cbdb92895"
    data = {
        "app_id": "2021000000000001",
        "out_trade_no": _compact_order_id(order_id),
        "trade_no": "2026111622000000000001",
        "trade_status": "TRADE_SUCCESS",
        "total_amount": "1999.00",
        "gmt_payment": "2026-11-16 18:00:00",
        "notify_time": "2026-11-16 18:00:01",
        "sign_type": "RSA2",
    }
    sign_content = "&".join(
        f"{k}={data[k]}" for k in sorted(data) if k not in ("sign", "sign_type")
    )
    data["sign"] = base64.b64encode(
        key.sign(sign_content.encode(), padding.PKCS1v15(), hashes.SHA256())
    ).decode()
    event = AlipayProvider().verify_event(
        raw_body=urlencode(data).encode(), headers={}, query={}
    )
    assert event.order_id == order_id
    assert event.trade_no == "2026111622000000000001"
    assert event.amount_yuan == pytest.approx(1999.0)


def test_gateway_not_configured_is_explicit(monkeypatch):
    for field in (
        "billing_wechat_appid",
        "billing_wechat_mchid",
        "billing_wechat_api_v3_key",
        "billing_wechat_serial_no",
        "billing_wechat_private_key",
        "billing_wechat_platform_public_key",
    ):
        monkeypatch.setattr(settings, field, "")
    with pytest.raises(AppError) as exc:
        WeChatPayProvider().create_intent(
            order_id="6c72815b-2059-4351-90be-ec1cbdb92895",
            order_code="TY6C72815B20594351",
            plan_code="basic",
            plan_name="基础套餐",
            amount_yuan=1999,
            notify_url="https://example.com/notify",
            return_url="https://example.com",
        )
    assert exc.value.code == "BILLING_PAYMENT_NOT_CONFIGURED"

    for field in (
        "billing_alipay_app_id",
        "billing_alipay_private_key",
        "billing_alipay_public_key",
    ):
        monkeypatch.setattr(settings, field, "")
    with pytest.raises(AppError) as exc2:
        AlipayProvider().create_intent(
            order_id="6c72815b-2059-4351-90be-ec1cbdb92895",
            order_code="TY6C72815B20594351",
            plan_code="basic",
            plan_name="基础套餐",
            amount_yuan=1999,
            notify_url="https://example.com/notify",
            return_url="https://example.com",
        )
    assert exc2.value.code == "BILLING_PAYMENT_NOT_CONFIGURED"


def test_wechat_create_intent_signs_and_parses_code_url(monkeypatch):
    _, merchant_priv, _ = _keypair()
    _, _, platform_pub = _keypair()
    monkeypatch.setattr(settings, "billing_wechat_appid", "wx-test")
    monkeypatch.setattr(settings, "billing_wechat_mchid", "1900000001")
    monkeypatch.setattr(settings, "billing_wechat_api_v3_key", "0123456789abcdef0123456789abcdef")
    monkeypatch.setattr(settings, "billing_wechat_serial_no", "SERIAL")
    monkeypatch.setattr(settings, "billing_wechat_private_key", merchant_priv)
    monkeypatch.setattr(settings, "billing_wechat_platform_public_key", platform_pub)

    captured: dict = {}

    class FakeResponse:
        status_code = 200
        content = b'{"code_url": "weixin://wxpay/bizpayurl?pr=test"}'

        @staticmethod
        def json():
            return {"code_url": "weixin://wxpay/bizpayurl?pr=test"}

    class FakeClient:
        def __init__(self, **_kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def post(self, url, content=None, headers=None):
            captured.update(url=url, content=content, headers=headers)
            return FakeResponse()

    import app.modules.billing.payment as payment_module

    monkeypatch.setattr(payment_module.httpx, "Client", FakeClient)
    intent = WeChatPayProvider().create_intent(
        order_id="6c72815b-2059-4351-90be-ec1cbdb92895",
        order_code="TY6C72815B20594351",
        plan_code="basic",
        plan_name="基础套餐",
        amount_yuan=1999,
        notify_url="https://example.com/api/v1/billing/payments/wechat_pay/notify",
        return_url="https://example.com",
    )
    assert intent.qr_payload == "weixin://wxpay/bizpayurl?pr=test"
    assert captured["headers"]["Authorization"].startswith("WECHATPAY2-SHA256-RSA2048 ")
    body = json.loads(captured["content"])
    assert body["out_trade_no"] == "6c72815b2059435190beec1cbdb92895"
    assert body["amount"]["total"] == 199900


def test_alipay_create_intent_returns_signed_redirect(monkeypatch):
    _, private_pem, public_pem = _keypair()
    monkeypatch.setattr(settings, "billing_alipay_app_id", "2021000000000001")
    monkeypatch.setattr(settings, "billing_alipay_private_key", private_pem)
    monkeypatch.setattr(settings, "billing_alipay_public_key", public_pem)
    intent = AlipayProvider().create_intent(
        order_id="6c72815b-2059-4351-90be-ec1cbdb92895",
        order_code="TY6C72815B20594351",
        plan_code="basic",
        plan_name="基础套餐",
        amount_yuan=1999,
        notify_url="https://example.com/api/v1/billing/payments/alipay/notify",
        return_url="https://example.com",
    )
    assert intent.mode == "redirect"
    assert "alipay.trade.page.pay" in (intent.pay_url or "")
    assert "sign=" in (intent.pay_url or "")
