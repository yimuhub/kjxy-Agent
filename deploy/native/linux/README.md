# Linux 原生版（不依赖 Docker）

## 前置要求

- Python 3.12+（Debian/Ubuntu 还需 `sudo apt install -y python3.12-venv`）
- Node.js 18+

## 安装（只需一次）

```bash
cd /path/to/kejixiaoyuan # 换成你的仓库路径
bash deploy/native/linux/install.sh
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
`yard_admin/yard123`、`expert/expert123`、`operator/operator123`。

## 长期运行

本机开发用上面的两个终端即可。如需开机自启、后台常驻、对外提供服务，
请改用（`deploy/native/server/`，systemd 托管）。

## 常见问题

- **端口被占用**：`ss -tlnp | grep -E '8000|5173'` 查到进程后处理，或改启动端口。
- **想清空重来**：删掉 `backend/data/` 目录，重新跑安装脚本。
