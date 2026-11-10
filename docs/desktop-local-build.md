# 桌面客户端本地打包教程

> 目标：在自己机器上打出一个能安装、能启动、自带本地引擎的完整安装包，不依赖 GitHub Actions。
> 脚本入口：`scripts/desktop/build_macos.sh`（macOS）、`scripts/desktop/smoke_macos.sh`（验收）。
> 三平台 CI 发布见 `.github/workflows/release.yml`，本文只讲本地。

## 1. 版本纪律：先对齐，再打包

包里的版本号写死在九处，任何一处没改都会导致 CI `verify` 失败（本地不会报错，但安装后「关于」与更新通道会对不上）。

```bash
# 查看当前版本是否九处一致
.venv/bin/python scripts/check_versions.py
# 期望输出：版本一致：3.73.4（9 处）

# 打正式标签前校验标签与九处版本相同
.venv/bin/python scripts/check_versions.py --tag v3.73.4
```

九处：`pyproject.toml`、`uv.lock`、`contracts/openapi.yaml`、`backend/app/main.py`、
`frontend/package.json`、`client/desktop/package.json`、`src-tauri/Cargo.toml`、
`src-tauri/Cargo.lock`、`src-tauri/tauri.conf.json`。

## 2. 环境准备（只需一次）

| 依赖 | 版本 | 检查命令 | 说明 |
|---|---|---|---|
| Xcode 命令行工具 | 任意较新版 | `xcode-select -p` | 提供 `codesign`、`hdiutil`、编译工具链 |
| Rust | stable | `cargo --version` | Tauri 编译 |
| Node.js | ≥ 22 | `node --version` | 前端与 Tauri CLI |
| Python | 3.12 | `.venv/bin/python -V` | 本地引擎 sidecar（PyInstaller） |

```bash
cd <仓库根目录>

# Python 虚拟环境与后端依赖（含 PyInstaller 构建所需的 dev 依赖）
uv venv --python 3.12
uv sync --frozen --extra dev

# 前端与桌面壳依赖
cd frontend && npm ci && cd ..
cd client/desktop && npm ci && cd ../..
```

产物目录约定：

| 路径 | 内容 |
|---|---|
| `frontend/dist` | 前端构建产物 |
| `client/desktop/dist-web` | 桌面壳加载的网页资源 |
| `client/desktop/src-tauri/frontend-dist` | 引擎托管的网页资源（`build.rs` 会校验非空） |
| `client/desktop/src-tauri/binaries/` | 按目标三元组命名的 sidecar |
| `client/desktop/src-tauri/asr-model/` | 内置语音模型（约 82MB） |
| `dist/desktop/` | 最终 dmg 与 `.sha256` |

## 3. macOS 一键打包

```bash
bash scripts/desktop/build_macos.sh
```

脚本按顺序做五件事：

1. **构建前端**：`npm run build`，把 `dist/` 同步到 `dist-web` 与 `frontend-dist`；
   随后把 `client/desktop/splash/`（启动页）拷进 `dist-web`——它是 Tauri 内置资源，不属于站点页面。
2. **构建本地引擎**：`backend/scripts/build_sidecar.sh` 用 PyInstaller 打单文件引擎，
   再按本机架构复制成 `binaries/techyard-sidecar-aarch64-apple-darwin`（Intel 机为 `x86_64-apple-darwin`）。
3. **下载内置语音模型**：校验哈希，已存在且校验通过则跳过。
4. **构建 .app**：`npx tauri build --bundles app`。`signingIdentity: "-"` 会在这一步
   给主程序与 sidecar 一起做 ad-hoc 签名，并写入 `Entitlements.plist`（麦克风权限、
   `disable-library-validation` 等）。
5. **校验 + 封装 DMG**：不再二次 `codesign --deep`（否则会丢掉 hardened runtime 与权限，
   掩盖 CI 才有的 library validation 问题）；校验整包签名与 sidecar 权限后
   `hdiutil create` 出 UDZO dmg → 写 `.sha256`。

