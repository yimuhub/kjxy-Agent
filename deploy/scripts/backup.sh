#!/usr/bin/env bash
# 数据库 + 文件备份。用法：./deploy/scripts/backup.sh /backup/techyard-YYYYMMDD
set -euo pipefail
DEST="${1:?请指定备份目录}"
mkdir -p "$DEST"
chmod 700 "$DEST"

echo "[1/2] 备份 PostgreSQL..."
docker compose exec -T postgres pg_dump -U app_user techyard | gzip > "$DEST/db.sql.gz"

echo "[2/2] 备份文件卷..."
docker run --rm -v tech-yard-platform_filedata:/data:ro -v "$DEST:/backup" alpine \
  tar czf /backup/files.tar.gz -C /data .

chmod 600 "$DEST"/*
echo "备份完成：$DEST"
ls -lh "$DEST"
