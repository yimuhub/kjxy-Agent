# 科技小院桌面壳（Tauri 2）

本地引擎 sidecar + splash + 级联退出 + **应用内自动升级**。本文档聚焦升级链路与发布操作；
构建/内测打包见 `.github/workflows/client.yml`（手动触发），正式发布见 `.github/workflows/release.yml`（推 `v*` 标签触发）。

## 升级链路（GitHub Releases 通道）

```
git tag vX.Y.Z && git push origin vX.Y.Z
        │
        ▼
release.yml：三平台矩阵构建 → tauri build 用私钥对更新包签名（.sig）
        → 建 GitHub Release，上传安装包 + .sig + 各平台更新清单（合并 latest.json）
        │
        ▼
客户端（tauri-plugin-updater）：
  · 启动 30 秒后静默检查（不弹窗，失败只打日志）
  · 清单地址：https://github.com/yimuhub/kjxy-Agent/releases/latest/download/latest.json
  · 发现新版：广播 app:update-available 事件 + 向所有窗口注入底部「有新版本」提示条
  · 用户点「检查更新 / 立即升级」（dist-web/index.html 版本条，或正式前端设置页）
      → check_for_updates（手动检查） → download_and_install（下载+校验签名+安装+自动重启）
```

Rust 侧实现：`src-tauri/src/lib.rs`（`spawn_update_watcher` / `check_for_updates` / `download_and_install`）。
下载进度事件：`app:update-downloaded-bytes`（累计字节数）；失败：`app:update-error` + 原生对话框。

### 前端接入（正式前端构建产物）

壳内任意页面（含引擎托管的 http://127.0.0.1 页面）若已启用全局 Tauri API
（`tauri.conf.json` 已开 `app.withGlobalTauri`）：

```js
const info = await window.__TAURI__.core.invoke('check_for_updates'); // null=已最新
await window.__TAURI__.core.invoke('download_and_install');           // 成功后应用自动重启
await window.__TAURI__.core.invoke('get_app_version');                // 当前版本号
```

占位页 `dist-web/index.html` 底部自带版本条示例，也可直接 `listen('app:update-available')` —— 见该文件脚本段。

## 升级密钥（必读）

- **私钥绝不进仓库。** 本机备份位置：`Documents/Hermes/kjxy-work/tauri_updater_private_key`（权限 600）。
- 公钥已写入 `src-tauri/tauri.conf.json` → `plugins.updater.pubkey`，客户端用它验签，伪造的更新包会被拒绝。
- 开发固定密码：`kjxy-dev-key`。**正式对外发版前必须重新生成密钥对并更换密码**：

```bash
cd client/desktop
npx @tauri-apps/cli signer generate \
  -w ~/Documents/Hermes/kjxy-work/tauri_updater_private_key -p "<新密码>" --ci
# 终端会打印 Public signature key → 替换 tauri.conf.json 的 plugins.updater.pubkey
```

注意：换公钥后，只有装了新公钥的客户端版本才能收到更新（旧版本会停在旧版）。

## GitHub Actions Secrets（仓库 Settings → Secrets and variables → Actions）

| Secret | 值 |
| --- | --- |
| `TAURI_SIGNING_PRIVATE_KEY` | 私钥文件完整内容（含首尾 `-----BEGIN/END` 行） |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | `kjxy-dev-key`（与上面的开发密码一致；换密码后同步改） |
| `GITHUB_TOKEN` | 不用配，工作流自带（`permissions: contents: write` 已声明） |

## 发版步骤

1. 同步升版本号三处：`src-tauri/tauri.conf.json`、`src-tauri/Cargo.toml`、`package.json`（必须一致，updater 按版本号判断新旧）。
2. `git tag vX.Y.Z && git push origin vX.Y.Z`（打标签是人工动作，本仓库流水线不执行 git）。
3. 等 release.yml 三平台全绿 → GitHub Release 出现安装包 + `latest.json`。
4. 灰度验证：老版本客户端 30 秒后应出现「有新版本」提示条，点击可完成升级重启。

## 已知边界

- 更新清单只拉 `releases/latest`：预发布（pre-release）不会推给正式版客户端。
- Linux 自动升级要求用户以 AppImage 方式运行；deb 安装的用户升级走重新下载安装包。
- macOS 未做公证（ad-hoc 签名），公网分发需补 Apple 证书后再放开。
