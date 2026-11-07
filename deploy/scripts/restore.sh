#!/usr/bin/env bash
# 从备份恢复（破坏性：会清空当前数据库 public schema 与文件卷）。
# 用法：
#   开发：  ./deploy/scripts/restore.sh /backup/techyard-YYYYMMDD
#   生产：  COMPOSE="docker compose -f compose.yaml -f compose.production.yaml" ./deploy/scripts/restore.sh /backup/...
#   非交互（脚本/演练）：加 -y 跳过确认
#
# 保证：
#   - 恢复前校验备份完整性（SHA256SUMS），并先把当前数据快照到临时目录（打印路径）；
#   - 数据库在**单事务**内「清空 public schema + 导入」，任一语句失败整体回滚，
#     不会留下半恢复状态；ON_ERROR_STOP=1 让 psql 遇错即退出；
#   - 恢复后严格比对 alembic 版本，逐文件比对 files.sha256；
#   - 全部校验通过才拉起服务并做健康检查。
set -euo pipefail

ASSUME_YES=0
case "${1:-}" in
  -y|--yes) ASSUME_YES=1; shift ;;
esac
SRC="${1:?请指定备份目录}"
COMPOSE="${COMPOSE:-docker compose}"
fail() { echo "错误：$*" >&2; exit 1; }

hash_host() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$@"
  else
    shasum -a 256 "$@"
  fi
}

counts_sql() {
  cat <<'SQL'
SELECT table_name,
       COALESCE((xpath('/row/c/text()',
                       query_to_xml(format('SELECT count(*) AS c FROM %I.%I', table_schema, table_name),
                                    false, true, '')))[1]::text, '0')::bigint AS rows
FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
ORDER BY table_name;
SQL
}

# ---------- 0. 备份完整性 ----------
[ -f "$SRC/db.sql.gz" ] || fail "找不到 $SRC/db.sql.gz"
[ -f "$SRC/files.tar.gz" ] || fail "找不到 $SRC/files.tar.gz"
gzip -t "$SRC/db.sql.gz" || fail "db.sql.gz 已损坏（gzip 校验失败）"
tar tzf "$SRC/files.tar.gz" >/dev/null || fail "files.tar.gz 已损坏（tar 校验失败）"
if [ -f "$SRC/SHA256SUMS" ]; then
  (cd "$SRC" && hash_host -c SHA256SUMS) || fail "备份文件校验和与 SHA256SUMS 不一致，拒绝恢复"
  echo "备份完整性校验通过"
else
  echo "警告：该备份缺少 SHA256SUMS（旧格式），跳过完整性校验" >&2
fi

if [ "$ASSUME_YES" -ne 1 ] && [ -t 0 ]; then
  printf '将清空当前数据库与文件卷并从 %s 恢复，输入 y 确认：' "$SRC"
  read -r answer
  case "$answer" in y|Y|yes|YES) ;; *) echo "已取消"; exit 1 ;; esac
fi

# ---------- 1. 解析文件卷 + 当前数据快照 ----------
API_CID=$($COMPOSE ps -a -q api 2>/dev/null | head -n1 || true)
FILES_VOL=""
if [ -n "$API_CID" ]; then
  FILES_VOL=$(docker inspect "$API_CID" \
    --format '{{range .Mounts}}{{if eq .Destination "/data/files"}}{{.Name}}{{end}}{{end}}' \
    2>/dev/null || true)
fi
[ -n "$FILES_VOL" ] || fail "未能从 api 容器解析 /data/files 挂载卷（请先创建并启动过本编排）"

SAFETY_DIR="$(mktemp -d "${TMPDIR:-/tmp}/kjxy-restore-safety.XXXXXX")"
echo "[1/4] 快照当前数据到 $SAFETY_DIR（恢复失败可回退）..."
$COMPOSE exec -T postgres pg_dump -U app_user techyard | gzip > "$SAFETY_DIR/db.sql.gz" \
  || fail "当前数据库快照失败，已中止（未做任何修改）"
docker run --rm -v "${FILES_VOL}:/data:ro" -v "$SAFETY_DIR:/backup" alpine \
  tar czf /backup/files.tar.gz -C /data . \
  || fail "当前文件卷快照失败，已中止（未做任何修改）"
