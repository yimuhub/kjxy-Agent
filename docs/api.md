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
（默认）与 `builtin_sources`（内置源子集）。

v3.33 搜索可用性：`/search/status` 新增 `sources` 逐源诊断（code/名称/是否可用/原因）；
内置源改为 `brave_web`、`baidu`、`sogou`、`so360`、`bing_news`、`google_news`，
可选 `bing`（网页 RSS）与 `duckduckgo`；默认不再
指向不存在的本机 SearXNG（旧零配置会被自动升级）。`/agents/declarative` 创建与更新
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
| GET/POST | /yards/current/roles | roles:manage | 角色列表（含中文名/可编辑标记）/ 新建自定义角色 |
| PUT/DELETE | /yards/current/roles/{code} | roles:manage | 修改 / 删除角色（保护型权限需负责人二次确认） |
| GET | /yards/current/menu | 登录（小院成员） | 工作台视图默认配置（默认模式 + 角色默认 + 共享预设） |
| PUT | /yards/current/menu | menu:manage | 保存工作台视图默认配置 |
| GET | /yards/current/members/{user_id}/permissions | members:manage | 成员有效权限 = 角色权限 ∪ 逐人授权 |
| PUT | /yards/current/members/{user_id}/permissions | 负责人 | 逐人授权（新增保护型权限需二次确认） |
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

## 跨小院协作 A2A（v3.20）

平台开关 `A2A_ENABLED`（默认关）。开启后：

| 端点 | 说明 |
|---|---|
| `GET /.well-known/agent.json`（兼容 `/.well-known/agent-card.json`） | 公开 Agent Card：协议版本、RPC 地址、开放技能（`yard.summary`、`knowledge.search`）；不含任何小院数据。未开启时 404 `A2A_DISABLED` |
| `POST /api/v1/a2a/rpc` | 入站 JSON-RPC 2.0。请求头 `Authorization: Bearer <协作令牌>`（兼容 `X-A2A-Token`）；令牌决定小院归属 |
| `GET/POST/PATCH/DELETE /api/v1/settings/a2a/peers[/{id}]` | 协作方 CRUD（读 `settings:manage`，写 `settings:manage_protected`） |
| `POST /api/v1/settings/a2a/peers/{id}/probe` | 依次请求 `/.well-known/agent-card.json`、`/.well-known/agent.json`，校验并缓存 Agent Card |
| `POST /api/v1/settings/a2a/peers/{id}/tasks` | 向协作方发起任务（`message/send`，`configuration.blocking=true`） |
| `GET /api/v1/settings/a2a/tasks[/{id}]` | 入站/出站任务记录（可按 `direction`、`status` 过滤） |
| `GET/PATCH /api/v1/settings/a2a`、`POST /api/v1/settings/a2a/token` | 入站开关、令牌生成/轮换（明文只返回一次） |

JSON-RPC 方法：

- `message/send`（兼容 `tasks/send`）：`params.message` 至少含 `parts[].{kind:"text",text}` 与 `messageId`；`message.metadata.skill` 指定技能，缺省或未知返回 `-32602`。同一 `messageId` 重复提交返回同一任务。
- `tasks/get`：`params.id` 取任务；不存在返回 `-32001`。
- `tasks/cancel`：仅非终态可取消，已结束返回 `-32002`。

任务对象：`{kind:"task", id, contextId, status:{state,timestamp}, artifacts:[{parts:[{kind:"text",text}]}], metadata:{skill,direction,errorCode}}`。状态机 `submitted → working → completed | failed | canceled`。

出网口径：协作方地址在保存、探测与每次发任务前都过 `net_guard`；响应上限 1MB；协作方令牌 Fernet 加密、接口只回 `token_configured`。

## 版本握手与 Skill 自动升级（v3.21）

### 版本握手

`GET /api/v1/meta` 返回后端版本与数据库迁移头，前端用它提示「后端版本较旧」：

```json
{ "data": { "version": "3.25.1", "migration_head": "0052_tool_catalog" } }
```

- 升级顺序：`alembic upgrade head` → 重启后端 → `curl /api/v1/meta` 确认。
- 前端编译期带自身版本，与后端不一致时顶部横幅提示；405（`METHOD_NOT_ALLOWED`）文案改为「后端版本较旧或该接口未启用」，不再让用户摸不着头脑。

### 调用埋点与自动升级

| 端点 | 说明 |
|---|---|
| `GET /api/v1/skills/auto-upgrade/overview` | 列表页用：按 `skill_code` 返回开关、门槛、冷却与近 30 天调用量（一次分组查询） |
| `GET /api/v1/skills/{skill_id}/auto-upgrade` | 单技能配置、近 14 天统计与最近 10 次升级记录 |
| `PATCH /api/v1/skills/{skill_id}/auto-upgrade` | 开关与阈值（`enabled`、`min_calls` 1–1000、`cooldown_hours` 1–8760），需 `skills:manage` |
| `POST /api/v1/skills/{skill_id}/auto-upgrade/run` | 手动尝试一次升级（仍需已开启且有调用证据） |

口径：

- 埋点只记「哪个技能参与了哪次回答」：`yard_id / skill_code / source(plan|declarative) / session_id / outcome`，不读取、不存储对话正文。
- 升级证据只取主题级信号：近 14 天调用次数、去重会话标题（截断 40 字）、带文字或点踩的反馈片段（截断 120 字）、来源分布与当前定义。
- 到门槛且冷却结束才调模型；输出 `needs_update / reason / description / prompt / steps`。
- **只产待审核草稿**：新版本以 `status=pending_review`、`created_from=auto:upgrade` 落库，发布仍需人工审核；`needs_update=false` 记 `skipped`，模型不可用或解析失败记 `failed` 与原因。
- 自我总结任务（3.4）结束前会顺带处理本小院已到期的自动升级，单条失败不影响其余；审计 `skill_upgrade_run`、`skill_auto_upgrade_toggled`。