产物：

```
dist/desktop/科技小院_<版本>_arm64.dmg
dist/desktop/科技小院_<版本>_arm64.dmg.sha256
client/desktop/src-tauri/target/release/bundle/macos/科技小院.app
```

增量重打（前端或引擎没改动时省时间）：

```bash
# 只重打 .app 与 dmg
SKIP_FRONTEND=1 SKIP_SIDECAR=1 SKIP_ASR=1 bash scripts/desktop/build_macos.sh
# 前端没改、引擎改了
SKIP_FRONTEND=1 SKIP_ASR=1 bash scripts/desktop/build_macos.sh
```

> 注意：跳过 sidecar 时脚本会直接复用 `binaries/` 里已有的文件，架构不对会在启动时才暴露，
> 换机器或换架构打包务必重跑 sidecar。

## 4. 为什么必须整包签名

macOS 上的 `.app` 是「目录 + 资源 + 可执行文件」。只由编译器在二进制上留临时签名，
而应用包本身没有 `Contents/_CodeSignature` 时：

- `codesign --verify --deep --strict` 报 `code has no resources but signature indicates they must be present`
- 用户从浏览器/下载器拿到的包带 `com.apple.quarantine`，Gatekeeper 校验失败，直接提示
  **「已损坏，无法打开。你应该将它移到废纸篓。」**

v3.73.3 就是这么出的问题：本地脚本有签名步骤，CI 流水线没有，于是同一份代码本机出的包正常、
CI 出的包在别人机器上报损坏。

现在有两道保障：

1. `client/desktop/src-tauri/tauri.conf.json` 里 `bundle.macOS.signingIdentity` 设为 `"-"`，
   任何 `tauri build` 都会在打包阶段对整包做 ad-hoc 签名并绑定 Info.plist。
2. 发布流水线把 Release 先建为草稿，在 macOS runner 上真挂载 dmg 跑
   `codesign --verify --deep --strict`，通过后才生成 `latest.json` 并公开。

`Entitlements.plist` 同时承担两件事：麦克风权限，以及 `disable-library-validation`
（必要时加 `allow-unsigned-executable-memory`）。后者是因为 PyInstaller onefile 解压出的
Python 运行库/扩展是 ad-hoc 签名，而 Tauri 的 hardened runtime 默认只允许加载同 Team ID 的库；
缺这项权限时引擎会立即退出，日志为
`Failed to load Python shared library … different Team IDs`。发布门禁会对 sidecar 的
entitlements 做断言，防止再次漏签。

打包后自查：

```bash
APP="client/desktop/src-tauri/target/release/bundle/macos/科技小院.app"
codesign --verify --deep --strict --verbose=2 "$APP"
codesign -dv "$APP" 2>&1 | grep -E "Identifier|Signature"
# 期望：valid on disk / satisfies its Designated Requirement
#       Identifier=cn.techyard.desktop / Signature=adhoc
```

ad-hoc 签名能保证「不报损坏」，但**没有公证**，别人首次打开仍会看到
「无法验证开发者」，需要右键 →「打开」，或去「系统设置 → 隐私与安全性」点「仍要打开」。
要做到纯双击直达，需要 Apple Developer ID 证书 + notarytool 公证，属于账号资质问题，不是打包脚本问题。

## 5. 冒烟验收（必跑）

```bash
bash scripts/desktop/smoke_macos.sh                          # 默认取 dist/desktop 最新 dmg
bash scripts/desktop/smoke_macos.sh /path/to/xxx.dmg         # 指定包
```

脚本做的是「真装真跑」，不是静态检查：

