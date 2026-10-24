# 变更日志（CHANGELOG）

本项目遵循《工程开发规范 V3.0》；版本号遵循语义化版本。

## [1.6.0] - 2026-10-24

桌面端全平台可用性升级 + 构建修复。

### Windows 兼容 Win7+（WebView2 固定版）
- `webviewInstallMode` 从 `embedBootstrapper` 改为 `fixedRuntime`（WebView2 10554）：
  安装包内含固定版 runtime，对 Win7/8/10/11 全兼容，无需用户联网装系统级 WebView2。
  10554 是微软最后支持 Win7 的 runtime，同时满足 Tauri 2 最低要求 10414。
- NSIS 安装包体积略增（~10MB 固定 runtime），换来安装后零网络依赖。

### 自动升级链路验证
- GitHub Release run 40075755769 失败原因：`TAURI_SIGNING_PRIVATE_KEY_PASSWORD`
  GitHub Secret 与本地密钥实际使用的密码不匹配（wrong password）。
  本地密钥在 `~/Documents/Hermes/kjxy-work/tauri_updater_private_key`，需用正确
  密码通过 CLI 重新确认，再同步到 GitHub Secrets。
  安装包构建本身成功（.app/.dmg/.tar.gz 全产出），只是签名步骤挂了。

### 桌面版零配置开箱即用确认
- sidecar 打包已含全部 Python 引擎依赖（`--collect-all app/alembic/pydantic/sqlalchemy/cryptography`）；
- 首启自动：创建数据目录 → alembic upgrade head → seed 演示租户 → 生成随机口令写
  `desktop-login.txt` → 启动 uvicorn 127.0.0.1:187xx → 窗口自动跳转登录页；
- splash 明确告知登录文件路径，用户只需配置模型供应商 API key，其余全自动化。

### Linux 包格式收窄
- `tauri.conf.json` bundle.linux.fileformat 显式 `["deb","appimage"]`：
  避开 rpm（Ubuntu runner 无 rpmbuild），安装走 deb 或 AppImage 免安装方式。

### macOS dmg 确认
- macOS bundle targets 含 `dmg`（aarch64-apple-darwin），安装后无需额外配置。

### 版本号对齐
- tauri.conf.json / Cargo.toml / pyproject.toml / client/desktop/package.json
  四处统一升至 1.6.0（updater 按 tauri.conf.json version 比较新旧）。


## [1.5.0] - 2026-10-24

IM 机器人网关第一波（企微/钉钉直连）+ Release 流水线修复。

### IM 机器人网关（Wave C1+C2）
- 新模块 `bots`：企业微信智能机器人（aibot 长连接协议：订阅/回调/流式回复/
  主动推送/30s心跳/被踢单连接保护）、钉钉 Stream（gateway open→WS→ping-pong/ACK）、
  飞书（lark-oapi 可选依赖：未安装可存配置，连接时明确报缺失原因）。
- 入站编排 gateway：msgId 幂等去重 → 陌生会话批准闸门（固定欢迎语）→ @提及剥离 →
  `/use 名称 问题` 会话内切换 → 6轮上下文滑窗 → supervisor 智能路由/定向执行
  （supervisor 新增 ask_directed 纯增量入口）→ 分段回复 → **消息账本只存
  sha12 摘要+长度**；30 天无活动自动清上下文明文（prune）。
- manager：进程内管家 lifecycle 自动拉起； 多实例 Redis 选主（SET NX EX +
  续租，无 redis 单机退化）；指数退避重连 1s→60s；连续失败 5 次自动停用（熔断）
  并写审计，人工重新启用清零。
- `/bots` 管理 API（租户管理员）：CRUD（凭据 Fernet 加密+掩码回显）、测试连通、
  状态、会话批准/吊销、消息账本；迁移 0019（可逆）；新用户可见性=默认需批准。
- 前端「IM 机器人」管理页：机器人卡列表（状态灯/启停/测试/编辑/删除）、对话框
  （平台/凭据/绑定方式/需批准）、会话审批抽屉（批准/吊销/最近消息摘要）。
- 测试：新增 31 个全离线用例（假连接回放协议帧/MockTransport/假 redis/熔断/跨租户隔离）；后端全量 650 passed。

