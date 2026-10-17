#!/usr/bin/env bash
# 从备份恢复。用法：
#   开发：  ./deploy/scripts/restore.sh /backup/techyard-YYYYMMDD
#   生产：  COMPOSE="docker compose -f compose.yaml -f compose.production.yaml" ./deploy/scripts/restore.sh /backup/...
set -euo pipefail
SRC="${1:?请指定备份目录}"
COMPOSE="${COMPOSE:-docker compose}"
[ -f "$SRC/db.sql.gz" ] || { echo "找不到 $SRC/db.sql.gz"; exit 1; }

echo "[1/3] 恢复数据库（将覆盖当前数据）..."
gunzip -c "$SRC/db.sql.gz" | $COMPOSE exec -T postgres psql -U app_user -d techyard

echo "[2/3] 恢复文件卷..."
FILES_VOL=$($COMPOSE config --volumes 2>/dev/null | grep -E 'filedata$' | head -n1 || true)
if [ -z "$FILES_VOL" ]; then
  echo "错误：当前 compose 配置中未找到 filedata 卷（COMPOSE 参数是否正确？）" >&2
  exit 1
fi
echo "      使用文件卷：$FILES_VOL"
docker run --rm -v "${FILES_VOL}:/data" -v "$SRC:/backup:ro" alpine \
  tar xzf /backup/files.tar.gz -C /data

echo "[3/3] 执行迁移并检查健康..."
$COMPOSE exec api alembic upgrade head
curl -sf http://localhost:8000/health/ready && echo "恢复完成，服务就绪"
