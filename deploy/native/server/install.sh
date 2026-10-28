#!/usr/bin/env bash
# =============================================================================
# 科技小院平台 · 服务器一键安装（免 Docker，systemd 托管）
#
# 用法：
#   bash deploy/native/server/install.sh [PREFIX]
#
# PREFIX 默认：root 运行 → /opt/techyard；普通用户 → $HOME/techyard
# 幂等：PREFIX 已有代码则跳过拷贝；.env 已存在则不覆盖；alembic/seed 幂等。
# 说明：安装生产依赖（不含 [dev] 测试工具）；前端执行 npm run build 生成 dist。
# =============================================================================
set -euo pipefail

# ---------- 0. 解析参数 ----------
ARG_PREFIX="${1:-}"
if [ -n "$ARG_PREFIX" ]; then
  PREFIX="$ARG_PREFIX"
elif [ "$(id -u)" -eq 0 ]; then
  PREFIX="/opt/techyard"
else
  PREFIX="$HOME/techyard"
fi
# 仓库根目录（脚本位于 deploy/native/server/，向上三级即仓库根）
SRC_DIR="$(cd "$(dirname "$0")/../../.." && pwd)"

echo "==> 安装目录 PREFIX：$PREFIX"
echo "==> 代码来源：$SRC_DIR"

# ---------- 1. 拷贝代码（已有则跳过） ----------
if [ -d "$PREFIX/backend" ]; then
  echo "==> $PREFIX 已有代码，跳过拷贝（更新代码见 README）"
else
  echo "==> 拷贝代码到 $PREFIX ..."
  mkdir -p "$PREFIX"
  # 排除虚拟环境、前端依赖、本地数据与缓存
  tar --exclude='.venv' --exclude='frontend/node_modules' \
      --exclude='backend/data' --exclude='__pycache__' \
      -cf - -C "$SRC_DIR" . | tar -xf - -C "$PREFIX"
fi
cd "$PREFIX"

# ---------- 2. 检查 python3（需要 3.12+） ----------
if ! command -v python3 >/dev/null 2>&1; then
  echo "[错误] 未找到 python3，请先安装 Python 3.12+。"
  exit 1
fi
if [ "$(python3 -c 'import sys; print("yes" if sys.version_info >= (3, 12) else "no")')" != "yes" ]; then
  echo "[错误] python3 需要 3.12+。"
  exit 1
fi

# ---------- 3. 建 venv 并安装生产依赖 ----------
if [ -x "$PREFIX/.venv/bin/python" ]; then
  echo "==> .venv 已存在，跳过创建"
else
  python3 -m venv "$PREFIX/.venv"
fi
echo "==> 安装后端生产依赖 ..."
"$PREFIX/.venv/bin/pip" install --upgrade pip -q
"$PREFIX/.venv/bin/pip" install -e . -q

# ---------- 4. 前端构建 ----------
if ! command -v node >/dev/null 2>&1; then
  echo "[错误] 构建前端需要 node 18+，请先安装。"
  exit 1
fi
echo "==> 安装前端依赖并构建（npm run build）..."
(cd "$PREFIX/frontend" && npm install && npm run build)
echo "==> 前端产物：$PREFIX/frontend/dist"

# ---------- 5. env（已存在则不覆盖；新生成随机 SECRET） ----------
if [ -f "$PREFIX/backend/.env" ]; then
  echo "==> backend/.env 已存在，跳过"
  echo "[提示] 请确认 APP_ENV=production，且未使用演示账号口令。"
  ADMIN_USER="$(grep -E '^PLATFORM_ADMIN_USERNAMES=' "$PREFIX/backend/.env" | head -1 | cut -d= -f2 | cut -d, -f1)"
  ADMIN_USER="${ADMIN_USER:-admin}"
