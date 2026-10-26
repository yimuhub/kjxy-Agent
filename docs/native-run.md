# 纯本机运行手册（零外部依赖）

> 不装 Docker、不装 Postgres、不装 Redis 也能跑。
> 数据存 SQLite 文件，模型用内置 Fake（免 Key），后台任务自动降级为本机线程。

## 1. 前置要求

- Python 3.12+（`python3 --version` 确认）
- Node.js 18+（`node --version` 确认）

## 2. 安装（只需一次）

```bash
cd /Users/czmj/Documents/kejixiaoyuan
cp backend/.env.example backend/.env   # 按需改 APP_SECRET_KEY
make native-install
```

## 3. 建表 + 灌演示数据（只需一次）

```bash
make native-migrate
make native-seed
```

演示账号：`admin/admin123`（平台管理员）、`yard_admin/yard123`、
`expert/expert123`、`operator/operator123`。

## 4. 启动（开两个终端）

终端 1（后端 API，http://127.0.0.1:8000）：

```bash
make native-api
```

终端 2（前端页面，http://127.0.0.1:5173）：

```bash
make native-web
```

浏览器打开 http://127.0.0.1:5173，用演示账号登录即可体验完整流程：
上传文档 → 自动解析 → 提问（带引用）→ 反馈纠错 → 专家审核 → 任务执行。

## 5. 工作原理

| 原 Docker 依赖 | 本机替代方案 |
|---|---|
| Postgres + pgvector | SQLite（`backend/data/dev.db`）；向量列自动回退为 JSON，检索用 Python 余弦 |
| Redis + Celery Worker | `WORKER_SYNC_FALLBACK=true`：投递失败时在 API 进程的后台线程同步执行 |
| 真实 LLM/Embedding | `LLM_PROVIDER=fake` / `EMBEDDING_PROVIDER=mock`，免 Key 开箱即用 |

## 6. 已知限制

1. **并发与规模**：SQLite + 本机线程只适合单人开发/演示；生产/多人请用 Docker Compose（Postgres + Redis）。
2. **任务重试语义**：本机线程执行失败不会像 Celery 那样自动重试，失败任务可在任务中心手动重试。
3. **健康检查**：`/health/ready` 在无 Redis 时返回 503 + `{"status":"not_ready"}`，
   这是预期行为（API 本体不受影响，`/health/live` 正常）。
4. **定时任务**：Celery Beat 的周期任务（Skill 审核提醒等）在本机模式下不运行。

## 7. 常见问题

- `python3: command not found` → 先安装 Python 3.12（官网 python.org 或 `brew install python@3.12`）。
- `npm: command not found` → 先安装 Node.js 18+（官网 nodejs.org）。
- 端口被占用 → `lsof -i :8000` / `lsof -i :5173` 找到进程后关掉，或改 `--port`。
- 想清空重来 → 删除 `backend/data/` 目录，重新执行第 3 步。