## 一句话业务（v3.22）

农业台账、作物诊断、告警与工单三个页面顶部的一句话条：先预览、再确认，两段式落库。

| 端点 | 说明 |
|---|---|
| `POST /api/v1/quick-actions/preview` | 只解析不落库；返回 `action`、结构化 `payload`、中文 `summary`、`missing`（缺字段提示）、`ready`、`source(rule\|llm)` 与 `fingerprint` |
| `POST /api/v1/quick-actions/confirm` | 带预览返回的 `payload` 与 `fingerprint` 确认落库；返回 `created` 与 `summary` |

请求体：

```json
// preview
{"module": "farm", "text": "新增地块 东一号田 水稻 12 亩，沙壤土"}
// confirm
{"module": "farm", "action": "plot.create", "payload": {"name": "东一号田", "crop": "水稻", "area_mu": 12}, "fingerprint": "<preview 返回值>"}
```

支持的模块与动作：

| module | action | 说明 |
|---|---|---|
| `farm` | `plot.create` / `record.create` | 新增地块、记一条农事（施肥/打药/灌溉/巡查/采收） |
| `diagnosis` | `diagnosis.create` | 一句话登记症状并出诊断 |
| `work_order` | `alert.convert` / `work_order.create` | 把未处理告警转工单、或直接开一张工单（可带负责人/到期日，落库后补更新） |

口径：

- **预览零副作用**：preview 不写库、不发通知、不建任务；只有 confirm 才走既有服务层（同样校验 `farm:write`、写审计 `quick_action_confirmed`）。
- **规则优先**：名称/数字+单位/日期/人名由确定性规则解析（农事单位不误取「亩」，相对日期如「今天/明天/后天」按请求里可选 `today` 解析，默认服务器当天）；规则缺字段时才调模型补齐，模型不可用或失败就返回 `missing` 让用户改用表单。
- **不假装成功**：`ready=false` 时 confirm 返回 422 `QUICK_ACTION_AMBIGUOUS`，前端给可读提示并可一键切回原表单预填。
- **指纹防篡改**：confirm 用 `HMAC.compare_digest` 比对 `(module, action, payload)` 的规范化 SHA-256；不一致返回 409 `QUICK_ACTION_STALE`，需重新预览。
- 一次只办一件事，不支持「顺便把别的也改了」。

## 设备数据智能分析（v3.23）

对设备读数做窗口统计、数据质量检查与规则发现，产出可复核的结论；critical 发现自动进入既有「告警 → 工单」链路。

| 端点 | 说明 |
|---|---|
| `POST /api/v1/devices/{device_id}/analyses` | 立即分析（管理员）。规则结论必出；同一天同一设备重复运行幂等返回既有结果（`created=false`） |
| `GET /api/v1/devices/{device_id}/analyses?limit=20` | 分析历史（时间倒序，成员可读） |
| `GET /api/v1/devices/insights?limit=10` | 概览卡：每台设备最近一条 watch/warning/critical 发现与计数 |

出参要点：

```json
{
  "analysis_id": "...",
  "status": "ok",              // ok | insufficient
  "level": "warning",          // info | watch | warning | critical
  "summary": "一号气象站 本轮发现 3 条：…",
  "findings": [
    {"code": "combo:irrigation", "level": "warning", "metric": "soil_moisture_20",
     "title": "土壤水分下降 11.0 个百分点且无有效降雨",
     "detail": "…从 35.0% 降到 24.0%，同期降雨累计 0.0 mm。",
     "evidence": [{"at": "...", "value": 35.0, "unit": "%"}],
     "suggestion": "建议核查墒情与灌溉计划，确认是否需要安排灌溉。", "rule": "combo"}
  ],
  "metrics": {"soil_moisture_20": {"mean": 29.5, "min": 24, "max": 35, "change": -11, "points": 12}},
  "quality": {"score": 92, "coverage": 0.5, "notes": ["…"]},
  "model": null, "tokens_used": 0, "trigger": "manual"
}
```

口径：

- **规则先行**：阈值（沿用设备告警规则的上下限与方向）、趋势（土壤水分/温度/水位/虫情窗口变化）、异常偏离（z-score ≥ 3）、传感器卡死（连续 6 点同值）、数据质量（覆盖率/越界/低电量）、农艺组合（高湿低温易病、土壤水分骤降且无雨需灌溉、虫情连续上升进入防治窗口）。每条发现都带具体数值与时间。
- **LLM 只做解释**：`explain_with_llm=true` 时把规则发现与统计摘要交给模型写「发生了什么/建议」，输出必须含数字，否则回落到规则摘要；每日 token 预算用尽即只出规则结论。
- **证据不足如实说**：窗口内点数/覆盖率不达标时 `status=insufficient`，不产出发现、不调模型、不生成告警；零读数时返回 422 `DEVICE_ANALYSIS_INSUFFICIENT_DATA`。
- **critical 自动告警**：阈值越限 20% 以上判 critical，写 `device_alerts`（同 rule+device 30 分钟去重）并推送，可在设备页一键开单；普通越限为 warning。
- **定时分析**：`yard_jobs` 新增 `device_analysis`（默认每天，可配 12 小时/每周），有启用设备的小院自动建任务；运行记录在 `yard_job_runs`（含每轮设备数、成功/证据不足/失败与失败原因）。
- **只读红线**：分析只读读数，不写设备控制指令；不做因果断言；用药类建议仍需作物诊断与专家复核。

