# 运维手册

## 启动

```bash
cp .env.example .env     # 填写 APP_SECRET_KEY、数据库密码、模型配置
make up                  # docker compose up --build -d（自动执行 migrate 服务）
make seed-dev            # 演示数据（仅开发环境）
```

> `migrate` 服务会在 API/Worker/Scheduler 启动前执行 `alembic upgrade head`；
> `make migrate` 仍保留，用于手动补迁移（幂等）。

等价基础命令：

```bash
docker compose up --build -d
docker compose ps
docker compose logs -f api worker
docker compose exec api alembic upgrade head
docker compose down
```

> `docker compose down -v` 会删除数据库卷，务必先备份（`make nuke` 有二次确认）。

## 首次初始化

- 不预置任何管理员账号。空库首次打开 Web 端会强制进入 `/setup` 向导，
  一次性创建超级管理员、首个小院、产业画像、内置技能、品牌与模型接入。
- 初始化完成后写入 `setup_state` 单行记录并自动登录；再次访问向导直接跳登录页。
- 判断「已初始化」= `setup_state` 有行 **或** 库中已有用户。迁移 `0082_setup_state`
  会为存量库补一行 `wizard_version='legacy'`，因此升级不会被误判为新装。
- `GET /api/v1/setup/status` 返回 `initialized`；桌面壳据此决定直连主界面还是进向导。
- 想重跑向导：停服、备份后清空业务数据（或换新库）再启动；`setup_state` 与 `users` 任一非空即视为已完成。


## 健康检查

- API：`GET /health/live`（存活）、`GET /health/ready`（数据库+Redis+schema 就绪）、
  `GET /health/worker`（Worker 心跳新鲜度，整改后新增）、`GET /metrics`（Prometheus 文本，
  默认关闭：`METRICS_ENABLED=true` 才开放，且该端口不得暴露公网——指标未鉴权，能读出 QPS 与耗时分布）。
  生产环境下 `ready` 会校验 Alembic 版本是否为 head，旧 schema 直接 503。
- Worker：compose 内置 healthcheck（`celery inspect ping`）；`/health/worker` 的 age_seconds
  超过 180s 视为 stale。周期任务（超时回收/心跳/月度重置）由 scheduler 服务承担，
  生产 compose 默认启用；开发 compose 需 `COMPOSE_PROFILES=scheduler` 或 `COMPOSE_PROFILES=scheduler make up`。
- 运维告警：配置 `ALERT_WEBHOOK_URL` 后配额/回收/入库失败等事件主动推送；未配置时以
  `[ALERT]` 前缀 WARNING 日志呈现。

## 备份

`deploy/scripts/backup.sh`（生产卷名自动从当前 compose 配置解析，硬编码卷名事故已修复）：

```bash
# 开发/默认编排
./deploy/scripts/backup.sh /backup/techyard-20261016
# 生产覆盖编排
COMPOSE="docker compose -f compose.yaml -f compose.production.yaml" ./deploy/scripts/backup.sh /backup/techyard-20261016
```

备份内容：PostgreSQL dump（`pg_dump`）、文件卷（`/data/files` 打包），以及三份校验清单：

| 文件 | 用途 |
| --- | --- |
| `files.sha256` | 备份时刻每个文件的 sha256，恢复后逐文件比对 |
| `db.revision` | 备份时刻的 Alembic 版本，恢复后严格比对（不一致直接失败） |
| `db.counts` | 备份时刻各基表行数，恢复后对照（备份与取数之间可能有写入，差异只告警） |
| `SHA256SUMS` | 上述产物的校验和，恢复前先验备份未损坏 |

备份文件设 600 权限，敏感环境加密存放。`pg_dump` 是快照一致的，备份期间不需要停服；
SQLite（原生部署）由 `deploy/native/server/upgrade.sh` 用 `sqlite3` backup API 备份，
包含 WAL 中已提交未 checkpoint 的事务。

> **上线前必做**：在预发/副本环境完整执行一次 backup→restore 演练并记录耗时（RTO）与数据范围；
> 未演练的备份不视为可用备份。

## 恢复

```bash
./deploy/scripts/restore.sh /backup/techyard-20261016        # 交互确认
./deploy/scripts/restore.sh -y /backup/techyard-20261016     # 脚本/演练免确认
```

恢复是**破坏性**操作，脚本按以下顺序执行，任一步失败即中止：

1. 校验 `SHA256SUMS`、gzip/tar 完整性；旧格式备份（无清单）会显式告警；
2. 把当前数据库与文件卷快照到临时目录并打印路径（恢复失败可回退）；
3. 停 `api/worker/scheduler`，在**单事务**内 `DROP SCHEMA public CASCADE` + 导入 dump
   （`psql -1 -v ON_ERROR_STOP=1`）：导入中途失败整体回滚，不会留下空库或半恢复状态；
4. 严格比对 Alembic 版本、对照行数清单；
5. 清空文件卷再解包，按 `files.sha256` 逐文件比对，不一致直接失败；
6. `alembic upgrade head` → 拉起服务 → `/health/ready` 通过才宣布完成。

恢复后仍需人工冒烟：登录→上传→问答→审核。

## 升级

1. 确认版本兼容矩阵（CHANGELOG）→ 2. 备份 → 3. 拉取固定版本镜像 → 4. 执行迁移 → 5. 重建容器 → 6. 健康检查 → 7. 冒烟测试 → 8. 记录版本。