| 检查项 | 判定方式 |
|---|---|
| 端口干净 | `18700-18710` 全空闲，否则可能复用旧实例导致令牌错配 |
| 包签名 | `codesign --verify --deep --strict` |
| 资源完整 | `Resources/frontend-dist`、`Resources/asr-model` 存在 |
| 办公文档 | `sidecar --docs-selfcheck` 真生成 pptx/docx |
| 引擎就绪 | 轮询 `http://127.0.0.1:<port>/health/ready`，最多 120 秒 |
| 静默登录 | 用 `desktop-bootstrap.token` 换 `access_token` |
| 技能铺设 | 内置 `office_*` 技能数量 > 0 |
| 语音模型 | 已复制到数据目录且有 `.verified.json` |
| 优雅退出 | 退出后引擎端口必须释放 |

全绿输出：

```
✅ 冒烟通过：/path/to/科技小院_x.y.z_arm64.dmg
```

## 6. Windows / Linux 本地打包

Tauri 不能交叉编译安装包，`x86_64-pc-windows-msvc` 必须在 Windows 上打，
deb/AppImage 在 Linux 上打。前端与 sidecar 的拼接步骤与 macOS 相同，只是目标三元组与 bundle 类型不同。

### Windows（PowerShell，管理员不需要）

```powershell
cd frontend; npm ci; npm run build
New-Item -ItemType Directory -Force ..\client\desktop\dist-web, ..\client\desktop\src-tauri\frontend-dist
Copy-Item dist\* ..\client\desktop\dist-web -Recurse -Force
Copy-Item dist\* ..\client\desktop\src-tauri\frontend-dist -Recurse -Force
cd ..\backend
$env:PYTHON = "..\.venv\Scripts\python.exe"
bash scripts/build_sidecar.sh
Copy-Item dist\techyard-sidecar-windows.exe ..\client\desktop\src-tauri\binaries\techyard-sidecar-x86_64-pc-windows-msvc.exe -Force
cd ..\client\desktop
npx tauri build --target x86_64-pc-windows-msvc --bundles nsis
```

产物：`client/desktop/src-tauri/target/x86_64-pc-windows-msvc/release/bundle/nsis/*.exe`。

安装器内嵌 WebView2 离线安装器（`bundle.windows.webviewInstallMode = offlineInstaller`），
装包过程不需要联网，体积约 290MB。未做 Authenticode 签名，SmartScreen 会提示「更多信息 → 仍要运行」。

### Linux（Ubuntu 22.04 为例）

```bash
sudo apt-get install -y libwebkit2gtk-4.1-dev build-essential curl wget file \
  libxdo-dev libssl-dev libayatana-appindicator3-dev librsvg2-dev

cd frontend && npm ci && npm run build
mkdir -p ../client/desktop/dist-web ../client/desktop/src-tauri/frontend-dist
cp -r dist/* ../client/desktop/dist-web/
cp -r dist/* ../client/desktop/src-tauri/frontend-dist/
cd ../backend && PYTHON=../.venv/bin/python bash scripts/build_sidecar.sh
cp dist/techyard-sidecar-linux ../client/desktop/src-tauri/binaries/techyard-sidecar-x86_64-unknown-linux-gnu
cd ../client/desktop
npx tauri build --target x86_64-unknown-linux-gnu --bundles deb,appimage
```

产物：`bundle/deb/*.deb`（双击安装）与 `bundle/appimage/*.AppImage`（免安装双击运行）。

## 7. 发布前检查清单

- [ ] `check_versions.py` 九处一致
- [ ] 后端门禁：`cd backend && uv run ruff check app tests && uv run ruff format --check app tests && uv run mypy app && uv run pytest tests -q`
- [ ] 契约无漂移：`uv run python backend/scripts/generate_openapi.py && git diff --exit-code contracts/openapi.yaml`
- [ ] 前端门禁：`cd frontend && npm run lint && npm run test:unit && npm run build`
- [ ] `build_macos.sh` 全量跑通
- [ ] `smoke_macos.sh` 全绿
- [ ] dmg 的 `.sha256` 已生成，并与 `shasum -a 256` 结果一致
- [ ] 若要发版：`git tag -a vX.Y.Z` 后推送，由 Release 流水线三平台出包（不手工上传安装包）