## 权限与千人千面（v3.24）

### 有效权限下发（唯一事实源）

`GET /auth/me` 除用户/角色/小院外，新增：

```json
{
  "data": {
    "user": {"user_id": "…"},
    "roles": ["operator"],
    "yard_id": "…",
    "is_owner": false,
    "permissions": ["chat:use", "devices:read", "farm:read", "knowledge:read", "tasks:read"]
  }
}
```

- `permissions` = 数据库角色权限 ∪ 负责人逐人授权；负责人与平台管理员恒为全集。
- 前端菜单、路由、按钮一律以该集合判定：无权限的功能既不在侧栏出现，直接输入 URL 也会落到「没有访问权限」页。

### 角色与权限矩阵

- 权限点由 33 项扩到 45 项，新增：`chat:use`、`agents:manage`、`devices:read/write`、`policies:read/write`、`evolution:manage`、`tools:manage`、`llm:manage`、`mcp:manage`、`menu:manage`、`demo:manage`。
- 全部权限点带中文名（`GET /yards/current/settings|roles` 返回 `permission_labels`），矩阵不再出现英文编码。
- 角色列表每项返回 `can_edit`（后端按层级计算）：负责人可维护除负责人外的所有角色；管理员可维护专家/操作员/普通成员与自定义角色，不能碰管理员角色与保护型权限。
- 保护型权限（`settings:manage_protected`、`insights:sensitive`、`finance:read`、`skills:publish_any`、`members:manage_admins`、`owner:transfer`）的写入必须由负责人带 `confirm_protected: true`；未确认返回 400 `PROTECTED_CONFIRM_REQUIRED`，管理员尝试配置返回 403 `ROLE_EDIT_FORBIDDEN`。

### 逐人授权

- `GET /yards/current/members/{user_id}/permissions` 返回 `role_permissions / grants / effective_permissions` 与分组、中文名、可授权范围。
- `PUT` 仅负责人可调，授权是角色之上的增量、不削弱角色；`owner:transfer`、`members:manage_admins` 不可逐人下放；新增保护型权限同样需要 `confirm_protected`。变更写入审计 `member_permissions_updated`（含新增/移除明细）。

### 工作台视图（原「导航菜单 DIY」）

- 设置项 `menu`：`default_mode`、`role_modes`（按角色的默认密度）、`profiles`（共享预设）、`allow_member_customize`；模式为 `minimal | standard | full`。
- `GET /yards/current/menu` 全员可读（登录后即可取默认视图），`PUT` 需 `menu:manage`（负责人/管理员），非法预设返回 400 `MENU_PROFILE_INVALID`。
- 优先级：成员本地自定义布局 > 角色默认 > 全局默认；侧栏功能池展示全系统所有用户组的功能，是否真正可见仍由权限裁决。

## 模型中心与工具目录（v3.25）

模型配置从「系统设置」迁到「小院管理 · 模型中心」（`/model-center`，需 `llm:manage`）：供应商、全局默认与兜底、按能力绑定、智能体模型集中在同一页；「可用工具」升级为可查看详情、可手动/一句话添加的目录。

v3.26 起「按智能体绑定」页签升级为「智能体模型」：一次列出平台助手与已发布自建助手，显示跟随来源/主用/兜底/可用性，并可试跑；同一页下方配置 `image_generation` / `video_generation` 两个视觉能力档位。

### 解析链（谁先谁后）

一次调用按下列顺序取第一个可用供应商，同层最多 3 档兜底（主用 + 2 档）：

1. 助手编辑页里指定的供应商（旧设置；在「智能体模型」保存一次即被接管并清空）
2. 按智能体绑定（`scope_type=agent`，`scope_key` = 助手 `agent_code`）
3. 助手专属能力绑定（`scope_type=agent_purpose`，`scope_key` = `助手编码::用途码`，v3.37）
4. 按能力绑定（`scope_type=purpose`，`scope_key` = 用途码）
5. 小院/平台全局默认（`scope_type=global`）
6. 小院当前生效供应商 → 7. 平台优先级 → 8. `settings` 环境变量兜底

供应商连续失败 5 次自动置为禁用并从候选链剔除，调用成功即清零计数。

| 接口 | 说明 |
|---|---|
| `GET /llm/capabilities` | 24 条能力（用途码/中文名/分组/说明，含 `image_generation`/`video_generation`）+ 三层绑定类型 + 解析层顺序 + `max_fallback` |
| `GET /llm/bindings?scope_type&scope_key` | 本小院可见绑定（含平台级），返回供应商名与模型名 |
| `PUT /llm/bindings` | **整层替换**：`{scope_type, scope_key, entries[]}`，顺序即兜底顺序；`scope_type` 取 `global/purpose/agent/agent_purpose`；`agent` 时同步清空该自建助手的旧 `supplier_id/model_name`（统一定义接管）；视觉能力（`image_generation`/`video_generation` 及其助手专属层）只接受图像/视频协议或对应模态的供应商，否则 400 `INVALID_REQUEST`；超过 3 条 400 `BINDING_TOO_MANY`，平台级仅平台管理员可改 403 `BINDING_READONLY` |
| `DELETE /llm/bindings/{id}` | 删单条绑定，不存在 404 `BINDING_NOT_FOUND` |
| `GET /llm/bindings/agents` | 智能体模型聚合：全部助手（平台 + 已发布自建）× 当前策略（跟随/指定/旧设置）× 绑定与解析结果，另含图像/视频两档默认（v3.26） |
| `GET /llm/bindings/resolution?purpose&agent_code` | 「这次会用谁」：逐档给出候选与不可用原因 |
| `POST /llm/bindings/test` | 按层真实试跑一条最小请求（会消耗一次调用），失败如实回 `ok=false` + 原因 |

