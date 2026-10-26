# 架构说明

## 总体架构

```text
Web 管理端（Vue 3 + TypeScript）
          │ HTTPS /api/v1
          ▼
API 服务（FastAPI：鉴权、小院上下文、请求校验、业务服务）
          │
          ├── 身份与小院模块（auth / yards）
          ├── 任务与工作流模块（tasks） ──► Celery Worker
          ├── 模型适配模块（llm） ────────► 外部/本地模型服务
          ├── Agent 运行模块（agents）
          ├── 知识库模块（knowledge） ────► 文档解析/Embedding Worker
          ├── 工具注册与权限模块（tools）
          ├── 专家审核与进化模块（evolution）
          ├── 农业领域模块（agriculture，分阶段）
          └── 政策领域模块（policy，P1 桩）
          │
          ├── PostgreSQL + pgvector（权威业务数据）
          ├── Redis（队列、短期缓存）
          └── 文件存储接口（本地持久卷 / 对象存储）
```

## 模块边界

模块化单体：按领域划分 Python package（`backend/app/modules/`），API 与 Worker 共享领域代码、进程分离。依赖规则：

| 模块 | 可依赖 | 不得依赖 |
|---|---|---|
| core | 标准库及少量通用依赖 | 具体农业业务、前端 |
| auth | core、yards、数据库层 | 领域 Agent 内部实现 |
| yards | core、数据库层 | 具体模型供应商 |
| tasks | core、数据库层、agents 公共接口 | 前端代码、具体政策抓取器 |
| agents | core、llm、tools、knowledge 的接口 | 绕过业务服务直接访问任意表 |
| llm | core、配置 | 领域业务规则 |
| knowledge | core、文件存储、数据库 | 不受限跨小院检索 |
| tools | core、业务服务接口 | 不受控任意代码/任意 SQL |
| evolution | core、knowledge、agents | 自动修改生产源代码 |
| agriculture | core、knowledge、tasks、tools | 直接操作模型供应商 SDK |
| policy | core、knowledge、tasks | 将未核验结果标为官方事实 |
| audit | core、数据库层 | 修改业务结果 |

跨模块调用通过 service 接口或注册表（AgentRegistry / ToolRegistry / TASK_HANDLERS）完成，禁止循环依赖。

## 关键设计

- **契约先行**：`contracts/openapi.yaml`、`contracts/json-schema/`、`contracts/error-codes.md` 是前后端与多 Agent 协作的唯一基准。
- **小院隔离**：`yard_id` 从已验证身份中取得；repository 查询默认带小院过滤；缓存键包含小院维度。
- **任务状态机**：`queued → running → succeeded | failed | cancelled | awaiting_review → …`，非法转移拒绝，所有转移写 `task_events`。
- **工具风险分级**：`read_only / draft / write_reversible / external_side_effect`；R3 级必须人工确认。
- **受控自进化**：反馈 → 候选知识/Skill → 评测 → 专家审核 → 版本化发布/回滚；禁止自动改生产代码。
- **模型解耦**：业务只依赖 `LLMProvider` 协议；首版提供 `fake`（开发/CI）与 `openai_compatible` 两种实现。

## 部署形态

开发/单机试点：Docker Compose（web、api、worker、scheduler、postgres+pgvector、redis）。API 与 Worker 为不同容器。生产覆盖配置见 `compose.production.yaml`。