原生 systemd 部署用 `deploy/native/server/upgrade.sh <tag>`：自动备份（SQLite backup API /
`pg_dump`）→ checkout → 迁移 → 重启 → 健康检查。迁移失败或健康检查失败都会回滚代码、
恢复备份库并重启；PG 恢复同样是 `ON_ERROR_STOP` + 单事务。

### 本地/单机升级顺序（避免「当前后端版本不支持该操作」）

```bash
cd backend
../.venv/bin/python -m alembic -c alembic.ini upgrade head   # 先迁移
# 再停止并重启后端进程（uvicorn），不要只 reload 前端
curl -s http://127.0.0.1:8000/api/v1/meta                   # 确认 version 与 migration_head
```

- 前端启动会调用 `GET /api/v1/meta` 比对版本；后端落后时页面顶部会出现提示横幅。
- 若接口返回 405/404 且提示「后端版本较旧」，优先检查后端进程是否早于本次代码启动，以及迁移是否执行。


## 回滚

1. 停止扩大影响 → 2. 恢复上版本镜像/配置 → 3. 若迁移不可逆，按备份恢复流程处理（禁止假设"回滚镜像=数据库自动恢复"）→ 4. 验证健康与主流程。

## 发布与签名

- 推 `v*` 标签触发 Release：先跑 `verify`（各版本面一致）与 `quality-gate`
  （后端 ruff/mypy/pytest/迁移可逆/契约漂移 + 前端 lint/单测/构建），全部通过才进入
  三平台构建；任一失败不会产出安装包。
- 依赖锁：Python 一律 `uv sync --frozen`（uv.lock）；前端与桌面一律 `npm ci`
  （各自 package-lock.json）；Node 统一 22，与 `frontend/package.json` 的 `engines>=22` 对齐。
- CI 另有真实浏览器冒烟（Playwright）与生产编排启动冒烟（compose build→migrate→healthy→
  web 入口），不再只做 `compose config` 与镜像构建。
- 更新包签名：Tauri updater 使用 minisign（Secrets 中的 `TAURI_SIGNING_PRIVATE_KEY`），
  应用内自动升级链路可用。
- **平台代码签名/公证尚未完成**：macOS 未做 Developer ID 签名与公证，Windows 未做
  Authenticode。首次安装会出现 Gatekeeper / SmartScreen 提示；对外正式分发前必须补齐
  证书与 CI Secrets，不能把「updater 有签名」当成「安装包已被系统信任」。

## 生产注意

- 服务器版（免 Docker）安装脚本默认 `APP_ENV=production`，初始账号为随机口令并写入
  `backend/.initial-credentials`（600 权限）；请立即保存并删除该文件，不再使用演示口令。
- 使用 `compose.production.yaml` 覆盖：固定镜像标签（禁 latest）、资源限制、不挂载源码；
  `migrate` 服务使用同一 `API_IMAGE`。
- 数据库与 Redis 不暴露公网端口；Redis 设密码。
- 首次发布前完成至少一次恢复演练并记录 RTO。

## 部署形态与能力差异

两种形态共用同一套代码，但后台能力不等价，交付时必须按客户实际形态说明（审查 O6）：

| 能力 | Docker / 生产栈（PostgreSQL + Redis + Celery） | 桌面 / 原生单机（SQLite + inline） |
| --- | --- | --- |
| 知识检索 | pgvector 向量候选，候选集由索引决定 | 关键词/向量退化路径，按 `retrieval_scan_limit`（默认 2000）截断扫描 |
| 后台任务 | Celery worker 执行、可重试、跨进程 | 进程内同步降级线程；进程退出时在途任务中断，靠超时回收兜底 |
| 超时任务回收 | beat 每 5 分钟 `tasks.reap_stale` | 内嵌维护线程每 5 分钟，同口径同实现 |
| legacy 配额月清零 | beat 每日 `quota.monthly_reset`，`reset_period` 列条件 UPDATE 幂等 | 内嵌维护线程每小时检查，同一实现，不依赖 Redis |
| 指标与告警 | `/metrics` 文本；多进程各自计数，需按实例聚合 | 单进程计数，重启归零；告警依赖本机通道 |
| 写并发 | 多 API 实例 + Redis 队列 | 单进程 + SQLite 写串行化 |
| 备份恢复 | `deploy/scripts/backup.sh` / `restore.sh`（PG dump + 卷） | `deploy/native/server/upgrade.sh` 自带库备份与自动回滚 |

桌面形态定位是「小院本地、5 人以内使用」；多院/高并发场景必须使用生产栈。

## 容量与 SLA 口径

- 现有性能基线（[docs/perf-baseline-2026-11-07.md](perf-baseline-2026-11-07.md)）是
  **本机 dev、SQLite + inline、单并发 n=10** 的口径，只回答「接口本身快不快」，
  不能作为生产 SLA 承诺。
- 生产 SLA 必须以**目标拓扑**（PostgreSQL + Redis + Celery + 多 API 实例）的压测结果为准；
  压测完成前只承诺可用性探针口径（`/health/live`、`/health/ready`、`/health/worker`），
  不承诺 P95 时延与并发数。
- SSE 长连接数、PG 写锁、会话列表大分页这三项必须进下一次 locust 场景。
