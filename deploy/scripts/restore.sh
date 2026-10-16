#!/usr/bin/env bash
# 从备份恢复。用法：./deploy/scripts/restore.sh /backup/techyard-YYYYMMDD
set -euo pipefail
SRC="${1:?请指定备份目录}"
[ -f "$SRC/db.sql.gz" ] || { echo "找不到 $SRC/db.sql.gz"; exit 1; }

echo "[1/3] 恢复数据库（将覆盖当前数据）..."
gunzip -c "$SRC/db.sql.gz" | docker compose exec -T postgres psql -U app_user -d techyard

echo "[2/3] 恢复文件卷..."
docker run --rm -v tech-yard-platform_filedata:/data -v "$SRC:/backup:ro" alpine \
  tar xzf /backup/files.tar.gz -C /data

echo "[3/3] 执行迁移并检查健康..."
docker compose exec api alembic upgrade head
curl -sf http://localhost:8000/health/ready && echo "恢复完成，服务就绪"