### 修复
- Release 流水线三平台同挂：Windows runner 默认 PowerShell 下 `mkdir -p` 在目录
  已存在时报错——补 `shell: bash`（release.yml/client.yml 各两处）；桌面壳
  tauri 版本号对齐 tag（updater 版本比较依据）。

## [1.4.0] - 2026-10-23

多形态发行第一波：桌面自动升级通道 + 手机扫码接入 + 生产部署三件套。

### 桌面客户端：自动升级（GitHub Releases 通道）
- updater 密钥对（minisign）本机生成：私钥仓外 600 权限，公开公钥入配置；
  更新源 `releases/latest/download/latest.json`，`createUpdaterArtifacts` 三平台开启。
- 壳启动 30 秒静默检查 → 底部「有新版本」提示条；`check_for_updates` /
  `download_and_install`（安装前级联停引擎，装完自启）/ `get_app_version` 命令组。
- 新增 `release.yml`：推 `v*` tag → macOS/Windows/Linux 三平台签名构建 →
  安装包 + `.sig` + 合并 `latest.json` 自动挂 GitHub Release（tauri-action）。
- 修复桌面壳存量编译错误（updater 引入前的 lib.rs 历史问题），`cargo check` 全绿。

### 手机扫码接入（局域网）
- `POST /api/v1/pairing/codes`：一次性配对码（只存 sha256、TTL 300s、同租户未用码
  上限 5），pair_url 携带局域网 IP；`/pairing/exchange` 原子占用防重放
  （无效/过期/已用同文案防枚举），换发 30 天设备令牌（type=pair，常规 API 物理隔离）。
- 设置页全新「手机接入」标签：点击复制链接二维码生成（300s 倒计时、过期自动置灰）、
  设备台账（名称/到期/吊销）。
- 桌面引擎 `standalone --lan`：显式开启才绑 0.0.0.0；二维码端口=实际监听端口。

### 生产部署
- `docker-compose.prod.yaml`：单机一键自包含构建（api/worker/scheduler/web +
  pgvector pg17 + redis7 带密码；密钥缺失 compose 层直接拒启）。
- `.env.production.example` 模板；服务器版 `deploy/native/server/upgrade.sh`：
  备份(SQLite/pg_dump) → 切 tag → 迁移 → 重启 → 健康检查失败自动回滚（含库恢复）。
- 测试 607 → **619**；迁移链新增 0018_pair_devices（可逆已验证）；openapi contracts（119 paths）。

## [1.3.0] - 2026-10-22

供应商体验升级 + 本地模型放行 + 自生长闭环。

### 供应商：默认模型改为下拉自动获取
- 新增 `POST /api/v1/llm/providers/probe-models`：OpenAI 兼容族 GET {base}/models 拉取模型
  （8s 超时、响应 1MB 上限、500 条截断）；anthropic/gemini 诚实回 `unsupported`；失败回 `error` + 中文原因。
- 设置页表单重排：基础地址 → **API Key → 默认模型**；地址/密钥失焦自动探测，默认模型变
  `el-select` 下拉选择（可搜索、可刷新）；探测失败自动回落「手动输入」兜底，不堵死流程。
- API Key 仅在请求内存中瞬时使用，不落库、不入日志与审计。

### 本地模型：SSRF 分级放行（修复「本地」连通失败）
- `check_egress_url(..., allow_local=True)`：LLM 供应商域（保存/测试/运行时/探测）放行
  127.0.0.0/8、::1 与内网私有段——Ollama / LM Studio 等本地推理是正当场景。
- **云元数据（169.254.0.0/16）、链路本地、组播、保留段永远封死**（任何开关都不放行）。
- 设备连接器、推送通道、MCP 服务器维持原严格策略不变。

### 自生长闭环（进化域）
- **Skill 自我升级**：`POST /api/v1/skills/suggest` 聚合近 7 天运行证据（成功样本/失败类别/
  高频工具序列）→ LLM 产出技能候选卡 → 落 `pending_review` 进既有审核队列；证据不足或
  LLM 不可用时**诚实拒绝**，不落半成品；AI 生成的技能带「AI 生成」徽章。
- **声明式插件**：`/plugins/register`（参数 + 工具/LLM 步骤，≤8 步，未知工具与 mcp__ 直接拒）、
  `/plugins/{id}/review`（批准/驳回）、`/plugins/{id}/run`（顺序解释执行，工具步骤全程复用
  风险闸与逐次审批）、`/plugins/suggest`（从历史挖掘重复出现的操作序列 ≥3 次自动合成草稿）。
