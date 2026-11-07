#!/usr/bin/env bash
# =============================================================================
# 科技小院平台 · 服务器升级脚本（native/systemd 部署，带自动回滚）
#
# 用法：
#   bash deploy/native/server/upgrade.sh <目标tag> [PREFIX]
#   例：bash deploy/native/server/upgrade.sh v0.4.0
#       bash deploy/native/server/upgrade.sh v0.4.0 /data/techyard
#
# 流程（任一步失败即中止并回滚）：
#   1) 记录当前版本 → 备份数据库（SQLite 用 sqlite3 backup API，含 WAL 已提交内容；
#      PostgreSQL 用 pg_dump）
#   2) git fetch --tags + checkout 目标 tag
#   3) venv 重装依赖 → alembic upgrade head；迁移失败立即回滚（不会带着半迁移的库重启）
#   4) systemctl restart techyard-api → 轮询 /health/ready
#   5) 健康检查失败：停服 → checkout 回旧 tag → 恢复备份库 → 重装依赖 → 重启 → 复查
#
# 前置：目标 tag 已在远端存在；backend/.env 可读；服务由 install.sh 注册。
# 幂等提示：重复执行同一 tag 安全（重装 + 重跑迁移均为幂等）。
# =============================================================================
set -euo pipefail

TARGET_TAG="${1:?用法：upgrade.sh <目标tag> [PREFIX]}"
ARG_PREFIX="${2:-}"
if [ -n "$ARG_PREFIX" ]; then
  PREFIX="$ARG_PREFIX"
elif [ "$(id -u)" -eq 0 ]; then
  PREFIX="/opt/techyard"
else
  PREFIX="$HOME/techyard"
fi
VENV="$PREFIX/.venv"
ENV_FILE="$PREFIX/backend/.env"
SERVICE="techyard-api"
HEALTH_URL="${TECHYARD_HEALTH_URL:-http://127.0.0.1:8000/health/ready}"
BACKUP_DIR="$PREFIX/backups"

log() { echo "[upgrade] $*"; }
fail() { echo "[upgrade][错误] $*" >&2; exit 1; }

# root 装法走系统级 unit，普通用户装法走 --user（与 install.sh 口径一致）
if [ "$(id -u)" -eq 0 ]; then
  SC=(systemctl)
else
  SC=(systemctl --user)
fi

# ---------- 0. 前置检查 ----------
[ -d "$PREFIX/backend" ] || fail "未找到安装目录 $PREFIX/backend，请先执行 install.sh"
[ -x "$VENV/bin/python" ] || fail "未找到 $VENV/bin/python，请先执行 install.sh"
git -C "$PREFIX" fetch --tags --quiet || fail "git fetch 失败（网络/远端配置？）"
git -C "$PREFIX" rev-parse "refs/tags/$TARGET_TAG" >/dev/null 2>&1 \
  || fail "目标 tag 不存在：$TARGET_TAG（git fetch 后仍找不到）"

CURRENT_REF="$(git -C "$PREFIX" describe --tags --always --dirty 2>/dev/null || git -C "$PREFIX" rev-parse HEAD)"
CURRENT_COMMIT="$(git -C "$PREFIX" rev-parse HEAD)"
log "当前版本：$CURRENT_REF → 目标：$TARGET_TAG"

