# 部署版本总览

本仓库只有**一份代码**，下面 5 种"版本"只是**部署方式不同**，
差异全部收敛在 `deploy/` 目录里，业务代码（`backend/`、`frontend/`）完全共用。
修 bug 只需改一处，所有版本自动生效。

| 版本 | 依赖 | 适合谁 | 一句话启动 |
|---|---|---|---|
| Docker 版 | Docker + Docker Compose | 任何系统，最省心 | `make up && make migrate && make seed-dev` |
| Mac 原生版 | Python 3.12+、Node 18+ | Mac 本机开发/演示 | 见 [`mac/`](mac/)：`bash deploy/native/mac/install.sh` |
| Windows 原生版 | Python 3.12+、Node 18+ | Windows 本机开发/演示 | 见 [`windows/`](windows/)：双击 `install.bat` |
| Linux 原生版 | Python 3.12+、Node 18+ | Linux 本机开发/演示 | 见 [`linux/`](linux/)：`bash deploy/native/linux/install.sh` |
| 服务器版（免 Docker） | Python 3.12（Node 仅构建时需要） | 云服务器长期运行 | 见 [`server/`](server/)：`bash deploy/native/server/install.sh` |

## 原生版的共同原理

- 数据库：SQLite 文件（`backend/data/dev.db`），零安装；向量列自动回退为 JSON，检索用 Python 余弦计算
- 异步任务：无 Redis 时，`WORKER_SYNC_FALLBACK=true` 让文档解析/任务执行自动降级为本机线程
- 模型：`LLM_PROVIDER=fake` + `EMBEDDING_PROVIDER=mock`，免 Key 开箱即用

## 选择建议

- **只是想先跑起来看看**：Docker 版（`make up`）或你所在系统的原生版
- **Mac/Windows 个人电脑**：对应原生版，两个终端分别起前后端
- **云服务器对外服务**：服务器版（systemd 托管 + nginx），或 Docker 版
- **生产多人使用**：建议 Docker 版（Postgres + Redis），SQLite 只适合单人/演示规模

各版本的详细步骤见对应子目录的 README；原生运行的通用说明见
[`docs/native-run.md`](../../docs/native-run.md)。
