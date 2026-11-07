#!/usr/bin/env bash
# 数据库 + 文件备份（带可校验清单）。
# 用法：
#   开发环境： ./deploy/scripts/backup.sh /backup/techyard-YYYYMMDD
#   生产环境： COMPOSE="docker compose -f compose.yaml -f compose.production.yaml" ./deploy/scripts/backup.sh /backup/...
#
# 产物（restore.sh 依赖，勿手工改名）：
#   db.sql.gz        pg_dump 明文（gzip 压缩）
#   files.tar.gz     /data/files 卷内容
#   files.sha256     备份时刻每个文件的 sha256（恢复后逐文件比对）
#   db.revision      备份时刻的 alembic 版本（恢复后严格比对）
#   db.counts        备份时刻各表行数（恢复后对照，见 restore.sh 说明）
#   SHA256SUMS       上述文件的校验和，restore.sh 会先校验备份未被损坏
set -euo pipefail
DEST="${1:?请指定备份目录}"
COMPOSE="${COMPOSE:-docker compose}"
mkdir -p "$DEST"
chmod 700 "$DEST"

hash_host() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$@"
  else
    shasum -a 256 "$@"
  fi
}

echo "[1/4] 备份 PostgreSQL..."
# --no-owner 之外保持默认：恢复时先清空 public schema 再单事务导入，见 restore.sh
$COMPOSE exec -T postgres pg_dump -U app_user techyard | gzip > "$DEST/db.sql.gz"
gzip -t "$DEST/db.sql.gz" || { echo "错误：db.sql.gz 校验失败" >&2; exit 1; }
[ -s "$DEST/db.sql.gz" ] || { echo "错误：db.sql.gz 为空" >&2; exit 1; }

echo "[2/4] 记录数据库版本与行数..."
$COMPOSE exec -T postgres psql -At -v ON_ERROR_STOP=1 -U app_user -d techyard \
  -c "SELECT version_num FROM alembic_version" > "$DEST/db.revision"
# 用 query_to_xml 对每张基表做精确 count（information_schema 不含视图/分区子表）
$COMPOSE exec -T postgres psql -At -v ON_ERROR_STOP=1 -U app_user -d techyard -f - > "$DEST/db.counts" <<'SQL'
SELECT table_name,
       COALESCE((xpath('/row/c/text()',
                       query_to_xml(format('SELECT count(*) AS c FROM %I.%I', table_schema, table_name),
                                    false, true, '')))[1]::text, '0')::bigint AS rows
FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
ORDER BY table_name;
SQL

echo "[3/4] 备份文件卷..."
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
docker run --rm -v "${FILES_VOL}:/data:ro" -v "$DEST:/backup" alpine sh -ec '
  cd /data
  find . -type f -print0 | xargs -0 -r sha256sum | LC_ALL=C sort -k2 > /backup/files.sha256
  tar czf /backup/files.tar.gz -C /data .
'
tar tzf "$DEST/files.tar.gz" >/dev/null || { echo "错误：files.tar.gz 校验失败" >&2; exit 1; }

echo "[4/4] 生成校验和..."
(
  cd "$DEST"
  hash_host db.sql.gz files.tar.gz files.sha256 db.revision db.counts > SHA256SUMS
)
chmod 600 "$DEST"/*
echo "备份完成：$DEST"
ls -lh "$DEST"