### 工具目录

| 接口 | 说明 |
|---|---|
| `GET /tools/catalog` | 三源合并（内置/本小院自定义/MCP 快照）+ 分组计数 + 风险与来源中文标签 |
| `GET /tools/{code}` | 详情：能力说明、参数表、调用示例、执行方式、最近调用（仅 `tool_executed` 审计） |
| `POST /tools/generate` | 一句话 → 规格草稿，**不落库**；模型不可用或两次尝试（含一轮修复）后仍不合规时，返回 `source="fallback"` 的保守草稿 |
| `POST /tools` | 新增自定义工具（`enabled=true` 才启用；`prompt` 需提示词、`http` 需合法 URL），重名 409 `DUPLICATE_TOOL_CODE` |
| `PUT /tools/{code}` | 编辑自定义工具；鉴权头明文进、Fernet 密文存，未提交头则沿用原密文 |
| `POST /tools/{code}/enable` / `disable` | 启用/停用；内置与 `mcp__` 前缀只读，403 `TOOL_READONLY` |
| `DELETE /tools/{code}` | 删除自定义工具，内置 403 `TOOL_READONLY` |

自定义工具执行方式二选一：`prompt`（复用小院模型，用途码 `tool_run`）与 `http`（出网受 SSRF 白名单管控、响应 200KB 上限、非 GET 一律升级为 `external_side_effect` 走审批闸）。非法定义 400 `TOOL_SPEC_INVALID`，外部接口异常 502/504 `TOOL_ERROR`。

## 增长技能与海报出图（v3.26）

内置增长技能 12 项：营销 6（品牌定位/卖点详情页/定价毛利/渠道节奏/选题排期/复购老客）、活动 4（活动策划/邀请通知/直播脚本/活动复盘）、海报 2（提示词工程/出图）。全部按 `SkillSpec` 带输入表、≥2 条评测用例与护栏；新小院注册即铺（12 农技 + 12 增长）。所有对外文案出门前可调用只读工具 `compliance.check_copy`（离线 236 词库、按句例外降级、只给建议不改语义）。

图像/视频生成一律由模型供应商供应（不自建出图服务）：供应商类型新增 `openai_images`（兼容 `POST {base_url}/images/generations`）、`dashscope_image`（百炼原生异步任务）、`openai_video` / `dashscope_video`（v3.37 起为真实通道，另有内置工具 `video.generate`）。它们都是 `suppliers.provider_type` 的字符串取值，**不需要迁移**。

v3.37 起通道判别以**模型模态**为兜底：通用 OpenAI 兼容供应商里 `modality=image/video` 的模型，即使协议仍标 `openai_chat`，也能被图像/视频通道接管；图像生成按「完整参数 → 去掉 n/response_format → 再去掉 size」逐级降级重试，错误信息带上游摘要；视频以「任务受理即连通」判定，轮询超时仍返回 `task_id`。

| 接口 | 说明 |
|---|---|
| `POST /growth/posters` | 出海报：`{prompt, size?, n?, title?, subtitle?, footer?, theme?}`；优先图像供应商，未配置或失败时降级本地 SVG 模板，`fallback=true` 且 `notice` 说明原因 |
| `GET /growth/posters/{key}?sig=` | 取图：随机键 + HMAC 签名的 capability URL（`<img>` 带不了鉴权头），只读 `posters/` 子目录，签名不符 404 |

内置工具 `image.generate`（`write_reversible`）：参数同上，返回 `assets[].url` 与 `provider/model/fallback`；对话里可按 `![标题](url)` 直接显示。前端 Markdown 渲染只放行 http(s) 与同源 `/api/v1/growth/posters/...?sig=<32位hex>` 图片地址。

## 多模型供应商与官方供应商（v3.36）

一个供应商不再只对应一个模型：`suppliers` 描述渠道，`supplier_models` 按行承载同渠道下的文本/图像/视频/多模态/Embedding/ASR 等模型。绑定优先写 `supplier_model_id`，解析链再依次回落模型名、供应商默认模型与旧 `model_id`，旧配置无需迁移即可继续工作。

### 模型导入与试跑

| 接口 | 说明 |
|---|---|
| `GET /llm/providers/{supplier_id}/models` | 列出渠道下全部模型（模态、协议、能力、上下文、单价、最近测试结果） |
| `POST /llm/providers/{supplier_id}/models` | 手工新增模型（`model_key` 供应商内唯一） |
| `PATCH /llm/providers/{supplier_id}/models/{model_id}` | 编辑模型；改默认模型请走供应商 PATCH 的 `default_model_id` |
| `DELETE /llm/providers/{supplier_id}/models/{model_id}` | 删除模型（默认模型不可直接删除） |
| `POST /llm/providers/{supplier_id}/models/import?dry_run=1` | 批量导入：`content` + `format`（json/yaml/csv，可自动识别）；`dry_run=1` 只返回新增/更新/跳过预览 |
| `POST /llm/providers/{supplier_id}/models/import-url?dry_run=1` | 按 URL 拉取目录导入：仅 http(s)、2MB 上限、每跳做 SSRF 校验 |
| `POST /llm/providers/import?dry_run=1` | 「新供应商 + 多模型」一次导入 |
| `POST /llm/providers/{supplier_id}/models/{model_id}/test` | 单模型真实试跑（按协议发最小请求，返回延迟与错误分类） |
| `POST /llm/providers/{supplier_id}/models/batch-test` | 批量试跑，逐条返回 `ok/latency_ms/error` |
| `GET /llm/provider-templates` | 供应商模板（含各模态协议的字段提示与示例） |

