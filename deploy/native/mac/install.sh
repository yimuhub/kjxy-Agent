#!/usr/bin/env bash
# =============================================================================
# 科技小院平台 · macOS 一键安装脚本（纯本机模式，不依赖 Docker）
#
# 用法：在仓库任意位置执行
#   bash deploy/native/mac/install.sh
#
# 幂等：重复运行不会报错（venv 已存在则跳过创建，.env 已存在则不覆盖，
#       alembic/seed 本身幂等）。
# =============================================================================
set -euo pipefail

# 仓库根目录（脚本位于 deploy/native/mac/，向上三级即仓库根）
REPO_DIR="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$REPO_DIR"

echo "==> 仓库目录：$REPO_DIR"

# ---------- 1. 检查 python3（需要 3.12+） ----------
if ! command -v python3 >/dev/null 2>&1; then
  echo "[错误] 未找到 python3，请先安装 Python 3.12+："
  echo "  官网 https://www.python.org/downloads/ 或 brew install python@3.12"
  exit 1
fi
PY_VER="$(python3 -c 'import sys; print("%d.%d" % (sys.version_info[0], sys.version_info[1]))')"
PY_OK="$(python3 -c 'import sys; print("yes" if sys.version_info >= (3, 12) else "no")')"
if [ "$PY_OK" != "yes" ]; then
  echo "[错误] python3 版本为 ${PY_VER}，需要 3.12+，请升级后重试。"
  exit 1
fi
echo "==> python3 版本 ${PY_VER}，符合要求"

# ---------- 2. 检查 node（需要 18+） ----------
if ! command -v node >/dev/null 2>&1; then
  echo "[错误] 未找到 node，请先安装 Node.js 18+："
  echo "  官网 https://nodejs.org/ 或 brew install node"
  exit 1
fi
NODE_MAJOR="$(node --version | sed 's/^v//' | cut -d. -f1)"
if [ "$NODE_MAJOR" -lt 18 ]; then
  echo "[错误] node 版本为 $(node --version)，需要 18+，请升级后重试。"
  exit 1
fi
echo "==> node 版本 $(node --version)，符合要求"

# ---------- 3. 创建虚拟环境（已存在则跳过） ----------
if [ -x "$REPO_DIR/.venv/bin/python" ]; then
  echo "==> .venv 已存在，跳过创建"
else
  echo "==> 创建虚拟环境 .venv ..."
  python3 -m venv "$REPO_DIR/.venv"
fi

# ---------- 4. 安装后端依赖 ----------
echo "==> 安装后端依赖（pip install -e \".[dev]\"）..."
"$REPO_DIR/.venv/bin/pip" install --upgrade pip -q
"$REPO_DIR/.venv/bin/pip" install -e ".[dev]" -q

# ---------- 5. 安装前端依赖 ----------
echo "==> 安装前端依赖（npm install）..."
(cd "$REPO_DIR/frontend" && npm install)

# ---------- 6. 复制后端 env（已存在则不覆盖） ----------
if [ -f "$REPO_DIR/backend/.env" ]; then
  echo "==> backend/.env 已存在，跳过复制"
else
  echo "==> 复制 backend/.env.example → backend/.env"
  cp "$REPO_DIR/backend/.env.example" "$REPO_DIR/backend/.env"
  echo "    提示：生产使用前请修改 backend/.env 中的 APP_SECRET_KEY"
fi

# ---------- 7. 数据库迁移 ----------
echo "==> 执行数据库迁移 ..."
(cd "$REPO_DIR/backend" && ../.venv/bin/alembic upgrade head)

# ---------- 8. 灌演示数据（幂等，已存在则跳过） ----------
echo "==> 灌演示数据 ..."
(cd "$REPO_DIR/backend" && ../.venv/bin/python -m app.db.seed)

echo ""
echo "================================================================"
echo " 安装完成！用下面两个命令分别启动后端和前端（开两个终端）："
echo ""
echo "   终端 1（后端 API）：cd \"$REPO_DIR\" && make native-api"
echo "   终端 2（前端页面）：cd \"$REPO_DIR\" && make native-web"
echo ""
echo " 然后浏览器打开 http://127.0.0.1:5173 ，用 admin / admin123 登录。"
echo "================================================================"
