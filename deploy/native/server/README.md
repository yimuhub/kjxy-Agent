# 服务器版（免 Docker，systemd 托管）

适合：云服务器 / 长期运行 / 对外提供服务。SQLite 存数据，无外部依赖。

## 安装

```bash
# root（一键装到 /opt/techyard，并创建 techyard 系统用户）
bash deploy/native/server/install.sh

# 普通用户（装到 $HOME/techyard）或自定义目录
bash deploy/native/server/install.sh /data/techyard
```

脚本会：拷贝代码 → 建 venv（生产依赖）→ 构建前端 dist →
生成随机 `APP_SECRET_KEY` → 建表 → 灌演示数据 → 注册并启动 systemd 服务。

## 服务管理

```bash
# root 安装
systemctl status techyard-api
systemctl restart techyard-api
journalctl -u techyard-api -f        # 看日志

# 普通用户安装
systemctl --user status techyard-api
journalctl --user -u techyard-api -f
```

## 前端：用 nginx 托管

安装 `nginx` 后新增站点配置（把 `/opt/techyard` 换成实际 PREFIX）：

```nginx
server {
    listen 80;
    server_name your-domain.example;   # 或 _

    root /opt/techyard/frontend/dist;

    location / {
        try_files $uri $uri/ /index.html;   # SPA 路由回退
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000;    # 反代后端 API
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

然后 `nginx -t && systemctl reload nginx`。

## 备份

数据都在一个目录里，整体拷贝即可：

```bash
tar -czf techyard-backup-$(date +%F).tgz -C /opt/techyard backend/data
```

恢复：停服务 → 解压覆盖 `backend/data/` → 起服务。

## 更新代码

```bash
cd /opt/techyard
# 用新版代码覆盖（保留 backend/.env 与 backend/data）
tar --exclude='.venv' --exclude='frontend/node_modules' \
    --exclude='backend/data' --exclude='backend/.env' --exclude='__pycache__' \
    -cf - -C /path/to/new-code . | tar -xf - -C /opt/techyard
/opt/techyard/.venv/bin/pip install -e . -q
(cd frontend && npm install && npm run build)
(cd backend && ../.venv/bin/alembic upgrade head)
systemctl restart techyard-api
```

## 已知限制

- SQLite + 单进程只适合中小规模；用户量/并发上来后建议切 Docker 版（Postgres + Redis）。
- 高并发可把 unit 的 `ExecStart` 加上 `--workers 4`（内存允许时）。
