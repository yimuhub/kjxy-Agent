# 多 Agent 并行开发任务单（首轮）

> 总指令：你是本平台的独立开发 Agent。严格遵守《工程开发规范与多 Agent 并行开发手册 V3.0》。
> 只修改分配的主责目录；需改他模块先提接口变更。先读现有代码、契约与测试；使用已定义的 Pydantic/OpenAPI Schema；接口未实现时用 Mock。
> 必须含测试、错误处理、日志、权限检查与类型定义；数据库变更提交 migration；不写真实密钥；不执行模型生成的任意代码；不绕过租户边界与人工审批。
> 提交前运行验收命令并真实报告结果；未执行不得标记通过。

## TASK-001 项目骨架、公共配置与 CI（技术负责人）✅ 已完成

## TASK-002 认证、成员与租户权限

主责：`backend/app/modules/auth/`、`tenants/`。交付登录/刷新/注销/当前用户、RBAC、租户上下文、成员管理、越权负向测试。

## TASK-003 任务状态机、事件与 Worker

主责：`backend/app/modules/tasks/`、`agents/`。交付状态机、TaskService、Celery 执行、Mock handler、Agent 协议/注册表/工作流运行器/orchestrator。

## TASK-004 模型 Provider 与 Tool Registry

主责：`backend/app/modules/llm/`、`tools/`。交付 LLMProvider 协议、fake/openai_compatible 实现、调用记录、Tool 注册/Schema/权限/审计。

## TASK-005 知识文件与 RAG

主责：`backend/app/modules/knowledge/`。交付上传/解析/切块/Embedding/检索、租户过滤、`knowledge.search` 工具。

## TASK-006 Vue 前端基础与首版核心页面

主责：`frontend/`。交付登录、工作台、问答、知识库、任务中心、专家审核、成员管理、系统设置。

## TASK-007 农业问答 Agent 与领域规则

主责：`backend/app/modules/agriculture/`、`plugins/crops/`。交付问答 Schema、带引用的问答流程、高风险规则、演示资料。

## TASK-008 安全、端到端测试与部署手册（集成阶段）

主责：`backend/tests/security/`、`deploy/`、`docs/operations.md`。交付 E2E、安全用例、备份恢复手册、发布清单。

## TASK-009 政策模块（P0 完成后启用）

主责：`backend/app/modules/policy/`、`plugins/policies/`。首版仅保留最小桩与 Schema。
