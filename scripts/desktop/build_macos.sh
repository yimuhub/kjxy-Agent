#!/usr/bin/env bash
# macOS 本地完整安装包：前端 + PyInstaller 引擎 + 内置语音模型 + .app + DMG。
#
# 用法：
#   bash scripts/desktop/build_macos.sh              # 全量构建
#   SKIP_FRONTEND=1 SKIP_SIDECAR=1 SKIP_ASR=1 bash scripts/desktop/build_macos.sh
#
# 产物：dist/desktop/科技小院_<版本>_<arch>.dmg（附 .sha256）
# 说明：本机没有 Apple 开发者证书时做 ad-hoc 签名；只适合本机/内测安装。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="$ROOT/.venv/bin/python"
DESKTOP="$ROOT/client/desktop"
TAURI="$DESKTOP/src-tauri"
OUT_DIR="$ROOT/dist/desktop"
ARCH="$(uname -m)"
case "$ARCH" in
  arm64) TRIPLE="aarch64-apple-darwin" ;;
  x86_64) TRIPLE="x86_64-apple-darwin" ;;
  *) echo "不支持的架构：$ARCH" >&2; exit 1 ;;
esac

if [[ ! -x "$PY" ]]; then
  echo "缺少 $PY（请先创建 .venv 并安装依赖）" >&2
  exit 1
fi

VERSION="$("$PY" -c "import tomllib;print(tomllib.load(open('$ROOT/pyproject.toml','rb'))['project']['version'])")"
echo "== 科技小院 macOS $VERSION ($ARCH) =="

# 1) 前端
if [[ "${SKIP_FRONTEND:-0}" != "1" ]]; then
  echo "== [1/5] 构建前端 =="
  cd "$ROOT/frontend"
  if [[ ! -d node_modules ]]; then
    npm ci --no-audit --no-fund
  fi
  npm run build
  mkdir -p "$DESKTOP/dist-web" "$TAURI/frontend-dist"
  rm -rf "$DESKTOP/dist-web"/*
  rm -rf "$TAURI/frontend-dist"/*
  cp -R dist/. "$DESKTOP/dist-web/"
  cp -R dist/. "$TAURI/frontend-dist/"
else
  echo "== 跳过前端构建（复用现有 dist-web / frontend-dist）=="
fi

# 启动页随 Tauri 内置资源发布；不属于站点页面，单独拷入 dist-web（SKIP_FRONTEND 时也要有）
mkdir -p "$DESKTOP/dist-web"
cp -R "$DESKTOP/splash/." "$DESKTOP/dist-web/"
test -f "$DESKTOP/dist-web/desktop-splash.html" || {
  echo "缺少启动页：$DESKTOP/dist-web/desktop-splash.html" >&2
  exit 1
}

# 2) 本地引擎 sidecar
if [[ "${SKIP_SIDECAR:-0}" != "1" ]]; then
  echo "== [2/5] 构建本地引擎 sidecar（PyInstaller）=="
  cd "$ROOT/backend"
  PYTHON="$PY" bash scripts/build_sidecar.sh
  mkdir -p "$TAURI/binaries"
  cp "dist/techyard-sidecar-macos" "$TAURI/binaries/techyard-sidecar-$TRIPLE"
  chmod +x "$TAURI/binaries/techyard-sidecar-$TRIPLE"
else
  echo "== 跳过 sidecar 构建 =="
fi
SIDECAR="$TAURI/binaries/techyard-sidecar-$TRIPLE"
if [[ ! -x "$SIDECAR" ]]; then
  echo "缺少 sidecar：$SIDECAR" >&2
  exit 1
fi
echo "   sidecar arch: $(lipo -archs "$SIDECAR" 2>/dev/null || file "$SIDECAR")"

# 3) 内置轻量语音模型（约 82MB，校验后复用）
if [[ "${SKIP_ASR:-0}" != "1" ]]; then
  echo "== [3/5] 下载并校验内置语音模型 =="
  "$PY" "$ROOT/scripts/desktop/download_asr_model.py" --dest "$TAURI/asr-model"
else
  echo "== 跳过语音模型下载 =="
fi

# 4) Tauri 构建 .app（不先出 DMG，签名后再封装）
echo "== [4/5] 构建 .app =="
cd "$DESKTOP"
if [[ ! -d node_modules ]]; then
  npm install --no-audit --no-fund
fi
npx tauri build --bundles app --config '{"bundle":{"createUpdaterArtifacts":false}}'

APP="$TAURI/target/release/bundle/macos/科技小院.app"
if [[ ! -d "$APP" ]]; then
  echo "未找到 .app：$APP" >&2
  exit 1
fi

# Tauri 打包阶段已按 tauri.conf 的 signingIdentity "-" 完成整包 ad-hoc 签名，
# 并给主程序与 sidecar 写入同一份 Entitlements.plist（含 hardened runtime）。
# 不要再 codesign --deep 重签：会丢掉 runtime/entitlements，还会掩盖 CI 与本地差异。
echo "== 校验 ad-hoc 签名与 sidecar 权限 =="
codesign --verify --deep --strict --verbose=2 "$APP"
SIDECAR_ENT="$(codesign -d --entitlements :- "$APP/Contents/MacOS/techyard-sidecar" 2>/dev/null)"
case "$SIDECAR_ENT" in
  *disable-library-validation*) ;;
  *)
    echo "sidecar 缺少 com.apple.security.cs.disable-library-validation，硬运行时下会拒绝加载 Python 运行库" >&2
    exit 1
    ;;
esac

# 5) 封装 DMG（拖入 Applications）
echo "== [5/5] 封装 DMG =="
mkdir -p "$OUT_DIR"
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/techyard-dmg.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
DMG="$OUT_DIR/科技小院_${VERSION}_${ARCH}.dmg"
rm -f "$DMG"
hdiutil create -volname "科技小院" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
( cd "$OUT_DIR" && shasum -a 256 "$(basename "$DMG")" > "$(basename "$DMG").sha256" )

echo
echo "✅ 构建完成"
echo "   APP: $APP"
echo "   DMG: $DMG"
echo "   大小: $(du -h "$DMG" | awk '{print $1}')"
echo "   校验: $(cat "$DMG.sha256")"