导入格式统一为模型对象数组，核心字段：`model_key`、`upstream_model_id`、`display_name`、`modality`、`protocol`、`input_modalities`、`output_modalities`、`capabilities`、`context_window`、`max_output_tokens`、`endpoint_path`、`pricing`、`default_params`、`limits`。未填 `modality` 时按模型名推断（如 `-vision/-vl` → multimodal、`embedding` → embedding、`whisper/asr` → asr），未知值拒绝并回报行号。

### 官方 / 私有 API 供应商

`relay_channels` 描述一个 OpenAI 兼容供应商通道（`kind=official|private`）：平台可维护全局官方通道，小院可维护自己的私有通道；密钥 Fernet 加密，出参只给掩码。

| 接口 | 说明 |
|---|---|
| `GET /llm/relay/channels` | 可见通道（平台官方 + 本小院私有） |
| `POST /llm/relay/channels` | 新建：`{name, kind, base_url, api_key?, catalog_path?, webhook_secret?}` |
| `PATCH /llm/relay/channels/{id}` / `DELETE` | 修改/删除；平台级通道仅平台管理员可写 |
| `POST /llm/relay/channels/{id}/sync` | 拉取 `catalog_path` 的模型目录，同步为 `source=relay` 的供应商与模型；返回新增/更新/跳过计数 |
| `GET /llm/relay/channels/{id}/models` | 查看该官方供应商当前目录（与供应商模型同构） |
| `POST /llm/providers/{id}/models/detect-modality` | 自动识别模型模态：模型名 → 上游目录 → unknown；返回 `source` 供前端决定是否手选 |

### 官方模型通道（试用 / 购买预留）

官方通道默认关闭：没有启用中的官方 `relay_channels` 时，除 `status` 外的接口返回 503 `OFFICIAL_CHANNEL_DISABLED`。金额只信服务端套餐，请求体只选套餐与模型；试用每小院每模型一次。

| 接口 | 说明 |
|---|---|
| `GET /llm/official/status` | 开通状态、官方通道数量、可用模型数与当前权益摘要（未开通也返回，前端据此渲染未开通态） |
| `GET /llm/official/models` | 当前小院有权益的官方模型；无权益为空 |
| `GET /llm/official/plans` | 可购买/试用套餐（单价、试用 token/天数、计费单位） |
| `POST /llm/official/trials` | 领取试用：`{model_key, plan_code?}`；重复领取 409 |
| `GET /llm/official/entitlements` | 本小院权益（来源 trial/order、额度总量/已用、有效期） |
| `POST /llm/official/orders` | 下单：`{plan_code, model_key, idempotency_key}`；返回订单与支付链接 |
| `GET /llm/official/orders/{id}` | 订单状态 |
| `POST /llm/official/webhooks/payment` | 支付回调：HMAC-SHA256 校验 `timestamp.raw_body`，±300 秒窗口，`event_id` 幂等；`paid` 激活权益、`refund` 撤销 |

### 官方技能 / 插件库

官方库与私有库共用一套协议：发布者用 Ed25519 私钥签清单，客户端用公钥验签，再按 `.kjxpack` 的 SHA-256 校验包体。插件包只允许声明式内容，不执行包内脚本；撤销只改状态，不删除已装数据。

| 接口 | 说明 |
|---|---|
| `GET /library/sources` | 可见源（平台官方 + 本小院私有），含状态、ETag、序号、最近同步 |
| `POST /library/sources` | 新建源：`{name, kind, base_url, public_key_b64, channel?}`；官方源仅平台管理员 |
| `PATCH` / `DELETE /library/sources/{id}` | 更新/删除；仍有安装台账时拒绝删除 |
| `POST /library/sources/{id}/sync` | 拉清单：ETag / 单调 `sequence` 条件跳过，验签后 upsert 并镜像市场条目；失败落 `library_sync_runs` |
| `GET /library/items?source_id&item_type&keyword` | 条目列表：`skill/plugin/agent_template/deck_theme/doc_template`，同键多版本倒序 |
| `POST /library/items/{id}/install` | 安装：下载、校验 SHA-256、安全解包；技能写版本表，插件只登记 |
| `GET /library/installs` | 本小院安装台账（已装版本、上一版本、固定/自动更新） |
| `POST /library/installs/{id}/update` / `rollback` | 升级 / 离线回滚到上一版本 |
| `POST /library/installs/{id}/pin` / `auto-update` | 固定版本 / 切换自动更新 |
| `GET /library/status` | 源与安装汇总、最近同步运行，前端设置页据此显示 |

发布工具在 `scripts/library/`：`build.py` 打包 `.kjxpack`、`sign.py` 生成/签名清单、`publish.py` 生成 ETag/序号、`verify.py` 客户端侧校验；使用说明见 `scripts/library/README.md`。

## 办公与正式写作（v3.36）

