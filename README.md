# 科技小院多智能体自生长开发平台

面向农业科技小院与农业科研 / 生产 / 推广场景的多智能体辅助工作平台。

- 统一的 Agent 运行底座：任务编排、工作流运行器、工具注册与权限
- 农业知识库：文档上传 → 解析 → 切块 → Embedding → 检索 → 引用溯源
- 统一模型适配接口（`LLMProvider`），业务层不绑定任何模型供应商
- 专家反馈与候选知识 / Skill 审核队列（受控自进化）
- 租户隔离、RBAC、审计日志贯穿全链路

> 开发基线：《科技小院多智能体自生长开发平台 · 工程开发规范与多 Agent 并行开发手册 V3.0》
> 首版目标：先交付可验证的农业业务闭环（阶段 A→D），再逐步平台化。

## 技术栈

| 层级 | 选型 |
|---|---|
| 后端 | Python 3.12 · FastAPI · Pydantic 2 · SQLAlchemy 2 · Alembic |
| 数据库 | PostgreSQL 17 + pgvector |
| 异步任务 | Celery + Redis |
| 前端 | Vue 3 + TypeScript + Vite + Element Plus |
| 部署 | Docker + Docker Compose |

## 快速开始

### 版本选择

本仓库只有一份代码，提供 5 种部署方式，差异只在部署层：

| 版本 | 说明 | 入口 |
|---|---|---|
| Docker 版 | 最省心，有 Docker 的任何系统 | 下方 Docker 快速开始 |
| Mac / Windows / Linux 原生版 | 不依赖 Docker，Python + Node 直接跑 | [`deploy/native/`](deploy/native/README.md) |
| 服务器版（免 Docker） | 云服务器，systemd 托管 | [`deploy/native/server/`](deploy/native/server/README.md) |

原生运行的通用说明见 [`docs/native-run.md`](docs/native-run.md)。

### Docker 版快速开始

```bash
cp .env.example .env
# 编辑 .env：至少修改 APP_SECRET_KEY、数据库密码；开发环境默认 LLM_PROVIDER=fake（无需模型 Key 即可跑通全链路）

make up          # 启动 postgres / redis / api / worker / web
make migrate     # 执行数据库迁移
make seed-dev    # 创建演示租户、账号与示例知识
make test        # 运行后端测试
```

服务地址（开发默认）：

- Web 管理端：http://localhost:8080
- API：http://localhost:8000（OpenAPI 文档：http://localhost:8000/docs）
- API 健康检查：http://localhost:8000/health/live

演示账号（`make seed-dev` 创建，仅开发环境）：

| 用户名 | 密码 | 角色 |
|---|---|---|
| `admin` | `admin123` | 平台管理员 |
| `tenant_admin` | `tenant123` | 演示租户管理员 |
| `expert` | `expert123` | 农业专家 |
| `operator` | `operator123` | 科研/生产人员 |

> 生产环境禁止使用 seed 账号；首次部署请按 `docs/operations.md` 创建正式管理员。

## 仓库结构

```text
backend/            # FastAPI 后端（模块化单体）
  app/
    api/v1/         # REST 接口层（只做鉴权、校验与 service 调用）
    core/           # 配置、错误模型、日志、权限上下文
    db/             # SQLAlchemy 基类、会话、seed
    modules/        # 领域模块：auth/tenants/tasks/agents/llm/tools/knowledge/evolution/agriculture/policy/audit
    workers/        # Celery 应用与后台任务入口
  migrations/       # Alembic 迁移脚本
  tests/            # unit / integration / security
frontend/           # Vue 3 + TS 管理端
contracts/          # OpenAPI、JSON Schema、错误码、调用示例（前后端契约基准）
plugins/            # 作物包 / 工作流模板 / 政策包（配置化能力包）
deploy/             # Docker、nginx、备份恢复脚本
docs/               # 架构、数据库、API、安全、运维、验收、ADR
```

## 常用命令

```bash
make up            # docker compose 启动全部服务
make down          # 停止服务（保留数据卷）
make logs          # 查看 api/worker 日志
make migrate       # 执行数据库迁移
make seed-dev      # 写入演示数据
make test          # 后端 pytest
make lint          # ruff + 前端 eslint
make build         # 构建生产镜像
```

等价基础命令见 `docs/operations.md`。

## API 契约

- 唯一前缀：`/api/v1`
- 成功响应：`{"data": {...}, "request_id": "..."}`；列表：`{"data": [...], "meta": {"page","page_size","total"}, "request_id": "..."}`
- 错误响应：`{"error": {"code": "...", "message": "...", "details": {}}, "request_id": "..."}`
- 契约基准文件：`contracts/openapi.yaml`、`contracts/error-codes.md`；代码与 OpenAPI 不一致时禁止合并。

## 安全须知

- 模型密钥只从环境变量读取，绝不写入 Git / 数据库明文 / 日志。
- 所有租户业务表强制 `tenant_id` 隔离；跨租户访问有自动化负向测试。
- 高风险操作（设备控制、正式派单、对外提交）默认需要人工确认。
- 详见 `docs/security.md`。

## 文档

- `docs/architecture.md` 架构与模块边界
- `docs/database.md` 数据模型说明
- `docs/api.md` 接口说明
- `docs/security.md` 安全规范
- `docs/operations.md` 部署、备份、升级、回滚
- `docs/acceptance.md` 验收标准
- `docs/agent-work-orders.md` 多 Agent 并行开发任务单
- `docs/adr/` 架构决策记录