- 安全底线：**自动挖掘只产草稿，启用必须经人批准**；插件内高风险工具运行时仍逐次过审批闸
  （发布闸 + 运行闸双保险，同 MCP 模式）。
- 测试 589 → 607；openapi 115 paths。

## [1.2.0] - 2026-10-22

大版本 UI 升级 + 全新对话模式。

### 设计系统 v2「静夜玻璃」（全站换装）
- 设计唯一事实源 `docs/DESIGN-v2.md`：深色玻璃拟态、靛紫→青品牌渐变（全站唯一渐变处）、圆角/控件/阴影各三档协调律。
- `styles/design-system.css`：--ds-* token + Element Plus 暗色变量桥接，覆盖 card/table/dialog/button/input/tag/menu/message/select/pagination/switch/tabs/descriptions/empty/drawer/dropdown/popconfirm 全组件；页面背景双光斑、玻璃卡片 hover、渐变主按钮。
- MainLayout 重造：232px 玻璃侧栏（可折叠 64px、分组导航小标、渐变激活条）+ 56px 顶栏（面包屑 + 用户胶囊角色徽章）；登录/注册全屏玻璃卡。
- 15 个业务页面全部套新装：旧 EP 硬编码色值清零（grep 验证 0 残留）、页头统一 .page-title/.page-actions、状态 tag 四态口径统一；顺带修复设备页空态时「新建设备」按钮被隐藏与空态文案矛盾的旧问题。

### 对话模式（新）
- `ChatView` 三段式：会话列表（自动标题/删除）→ 消息流（用户/助手气泡 + 「↳ 由 X 回答 · 规则命中/智能分类/兜底」诚实路由徽章）→ 粘性输入（Enter 发送）。
- 后端 `app/modules/chat`（迁移 0017）：会话/消息持久化（seq 序号、失败诚实落 failed 行）、复用 supervisor 三级路由或指定 agent/team 直跑、会话对创建者私有、跨用户/跨租户统一 404 防枚举。
- 乐观上屏 + 失败撤回 + 自动标题；全部门禁绿（后端 589 测试）。

## [1.1.0] - 2026-10-22

多 Agent 协作版（对标开源编排框架的系统性升级，发布见 GitHub Release v1.1.0）。

- **P0 协作内核**：LLM function-call 工具循环（风险闸/审计/护栏全复用）+ `agent_run_steps` checkpoint 断点续跑（副作用不重放）+ SSE 步骤流；迁移 0013。
- **P0 班组协作**：`agent_teams` 顺序接力 + Critic 审校 + 答案拼接策略；成员级断点续跑与成员级 SSE；工厂 as_team 一键装配；前端班组页（步骤条/实时流/失败续跑/成员下钻）。
- **观测与通道**：`llm_traces` 逐调用观测（prompt 只存 sha12+200 字隐私摘要）+ 企微/钉钉/通用 webhook 推送（SSRF 双检、Fernet、熔断、限频）+ 评估集骨架 `run_evals.py`。
- **MCP 工具连接器**：手写 streamable-HTTP JSON-RPC 客户端（SSE/JSON 双解析、1MB 限制、SSRF 每次外呼复检）+ 租户动态工具 `mcp__` 回落 + 高风险双闸（发布必平台审核 + 运行审批）+ 5 连败熔断。
- **总控问答**：`/agents/supervisor/ask` 三级路由（农业域关键词计分 → LLM 分类 → 近 7 天频率兜底，routed_by 诚实标记）+「问总控」条。
- **稳定性**：SSE 观测流 DB 异常统一 snapshot_error 帧收尾；CI 迁移前置 + 可逆链校验（根治 PG 无表红灯历史）。
- 测试 513 → 576；迁移链 0011→0016 可逆（SQLite + PostgreSQL 双环境实测）。

## [1.0.0] - 2026-10-19

首个正式版本。整合 10-17 安全整改、10-18 能力中心与 10-19 设施接入/Agent 市场/Agent 工厂三波迭代。

### 设施接入·市场·工厂·智能编排（2026-10-19）

