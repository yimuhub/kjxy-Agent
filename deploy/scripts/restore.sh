#!/usr/bin/env bash
# 从备份恢复。用法：
#   开发：  ./deploy/scripts/restore.sh /backup/techyard-YYYYMMDD
#   生产：  COMPOSE="docker compose -f compose.yaml -f compose.production.yaml" ./deploy/scripts/restore.sh /backup/...
set -euo pipefail
SRC="${1:?请指定备份目录}"
COMPOSE="${COMPOSE:-docker compose}"
[ -f "$SRC/db.sql.gz" ] || { echo "找不到 $SRC/db.sql.gz"; exit 1; }

echo "[1/3] 恢复数据库（将覆盖当前数据）..."
# 先解析卷名（要用容器的实际挂载点），再停写入方，避免恢复过程中 API/Worker 继续写库
API_CID=$($COMPOSE ps -a -q api 2>/dev/null | head -n1 || true)
FILES_VOL=""
if [ -n "$API_CID" ]; then
  FILES_VOL=$(docker inspect "$API_CID" \
    --format '{{range .Mounts}}{{if eq .Destination "/data/files"}}{{.Name}}{{end}}{{end}}' \
    2>/dev/null || true)
fi
if [ -z "$FILES_VOL" ]; then
  echo "错误：未能从 api 容器解析 /data/files 挂载卷（请先创建并启动过本编排）" >&2
  exit 1
fi
$COMPOSE stop api worker scheduler >/dev/null 2>&1 || true
gunzip -c "$SRC/db.sql.gz" | $COMPOSE exec -T postgres psql -U app_user -d techyard

echo "[2/3] 恢复文件卷..."
echo "      使用文件卷：$FILES_VOL"
docker run --rm -v "${FILES_VOL}:/data" -v "$SRC:/backup:ro" alpine \
  tar xzf /backup/files.tar.gz -C /data

echo "[3/3] 执行迁移并检查健康..."
# api 已停：用 migrate 服务跑迁移，再拉起写入方（容器内探活，生产未对宿主暴露 8000）
$COMPOSE run --rm migrate
$COMPOSE up -d api worker scheduler
for _ in $(seq 1 30); do
  if $COMPOSE exec -T api python -c \
    "import urllib.request;urllib.request.urlopen('http://localhost:8000/health/ready')" >/dev/null 2>&1; then
    echo "恢复完成，服务就绪"
    exit 0
  fi
  sleep 2
done
echo "恢复完成，但健康检查未通过，请查看 api 日志" >&2
exit 1
