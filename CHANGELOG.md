# 变更日志（CHANGELOG）

本项目遵循《工程开发规范 V3.0》；版本号遵循语义化版本。

## Unreleased（2026-10-17 整改升级）

依据《五维测评报告（2026-10-17）》实施的安全与运营整改：

### 安全（阻断级修复）
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

### 任务与数据完整性
- 候选知识审核入库失败不再静默返回成功：响应透出 `ingest_ok/ingest_error`。
- Worker 在安全点消费 `cancel_requested`；queued/running 超时由周期任务回收
  （`QUEUED_TIMEOUT`/`RUNNING_TIMEOUT`）；`enqueue_failed` 的 queued 任务可重试。
- Worker 心跳：`/health/worker` 返回心跳新鲜度；compose 增加 worker 独立 healthcheck。

### 检索与内容安全
- 混合检索关键词满覆盖保底（不被向量分稀释，阈值未经评估集标定前不再产生“有答案也拒答”）。
- 高风险内容关键词扩展（种衣剂/拌种/药种比/稀释倍数/施药/安全间隔期等）；
  新旧资料数值冲突时强制转专家复核并列出分歧值。
- 知识切块可信级按来源映射（expert_correction→expert_reviewed 等）。
- 新增真实评估集 `backend/eval/qa_eval_v1.json`（≥20 用例，含跨租户/注入类目）与 CI 评估测试。

### 可观测性与运营
- `/metrics` Prometheus 文本端点（HTTP 计数/时延直方图、LLM 调用计数、告警计数）。
- 日志行注入 request_id（contextvar + logging Filter）；恢复 uvicorn 访问日志。
- 运维告警通道 `ALERT_WEBHOOK_URL`（配额 90%/100%、超时回收等事件推送；未配置降级 [ALERT] 日志）。
- 配额管理 API `PATCH /api/v1/admin/quotas/{tenant_id}`（写审计）。
- 周期任务 beat_schedule：超时回收/心跳/月度配额重置；生产 compose 默认启用 scheduler。

### 知识与进化闭环
- 审核入库失败显式化（响应透出 `ingest_ok/ingest_error`）；混合检索关键词满覆盖保底；
  高风险词表扩至 14 词且新旧资料数值冲突强制转专家复核；切块可信级按来源映射；
  新端点 reparse / batch-review；评估集 `backend/eval/qa_eval_v1.json`（22 用例）+ CI 评估测试通过率 100%。

### 前端体验（21 文件）
- 界面去演示密码/去内部术语（租户→使用空间等）、角色化菜单与路由守卫、
  AsyncState 四态、任务 5s 轮询、批量审核、重新解析、新手引导与示例问题、≤900px 响应式。

### 部署修复
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