- **设备中心（智慧农业设施接入）**：设备/读数/告警三模型迁移；Webhook 令牌接入（一次性 token、限频、审计）；自定义连接器（Fernet 密文鉴权头、SSRF 出网校验、连续失败自动熔断、beat/inline 双模轮询）；18 项农业指标字典归一（高置信同义词白名单 + `_cm` 单位后缀宽容，未知字段进 other 保原始报文）；前端设备中心页（卡片墙/一次性 token 警示/连接器映射表单/纯 CSS 数据图/告警规则管理）。
- **告警→一键工单**：设备告警一键生成高优工单（幂等：重复点击返回同一工单号），告警记录回填工单编号；复用 tasks 服务与 external_side_effect 人工审批闸。
- **`sensor.query` 助手工具**：租户内读数概况/窗口统计/规则评估，只读零副作用，已入内置工具注册表与风险闸。
- **Agent 市场**：14 个中文农业科技种子助手（安全角色内嵌红线），目录/一键装载（幂等跳过）。
- **Agent 工厂增强**：SSE 实时里程碑、启发式兜底诚实标注、蓝图→一键装配走发布分级。
- **模拟设备**：`backend/scripts/sim_devices.py` 一键造 24h 昼夜曲线数据，端到端演示零外部依赖。
- 质量：后端 492 测试全绿（ruff/mypy 零告警），前端 lint 0 error + vue-tsc build，CI 守护。

### 能力中心与 Agent 工厂（原 Unreleased，2026-10-18）

- **模型供应商管理权下放租户**（ADR-0005）：tenant_admin 可自助接入/切换本租户大模型（Fernet 密文存储、密钥永不回显、SSRF 出网校验、平台全局行只读可选）；写操作全审计。
- **声明式 Agent**：DB 配置即助手（人设+任务模板+知识范围+工具白名单），租户内免发版创建/发布/试跑；风险自动分级——低风险 tenant_admin 自批，高风险自动进平台审核队列（platform_admin 批准/驳回）。
- **Agent 工厂**：输入行业描述 → LLM 生成 3-6 角色能力蓝图（失败自动启发式兜底并诚实标注 generated_by）→ 知识库覆盖探测（0 覆盖标记 needs_knowledge）→ SSE 实时里程碑（planning→blueprint_ready→generating→completed）→ 一键装配（逐项走发布分级，人工闸门不放松）。
- 修复：SUPPLIER_KEY_SECRET 读取支持 .env；发布动作幂等化（双击/重试安全）。

### 整改升级（原 Unreleased，2026-10-17）

依据《五维测评报告（2026-10-17）》实施的安全与运营整改：

#### 安全（阻断级修复）
- **生产启动 fail-fast**：`APP_ENV=production` 时若 `APP_SECRET_KEY` 为默认占位值/短于 32 位、
  `PLATFORM_ADMIN_USERNAMES` 含 `admin` 等弱名单、数据库为 SQLite —— 直接拒绝启动（新增
  `Settings.enforce_runtime_safety()`）。
- 平台管理员 bootstrap 加固：非生产使用弱名单账号时写审计告警；注册接口拒绝保留用户名
  （admin/root/system 等）；注册密码最小长度 8（生产强制 ≥10）。
- seed 脚本生产守卫：`APP_ENV=production` 且未显式 `SEED_ALLOW_PRODUCTION=YES` 时拒绝执行；
  支持 `SEED_RANDOM_PASSWORDS=YES` 随机演示口令。
- SSRF 出网防护（新增 `core/net_guard.py`）：供应商连通性探测、模型 chat、Embedding 出网前
  校验解析后 IP，始终阻断回环/link-local（含云元数据 169.254.169.254）/保留地址；私有网段
  默认放行以兼容内网私有化模型部署（`ENFORCE_MODEL_EGRESS_GUARD=true` 可全部禁止，见 ADR-0003）。
- 自助注册可用 `SIGNUP_ENABLED=false` 一键关闭（试运行建议关闭）。

#### 任务与数据完整性
- 候选知识审核入库失败不再静默返回成功：响应透出 `ingest_ok/ingest_error`。
- Worker 在安全点消费 `cancel_requested`；queued/running 超时由周期任务回收
  （`QUEUED_TIMEOUT`/`RUNNING_TIMEOUT`）；`enqueue_failed` 的 queued 任务可重试。
- Worker 心跳：`/health/worker` 返回心跳新鲜度；compose 增加 worker 独立 healthcheck。

