# 运维手册

## 启动

```bash
cp .env.example .env     # 填写 APP_SECRET_KEY、数据库密码、模型配置
make up                  # docker compose up --build -d
make migrate             # alembic upgrade head
make seed-dev            # 演示数据（仅开发环境）
```

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

- API：`GET /health/live`（存活）、`GET /health/ready`（数据库+Redis 就绪）。
- Worker：Celery 心跳；队列堆积通过 `/admin/stats` 观察。

## 备份

`deploy/scripts/backup.sh`：

```bash
./deploy/scripts/backup.sh /backup/techyard-20261016
```

备份内容：PostgreSQL dump（`pg_dump`）、文件卷（`/data/files` 打包）。备份文件设 600 权限，敏感环境加密存放。

## 恢复

```bash
./deploy/scripts/restore.sh /backup/techyard-20261016
```

恢复后执行：`alembic upgrade head` → 健康检查 → 主流程冒烟（登录→上传→问答→审核）。

## 升级

1. 确认版本兼容矩阵（CHANGELOG）→ 2. 备份 → 3. 拉取固定版本镜像 → 4. 执行迁移 → 5. 重建容器 → 6. 健康检查 → 7. 冒烟测试 → 8. 记录版本。

## 回滚

1. 停止扩大影响 → 2. 恢复上版本镜像/配置 → 3. 若迁移不可逆，按备份恢复流程处理（禁止假设"回滚镜像=数据库自动恢复"）→ 4. 验证健康与主流程。

## 生产注意

- 使用 `compose.production.yaml` 覆盖：固定镜像标签（禁 latest）、资源限制、不挂载源码。
- 数据库与 Redis 不暴露公网端口；Redis 设密码。
- 首次发布前完成至少一次恢复演练并记录 RTO。
