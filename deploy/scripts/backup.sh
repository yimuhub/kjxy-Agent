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
# 卷名必须取自 api 容器的实际挂载：`compose config --volumes` 只给短名（filedata/files），
# 直接 docker run -v 短名会挂到另一个空卷，导致备份静默为空。开发/生产命名不同也一并覆盖。
API_CID=$($COMPOSE ps -a -q api 2>/dev/null | head -n1 || true)
FILES_VOL=""
if [ -n "$API_CID" ]; then
  FILES_VOL=$(docker inspect "$API_CID" \
    --format '{{range .Mounts}}{{if eq .Destination "/data/files"}}{{.Name}}{{end}}{{end}}' \
    2>/dev/null || true)
fi
if [ -z "$FILES_VOL" ]; then
  echo "错误：未能从 api 容器解析 /data/files 挂载卷（请确认服务已创建）" >&2
  exit 1
fi
echo "      使用文件卷：$FILES_VOL"
docker run --rm -v "${FILES_VOL}:/data:ro" -v "$DEST:/backup" alpine \
  tar czf /backup/files.tar.gz -C /data .

chmod 600 "$DEST"/*
echo "备份完成：$DEST"
ls -lh "$DEST"
