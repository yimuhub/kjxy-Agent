# Mac 原生版（不依赖 Docker）

## 前置要求

- Python 3.12+（`python3 --version` 确认）
- Node.js 18+（`node --version` 确认）

## 安装（只需一次）

```bash
cd /Users/czmj/Documents/kejixiaoyuan   # 换成你的仓库路径
bash deploy/native/mac/install.sh
```

脚本会：建虚拟环境 → 装前后端依赖 → 配 `backend/.env` → 建表 → 灌演示数据。
重复运行不会报错。

## 启动（开两个终端）

```bash
# 终端 1：后端 API（http://127.0.0.1:8000）
make native-api

# 终端 2：前端页面（http://127.0.0.1:5173）
make native-web
```

浏览器打开 http://127.0.0.1:5173 ，演示账号：`admin/admin123`（平台管理员）、
`tenant_admin/tenant123`、`expert/expert123`、`operator/operator123`。

## 开机自启（可选）

把 `com.techyard.api.plist` 中的 `__INSTALL_DIR__` 替换为仓库绝对路径，
复制到 `~/Library/LaunchAgents/` 后执行：

```bash
launchctl load ~/Library/LaunchAgents/com.techyard.api.plist
```

## 常见问题

- **端口被占用**：`lsof -i :8000` / `lsof -i :5173` 查到进程后关掉，或改启动端口。
- **想清空重来**：删掉 `backend/data/` 目录，重新跑安装脚本。
- **缺 python3/node**：官网下载或 `brew install python@3.12 node`。