echo "      文件卷：$FILES_VOL"

# ---------- 2. 单事务恢复数据库 ----------
echo "[2/4] 恢复数据库（单事务：清空 public schema + 导入，失败整体回滚）..."
$COMPOSE stop api worker scheduler >/dev/null 2>&1 || true
# psql -1 把整段输入包进一个事务；DROP/CREATE SCHEMA 也在同一事务内，
# 因此导入中途失败时旧数据仍在（不会留下空库）。
{
  printf 'DROP SCHEMA IF EXISTS public CASCADE;\nCREATE SCHEMA public;\n'
  gunzip -c "$SRC/db.sql.gz"
} | $COMPOSE exec -T postgres psql -1 -v ON_ERROR_STOP=1 -U app_user -d techyard \
  || fail "数据库恢复失败，事务已回滚（旧数据保持不变）。当前数据快照：$SAFETY_DIR"

REV_EXPECT="$(cat "$SRC/db.revision" 2>/dev/null || true)"
REV_ACTUAL="$($COMPOSE exec -T postgres psql -At -v ON_ERROR_STOP=1 -U app_user -d techyard \
  -c 'SELECT version_num FROM alembic_version' | tr -d '\r')"
if [ -n "$REV_EXPECT" ] && [ "$REV_ACTUAL" != "$REV_EXPECT" ]; then
  fail "恢复后迁移版本不符：期望 $REV_EXPECT，实际 $REV_ACTUAL（当前数据快照：$SAFETY_DIR）"
fi
echo "      迁移版本：${REV_ACTUAL:-未知}"

if [ -f "$SRC/db.counts" ]; then
  counts_sql | $COMPOSE exec -T postgres psql -At -v ON_ERROR_STOP=1 -U app_user -d techyard -f - \
    | tr -d '\r' > "$SAFETY_DIR/counts.after"
  if ! diff -u "$SRC/db.counts" "$SAFETY_DIR/counts.after"; then
    # 备份与取行数之间若有写入，行数可能天然有差异；版本严格校验已通过，
    # 这里只提示不阻断，避免误伤紧急恢复。
    echo "警告：恢复后行数与备份清单有差异（见上方 diff），请核对是否恢复窗口内有写入" >&2
  else
    echo "      行数与备份清单一致"
  fi
fi

# ---------- 3. 恢复文件卷（先清空再解包，然后逐文件校验） ----------
echo "[3/4] 恢复文件卷（清空后解包，按清单逐文件校验）..."
docker run --rm -v "${FILES_VOL}:/data" -v "$SRC:/backup:ro" alpine sh -ec '
  tar tzf /backup/files.tar.gz >/dev/null
  find /data -mindepth 1 -maxdepth 1 -exec rm -rf {} \;
  tar xzf /backup/files.tar.gz -C /data
  cd /data
  find . -type f -print0 | xargs -0 -r sha256sum | LC_ALL=C sort -k2 > /tmp/restored.sha256
  if [ -f /backup/files.sha256 ]; then
    diff -u /backup/files.sha256 /tmp/restored.sha256
    echo "      文件卷与备份清单一致"
  else
    echo "警告：旧备份无 files.sha256，跳过逐文件校验" >&2
  fi
' || fail "文件卷恢复校验失败（备份：$SRC，当前数据快照：$SAFETY_DIR）"

# ---------- 4. 迁移 + 拉起 + 健康检查 ----------
echo "[4/4] 执行迁移并检查健康..."
$COMPOSE run --rm migrate
$COMPOSE up -d api worker scheduler
for _ in $(seq 1 30); do
  if $COMPOSE exec -T api python -c \
    "import urllib.request;urllib.request.urlopen('http://localhost:8000/health/ready')" >/dev/null 2>&1; then
    echo "恢复完成，服务就绪（恢复前数据快照：$SAFETY_DIR）"
    exit 0
  fi
  sleep 2
done
echo "恢复完成且数据校验通过，但健康检查未通过，请查看 api 日志（快照：$SAFETY_DIR）" >&2
exit 1
