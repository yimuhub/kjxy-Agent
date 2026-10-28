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

## 接口一览（v3.3 新增）

| 方法 | 路径 | 权限 | 用途 |
|---|---|---|---|
| POST | /market/agent/generate | skills:generate | 新增 `kind` 参数：`skill`（默认）/ `plugin`；`plugin` 按工作流模板生成并一律待审 |
| POST | /chat/sessions/{id}/messages（含 stream） | 登录 + skills:generate | 「插件」类意图改走插件生成，消息载荷新增 `plugin_card` |

v3.3 行为变化：

- 插件生成没有「私有即用」：`kind=plugin` 的产物经 `register_workflow` 直接落
  `status=pending_review`（type=workflow、小院范围），须经 `POST /plugins/{id}/review`
  （负责人 / 小院管理员 / 平台管理员）审核并 `enable` 后才能运行；专家与普通成员只能等待。
- 工作流只允许工具注册表内的工具，拒绝 `mcp__` 前缀；单插件最多 8 步；manifest 非法返回
  `PLUGIN_MANIFEST_INVALID` 400。生成失败不落库。
- 技能与插件共用 `skill_lab` 配额：`daily_limit` 用尽同样返回 `SKILL_QUOTA_EXCEEDED` 429，
  `enabled=false` 返回 `SKILL_LAB_DISABLED` 403。
- 对话内 `plugin_card` 载荷：`{type:"plugin_card", state:"created"|"failed", utterance,
  plugin:{plugin_id,name,description,status,step_count}, manifest:{name,description,params,steps},
  sources, note}`；`failed` 卡片可按原话重试。
- 新增工具：`skill.search`（read_only）、`skill.generate`（draft）、`skill.install`
  （external_side_effect，执行需审批），均按 `skills:generate` / `market:install` 鉴权。
- 安全加固：对话内技能 / 插件生成与 HTTP 端点同口径，缺 `skills:generate` 一律返回
  403 `FORBIDDEN`（此前对话链路未校验）。
- `push` 事件新增 `skill_force_published`：负责人把私有技能转为小院公共时通知创建者。

## 接口一览（v3.2 新增）

| 方法 | 路径 | 权限 | 用途 |
|---|---|---|---|
| POST | /market/agent/request | skills:generate | 一句话检索候选（本地优先，无命中试 GitHub，离线降级本地） |
| POST | /market/agent/generate | skills:generate | 一句话生成技能草稿（联网找资料 → 模型出规格 → 校验 → 落私有草稿） |
| POST | /market/agent/install | market:install | 按 item_key 安装市场候选；按 skill_id 把自己的私有草稿送审 |
| GET | /skills?scope=mine\|yard\|platform | 登录 | 技能归属视图（我的 / 小院公共 / 平台共享；缺省为本小院全部） |
| POST | /skills/{id}/promote | skills:publish_any | 负责人把私有技能转为小院公共并发布（保护型权限，可逐人下放） |

v3.2 行为变化：

- 一句话生成的技能一律落为**私有草稿**（`visibility=private`、`owner_user_id=当前用户`），
  需经 `/skills/{id}/review` 的 `submit_public` 送审后才进入小院公共库。
- `/market/agent/generate` 受小院设置 `skill_lab` 控制：`enabled`（开关）、`daily_limit`
  （每日生成配额，超出返回 `SKILL_QUOTA_EXCEEDED` 429）、`use_web`（是否联网找资料）、
  `max_sources`（引用来源上限）。开关关闭时返回 `SKILL_LAB_DISABLED` 403（检索不受影响）。
- `skill_lab` 走通用设置接口：`GET /yards/current/settings` 随全部设置项下发，
  `PUT /yards/current/settings/skill_lab` 需 `settings:manage` 权限。
- 生成失败**不落库**，接口返回原因（`note`），前端在向导内展示而非整页报错。
- `/skills` 新增 `scope` 查询参数；`promote` 幂等：已非 private 时返回当前对象。

## 接口一览（v3.1 新增）

| 方法 | 路径 | 权限 | 用途 |
|---|---|---|---|
| GET | /agents/bindable-extensions | 登录（小院上下文） | 编辑弹窗「扩展技能 / 扩展插件」候选聚合（含来源、风险、可见性、被引用数） |

v3.1 行为变化：`/search/status` 新增 `internet` 字段；搜索设置 `provider` 支持 `auto`
（默认）与 `builtin_sources`（bing/sogou/so360 子集）。`/agents/declarative` 创建与更新
会对 `bound_skills` 逐项校验，未知编码返回 422。

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

## 技能签名与评测（v3.17）

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | /skills/signing-key | 登录 | 技能签名公钥（Ed25519）与 key_id，用于离线校验 |
| GET | /skills/{id}/attestation | 登录 | 定义摘要、签名、验签结论（ok/unsigned/definition_mismatch/bad_signature/key_mismatch）与最近一份评测报告 |
| GET | /skills/{id}/eval-runs | 登录 | 该版本评测历史（摘要字段，不含报告全文） |
| GET | /skills/{id}/eval-runs/{run_id} | 登录 | 单次评测报告全文（静态检查项、数据集通过率、逐用例结论） |

