#!/usr/bin/env bash
# 安装包冒烟：挂载 DMG → 复制 .app → 用独立数据目录启动 → 探健康 → 静默登录 → 关停。
#
# 用法：bash scripts/desktop/smoke_macos.sh [path/to/xxx.dmg]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DMG="${1:-$(ls -t "$ROOT"/dist/desktop/科技小院_*.dmg 2>/dev/null | head -1)}"
if [[ -z "${DMG:-}" || ! -f "$DMG" ]]; then
  echo "未找到 DMG，请先运行 build_macos.sh" >&2
  exit 1
fi

# 端口预检：若有其它实例占着引擎端口，新实例会复用旧引擎，导致令牌/数据目录错配
for p in $(seq 18700 18710); do
  if lsof -ti "tcp:$p" >/dev/null 2>&1; then
    echo "引擎端口 $p 已被占用（可能还有一个科技小院在运行），请先退出再跑冒烟" >&2
    exit 1
  fi
done

WORK="$(mktemp -d "${TMPDIR:-/tmp}/techyard-smoke.XXXXXX")"
# macOS 的 /var、/tmp 都是符号链接：Tauri 侧车解析拒绝含符号链接的路径
WORK="$(cd "$WORK" && pwd -P)"
MNT="$WORK/mnt"
DATA="$WORK/data"
mkdir -p "$MNT" "$DATA"
APP_PID=""
PORT=""

cleanup() {
  if [[ -n "$APP_PID" ]] && kill -0 "$APP_PID" 2>/dev/null; then
    kill -TERM "$APP_PID" 2>/dev/null || true
    sleep 1
    kill -KILL "$APP_PID" 2>/dev/null || true
  fi
  if [[ -n "$PORT" ]]; then
    local side_pid
    side_pid="$(lsof -ti "tcp:$PORT" 2>/dev/null || true)"
    [[ -n "$side_pid" ]] && kill -TERM $side_pid 2>/dev/null || true
  fi
  hdiutil detach "$MNT" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

echo "== 挂载 DMG =="
hdiutil attach "$DMG" -mountpoint "$MNT" -nobrowse -readonly >/dev/null
APP="$MNT/科技小院.app"
[[ -d "$APP" ]] || { echo "DMG 内没有科技小院.app" >&2; exit 1; }

echo "== 校验签名与结构 =="
codesign --verify --deep --strict "$APP"
find "$APP/Contents/MacOS" -maxdepth 1 -type f -perm -111 | grep -q . || { echo "缺少可执行文件" >&2; exit 1; }
test -d "$APP/Contents/Resources/frontend-dist" || { echo "缺少前端资源" >&2; exit 1; }
test -d "$APP/Contents/Resources/asr-model" || { echo "缺少内置语音模型" >&2; exit 1; }

echo "== 复制到临时目录并启动（独立数据目录）=="
cp -R "$APP" "$WORK/科技小院.app"
xattr -dr com.apple.quarantine "$WORK/科技小院.app" 2>/dev/null || true

echo "== 校验办公文档渲染资源（pptx/docx 模板随包）=="
SIDECAR="$WORK/科技小院.app/Contents/MacOS/techyard-sidecar"
[[ -x "$SIDECAR" ]] || { echo "缺少 sidecar 可执行文件" >&2; exit 1; }
SELFCHECK_LOG="$WORK/docs-selfcheck.log"
if ! "$SIDECAR" --docs-selfcheck >"$SELFCHECK_LOG" 2>&1; then
  echo "❌ pptx/docx 自检失败（模板资源可能漏打包）；日志：" >&2
  tail -20 "$SELFCHECK_LOG" >&2
  exit 1
fi
grep -q "selfcheck.pptx ok" "$SELFCHECK_LOG" || { echo "pptx 自检未产出文件" >&2; exit 1; }
grep -q "selfcheck.docx ok" "$SELFCHECK_LOG" || { echo "docx 自检未产出文件" >&2; exit 1; }
echo "   pptx/docx 生成成功"
BIN="$(find "$WORK/科技小院.app/Contents/MacOS" -maxdepth 1 -type f -perm -111 ! -name 'techyard-sidecar*' | head -1)"
if [[ -z "$BIN" ]]; then
  echo "找不到应用主程序（Contents/MacOS）" >&2
  exit 1
fi
TECHYARD_DATA_DIR="$DATA" "$BIN" >"$WORK/app.log" 2>&1 &
APP_PID=$!

echo "== 等待引擎就绪（18700-18710）=="
for _ in $(seq 1 120); do
  for p in $(seq 18700 18710); do
    if curl -fsS --max-time 1 "http://127.0.0.1:$p/health/ready" >/dev/null 2>&1; then
      PORT="$p"
      break 2
    fi
  done
  sleep 1
done
if [[ -z "$PORT" ]]; then
  echo "❌ 引擎 120 秒内未就绪；日志尾部：" >&2
  tail -30 "$WORK/app.log" >&2 || true
  exit 1
fi
echo "   引擎端口：$PORT"

echo "== 静默登录（壳令牌）=="
TOKEN_FILE="$DATA/desktop-bootstrap.token"
[[ -f "$TOKEN_FILE" ]] || { echo "缺少引导令牌文件" >&2; exit 1; }
TOKEN="$(cat "$TOKEN_FILE")"
RESP="$(curl -fsS --max-time 10 -X POST "http://127.0.0.1:$PORT/api/v1/auth/desktop-bootstrap" \
  -H 'Content-Type: application/json' -d "{\"token\":\"$TOKEN\"}")"
