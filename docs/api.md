# API 说明

基准契约：`contracts/openapi.yaml`（机器可读）、`contracts/error-codes.md`（错误码）。

## 通用规则

- 前缀 `/api/v1`；JSON UTF-8；时间 ISO 8601 UTC；ID 为 UUID 字符串。
- 请求可带 `X-Request-ID`，服务端透传回响应头与响应体。
- 需身份接口：`Authorization: Bearer <access_token>`。
- 创建接口支持 `Idempotency-Key`（同小院同键返回同一结果）。
- 列表接口：`page`、`page_size`（默认 20，最大 100）。
- 成功：`{"data": {...}, "request_id": "..."}`；列表加 `meta`；错误：`{"error": {"code","message","details"}, "request_id": "..."}`。

## 接口一览（P0）

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | /auth/login | 登录（多小院用户需传 yard_id） |
| POST | /auth/refresh | 刷新令牌 |
| POST | /auth/logout | 注销 |
| GET | /auth/me | 当前用户 |
| GET | /yards/current | 当前小院概要 |
| GET/POST | /yards/current/members | 成员列表 / 添加成员 |
| DELETE | /yards/current/members/{user_id} | 移除成员 |
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

## 接口一览（v3.0 新增）

| 方法 | 路径 | 权限 | 用途 |
|---|---|---|---|
| GET | /search/status | 登录 | 联网状态、供应商与今日配额 |
| POST | /search/test | search:manage | 真实连通性测试 |
| POST | /market/items/search | market:install | 内置/缓存/GitHub 条目检索（离线回退） |
| GET | /market/items/{item_key} | market:install | 条目详情（含 definition/manifest） |
| POST | /market/items/{item_key}/install | market:install | 安装技能/插件（第三方转待审） |
| GET | /market/installed | market:install | 本小院已安装技能与插件 |
| POST | /market/skills/import | market:install | 本地 Skill 文件导入（≤256KB） |
| POST | /market/plugins/import | market:install + market:review | 插件 zip 导入（≤2MB） |
| POST | /market/github/search | market:install | GitHub 农业技能检索（服务端代理） |
| POST | /market/github/preview | market:install | 预览 SKILL.md 并落库为可安装条目 |
| GET | /yards/current/members | 登录（无管理权脱敏） | 成员目录 |
| POST | /yards/current/members | members:manage | 新建/挂接成员（返回一次性初始密码） |
| PATCH | /yards/current/members/{user_id} | members:manage / manage_limited | 资料/角色/状态 |
| POST | /yards/current/members/{user_id}/reset-password | members:reset_password | 重置密码（一次性临时密码） |
| DELETE | /yards/current/members/{user_id} | members:manage | 软删除成员 |
| GET | /yards/current/registrations | members:approve | 注册申请列表 |
| POST | /yards/current/registrations/{id}/approve | members:approve | 通过注册 |
| POST | /yards/current/registrations/{id}/reject | members:approve | 拒绝注册（可带原因） |
| GET | /yards/current/settings | settings:manage | 注册/安全/搜索设置（密钥脱敏） |
| PUT | /yards/current/settings/{key} | settings:manage / search:manage | 更新设置项 |
| GET/POST | /yards/current/roles | roles:manage | 角色列表 / 新建自定义角色 |
| PUT/DELETE | /yards/current/roles/{code} | roles:manage | 修改 / 删除自定义角色 |
| POST | /auth/register | 公开（限流） | 自助加入小院（approval/open） |
| PATCH | /auth/me | 登录 | 修改本人资料 |
| POST | /auth/change-password | 登录 | 修改密码（旧令牌失效） |
| GET | /yards/current/owner/overview | insights:read | 驾驶舱总览 |
| GET | /yards/current/owner/member-usage | insights:read | 按人用量 |
| GET | /yards/current/owner/daily-usage | insights:read | 按日趋势 |
| GET | /yards/current/owner/agent-usage | insights:read | 按 Agent 用量 |
| GET | /yards/current/owner/sessions | insights:sensitive | 会话审计列表 |
| GET | /yards/current/owner/sessions/{id}/messages | insights:sensitive | 查看正文（需 reason，落审计） |
| GET/PUT | /yards/current/owner/grants[/{user_id}] | members:manage_admins | 敏感权限逐人授权 |
| POST | /yards/current/owner/transfer | owner:transfer | 负责人转让 |
| GET | /yards/current/owner/audit | audit:read | 近期审计 |

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
