# 客户端矩阵（六端"点开就用"）

> 架构决策（2026-10-17，借鉴 agency-orchestrator 的"四层同一引擎"）：
> **桌面=本地引擎（sidecar，数据不出本机）**；**移动/鸿蒙=云端壳（连试点服务器）**。
> 一套 FastAPI 引擎 + 一套 Vue 前端，壳只做"拉起/指向/生命周期"四件事。

## 现状矩阵

| 端 | 形态 | 现在能产什么 | 差什么 | 怎么产 |
|---|---|---|---|---|
| Windows | Tauri 2 + PyInstaller sidecar | CI 手动触发产**未签名 exe**（内测：右键→仍要运行） | 代码签名证书（EV/OV，杀软信誉） | Actions → Run workflow `Client Build`/desktop |
| macOS | 同上 | CI 产 **ad-hoc 签名** .app（本机可跑；分发需用户 `xattr -dr` 或公证） | Apple 开发者证书 + 公证（$99/年） | 同上；参考 `docs/SIGNING`（待有证书后补） |
| Linux | 同上 | CI 产 ELF 可执行 + deb/rpm（bundle 阶段放开后） | 无（内测零资质） | 同上 |
| Android | Tauri 2 云端壳 | **debug APK 直装**（零资质） | 国内商店上架=软著+实名（上架时才需要） | `client/android/README.md` |
| iOS | Tauri 2 云端壳 | 模拟器演示；Archive 需签名 | **Apple 账号 $99/年**（TestFlight/真机） | `client/iOS/README.md` |
| 鸿蒙 NEXT | ArkWeb 原生壳 | DevEco 调试 HAP（个人实名免费） | 上架 AppGallery 需加原生能力过 4.2 | `client/harmony/README.md` |

## 桌面本地引擎要点（client/desktop）

- 引擎= `backend/standalone.py`（PyInstaller 单文件），SQLite + inline 任务 + 本地文件存储，
  数据全在系统标准数据目录（macOS `~/Library/Application Support/科技小院`，
  Windows `%LOCALAPPDATA%`，Linux `$XDG_DATA_HOME`）；只监听 `127.0.0.1:18700-18710`。
- 首启自动：迁移 → seed 演示空间 → 生成随机管理员口令写 `desktop-login.txt`（splash 指路）。
- JWT 密钥每桌面实例独立生成（`secret.key`），与服务器部署完全隔离。
- 关窗级联杀 sidecar；再开复用健康引擎不重拉；启动失败给原生对话框+原因+下一步（失败即教学）。
- `client.json` 可切 `{"mode":"remote","url":"https://试点"}`——桌面也能连服务器（数据回云端）。

## 服务器地址配置

前端构建期 `VITE_API_BASE`（云端壳）；桌面壳由 sidecar 本机提供，无需服务器。
试点上线后把 `https://REPLACE_PILOT_HOST` 占位统一替换（android/harmony 各一处）。

## 升级策略（AO 模式）

安装包升级=**备份旧版→staging→原子替换→失败回滚**；数据目录与程序目录分离，
升级永不触碰用户数据；数据库版本随 app 打包，启动时 alembic 自动升级（只向前）。

## 未验证项（诚实清单）

- Rust 启动器/CI 构建流**未经本地编译**（开发机无 Rust 工具链），首次以 CI 实跑为准；
- PyInstaller 对 alembic/fastapi 的隐藏依赖收集需 CI 首跑校准（脚本已列坑位注释）；
- 安卓/鸿蒙壳未过真机；iOS 无账号未 Archive。以上都在 CI 首跑与真机迭代中收敛。
