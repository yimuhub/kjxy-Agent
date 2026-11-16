# 支付网关配置（微信支付 / 支付宝）

平台已内置三条真实支付链路的**代码适配**：微信支付 v3 Native 扫码、支付宝电脑网站支付、对公转账。默认仍是 `manual`（对公转账 + 回单核销），不会因为升级而擅自改变现有收款方式。

> 没有商户号/证书/公网回调域名时，渠道会被标记为不可用；下单会明确返回 `BILLING_PAYMENT_NOT_CONFIGURED`，不会静默降级成假支付。

## 一、通用要求

- 生产必须是公网 HTTPS 域名，且能在网关后台配置回调地址。
- 回调地址（把 `https://your-domain` 换成实际域名）：
  - 微信：`https://your-domain/api/v1/billing/payments/wechat_pay/notify`
  - 支付宝：`https://your-domain/api/v1/billing/payments/alipay/notify`
- 金额单位为元；订单号使用 UUID 去连字符后的 32 位，满足微信 `out_trade_no` ≤32 的限制。
- 到账完全由**验签通过的回调**驱动：签名失败/金额不符/非成功状态一律拒绝；同一流水重复回调幂等，不会重复发额度。

## 二、微信支付 v3

申请商户号、绑定 AppID、下载商户 API 证书与平台证书后配置：

```dotenv
BILLING_PAYMENT_PROVIDER=wechat_pay
BILLING_WECHAT_APPID=wx...
BILLING_WECHAT_MCHID=190...
BILLING_WECHAT_API_V3_KEY=32位APIv3密钥
BILLING_WECHAT_SERIAL_NO=商户证书序列号
BILLING_WECHAT_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----"
BILLING_WECHAT_PLATFORM_PUBLIC_KEY="-----BEGIN CERTIFICATE-----\\n...\\n-----END CERTIFICATE-----"
# 可选：私有化/代理网关
BILLING_WECHAT_API_BASE=https://api.mch.weixin.qq.com
```

- 下单接口：`POST /v3/pay/transactions/native`，返回 `code_url`，前端渲染二维码。
- 回调验签：`Wechatpay-Timestamp/Nonce/Signature` + 平台证书；AES-256-GCM 解密 `resource`。
- 平台证书轮换时只需替换 `BILLING_WECHAT_PLATFORM_PUBLIC_KEY`，代码无需改动。

## 三、支付宝

在支付宝开放平台创建应用、配置 RSA2 密钥、签约“电脑网站支付”后配置：

```dotenv
BILLING_PAYMENT_PROVIDER=alipay
BILLING_ALIPAY_APP_ID=2021...
BILLING_ALIPAY_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----"
BILLING_ALIPAY_PUBLIC_KEY="-----BEGIN PUBLIC KEY-----\\n...\\n-----END PUBLIC KEY-----"
# 可选：沙箱/私有化网关
BILLING_ALIPAY_GATEWAY=https://openapi.alipay.com/gateway.do
```

- 下单走 `alipay.trade.page.pay`，前端跳转收银台；支付完成后回跳平台订单页刷新状态。
- 回调是 form 表单，按参数排序做 RSA2 验签；`trade_status` 必须为 `TRADE_SUCCESS` 或 `TRADE_FINISHED`。
- 支付宝要求回调应答纯文本 `success`，平台已按渠道区分应答体。

## 四、本地联调

没有商户资质时用沙箱渠道跑通全链路：

```dotenv
BILLING_PAYMENT_PROVIDER=sandbox
```

沙箱回调使用 HMAC-SHA256，与真实网关共用同一套“回调 → 验签 → 金额校验 → 幂等到账”代码；生产环境启动校验会拒绝 `sandbox`。

## 五、验收清单

1. 下单后拿到 `code_url`（微信）或 `pay_url`（支付宝）。
2. 用真实账号支付最小金额，确认回调到达且订单变 `paid`、额度到账。
3. 重复投递同一回调，确认不重复发放额度。
4. 篡改金额/签名，确认返回 4xx 且订单仍为 `pending`。
5. 在网关后台确认回调成功率与重试记录。