else
  cp "$PREFIX/backend/.env.example" "$PREFIX/backend/.env"
  if command -v openssl >/dev/null 2>&1; then
    SECRET="$(openssl rand -hex 32)"
  else
    SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  fi
  ADMIN_USER="owner_$(python3 -c 'import secrets; print(secrets.token_hex(3))')"
  sed -i "s|^APP_SECRET_KEY=.*|APP_SECRET_KEY=$SECRET|" "$PREFIX/backend/.env"
  # 服务器版默认生产环境：禁用开发默认值、禁止固定口令演示账号
  sed -i "s|^APP_ENV=.*|APP_ENV=production|" "$PREFIX/backend/.env"
  sed -i "s|^PLATFORM_ADMIN_USERNAMES=.*|PLATFORM_ADMIN_USERNAMES=$ADMIN_USER|" "$PREFIX/backend/.env"
  # 服务器单机版使用 SQLite：显式允许（启动仍告警），避免生产安全校验拒绝启动
  sed -i "s|^#ALLOW_SQLITE_IN_PRODUCTION=.*|ALLOW_SQLITE_IN_PRODUCTION=true|" "$PREFIX/backend/.env"
  grep -q '^ALLOW_SQLITE_IN_PRODUCTION=' "$PREFIX/backend/.env" || \
    echo 'ALLOW_SQLITE_IN_PRODUCTION=true' >> "$PREFIX/backend/.env"
  echo "==> 已生成 backend/.env（APP_ENV=production，管理员用户名 $ADMIN_USER，密钥随机）"
fi

# ---------- 6. 迁移 + 初始化账号（随机口令，仅打印一次） ----------
echo "==> 数据库迁移 ..."
(cd "$PREFIX/backend" && ../.venv/bin/alembic upgrade head)

CRED_FILE="$PREFIX/backend/.initial-credentials"
if [ -f "$CRED_FILE" ]; then
  echo "==> 初始账号文件已存在，跳过初始化：$CRED_FILE"
else
  echo "==> 初始化账号（随机口令，仅写入 $CRED_FILE，请立即妥善保存）..."
  (cd "$PREFIX/backend" && \
    SEED_ALLOW_PRODUCTION=YES SEED_RANDOM_PASSWORDS=YES SEED_PLATFORM_ADMIN_USERNAME="$ADMIN_USER" \
    ../.venv/bin/python -m app.db.seed) | tee "$CRED_FILE"
  chmod 600 "$CRED_FILE"
  echo "==> 初始账号已写入：$CRED_FILE"
fi

if grep -q '^LLM_PROVIDER=fake' "$PREFIX/backend/.env" 2>/dev/null; then
  echo "[警告] 当前 LLM_PROVIDER=fake：模型回答为离线模板，请在 backend/.env 配置真实模型供应商后重启服务。"
fi

# ---------- 7. 运行用户 ----------
if [ "$(id -u)" -eq 0 ]; then
  RUN_USER="techyard"
  if ! id "$RUN_USER" >/dev/null 2>&1; then
    echo "==> 创建系统用户 $RUN_USER ..."
    useradd --system --create-home --shell /usr/sbin/nologin "$RUN_USER"
  fi
  chown -R "$RUN_USER:$RUN_USER" "$PREFIX"
  UNIT_DIR="/etc/systemd/system"
else
  RUN_USER="${USER:-$(id -un)}"
  UNIT_DIR="$HOME/.config/systemd/user"
fi
echo "==> 服务运行用户：$RUN_USER"

# ---------- 8. 渲染并安装 systemd unit ----------
mkdir -p "$UNIT_DIR"
sed -e "s|__INSTALL_DIR__|$PREFIX|g" -e "s|__RUN_USER__|$RUN_USER|g" \
  "$SRC_DIR/deploy/native/server/systemd/techyard-api.service" \
  > "$UNIT_DIR/techyard-api.service"
echo "==> 已写入 $UNIT_DIR/techyard-api.service"

# ---------- 9. 启动服务 ----------
if [ "$(id -u)" -eq 0 ]; then
  systemctl daemon-reload
  systemctl enable --now techyard-api
  echo "==> 服务已启动：systemctl status techyard-api"
else
  if systemctl --user daemon-reload 2>/dev/null \
     && systemctl --user enable --now techyard-api 2>/dev/null; then
    echo "==> 用户级服务已启动：systemctl --user status techyard-api"
  else
    echo "[提示] 用户级 systemd 不可用（如未开 linger），请手动执行："
    echo "  loginctl enable-linger $RUN_USER"
    echo "  systemctl --user daemon-reload && systemctl --user enable --now techyard-api"
  fi
fi

echo ""
echo "================================================================"
echo " 安装完成！"
echo "  API 服务：127.0.0.1:8000（systemd 托管，开机自启）"
echo "  前端 dist：$PREFIX/frontend/dist（用 nginx 托管，见 README）"
echo "  初始账号：$CRED_FILE（随机口令，仅首次安装生成）"
echo "  下一步：配置 backend/.env 的真实模型/搜索供应商，然后 systemctl restart techyard-api"
echo "================================================================"