#### 检索与内容安全
- 混合检索关键词满覆盖保底（不被向量分稀释，阈值未经评估集标定前不再产生“有答案也拒答”）。
- 高风险内容关键词扩展（种衣剂/拌种/药种比/稀释倍数/施药/安全间隔期等）；
  新旧资料数值冲突时强制转专家复核并列出分歧值。
- 知识切块可信级按来源映射（expert_correction→expert_reviewed 等）。
- 新增真实评估集 `backend/eval/qa_eval_v1.json`（≥20 用例，含跨租户/注入类目）与 CI 评估测试。

#### 可观测性与运营
- `/metrics` Prometheus 文本端点（HTTP 计数/时延直方图、LLM 调用计数、告警计数）。
- 日志行注入 request_id（contextvar + logging Filter）；恢复 uvicorn 访问日志。
- 运维告警通道 `ALERT_WEBHOOK_URL`（配额 90%/100%、超时回收等事件推送；未配置降级 [ALERT] 日志）。
- 配额管理 API `PATCH /api/v1/admin/quotas/{tenant_id}`（写审计）。
- 周期任务 beat_schedule：超时回收/心跳/月度配额重置；生产 compose 默认启用 scheduler。

#### 知识与进化闭环
- 审核入库失败显式化（响应透出 `ingest_ok/ingest_error`）；混合检索关键词满覆盖保底；
  高风险词表扩至 14 词且新旧资料数值冲突强制转专家复核；切块可信级按来源映射；
  新端点 reparse / batch-review；评估集 `backend/eval/qa_eval_v1.json`（22 用例）+ CI 评估测试通过率 100%。

#### 前端体验（21 文件）
- 界面去演示密码/去内部术语（租户→使用空间等）、角色化菜单与路由守卫、
  AsyncState 四态、任务 5s 轮询、批量审核、重新解析、新手引导与示例问题、≤900px 响应式。

#### 交互赋能与六端客户端（借鉴 agency-orchestrator，Apache-2.0，设计移植）
- **SSE 任务事件流**：`GET /api/v1/tasks/{id}/events/stream`（stream-init/task-event/task-status/stream-end）；
  任务详情抽屉实时上屏，断线重连≤3 次自动降级轮询；关页只停"看"不停"跑"（服务端语义显式）。
- **双层验收预筛**：候选知识机器预检（纯函数断言，why 必引原话）+ 可选 LLM 深检
  （固定三标准+裁判不可靠兜底改判）；`LLM_PRECHECK_ENABLED` 开关，S1 展示性不阻断审核。
- **admin doctor 体检**：`GET /api/v1/admin/doctor` 逐项检查+fix_hint 失败即教学；密钥值红线不进响应。
- **桌面单机模式**：`TASKS_INLINE_MODE`（inline 任务/无 Redis 就绪/inline health ready）+
  `backend/standalone.py` 本地引擎 + `client/` 六端壳骨架（Tauri 桌面/安卓云端壳/鸿蒙 ArkWeb/iOS 说明）+
  手动 CI 打包流 `client.yml`；详见 `docs/client.md`。设计决策见 `docs/adr/0004-declarative-workflow-engine.md`。

#### 部署修复
- `web.Dockerfile` COPY 越出构建上下文的必败构建修复（web 构建上下文改为仓库根）。
- `api/worker.Dockerfile` editable 安装顺序缺陷修复（源码缺失时 setuptools 构建必败）→ 非 editable。
- nginx 增加 `client_max_body_size 52m`（否则知识库上传 >1MB 即 413）。
- 备份/恢复脚本参数化（动态解析 compose 卷名；修复生产项目名下静默备份空卷的事故隐患）；补执行位。
- 生产 compose：强制 `POSTGRES_PASSWORD`；redis requirepass 后各服务 URL 带凭证（修复生产必断连）；
  新增 `.dockerignore`。
- 新增 `/metrics`、`/health/worker`；生产 compose 默认启用 scheduler；beat 月度配额清零
  `quota.monthly_reset`（Redis 幂等键方案，无需 migration）。

## 0.1.0（2026-10-16）

- P0 闭环：认证/租户/RBAC、知识库 RAG（上传→解析→切块→检索→引用）、任务状态机与
  Worker、专家反馈与候选审核、Fake LLM 全链路、OpenAPI 契约与 CI。