- **PPT 设计系统**：对话里生成 `.pptx` 时先产出严格 `DeckPlan`（5–30 页、15 种版式、8 套主题），再经 23 项结构质检（溢出/空页/重复标题/图表与表格约束等）自动修复后渲染；支持真实图表、表格、时间线、流程图、演讲者备注、页码与东亚字体。可选 `soffice` 视觉质检在缺少 LibreOffice 时跳过并说明。
- **PPT 专项技能**：`office_ppt_storyline`（叙事）、`office_ppt_design`（视觉）、`office_ppt_chart`（图表）、`office_ppt_review`（质检）、`office_ppt_template`（模板替换），与 `office_ppt_deck` 组合使用。
- **正式写作技能 28 项**：公文 10（通知/请示/报告/函/纪要/工作计划/总结/批复/讲话稿/简报）、申报 7（项目申报/科技计划/补贴/荣誉/中期/验收/预算）、招投标 7（招标文件/投标文件/响应矩阵/技术标/商务标/风险审查/澄清答复）、通用 4（长篇正式文/高管简报/政策解读/会议包），新小院注册即铺并自动建议绑定。
- **Word 公文版式**：`DocModel.doc_style=official_cn|grant|tender|brief`，A4、版心与页边距、仿宋/黑体/楷体东亚字体、标题分级、页码页脚、封面与目录；由绑定技能或需求意图自动选择。
- **渲染健壮性（v3.38）**：模型输出的 `DeckPlan` 先做形状修复（`None/字符串/对象` 归一为列表或文本，不改版式枚举与必填标题）再严格校验；`python-pptx`/`python-docx` 模板资源随 sidecar 打包（`build_sidecar.sh --collect-all pptx/docx`）；渲染组件缺失抛 `DOC_RENDER_FAILED`（503），对话链路降级为可下载的 Markdown 大纲而不是「服务暂时不可用」。

## 视觉能力与助手专属模型（v3.37）

### 供应商 → 模型级联选择

模型中心的所有选模型入口（全局默认、按能力、按助手、助手专属能力、视觉能力、助手编辑页）统一为一个级联下拉：按供应商分组列模型，只列**已启用供应商 + 已启用且模态匹配的模型**；允许只绑供应商时提供「供应商 · 使用默认模型」。写入优先 `supplier_model_id`，旧数据（只有 `model_name`/供应商默认模型）继续可解析。

| 接口 / 字段 | 说明 |
|---|---|
| `GET /llm/bindings/agents` | 每行新增 `capabilities[]`（`purpose/label/bindings`），展示该助手的专属图像/视频绑定 |
| `GET /llm/bindings?scope_type=agent_purpose` | 读取助手专属能力绑定（`scope_key=助手编码::用途码`） |
| `PUT /llm/bindings` + `scope_type=agent_purpose` | 写入单个助手的专属能力模型；`::` 分隔校验失败 400 |
| `POST /llm/providers/{id}/models/{model_id}/test` | 图像/视频模型按模态走真实生成最小请求；前端超时 180 秒（批量 600 秒） |

### 图像生成

- 通道判别：模型 `protocol` → 供应商 `provider_type` → 模型 `modality=image` 依次判定；通用兼容供应商里模态为图像的模型可直接用于出图。
- 请求降级：400/422 时依次去掉 `n`/`response_format`、再去掉 `size` 重试；返回解析同时支持 `data[]` 与 `output.results[]`。
- 百炼供应商 `base_url` 自动去掉 `/compatible-mode/v1` 等后缀，避免拼出重复路径；错误信息带上游摘要（截断、不含密钥）。

### 视频生成

- `openai_video`：优先 `endpoint_path`，否则尝试 `POST {base_url}/videos`，失败再试 `/video/generations`；轮询任务状态，完成后取 `url/video_url/output_url`。
- `dashscope_video`：百炼原生异步任务（`X-DashScope-Async` + `/api/v1/tasks/{id}`），成功取 `output.video_url`。
- **连通判定 = 任务受理**：供应商返回 `task_id` 即认为通道可用，轮询超时也返回 `task_id` 与 `accepted=true`，不把「还在生成」误报为失败。
- 内置工具 `video.generate`（`external_side_effect`，需审批；超时 240 秒，默认最多等待 120 秒）按助手编码解析视频供应商，返回 `{ok, accepted, url, task_id, model, provider, latency_ms, message}`。

## 指挥官编排（v3.38 起，v3.40 自主补救）

对话里的「指挥官」是一个主 Agent：理解自然语言目标 → 拆成带依赖的子任务 → 从本小院已发布助手里选兵（找不到合适助手时自动创建，默认草稿态）→ 受控并发执行 → 汇总成一条回答。会话可单独选择执行模式，普通对话零变化。

### 设置（`yard_settings.key="commander"`）

`GET /yards/current/settings` 返回完整对象；`PUT /yards/current/settings/commander` 部分更新（未知字段 400，类型严格）。权限点 `settings:manage`。