# ---------- 1. 备份数据库 ----------
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
DB_BACKUP=""
DB_URL="$(grep -E '^DATABASE_URL=' "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
case "$DB_URL" in
  sqlite*)
    DB_FILE="${DB_URL#sqlite:///}"
    # 去掉 URL 上的查询参数（?mode=rwc 之类）
    DB_FILE="${DB_FILE%%\?*}"
    # 相对路径按 backend/ 为工作目录解析（与 systemd WorkingDirectory 一致）
    case "$DB_FILE" in /*) : ;; *) DB_FILE="$PREFIX/backend/$DB_FILE" ;; esac
    if [ -f "$DB_FILE" ]; then
      DB_BACKUP="$BACKUP_DIR/db-$STAMP.sqlite"
      # 服务在线时直接 cp 会漏掉 WAL 中已提交但未 checkpoint 的事务；
      # sqlite3 的 backup API 走读事务，能拿到包含 WAL 的一致快照。
      if "$VENV/bin/python" - "$DB_FILE" "$DB_BACKUP" <<'PYEOF'
import sqlite3, sys
src_path, dst_path = sys.argv[1], sys.argv[2]
src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True, timeout=30)
dst = sqlite3.connect(dst_path)
try:
    with dst:
        src.backup(dst)
finally:
    dst.close()
    src.close()
PYEOF
      then
        log "已备份 SQLite（backup API，含 WAL）：$DB_BACKUP"
      else
        rm -f "$DB_BACKUP"
        fail "SQLite 备份失败，中止升级（无备份不升级）"
      fi
    else
      log "警告：DATABASE_URL 指向的 SQLite 文件不存在（$DB_FILE），跳过备份"
    fi
    ;;
  postgres*)
    # 生产 PostgreSQL：pg_dump 整库（凭据取 DATABASE_URL）
    DB_BACKUP="$BACKUP_DIR/db-$STAMP.sql.gz"
    if "$VENV/bin/python" - "$DB_URL" "$DB_BACKUP" <<'PYEOF'
import sys, subprocess, urllib.parse
url = urllib.parse.urlsplit(sys.argv[1].replace("+psycopg", ""))
cmd = ["pg_dump", "-h", url.hostname or "localhost", "-p", str(url.port or 5432),
       "-U", url.username or "", "-d", (url.path or "/").lstrip("/")]
if url.password:
    import os; os.environ["PGPASSWORD"] = url.password
with open(sys.argv[2], "wb") as out:
    subprocess.run(cmd, stdout=out, check=True)
import gzip
subprocess.run(["gzip", "-f", sys.argv[2]], check=True)
PYEOF
    then
      log "已备份 PostgreSQL：$DB_BACKUP.gz"
      DB_BACKUP="$DB_BACKUP.gz"
    else
      fail "pg_dump 备份失败，中止升级（无备份不升级）"
    fi
    ;;
  *)
    log "警告：未能从 $ENV_FILE 解析 DATABASE_URL，跳过数据库备份（升级风险自负）"
    ;;
esac

restore_db() {
  if [ -z "$DB_BACKUP" ]; then
    echo "[upgrade][回滚] 无数据库备份，跳过库恢复"
    return 0
  fi
  case "$DB_BACKUP" in
    *sqlite)
      # 服务已停：清掉残留 WAL/SHM 后再整文件覆盖，避免旧 WAL 回放污染恢复结果
      rm -f "$DB_FILE-wal" "$DB_FILE-shm"
      if cp "$DB_BACKUP" "$DB_FILE"; then
        echo "[upgrade][回滚] 已恢复 SQLite 备份"
      else
        echo "[upgrade][回滚] SQLite 覆盖失败，请人工用 $DB_BACKUP 恢复" >&2
      fi
      ;;
    *.gz)
      # 恢复脚本自己打开 gz（不能用 gunzip 管道喂 heredoc：heredoc 会抢占 stdin）；
      # 先清空 public schema 再导入（备份库与当前库版本/对象不一致时直接导入会冲突），
      # ON_ERROR_STOP + 单事务：导入失败整体回滚，不会留下半恢复的库。
      if "$VENV/bin/python" - "$DB_URL" "$DB_BACKUP" <<'PYEOF'
import gzip, subprocess, sys, urllib.parse
url = urllib.parse.urlsplit(sys.argv[1].replace("+psycopg", ""))
cmd = ["psql", "-h", url.hostname or "localhost", "-p", str(url.port or 5432),
       "-U", url.username or "", "-d", (url.path or "/").lstrip("/"),
       "-1", "-v", "ON_ERROR_STOP=1", "-f", "-"]
if url.password:
    import os

    os.environ["PGPASSWORD"] = url.password
proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
proc.stdin.write(b"DROP SCHEMA IF EXISTS public CASCADE;\nCREATE SCHEMA public;\n")
with gzip.open(sys.argv[2], "rb") as fh:
    while chunk := fh.read(1 << 20):
        proc.stdin.write(chunk)
proc.stdin.close()
sys.exit(proc.wait())
PYEOF
      then
        echo "[upgrade][回滚] 已恢复 PostgreSQL 备份"
      else
        echo "[upgrade][回滚] PG 恢复失败（事务已回滚），请人工用 psql 导入 $DB_BACKUP" >&2
      fi
      ;;
  esac
}

rollback() {
  local reason="$1"
  echo "[upgrade][回滚] $reason，正在回滚到 $CURRENT_REF ..."
  # 先停服再动库/代码：避免旧进程持有句柄或继续写入
  "${SC[@]}" stop "$SERVICE" || true
  git -C "$PREFIX" checkout --quiet "$CURRENT_COMMIT" || echo "[upgrade][回滚] checkout 失败，请人工处理" >&2
  restore_db
  (cd "$PREFIX" && "$VENV/bin/pip" install -e . -q) || echo "[upgrade][回滚] 依赖重装失败，请人工处理" >&2
  "${SC[@]}" start "$SERVICE" || true
  echo "[upgrade][回滚] 已回到 $CURRENT_REF（$CURRENT_COMMIT），备份保留在 $DB_BACKUP"
  echo "[upgrade][回滚] 请用 journalctl -u $SERVICE 复查日志"
}

# ---------- 2. checkout 目标 tag ----------
git -C "$PREFIX" checkout --quiet "$TARGET_TAG" || fail "checkout $TARGET_TAG 失败（工作区有未提交改动？）"
log "已切换到 $TARGET_TAG"

# ---------- 3. 依赖 + 迁移 ----------
(cd "$PREFIX" && "$VENV/bin/pip" install -e . -q) || fail "依赖安装失败（未启动迁移，服务未动）"
if ! (cd "$PREFIX/backend" && "$VENV/bin/alembic" upgrade head); then
  # 迁移可能已部分提交（尤其 SQLite batch 模式），必须把库一起回滚，不能只回退代码
  rollback "迁移失败"
  exit 1
fi
log "迁移完成（alembic upgrade head）"

# ---------- 4. 重启 + 健康检查 ----------
"${SC[@]}" restart "$SERVICE" || fail "systemctl restart 失败，请 journalctl -u $SERVICE 排查"
healthy=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
  sleep 3
  if curl -sf --max-time 5 "$HEALTH_URL" >/dev/null 2>&1; then
    healthy=1
    break
  fi
done
if [ "$healthy" -ne 1 ]; then
  rollback "健康检查未通过"
  exit 1
fi
log "健康检查通过：升级完成（$CURRENT_REF → $TARGET_TAG）"
log "备份保留在 $DB_BACKUP，确认稳定运行后可自行清理"
