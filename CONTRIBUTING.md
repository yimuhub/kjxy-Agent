# 贡献指南

## 分支与合并

- `main`：可发布版本，禁止直接推送，只能通过 PR 合并。
- 功能分支命名：`feat/<module>-<short-name>`、`fix/...`、`docs/...`。
- 合并前必须通过：ruff、类型检查、单元测试、相关集成测试与代码评审。
- 禁止通过覆盖他人文件解决冲突；先对齐模块契约，再由文件主责人处理。

## 模块所有权

| 目录 | 主责 |
|---|---|
| `contracts/`、根工程文件、Compose/CI | 技术负责人 |
| `backend/app/modules/auth/`、`tenants/` | 身份与租户 |
| `backend/app/modules/tasks/`、`agents/`、`workers/` | 任务与编排 |
| `backend/app/modules/llm/`、`tools/` | 模型与工具 |
| `backend/app/modules/knowledge/` | 知识库 / RAG |
| `frontend/` | 前端 |
| `backend/app/modules/agriculture/`、`plugins/crops/` | 农业领域 |
| `backend/tests/security/`、`deploy/`、`docs/operations.md` | 测试 / 安全 / 部署 |

跨模块修改先提接口变更提案，更新契约后再实现。

## 代码规范

- 后端：ruff + 类型注解；endpoint 只做鉴权/校验/service 调用，不写复杂业务逻辑。
- 文件命名：`models.py`（ORM）、`schemas.py`（Pydantic 输入输出）、`service.py`（业务编排）、`repository.py`（持久化查询）、`router.py`（接口）。
- 所有数据库变更必须提交 Alembic migration，不修改已共享的旧 migration。
- 不得写入真实密钥；不得执行模型生成的任意代码；不得绕过租户边界。

## 提交前自查

1. 相关测试真实执行通过（未执行不得标记通过）；
2. `contracts/openapi.yaml` 与实现一致；
3. 错误响应使用 `contracts/error-codes.md` 中的稳定错误码；
4. 日志不含密钥、密码哈希与内部堆栈；
5. 完成报告列明：改动文件、测试命令与结果、已知问题、接口依赖。
