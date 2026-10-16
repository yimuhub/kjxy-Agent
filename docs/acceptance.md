# 验收标准

> 状态标注约定（规范 §19.4：未运行必须诚实标记）：
> - ✅ **已验证**：有自动化测试或真实执行记录支撑
> - ⚠️ **未验证**：实现/文档已就位，但缺少真实环境执行或演练记录
> - ❌ **未实现**：能力缺失或超出首版范围

最后更新：2026-10-17（随整改提交）

## P0 验收清单

- [x] ✅ 新开发者按 README 能在干净环境启动本地服务
  - 证据：README + `docs/native-run.md` 双路径（Docker / 纯本机）；本机 `.venv` 全量测试 289 passed
  - 注意：CI 首次运行前，"干净环境"以 CI 为准（GitHub Actions ubuntu-latest）
- [ ] ⚠️ `docker compose up --build` 完成标准启动
  - compose.yaml 已定义 web/api/worker/postgres/redis 健康检查；**未在本机实际起容器验证**（本机无 Docker 依赖环境），需在有 Docker 的机器上跑一次并留记录
- [x] ✅ 数据库 migration 可从空库完整执行（含 downgrade/upgrade 回环）
  - 证据：CI 步骤 "Migrations on clean database"（`alembic upgrade head` → `downgrade -1` → `upgrade head`，PG/pgvector 镜像）；本机 SQLite 全量 0001–0008 可执行
- [x] ✅ 未登录用户无法访问受保护接口
  - 证据：`tests/security/` 匿名访问负向用例（401/403 断言）
- [x] ✅ 租户 A 无法通过替换 ID 访问租户 B 的文档、任务、反馈、知识片段、文件
  - 证据：`tests/security/` 跨租户越权负向用例（文档/任务/反馈/知识/文件五类 ID 替换）；repository 层强制 tenant_id 过滤
- [x] ✅ 上传文档有清晰处理状态；失败时有可追踪原因
  - 证据：knowledge 模块 `queued/processing/ready/failed` 状态流转测试 + 失败原因落 `error_message`
- [x] ✅ 问答回答展示实际检索到的来源；无证据时不伪造引用
  - 证据：qa_agent 无命中走 `NO_HIT_ANSWER` 测试；citations 仅来自实际检索结果；提示注入/伪造引用用例在 `tests/unit/` 农业问答测试
- [x] ✅ 任务创建后立即返回任务 ID；状态查询与失败信息可用
  - 证据：tasks 集成测试（创建→ID→状态查询→事件→失败原因）；P0-2 修复后 native 模式 handler 已注册（`tests/unit/test_startup_handlers.py` 回归）
- [x] ✅ 专家审核有权限检查与审计记录
  - 证据：evolution 审核队列测试 + `log_audit` 断言；`tests/unit/test_approval_ttl.py` 覆盖审批 self-decide 校验
- [x] ✅ 模型调用超时/报错时 API 不崩溃，任务进入合理失败状态
  - 证据：llm provider 错误分类测试（FakeProvider 可注入失败）+ tasks 失败状态机测试
- [x] ✅ 高风险工具未审批不能执行
  - 证据：`tests/unit/test_tool_registry.py::test_risk_blocked_without_approval`（TOOL_RISK_BLOCKED 403）+ 审批 TTL/复用上限（`tests/unit/test_approval_ttl.py`，6 用例）
- [x] ✅ 日志与错误响应不含密钥或内部堆栈
  - 证据：统一错误响应只暴露 error-code + request_id；密钥扫描 `.env` 0 命中（2026-10-16 审计）；审计日志只记参数键不记值（`test_success_audits_param_keys_only`）
- [ ] ⚠️ 自动化测试在 CI 执行，主流程与安全规则通过
  - CI 工作流 `.github/workflows/ci.yml` 已定义（lint/mypy/全量测试/迁移回环/契约漂移检查）；**GitHub 仓库已创建，首次 CI 运行结果待确认**
- [ ] ⚠️ 数据备份与恢复操作说明已提供
  - `docs/operations.md` 含备份/恢复/升级/回滚手册；**恢复演练未执行**（规范 §15.5 要求首次发布前至少一次演练），发布前必须补演练记录

## 测试层级

1. 单元测试：状态机、权限判断、Schema、切块、Mock provider ✅（289 passed，含审批 TTL/pgvector 路径回归）
2. 集成测试：API + 临时库、迁移、任务队列 ✅（sqlite 内存库；PG 路径由 CI 迁移步骤覆盖）
3. 契约测试：OpenAPI 与实现一致 ✅（`make contract-check` + CI 漂移检查，`backend/scripts/generate_openapi.py` 自动生成）
4. 端到端：登录→上传→问答→引用→反馈→审核 ✅（integration tests）
5. 安全测试：跨租户、越权、提示注入、文件上传、密钥泄露 ✅（tests/security/）
6. AI 评估：检索召回、来源正确率、拒答恰当性、跨租户泄漏（必须为 0） ⚠️ 评估 runner 就位（`app/eval/`），**专家评估集（≥50 条）尚未建立**，基线报告缺失
7. 部署恢复测试：干净启动、迁移、备份、恢复、升级回滚 ⚠️ 迁移回环已自动化；**备份恢复演练未执行**

## 发布准入

- [ ] 迁移验证通过（CI 通过后可勾选）
- [ ] 高风险权限/租户隔离用例通过（本地已绿，CI 首跑确认）
- [ ] P0 主流程 E2E 通过（本地已绿，CI 首跑确认）
- [ ] 无未处理阻断级安全问题（2026-10-16 审计 0 阻断项；platform_admin 偏差已 ADR-0002 记录）
- [ ] 配置变更和回滚步骤可执行 ⚠️（手册在 operations.md，回滚含"数据库不自动反向"的明确说明）
- [ ] 发布说明含版本、变更、已知问题、恢复办法 → 见本仓库 `docs/` + 本文件状态

## 整改记录（2026-10-17 提交）

| 项 | 内容 | 状态 |
|---|---|---|
| P0-1 | flaky 测试（conftest 环境变量固定 + workflow 测试 NameError） | ✅ 已修，289 全绿 |
| P0-2 | native 模式 knowledge_qa handler 未注册 | ✅ main.py 启动装配 + 回归测试 |
| P0-3 | OpenAPI 契约漂移（55 路径，重复挂载已修） | ✅ 生成脚本 + CI 漂移检查 |
| P0-4 | 无版本管理 | ✅ git 仓库 + uv.lock + npm lock + CI |
| 6 | platform_admin / 供应商密钥边界 ADR | ✅ docs/adr/0002、0003 |
| 7 | 审批 TTL + 复用上限 | ✅ 0007 迁移 + service + 6 用例 |
| 8 | 检索 pgvector 索引路径 + 截断指标 | ✅ 0008 HNSW 迁移 + search() DB 侧排序 + 截断日志 |
| 9 | 专家评估集 ≥50 条 + 基线报告 | ⚠️ 待试点专家数据 |
| 10 | 备份恢复演练 + Worker 心跳告警 | ⚠️ 手册就位，演练/告警未执行 |
