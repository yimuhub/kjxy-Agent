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

## 健康检查

- API：`GET /health/live`（存活）、`GET /health/ready`（数据库+Redis+schema 就绪）、
  `GET /health/worker`（Worker 心跳新鲜度，整改后新增）、`GET /metrics`（Prometheus 文本）。
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

备份内容：PostgreSQL dump（`pg_dump`）、文件卷（`/data/files` 打包）。备份文件设 600 权限，敏感环境加密存放。

> **上线前必做**：在预发/副本环境完整执行一次 backup→restore 演练并记录耗时（RTO）与数据范围；
> 未演练的备份不视为可用备份。

## 恢复

```bash
./deploy/scripts/restore.sh /backup/techyard-20261016
```

恢复后执行：`alembic upgrade head` → 健康检查 → 主流程冒烟（登录→上传→问答→审核）。

## 升级

1. 确认版本兼容矩阵（CHANGELOG）→ 2. 备份 → 3. 拉取固定版本镜像 → 4. 执行迁移 → 5. 重建容器 → 6. 健康检查 → 7. 冒烟测试 → 8. 记录版本。

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

## 生产注意

- 服务器版（免 Docker）安装脚本默认 `APP_ENV=production`，初始账号为随机口令并写入
  `backend/.initial-credentials`（600 权限）；请立即保存并删除该文件，不再使用演示口令。
- 使用 `compose.production.yaml` 覆盖：固定镜像标签（禁 latest）、资源限制、不挂载源码；
  `migrate` 服务使用同一 `API_IMAGE`。
- 数据库与 Redis 不暴露公网端口；Redis 设密码。
- 首次发布前完成至少一次恢复演练并记录 RTO。