口径：

- 发布路径统一出报告：审核 `approve`、负责人 `promote`、市场安装内置技能都会先跑静态质量检查；技能定义里配置了 `evaluation.test_set_id` 时再跑数据集评测。静态有致命项（空定义、提示词过长、步骤超限、工具步骤未声明）返回 `SKILL_EVAL_BLOCKED`（409），数据集未达通过率返回 `EVAL_GATE_BLOCKED`（409）。
- 报告写入 `eval_runs.report_json`，附 `report_sha256` 与 `dataset_sha256`；`kind` 区分 `gate|static|install|promote`。
- 通过后对 `skill_code + version + definition_sha256` 做 Ed25519 签名；回滚与灰度发布前验签，定义被改动返回 `SKILL_ATTESTATION_INVALID`（409）。3.17 之前的存量已发布版本没有签名，接口如实返回 `unsigned`，回滚/灰度时补签。

## 单点登录 SSO/OIDC（v3.18）

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | /auth/sso/providers | 公开 | 登录页可用的第三方登录方式（仅名称、小院、IdP 主机名；全局开关关闭时为空） |
| GET | /auth/sso/{provider_id}/start | 公开 | 生成授权地址与签名 state，并把 PKCE code_verifier 写进 HttpOnly Cookie |
| GET | /auth/sso/callback | 公开 | IdP 回调：校验 state/nonce/PKCE → 换令牌 → 建号/绑定 → 302 回前端并带一次性 code |
| POST | /auth/sso/exchange | 公开 | 一次性 code 换正式登录响应（access/refresh、roles、yards、yard_id） |
| GET | /settings/sso-providers | settings:manage | 本小院身份提供方列表（密钥只回 `client_secret_configured`） |
| POST | /settings/sso-providers | settings:manage_protected | 新增身份提供方（同名 409） |
| PATCH | /settings/sso-providers/{id} | settings:manage_protected | 修改配置；`client_secret` 留空表示沿用已存密钥 |
| DELETE | /settings/sso-providers/{id} | settings:manage_protected | 删除配置并解绑其身份记录（本地账号保留） |
| POST | /settings/sso-providers/{id}/test | settings:manage_protected | 探测 discovery 与 JWKS，只回端点与公钥数量 |

口径：

- 授权码流程 + `state` JWT（类型 `sso_state`，含 IdP id 与 nonce）+ PKCE S256 + 一次性短码（默认 60 秒，哈希存储、用后即焚）。
- id_token 只接受非对称算法（RS256/384/512、ES256/384、EdDSA），按 JWKS 验签；校验 `iss/aud/exp/iat/nonce`，`HS*` 一律拒绝。
- 角色只来自小院成员关系：IdP 分组经 `role_mapping` 映射到 `yard_admin/expert/operator/viewer`，超出范围的映射在保存配置时即 422；负责人与平台管理员永远不能由 IdP 授予。
- JIT：默认自动建号（随机密码、状态 active），要求邮箱已验证；可关闭自动建号、关闭按邮箱关联，并限制邮箱域名。
- 全局开关 `SSO_ENABLED` 关闭时，公开端点返回空列表/`SSO_DISABLED`，已配置的 IdP 不生效。
- 出网走 `net_guard`（元数据/链路本地地址始终禁止；IdP 地址按负责人配置的可信输入放行内网与本机），单次响应上限 1MB；错误只回错误码，不回 IdP 原始响应。

## 离线写入与冲突合并（v3.19）

台账（地块/周期/农事记录）与工单写接口支持离线队列：

| 位置 | 字段 | 说明 |
|---|---|---|
| 创建类请求体（POST /farm/plots、/farm/plots/{id}/cycles、/farm/records、/farm/work-orders、/farm/work-orders/{id}/receipt） | `client_op_id`（可选，8-64 位安全字符） | 幂等键；同一键重放只落一条、不重复写事件。前端离线队列自动生成 |
| 更新类请求体（PATCH /farm/plots/{id}、/farm/cycles/{id}、/farm/records/{id}、/farm/work-orders/{id}） | `expected_revision`（可选，≥1） | 乐观并发；与服务器版本不一致返回 `FARM_REVISION_CONFLICT`（409），details 带 `current_revision` 与 `current` 副本 |
| 删除类查询参数（DELETE 同上资源） | `expected_revision`（可选） | 同上 |
| 出参 | `revision` | 每次成功写入 +1（含工单回执/评论/升级），供下次更新回传 |

口径：

- 回执重放：同一 `client_op_id` 只写一条回执事件、只推进一次版本；重复提交返回当前工单。
- 前端「用我的覆盖」= 取服务器 `current_revision` 作为新的 `expected_revision` 再发一次，不做静默强制覆盖。
- 网络不可达的写操作存在浏览器 IndexedDB 队列里，联网后按创建顺序串行补发；服务端明确拒绝（4xx）的操作不会自动重试，需人工处理。

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