## 8. 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| 「已损坏，无法打开」 | 应用包没有整体签名，只有二进制临时签名 | 用本脚本重打；或对已装应用临时绕行：`xattr -dr com.apple.quarantine "/Applications/科技小院.app"` |
| 「无法验证开发者」 | ad-hoc 签名、未公证 | 右键 →「打开」一次，或「系统设置 → 隐私与安全性 → 仍要打开」 |
| 安装后白屏 | `frontend-dist` 没同步或为空 | 重跑第 1 步并确认 `src-tauri/frontend-dist/index.html` 存在 |
| 启动页一直白（无步骤） | 旧版启动页是顶层 `data:` URL，WKWebView 不放行；或 `dist-web` 缺启动页 | v3.73.5 起为随包 `desktop-splash.html`；确认 `dist-web/desktop-splash.html` 与 `.js` 存在（脚本会自动拷入） |
| 启动页停在「解压引擎」或超过 2 分钟 | 首启被 Gatekeeper/安全软件扫描，慢机器 2–3 分钟属正常 | v3.73.5 起最多等 10 分钟并持续显示阶段；可点「在 Finder 中显示日志」确认，不要直接强杀 |
| 启动页提示「引擎进程已退出」，日志含 `different Team IDs` | sidecar 在 hardened runtime 下加载 ad-hoc Python 运行库被 library validation 拒绝 | v3.73.6 起 `Entitlements.plist` 带 `disable-library-validation`；确认 sidecar 权限后重打，详情见第 4 节 |
| 启动即退 | 引擎起不来 | 看 `~/Library/Application Support/科技小院/logs/engine.log` 尾部 |
| 提示端口被占用 | 已有实例在跑（冒烟要求 18700-18710 全空） | `lsof -nP -iTCP:18700-18710 -sTCP:LISTEN` 后退出旧实例 |
| sidecar 架构不符（Intel 机打 arm64 包等） | 复用了别的架构的 `binaries/` | `lipo -archs <sidecar>` 与 `uname -m` 对照后重跑第 2 步 |
| 语音模型下载失败 | 网络受限或哈希校验不通过 | 重跑 `download_asr_model.py`；赶时间可 `SKIP_ASR=1`（语音能力降级） |
| `--docs-selfcheck` 失败 | pptx/docx 模板资源漏进包 | 重跑 sidecar 构建，确认 `--add-data` 资源未丢 |
| dmg 打不开或校验失败 | 封装中断或磁盘空间不足 | `hdiutil verify <dmg>`；清理 `dist/desktop` 后重跑第 5 步 |

## 9. 运行时落点

| 项 | 位置 |
|---|---|
| 数据目录（macOS） | `~/Library/Application Support/科技小院` |
| 引擎日志 | `<数据目录>/logs/engine.log` |
| 引擎监听 | 仅 `127.0.0.1:18700-18710`（自动挑空闲端口） |
| 首次登录 | 无预置账号：全新安装直接进初始化向导，自行创建超级管理员 |
| 初始化状态 | `setup_state` 表（迁移会为存量库补 `legacy` 行）；有该行或库中已有用户即视为已初始化 |
| JWT 密钥 | 数据目录下 `secret.key`，每实例独立，勿外传 |

打包后直接双击会复用真实数据目录；想模拟「全新用户首启」而不动现有数据，用独立目录启动：

```bash
TECHYARD_DATA_DIR="$(mktemp -d)" \
  "client/desktop/src-tauri/target/release/bundle/macos/科技小院.app/Contents/MacOS/techyard-desktop"
```

`smoke_macos.sh` 内部就是这么做的，验收完临时目录会清理。
注意：新版本首次连旧数据目录会自动跑迁移，旧版本随后将无法再打开该库。

本地打包产物仅用于本机与内测；对外分发请走 `v*` 标签触发的 Release 流水线，
由 CI 统一出三平台包、签名与 `latest.json` 更新清单。
