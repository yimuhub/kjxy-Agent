# API 说明

基准契约：`contracts/openapi.yaml`（机器可读）、`contracts/error-codes.md`（错误码）。

## 通用规则

- 前缀 `/api/v1`；JSON UTF-8；时间 ISO 8601 UTC；ID 为 UUID 字符串。
- 请求可带 `X-Request-ID`，服务端透传回响应头与响应体。
- 需身份接口：`Authorization: Bearer <access_token>`。
- 创建接口支持 `Idempotency-Key`（同租户同键返回同一结果）。
- 列表接口：`page`、`page_size`（默认 20，最大 100）。
- 成功：`{"data": {...}, "request_id": "..."}`；列表加 `meta`；错误：`{"error": {"code","message","details"}, "request_id": "..."}`。

## 接口一览（P0）

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | /auth/login | 登录（多租户用户需传 tenant_id） |
| POST | /auth/refresh | 刷新令牌 |
| POST | /auth/logout | 注销 |
| GET | /auth/me | 当前用户 |
| GET | /tenants/current | 当前租户概要 |
| GET/POST | /tenants/current/members | 成员列表 / 添加成员 |
| DELETE | /tenants/current/members/{user_id} | 移除成员 |
| POST/GET | /tasks | 创建任务 / 任务列表 |
| GET | /tasks/{id} | 任务详情 |
| GET | /tasks/{id}/events | 进度事件 |
| POST | /tasks/{id}/cancel | 取消 |
| POST | /tasks/{id}/retry | 重试 |
| POST/GET | /knowledge/documents | 上传文档 / 文档列表 |
| GET/DELETE | /knowledge/documents/{id} | 文档详情 / 软删除 |
| POST | /knowledge/search | 知识检索 |
| GET | /knowledge/candidates | 待审核知识 |
| POST | /knowledge/candidates/{id}/review | 审核（通过/驳回） |
| POST | /qa/answers | 问答（sync/async） |
| POST/GET | /feedback | 提交反馈 / 查看反馈 |
| GET/POST | /skills | Skill 列表 / 创建草稿 |
| POST | /skills/{id}/review | Skill 审核流转 |
| GET | /llm/status | 模型配置状态（无密钥） |
| GET | /tools | 工具列表 |
| GET | /audit/logs | 审计日志 |
| GET | /admin/stats | 运营概况 |

## 任务状态机

`queued → running → succeeded | failed | cancelled | awaiting_review → succeeded | failed | cancelled`

非法转移返回 `INVALID_TASK_TRANSITION`（409）；每次转移写入 `task_events`。

## 示例

创建任务：

```bash
curl -X POST http://localhost:8000/api/v1/tasks \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"task_type":"knowledge_qa","input":{"question":"水稻分蘖期需要重点观察什么？"}}'
```

问答（同步）：

```bash
curl -X POST http://localhost:8000/api/v1/qa/answers \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"question":"水稻分蘖期需要重点观察什么？","mode":"sync"}'
```
