# 变更日志（CHANGELOG）

本项目遵循《工程开发规范 V3.0》；版本号遵循语义化版本。

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