echo "$RESP" | grep -q '"access_token"' || { echo "静默登录失败：$RESP" >&2; exit 1; }
echo "   已签发登录态"

echo "== 校验内置办公技能已铺设 =="
ACCESS="$(printf '%s' "$RESP" | /usr/bin/python3 -c 'import json,sys;print(json.load(sys.stdin)["data"]["access_token"])')"
SKILLS="$(curl -fsS --max-time 10 -X POST "http://127.0.0.1:$PORT/api/v1/market/items/search" \
  -H 'Content-Type: application/json' -H "Authorization: Bearer $ACCESS" \
  -d '{"source":"builtin","item_type":"skill","limit":50}')"
OFFICE_COUNT="$(printf '%s' "$SKILLS" | /usr/bin/python3 -c '
import json, sys
data = json.load(sys.stdin)["data"]["items"]
codes = [str(item.get("code") or "") for item in data]
assert "office_excel_table" in codes, codes
print(sum(1 for code in codes if code.startswith("office_")))
')" || { echo "办公技能未铺设：$SKILLS" >&2; exit 1; }
echo "   办公技能：$OFFICE_COUNT 项"

echo "== 校验内置语音模型已复制 =="
test -s "$DATA/models/asr/paraformer-zh-small/model.int8.onnx" || {
  echo "语音模型未复制到数据目录" >&2
  exit 1
}
test -f "$DATA/models/asr/paraformer-zh-small/.verified.json" || {
  echo "语音模型校验标记缺失" >&2
  exit 1
}
echo "   语音模型：$(du -h "$DATA/models/asr/paraformer-zh-small/model.int8.onnx" | awk '{print $1}')"

echo "== 优雅退出 =="
osascript -e 'tell application "科技小院" to quit' >/dev/null 2>&1 || kill -TERM "$APP_PID" 2>/dev/null || true
for _ in $(seq 1 15); do
  kill -0 "$APP_PID" 2>/dev/null || break
  sleep 1
done
if [[ -n "$(lsof -ti "tcp:$PORT" 2>/dev/null || true)" ]]; then
  echo "❌ 退出后引擎仍在监听 $PORT" >&2
  exit 1
fi
echo "   引擎已随应用退出"

echo
echo "✅ 冒烟通过：$DMG"
echo "   引擎日志：$DATA/logs/engine.log（已随临时目录清理）"
