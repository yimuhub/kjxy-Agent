#!/usr/bin/env bash
# 桌面本地引擎 sidecar 打包：PyInstaller onefile → dist/techyard-sidecar-<os>
#
# 用法（CI 三平台各自执行，见 .github/workflows/client.yml）：
#   cd backend && bash scripts/build_sidecar.sh
#
# 前置：已 pip install -e .（项目依赖）+ pip install pyinstaller。
#
# ── PyInstaller 隐藏导入坑（全部实测在 PyInstaller 6.x / py3.12 需要显式处理）──
# 1. app.* 是业务包且大量"字符串动态导入"（migrations/env.py import
#    app.db.models_registry、seed 里函数内 import、任务 handler 注册），
#    静态分析追不全 → --collect-all app 整体收集（体积换正确性，桌面版可接受）。
# 2. alembic：env.py 在运行时按路径加载 migrations/，且 alembic 自身用
#    插件注册（alembic.dialects entry_points）→ --collect-all alembic。
# 3. alembic.ini 与 migrations/ 不是 Python 模块，必须 --add-data 进包；
#    standalone.py 的 _resource_root() 约定它们位于包根（=sys._MEIPASS），
#    目标名 "." 与 "migrations" 不能改，改了 standalone 找不到迁移。
# 4. pydantic v2 / pydantic-core 是编译扩展 + Rust 核，偶发漏收 →
#    --collect-all pydantic / pydantic_core；pydantic_settings 依赖的
#    dotenv（python-dotenv）同理。
# 5. uvicorn 全部走字符串动态导入（standard/asyncio/lifespan/http h11）→
#    --collect-all uvicorn。
# 6. SQLAlchemy 2 的 dialect 注册走 entry_points：sqlite 必须显式带上
#    sqlalchemy.dialects.sqlite；psycopg 驱动一并带上（用户可切 DATABASE_URL）。
# 7. cryptography（passlib[bcrypt]/JWT 链路中间接依赖）有二进制 .so，
#    collect-all 防漏。email-validator 是 pydantic EmailStr 的运行期导入。
# 8. sherpa-onnx（v3.30 本机语音转写）：sherpa_onnx 是 ctypes 包，原生库在
#    sherpa_onnx_core 里，靠动态查找加载 → 两个包都 collect-all；
#    模型文件**不打进安装包**（首次使用按需下载到用户数据目录）。
# ────────────────────────────────────────────────────────────────────────────
set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_DIR="$(dirname "$BACKEND_DIR")"
cd "$BACKEND_DIR"

case "$(uname -s)" in
  Darwin) OS_TAG="macos" ;;
  Linux)  OS_TAG="linux" ;;
  MINGW*|MSYS*|CYGWIN*) OS_TAG="windows" ;;
  *) echo "unsupported os: $(uname -s)" >&2; exit 1 ;;
esac
NAME="techyard-sidecar-${OS_TAG}"

PY="${PYTHON:-python3}"
if ! "$PY" -c "import PyInstaller" >/dev/null 2>&1; then
  "$PY" -m pip install --quiet pyinstaller
fi

# 分隔符：mac/linux 用 ':'，windows 用 ';'
SEP=":"; [ "$OS_TAG" = "windows" ] && SEP=";"

rm -rf "build/${NAME}" "dist/${NAME}" "dist/${NAME}.spec" "${NAME}.spec"

"$PY" -m PyInstaller \
  --onefile \
  --name "$NAME" \
  --noconfirm \
  --console \
  --paths "$BACKEND_DIR" \
  --add-data "alembic.ini${SEP}." \
  --add-data "migrations${SEP}migrations" \
  --collect-all app \
  --collect-all alembic \
  --collect-all pydantic \
  --collect-all pydantic_core \
  --collect-all pydantic_settings \
  --collect-all dotenv \
  --collect-all uvicorn \
  --collect-all sqlalchemy \
  --collect-all cryptography \
  --collect-all celery \
  --collect-all kombu \
  --collect-all vine \
  --collect-all billiard \
  --collect-submodules email_validator \
  --collect-all sherpa_onnx \
  --collect-all sherpa_onnx_core \
  --hidden-import app.main \
  --hidden-import app.db.models_registry \
  --hidden-import app.db.seed \
  --hidden-import sqlalchemy.dialects.sqlite \
  --hidden-import sqlalchemy.dialects.postgresql \
  standalone.py

echo
echo "✅ 产出：dist/${NAME}$( [ "$OS_TAG" = windows ] && echo .exe )"
echo "   下一步：拷到 client/desktop/src-tauri/binaries/ 并按 Rust target triple 重命名"
echo "   （techyard-sidecar-<triple>，见 client/desktop/README.md）"