| 字段 | 默认 | 说明 |
|---|---|---|
| `enabled` | `true` | 关闭后请求 commander 一律降级 standard，运行入口 403 `COMMANDER_DISABLED` |
| `display_name` / `emoji` | `指挥官` / `🧭` | 自定义展示名与图标 |
| `default_mode` | `false` | 新会话默认进入指挥官模式（默认关，小问题不必付多 Agent 成本） |
| `max_parallel` | `3` | 同时执行子任务数上限（1–10，前端给 3/5/8/10/自定义） |
| `fallback_parallel` | `2` | 上游不支持高并发时降级到的并发数（最低 1） |
| `max_subtasks` | `6` | 单次计划子任务上限（2–12） |
| `subtask_timeout_seconds` | `180` | 单个子任务超时 |
| `run_timeout_seconds` | `600` | 整轮运行总超时 |
| `preview_plan` | `false` | 默认计划生成即执行；开启后先出计划卡等确认 |
| `clarify_enabled` | `false` | 默认先按最合理假设开跑；开启后目标过于笼统（缺对象/交付物/范围）时先问 1–2 个问题。会话为「完全控制」时始终不问 |
| `auto_create_agent` | `true` | 无匹配助手时自动创建（**始终草稿态**，ADR-0005） |
| `daily_agent_limit` | `10` | 每小院每天自动创建助手上限（1–50），超出后回退最接近的已有助手 |
| `auto_remediate` | `true` | 子任务失败后自主补救：重试 → 换助手 → 新建助手 → 指挥官带工具亲自执行，全程不向用户提问 |
| `remediate_rounds` | `5` | 单个子任务最多自主尝试轮数（1–10）；轮尽才报告能力缺口与原因 |
| `auto_publish_agent` | `false` | 自动创建的助手是否直接发布；开启属受控动作并写审计 |
| `critic_enabled` | `false` | 汇总前额外质检（预留） |
| `token_budget` | `120000` | 单轮运行 token 预算 |
| `supplier_id` / `supplier_model_id` | `null` | 规划/汇总模型绑定；空 = 跟随模型中心全局默认 |

### 会话执行模式

| 位置 | 字段 | 说明 |
|---|---|---|
| `POST /chat/sessions` | `execution_mode: standard\|commander` | 不带则按 `default_mode` 决定 |
| `PATCH /chat/sessions/{id}` | `execution_mode` | 手动切换；指挥官被关闭时服务端降级 `standard` 并在响应中返回真实值 |
| `POST /chat/sessions/{id}/messages` | `execution_mode` | 随本轮消息指定；落库到会话 |
| 会话对象 | `execution_mode` | 列表与详情均返回 |
| 消息路由 | `routed_by="commander"`、`choice.kind="commander"` | 历史回放可辨认 |

群聊 `policy` 优先：同一请求带 `policy` 仍走群聊编排，指挥官不介入。锁定助手/班组会话（`target_code`）也不进指挥官。

### 流式帧（`POST /chat/sessions/{id}/messages/stream`）

在既有 `chat-init / step / message / card / error / chat-end` 之外新增三帧：

| 帧 | 数据 | 时机 |
|---|---|---|
| `commander-plan` | `{run_id, goal, subtasks[], max_parallel, effective_parallel, degraded, plan_fallback}` | 计划生成后（预览模式下即这一轮的最终卡） |
| `subtask-status` | 单个子任务 `{id, seq, title, agent_name, status, attempts, remediation[], tokens, output_excerpt, error_code, artifacts}` | 子任务每次状态变化（含换助手/建助手/自执行的补救） |
| `commander-summary` | `{run_id, answer, artifacts[], failed[], tokens_total, degraded}` | 全部子任务收口后，随后仍有正式 `message` 帧 |

消息载荷类型：`commander_plan`（计划卡，含「开始执行」）与 `commander_summary`（汇总卡，含产物与失败清单）。

### 运行管理接口

| 接口 | 说明 |
|---|---|
| `POST /commander/preview` | 只规划不执行：`{content, session_id, context?}` → `{run, clarify[]}`；落 queued 运行供预览/编辑 |
| `GET /commander/runs/{run_id}` | 运行详情 + 子任务列表（越权/不存在 404 `COMMANDER_RUN_NOT_FOUND`） |
| `PUT /commander/runs/{run_id}/plan` | 计划卡编辑（仅 queued）：整表提交子任务 `{title,instruction,expected_output?,agent_id?,depends_on?[]}`，返回重排后的 run；非 queued 409 `COMMANDER_RUN_STATE`，助手不可见/不可用 400 `INVALID_REQUEST` |
| `POST /commander/runs/{run_id}/execute` | 启动排队中的运行（202，后台执行并把汇总写成一条助手消息） |
| `POST /commander/runs/{run_id}/retry` | 重置失败/跳过的子任务为 pending，运行回到 queued |
| `POST /commander/runs/{run_id}/cancel` | 取消：pending → skipped，已完成保留 |
| `GET /commander/stats?days=30` | 运行指标：运行/子任务成功与失败数、计划成功率与 fallback 率、平均并发、降级次数、自动创建助手数、P50/P90/最大耗时 |

计划编辑语义：`depends_on` 按**提交数组的新位置（1-based）**解释，只保留指向更早位置的项；删除即从数组移除，顺序即新执行顺序。`agent_id=null` 表示「指挥官代执行」；可选助手须为已发布且当前用户可见，或本轮指挥官生成的草稿。

运行详情/子任务字段新增：`approval_mode`（创建时快照的会话授权模式）与 `remediation`（自主补救轨迹 `[{round, action, agent_name, code, message}]`，`action` 取 `retry|alt_agent|new_agent|self`）。

### 自主补救（v3.40）

