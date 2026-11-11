# 版本一致性规范（VERSIONING）

本仓库的版本号只有一个事实源：根目录 `pyproject.toml` 的 `[project].version`。
其余 8 处必须与它逐字一致，任何提交前都要校验。

## 1. 提交前强制校验

每次 `git commit` 前必须执行（或让钩子自动执行）：

```bash
.venv/bin/python scripts/check_versions.py
# 期望输出：版本一致：X.Y.Z（9 处）
```

启用提交前钩子（每个克隆只需一次）：

```bash
bash scripts/install_git_hooks.sh
```

钩子会在每次提交前运行同一检查；不一致直接拒绝提交。
CI（`.github/workflows/ci.yml` 的 `Version consistency` 步骤）与
Release（`release.yml` 的 `check_versions.py --tag vX.Y.Z`）使用同一脚本，
本地、CI、发版三处口径完全一致。

## 2. 9 处版本面

| # | 文件 | 字段 |
|---|------|------|
| 1 | `pyproject.toml` | `[project].version`（唯一事实源） |
| 2 | `uv.lock` | `tech-yard-platform` 包版本 |
| 3 | `client/desktop/src-tauri/Cargo.toml` | `[package].version` |
| 4 | `client/desktop/src-tauri/Cargo.lock` | `techyard-desktop` 包版本 |
| 5 | `client/desktop/src-tauri/tauri.conf.json` | `version` |
| 6 | `client/desktop/package.json` | `version` |
| 7 | `frontend/package.json` | `version` |
| 8 | `backend/app/main.py` | `FastAPI(..., version="X.Y.Z")` |
| 9 | `contracts/openapi.yaml` | `info.version` |

## 3. 升级版本

不要手改多处，统一用脚本：

```bash
.venv/bin/python scripts/bump_version.py 3.77.0
.venv/bin/python scripts/check_versions.py
```

脚本会一次性改写上述 9 处；`uv.lock` / `Cargo.lock` 只改本项目的包版本，
不改依赖解析结果。

版本纪律：只递增 `3.x` 小版本；未获明确同意不升 `4.0`。

## 4. 发版纪律（防止客户端落后）

标签名必须是 `vX.Y.Z`，且与 9 处版本面完全一致：

```bash
git tag v3.77.0
git push origin v3.77.0
```

推 `v*` 标签会触发 `release.yml`：先跑 `check_versions.py --tag` 与质量门禁，
再构建 macOS（arm64/Intel）、Windows、Linux 安装包并上传 Release
（含更新包 `.sig` 与 `latest.json`）。

**每次版本升级必须同时打标签并发版。** 历史事故：v3.75.0、v3.76.0 两个版本
只提交未打标签，GitHub 最新 Release 停在 v3.73.6，导致桌面客户端长期无法
通过 updater 升级。客户端 updater 读取
`https://github.com/yimuhub/kjxy-Agent/releases/latest/download/latest.json`，
没有新 Release 就没有新版本。

## 5. 发布前自查清单

- [ ] `scripts/check_versions.py` 输出「版本一致（9 处）」
- [ ] `CHANGELOG.md` 已补对应版本条目
- [ ] 提交信息与标签一致（`vX.Y.Z：...`）
- [ ] 标签已推送，Release 三条平台产物齐全（含 `.sig` 与 `latest.json`）
- [ ] 客户端「关于/侧边栏」显示的版本与网页端一致
