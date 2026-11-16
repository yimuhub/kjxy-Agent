# v3.82.0 四项遗留项交付说明

本轮把此前列为“需产品/外部条件”的四项全部落到真实代码，并保留原有功能与入口。

## 1. 真实支付网关

`backend/app/modules/billing/payment.py`

- **微信支付 v3 Native 扫码**
  - 下单：`POST /v3/pay/transactions/native`，`WECHATPAY2-SHA256-RSA2048` 签名，返回 `code_url`。
  - 回调：校验 `Wechatpay-Timestamp` / `Wechatpay-Nonce` / `Wechatpay-Signature`，5 分钟防重放；用平台证书/公钥验签；AES-256-GCM 解密 `resource`；校验 `trade_state` 与金额。
  - 订单号：标准 UUID 去连字符成 32 位，满足 `out_trade_no ≤ 32`；回调再展开回标准 UUID，复用 `settle_payment_event`。
- **支付宝电脑网站支付**
  - 下单：`alipay.trade.page.pay`，RSA2 签名，生成带 `sign` 的收银台 URL。
  - 回调：解析 form 表单，剔除 `sign`/`sign_type` 后按 key 排序验签；校验 `app_id`、`trade_status ∈ TRADE_SUCCESS/TRADE_FINISHED`、金额。
  - 异步通知返回纯文本 `success`；微信仍返回 JSON `code=SUCCESS`。
- **配置与门禁**
  - 新增 `BILLING_WECHAT_SERIAL_NO`、`BILLING_WECHAT_PRIVATE_KEY`、`BILLING_WECHAT_PLATFORM_PUBLIC_KEY`、`BILLING_WECHAT_API_BASE`、`BILLING_ALIPAY_GATEWAY`。
  - 生产环境选择 `wechat_pay`/`alipay` 但凭证不全时，`Settings.validate_for_runtime` 启动即阻断，避免“选了真实渠道却收不了款”。
  - `GET /billing/payment/providers` 的 `available` 只对当前启用渠道为 true；新增 `configured` 表示“凭证齐全但未启用”。
- **前端**
  - 支付宝：显示“前往支付宝支付”，支持“我已完成支付，刷新状态”。
  - 微信：扫码后显示“我已完成支付，刷新状态”，不再误调沙箱。
  - 到账仍只由真实回调驱动，前端刷新只查订单状态。

## 2. 政策模块真实种子

`backend/app/modules/policy/seed.py`

- 内置 6 条官方来源政策：
  1. 山东省 2024—2026 年农机购置与应用补贴实施方案（鲁农机字〔2024〕7号）
  2. 2026 年山东“优机优补”“有进有出”实施方案（鲁农机字〔2026〕7号）
  3. 山东 2026 年粮食烘干中心成套设备补贴方案（鲁农函字〔2026〕194号）
  4. 2025 年中央一号文件
  5. 新型农业经营主体提质增效行动方案（农业农村部）
  6. 关于支持建设一批科技小院的通知（教育部/农业农村部/中国科协）
- 每条写 `source_url`、`source_hash`、`review_status=verified`、`verified_at`、`last_checked_at`、`apply_conditions_json`、`material_requirements_json`。
- 正文为官方原文摘要，并标注“原文见来源链接”；条件只写官方明确的地域/主体，材料不确定处注明“以当地当年通知为准”，不编造金额、条件、链接。
- 新小院：`setup/service.py` 第 ④ 步 `seed_default_policies(db, yard.id)`，返回体新增 `policy_count`。
- 老小院：`main.py` lifespan 启动 `seed_all_yards(db)` 幂等回填，失败只警告不阻断。

## 3. IA 三层重构与 ID 字段统一

`frontend/src/stores/menu.ts`

- 三层导航：
  - ① 今天要做的：今日工作台、农事台账、作物诊断、智能对话、工作日志。
  - ② 日常业务：知识库、惠农政策、告警工单、任务中心、专家审核、设备接入、区域工作台。
  - ③ 高级能力：AI 任务编排、AI 能力（我的智能体/官方助手/AI 团队协作/智能体工厂/Agent 市场/插件与技能库/文件工作区）、组织架构、用量额度、能力升级、管理与统计（经营驾驶舱/投入产出/运行大屏/全局监管/模型与供应商/成员与权限/IM 机器人/系统设置）。
- 去黑话改名：枢纽工作台→区域工作台、指挥官工作台→AI 任务编排、协作班组→AI 团队协作、智能工厂→智能体工厂、工作区→文件工作区、自我升级→能力升级、农业台账→农事台账、政策服务→惠农政策、模型中心→模型与供应商、成员管理→成员与权限。
- 所有原路由/入口保留；30 个菜单原子无缺失；默认布局只额外增加 `capability`、`admin` 两个父节点和 `owner` 子项。
- 缓存版本 `kjxy_menu_diy_v4_` → `v5`，旧四组布局一次性重建；不改数据库、不删功能。
- ID 统一采用“新增 `id`、保留旧字段”非破坏式兼容：
  - `farm/service.py` 的 `_plot_out`/`_cycle_out`/`_record_out` 增 `id`。
  - `farm/diagnosis_service.py` 的诊断依据/详情、随访、附件增 `id`。
  - 旧 `plot_id/cycle_id/record_id/diagnosis_id/followup_id/attachment_id` 全保留。

## 4. `/llm/providers` 成员最小权限

`backend/app/modules/llm/supplier_router.py`

- 管理员（`platform_admin`/`yard_admin`/`yard_owner`）继续拿完整 `SupplierOut`（`base_url`、Key 掩码、价格、失败信息等）。
- 普通成员只返回安全字段：`id/name/provider_type/model_id/default_model_id/models/channel_kind/status/is_active/readonly`；`base_url=""`、`api_key_masked=""`；不返回 `last_error`、价格、探测延迟。
- 成员仍可读（编辑智能体需要选模型），但 `POST/PUT/DELETE` 仍为 403。

## 验证

- 全量后端：`2662 passed, 7 skipped`（比上一轮多 9 条）。
- 新增单测：
  - `test_billing_payment_gateways.py` 6 条：微信下单签名/回调验签+解密、支付宝下单 URL/回调验签、缺配置报错。
  - `test_policy_seed.py` 1 条：幂等、可溯源。
  - `test_llm_supplier_permissions.py` 2 条：成员/管理员投影。
- 隔离 API：政策 6 条、重启后仍 6、再次 seed 返回 0；成员只读 12 个安全字段、写 403；农事 `id == 旧字段`；支付 providers 口径正确、manual 下单意图成功。
- 前端 `npm run build`（含 `vue-tsc --noEmit`）通过。
- E2E `regress_fixes.py`：9/9 PASS。

## 真实扣款前置条件

代码与签名/验签/解密已完成，但真实资金链路验收仍需外部条件：

- 微信：商户号、商户证书序列号、商户私钥、APIv3 密钥（32 字节）、平台证书。
- 支付宝：应用 ID、应用私钥、支付宝公钥。
- 可公网 HTTPS 访问的回调域名：
  - `https://<domain>/api/v1/billing/payments/wechat_pay/notify`
  - `https://<domain>/api/v1/billing/payments/alipay/notify`

配置项与上线检查见 `docs/payment-gateway.md`。