- 子任务失败后不向用户提问，按顺序自救：原执行者重试（仅瞬时错误）→ 换用关键词相关的其他已发布助手 → 新建专用助手（受 `auto_create_agent` 与每日上限约束）→ 指挥官用临时执行器（绑定全部已注册工具）亲自完成；换人/建人都不可用时，剩余轮数继续交给指挥官亲自尝试。
- 总尝试次数由 `remediate_rounds` 限制（默认 5）；轮尽仍失败时子任务记为 `COMMANDER_CAPABILITY_GAP`，`output_excerpt` 用自然语言说明「已尝试几轮、用了哪些办法、失败原因、这是能力缺口而不是缺信息」，汇总卡与失败清单直接展示该说明；全部失败且均为能力缺口时运行级 `error_code` 也是该值。
- 风险闸拦截 `TOOL_RISK_BLOCKED` 时立即停止补救（换人无意义），提示把会话权限切到「完全控制」；配额/鉴权类硬错误同样不再放大成本。
- **完全控制继承**：运行创建时把会话 `approval_mode` 快照到 `commander_runs.approval_mode`，执行器按快照重建上下文；`full` 会话里子任务的高风险工具直接执行、不再逐次审批（仅对具备升级角色的成员生效，且每次写 `tool_risk_auto_approved` 审计），`clarify_enabled` 与 `preview_plan` 也被忽略——完全控制 = 不问、不等确认。
- **成品反馈迭代**：指挥官重规划时自动带上最近 6 条会话消息（总长 ≤2500 字，含上一版产物标题），「第 3 页排版不好」这类反馈会被理解为对上一版成品的修改，而不是新任务。

### 文件交付（v3.41）

- 用户明确要文件（PPT/幻灯片/演示文稿、Word/docx、Excel/xlsx/电子表格、PDF、Markdown）时，计划必须包含交付子任务；模型规划失败、澄清关闭或计划未覆盖时，服务端自动追加依赖全部前序任务的「生成 X 交付物」（达到 `max_subtasks` 时并入最后一个任务），计划编辑删除交付任务也会自动补回。
- 交付子任务固定由指挥官确定性执行：直接调用对话文档链路（PPT 走 DeckPlan 版式渲染）生成 `.pptx/.docx/.xlsx/.pdf` 并落为会话附件，不依赖模型是否会在工具循环里调用工具；渲染结果格式与要求不一致时按 `COMMANDER_DELIVERABLE_MISSING` 失败并进入自主补救。
- 交付子任务不参与选兵（不会硬绑没有文档能力的助手，也不消耗自动创建额度）；运行时产物写入子任务 `artifact_json`，汇总卡可直接预览/下载。
- 新增内置工具 `doc.generate`（入参 `request`、可选 `format`、`context`），声明式 Agent 与指挥官自执行器均可调用；工具产出的产物随 `AgentResult.output.artifacts` 上抛。

## 系统日志（v3.41）

负责人排障入口：设置页「系统日志」。应用日志写入 `<data_dir>/logs/app.log`（10MB × 5 轮转，含 uvicorn 访问/错误日志）；桌面版 sidecar 的 `engine.log` 作为第二来源。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | /system/logs | 查询尾部日志；`source=app\|engine`、`level=DEBUG\|INFO\|WARNING\|ERROR\|CRITICAL`、`q` 关键词、`limit`（20–1000）、`offset`；返回 `items[]`（新→旧）与 `meta{path,exists,size_bytes,mtime,scanned_bytes,truncated,matched,sources[]}` |
| GET | /system/logs/download | 下载脱敏后的尾部日志（最多 5MB），文件名带时间戳 |

- 权限：仅 `platform_admin` 或当前小院负责人（`yard_owner`）；其他角色 403 `FORBIDDEN`。日志是进程级资源，多小院部署不向普通管理员开放。
- 脱敏：输出前统一替换 `api_key/token/password/Authorization/Bearer`、`sk-*`、GitHub token；下载同样脱敏。
- 读取上限：单次扫描最近 4MB，`meta.truncated=true` 时提示下载查看更早内容。
- 指挥官关键日志：计划 fallback 原因、交付任务补齐、选兵决策、子任务开始/完成/失败（耗时、产物数）、自主补救动作、文档生成文件与大小。

### 选兵与澄清

- 选兵顺序：计划里显式 `agent_code` → 规则打分（关键词 + 近 30 天成功率/频次 + 当前负载）→ 前 8 个候选交小模型重排（用途码 `commander_match`，置信度 <0.6 视为「无合适」）→ 近 90 天指挥官创建的同意图草稿复用 → 自动创建（受开关与每日上限约束）→ 最接近候选/指挥官代执行。
- 澄清：规划模型判定信息不足时只回 `clarify`（≤2 个问题），不建子任务；消息以普通助手文本返回，用户回答后重新规划。该运行状态为 `cancelled`、`error_code=COMMANDER_NEEDS_INPUT`，不计入运行指标。

### 审计与观测

- 审计事件：`commander_plan_created`、`commander_plan_edited`、`commander_run_started`、`commander_run_finished`、`commander_run_cancelled`、`commander_degraded`、`commander_subtask_remediated`、`commander_agent_created`、`commander_agent_published`、`commander_subtask_retried`。
- 指标来自 `GET /commander/stats`（只读聚合，不新增表）；设置页「指挥官」顶部展示近 30 天运行概况。

### 权限与安全

- 调度权不等于越权：每个子任务都以原发起人的角色快照重建上下文，助手可见性/知识库边界与普通对话一致；工具授权按运行创建时的会话模式快照执行——`ask` 仍需人工批准，`auto` 复用本会话已批参数指纹，`full`（完全控制，仅升级角色）直接执行并逐次写审计；指挥官调度不会额外放大权限。
- 自动创建的助手默认**草稿**，本轮可直接使用；是否发布由 `auto_publish_agent` 决定（默认关），开启与自动发布均写审计 `commander_auto_publish_enabled` / `commander_agent_published`。
- 正文、密钥、上游错误原文不写入计划/子任务存证；子任务只存截断后的 `output_excerpt` 与产物引用。

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
