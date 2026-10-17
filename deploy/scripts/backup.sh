#!/usr/bin/env bash
# 数据库 + 文件备份。
# 用法：
#   开发环境： ./deploy/scripts/backup.sh /backup/techyard-YYYYMMDD
#   生产环境： COMPOSE="docker compose -f compose.yaml -f compose.production.yaml" ./deploy/scripts/backup.sh /backup/...
set -euo pipefail
DEST="${1:?请指定备份目录}"
COMPOSE="${COMPOSE:-docker compose}"
mkdir -p "$DEST"
chmod 700 "$DEST"

echo "[1/2] 备份 PostgreSQL..."
$COMPOSE exec -T postgres pg_dump -U app_user techyard | gzip > "$DEST/db.sql.gz"

echo "[2/2] 备份文件卷..."
# 卷名必须从当前 compose 配置动态解析：开发/生产项目名不同（tech-yard-platform vs
# tech-yard-platform-prod），此前硬编码会在生产静默备份到空卷（重大隐患，已整改）
FILES_VOL=$($COMPOSE config --volumes 2>/dev/null | grep -E 'filedata$' | head -n1 || true)
if [ -z "$FILES_VOL" ]; then
  echo "错误：当前 compose 配置中未找到 filedata 卷（COMPOSE 参数是否正确？）" >&2
  exit 1
fi
echo "      使用文件卷：$FILES_VOL"
docker run --rm -v "${FILES_VOL}:/data:ro" -v "$DEST:/backup" alpine \
  tar czf /backup/files.tar.gz -C /data .

chmod 600 "$DEST"/*
echo "备份完成：$DEST"
ls -lh "$DEST"
