# 开发者门户（Developer Portal）

> 面向二次开发者与集成方：如何在本平台之上构建能力、对接事件、消费配额。
> 配套审查报告（2026-11-07）P2 项「对外开放不成体系」的第一步——先把入口与
> 目录写清楚，API 门户站点与计费文档随后迭代。

## 1. 平台开放面总览

| 能力 | 协议 | 入口 | 鉴权 |
| --- | --- | --- | --- |
| REST API（415 路径） | HTTP + JSON 信封 | `/api/v1/*` | Bearer JWT（登录/refresh 轮换） |
| 机器协作入站 | A2A JSON-RPC | `POST /a2a/rpc`（Agent Card 见 `/.well-known`） | 配对凭据 + 限流 |
| 工具协议 | MCP | `/mcp/*`（工具注册表 `tools/registry.py`） | 令牌 |
| IM 机器人 | Webhook | `POST /bots/*`（飞书/钉钉/企微通道） | 通道密钥 |
| 设备数据上报 | HTTP webhook | `POST /devices/*`（source_type=webhook） | 设备凭据 |
| 官方模型支付回调 | Webhook | `POST /llm/official/webhooks/payment` | HMAC 验签（X-Signature/X-Timestamp） |

## 2. REST API 约定

- **信封**：`{ "data": ..., "request_id": ... }`；分页为
  `{ "data": [...], "meta": { page, page_size, total } }`；错误为
  `{ "error": { "code", "message", "details" } }`，机器可读码见
  `docs/error-codes.md`。
- **契约**：`contracts/openapi.yaml` 与代码强制同步（CI 跑 `make contract-check`），
  面向客户端生成请以它为唯一事实源。
- **租户边界**：所有小院资源端点的 `yard_id` 一律取自 JWT 上下文，不接受请求
  注入；run 级资源越权统一 404（防枚举）。

## 3. 配额与计费（v3.71 起）

- 配额按「作用域 × 指标（tokens/calls/cost）× 窗口（day/month）」建模，
  超限行为 warn/block/approve 由策略决定（`/quota/*`）。
- 自助开通：`GET /billing/plans` → 线下收款后
  - 兑换码：`POST /billing/redeem`（即时到账，写 `quota_grants`）；或
  - 对公订单：`POST /billing/orders` → 平台管理员确认 → 自动到账。
- 消耗明细：`llm_calls` 账本（每次调用 input/output tokens 与估算成本），
  聚合视图 `/yards/current/owner/roi`。

## 4. 事件与回调目录（当前）

| 事件 | 方向 | 触发 |
| --- | --- | --- |
| 任务进度（SSE） | 平台 → 前端 | `GET /tasks/{id}/events/stream`，milestone/progress 帧 |
| 官方模型支付结果 | 网关 → 平台 | `POST /llm/official/webhooks/payment`（HMAC 验签，幂等 event_id） |
| 设备告警 | 设备 → 平台 | webhook 上报 → 规则引擎 → 告警/自动转工单 |
| 出站推送 | 平台 → IM | `/settings/push-channels` 配置的企微机器人/钉钉/webhook |

> 出站 Webhook 订阅（事件目录 + 重试 + 签名）在排期中；当前出站通知以
> 推送通道轮询/触发为主，接入方如需事件流请先用 SSE。

## 5. 本地开发

```bash
# 后端（SQLite + inline 模式，无需 Celery）
cd backend && uv run uvicorn app.main:app --port 8000
# 前端
cd frontend && npm run dev   # 代理 /api 到 127.0.0.1:8000
# 契约与门禁
make contract && make check   # 版本面九处校验
```

更完整的部署/运维说明见 `docs/operations.md` 与 `README.md`。
