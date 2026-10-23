#!/usr/bin/env python3
"""桌面本地引擎单文件入口：为 Tauri 桌面壳（client/desktop）提供"点开就用"的后端。

职责（启动顺序固定，与 docs/client.md 一致）：
  1. 解析/创建跨平台数据目录（TECHYARD_DATA_DIR 优先，其次按 OS 默认值）
  2. 写入 standalone 环境变量契约（见 _apply_env_contract）
  3. alembic upgrade head（迁移后 schema 与服务器部署完全一致）
  4. users 表为空时 seed 演示租户，生成随机管理员口令写 <data>/desktop-login.txt，
     并只在 stdout 打印一次（Rust 壳负责在 splash 里指路这个文件）
  5. 挂载前端静态文件（FRONTEND_DIST 存在时，SPA history 回退到 index.html）
  6. uvicorn 绑定 127.0.0.1:PORT —— 只监听回环，绝不外露端口

环境变量契约（本文件负责设置，后端并行同事按此实现）：
  TASKS_INLINE_MODE=true    约定：True 时任务在 API 进程内同步执行、
                            /health/ready 不再强依赖 Redis、Celery 不要求存在。
                            —— 后端同事会在 config/main 中接入该开关；本文件
                            仅负责在 standalone 模式把它设置好。
  WORKER_SYNC_FALLBACK=true 现有开关：Redis/Celery 不可用时后台线程降级执行，
                            在 TASKS_INLINE_MODE 落地前先兜住"无 Redis 也能用"。

Celery：本入口【永远不启动】worker/beat 进程，也不需要 broker 可用；任务执行
由上面两个开关兜底。若未来接入需要真队列的功能，属于服务器模式，不属于桌面端。

打包：由 backend/scripts/build_sidecar.sh 以 PyInstaller onefile 打成
dist/techyard-sidecar-<os>，作为 Tauri sidecar 由 Rust 壳拉起。
"""

from __future__ import annotations

import argparse
import contextlib
import itertools
import os
import secrets
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 仅类型检查期：运行时 app.* 一律延迟导入（见各函数内 import）
    from fastapi import FastAPI
    from starlette.types import Scope

# 演示账号用户名（与 app/db/seed.py 的 SEED_USERS 保持一致；seed.py 非本文件
# 私有 API，这里只取用户名集合用于"空库判定后自行生成口令"）
SEED_USERNAMES = ("admin", "tenant_admin", "expert", "operator")


# ---------------------------------------------------------------------------
# 数据目录（跨平台 + AO 式防御）
# ---------------------------------------------------------------------------


def _default_data_dir() -> Path:
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "科技小院"
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else home / "AppData" / "Roaming"
        return base / "科技小院"
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else home / ".local" / "share"
    return base / "techyard"


def resolve_data_dir() -> Path:
    """确定数据目录：TECHYARD_DATA_DIR > OS 默认。绝不落在仓库源码树内。

    【agent-orchestrator 式防御，务必保留】曾出现过壳程序把"仓库检出目录
    （内含 node_modules/）"当数据/工作目录，导致迁移、seed、日志全部写进
    依赖树，打包体被污染且用户数据被 `npm ci` 一把清掉。因此只要路径里
    出现名为 node_modules 的组件，一律拒绝启动，而不是默默把数据写进去。
    """
    raw = os.environ.get("TECHYARD_DATA_DIR", "").strip()
    data_dir = Path(raw).expanduser() if raw else _default_data_dir()
    data_dir = data_dir.resolve() if data_dir.exists() else data_dir.absolute()
    lowered = {part.lower() for part in data_dir.parts}
    if "node_modules" in lowered:
        raise SystemExit(f"[standalone] 拒绝启动：数据目录路径含 node_modules：{data_dir}")
    for sub in ("files", "logs"):
        (data_dir / sub).mkdir(parents=True, exist_ok=True)
    return data_dir


# ---------------------------------------------------------------------------
# standalone 环境变量契约（必须在 import app.* 之前完成）
# ---------------------------------------------------------------------------


def _resource_root() -> Path:
    """PyInstaller 打包后的资源根（_MEIPASS）；未打包时即 backend/ 目录。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(str(meipass))
    return Path(__file__).resolve().parent


def _apply_env_contract(data_dir: Path, port: int) -> None:
    """强制 standalone 运行环境。用赋值而非 setdefault：桌面壳语义就是单机。"""
    db_path = (data_dir / "app.db").as_posix()
    os.environ["APP_ENV"] = "development"  # 桌面单机模式；生产 fail-fast 规则不适用
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["FILE_STORAGE_BACKEND"] = "local"
    os.environ["LOCAL_STORAGE_PATH"] = str(data_dir / "files")
    # 约定别名：文档/口头契约里常写作 FILE_STORAGE，一并设置避免歧义
    os.environ["FILE_STORAGE"] = "local"
    os.environ["TASKS_INLINE_MODE"] = "true"
    os.environ["WORKER_SYNC_FALLBACK"] = "true"
    os.environ["SEED_ALLOW_PRODUCTION"] = "YES"
    os.environ["USE_PGVECTOR"] = "false"
    os.environ["TECHYARD_DATA_DIR"] = str(data_dir)
    os.environ["TECHYARD_PORT"] = str(port)
    # 独立 APP_SECRET_KEY：首启生成，持久化在 data_dir/secret.key（AO 同款做法，
    # 每次随机会导致重启后所有登录态/签名失效，绝不能每次现生成）
    secret_file = data_dir / "secret.key"
    if not secret_file.exists():
        secret_file.write_text(secrets.token_urlsafe(48), encoding="utf-8")
        with contextlib.suppress(OSError):  # Windows 无 POSIX 权限语义
            secret_file.chmod(0o600)
    os.environ["APP_SECRET_KEY"] = secret_file.read_text(encoding="utf-8").strip()


# ---------------------------------------------------------------------------
# 迁移 + 首启 seed
# ---------------------------------------------------------------------------


def run_migrations() -> None:
    """alembic upgrade head。alembic.ini 与 migrations/ 由 PyInstaller --add-data
    放在资源根目录；未打包运行时用 backend/ 下的原件，两条路径同一套代码。"""
    from alembic import command
    from alembic.config import Config

    root = _resource_root()
    ini = root / "alembic.ini"
    migrations = root / "migrations"
    if not ini.exists() or not migrations.exists():
        raise SystemExit(
            f"[standalone] 缺少迁移资源：{ini} / {migrations}（检查打包脚本 --add-data）"
        )
    cfg = Config(str(ini))
    original = sys.path[:]
    cfg.set_main_option("script_location", str(migrations))
    # migrations/env.py 会 import app.*：backend/=root 内含 app/ 包，插到最前保证可导入
    backend_dir = str(root)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    try:
        command.upgrade(cfg, "head")
    finally:
        sys.path[:] = original


def _users_table_count() -> int:
    from app.db.session import engine
    from sqlalchemy import text

    with engine.connect() as conn:
        return int(conn.execute(text("SELECT COUNT(*) FROM users")).scalar_one())


def seed_if_empty(data_dir: Path) -> None:
    """空库首启：seed 演示租户 + 随机管理员口令 → desktop-login.txt。

    口令生成方式：先自行生成全部随机口令，再把 app.db.seed._random_password
    打桩为按调用次序查表返回——这样明文完全由本函数掌控，可写入
    desktop-login.txt；seed 自带的 stdout 打印用 redirect_stdout 吞掉，
    保证明文"只打印一次"。（依赖 seed 的 SEED_USERS 顺序与 SEED_USERNAMES 一致）
    """
    if _users_table_count() > 0:
        return

    import io

    import app.db.seed as seed_mod

    passwords = {name: secrets.token_urlsafe(12) for name in SEED_USERNAMES}
    order = list(SEED_USERNAMES)
    calls = itertools.count()

    def _stubbed_random_password() -> str:
        i = min(next(calls), len(order) - 1)
        return passwords[order[i]]

    seed_mod._random_password = _stubbed_random_password  # type: ignore[method-assign]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        seed_mod.seed(random_passwords=True)

    login_file = data_dir / "desktop-login.txt"
    lines = [
        "# 科技小院桌面版 · 演示账号（仅打印/写入这一次，文件权限 600）",
        "# 这些账号仅监听 127.0.0.1，数据全部在本目录 app.db 中。",
        "# 删除 app.db 并重启应用即可恢复到全新演示状态。",
    ]
    lines += [f"{name} {passwords[name]}" for name in SEED_USERNAMES]
    login_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with contextlib.suppress(OSError):
        login_file.chmod(0o600)
    # 只在 stdout 打印一次（Rust splash 期会把 sidecar 输出留存在日志里）
    for line in lines[1:]:
        print(line)
    print(f"[standalone] 演示账号已写入 {login_file}")


# ---------------------------------------------------------------------------
# 前端静态挂载 + 启动
# ---------------------------------------------------------------------------


def _mount_frontend(app: FastAPI) -> None:
    """FRONTEND_DIST 指到前端构建产物目录时，把它挂到 "/"（SPA history 回退）。

    前端 axios baseURL 默认是相对路径 '/api/v1'，同源部署天然可用，无需改前端；
    vue-router 用 createWebHistory，未知路径必须回退 index.html，因此不能直接
    用 StaticFiles(html=True)（它只兜 '/'，不兜 '/tasks/123' 这类深链）。
    """
    dist_raw = os.environ.get("FRONTEND_DIST", "").strip()
    if not dist_raw:
        return
    dist = Path(dist_raw).expanduser()
    index = dist / "index.html"
    if not index.is_file():
        print(f"[standalone] FRONTEND_DIST 无 index.html，跳过前端挂载：{dist}")
        return

    from fastapi.staticfiles import StaticFiles
    from starlette.responses import FileResponse, Response

    class SpaFiles(StaticFiles):
        async def get_response(self, path: str, scope: Scope) -> Response:
            try:
                response = await super().get_response(path, scope)
            except Exception:
                response = Response(status_code=404)
            if response.status_code == 404 and "." not in Path(path).name:
                # 非静态资源（无扩展名）视为前端路由，交给 SPA 壳
                return FileResponse(index)
            return response

    app.mount("/", SpaFiles(directory=str(dist), html=True), name="frontend")
    print(f"[standalone] 前端已挂载：{dist}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    # --lan 显式开启局域网模式（默认关闭 → 行为与桌面版完全一致，只绑回环）。
    # 用 parse_known_args：Rust 壳可能追加未知参数，绝不允许因认不出就崩启动。
    parser = argparse.ArgumentParser(prog="techyard-sidecar", description="科技小院桌面本地引擎")
    parser.add_argument(
        "--lan",
        action="store_true",
        help="局域网模式：绑定 0.0.0.0 并打印局域网访问地址（默认关闭，仅监听回环）",
    )
    args, _unknown = parser.parse_known_args()

    # 端口由 Rust 壳传入（TECHYARD_PORT，18700-18710 里选的空闲端口）；兼容 PORT
    raw_port = os.environ.get("TECHYARD_PORT") or os.environ.get("PORT") or "18700"
    try:
        port = int(raw_port)
    except ValueError:
        raise SystemExit(f"[standalone] PORT 非法：{raw_port!r}") from None
    if not (1 <= port <= 65535):
        raise SystemExit(f"[standalone] PORT 越界：{port}")

    data_dir = resolve_data_dir()
    _apply_env_contract(data_dir, port)
    print(f"[standalone] data_dir={data_dir} port={port}")

    run_migrations()
    seed_if_empty(data_dir)

    # 此处 import 才创建 Settings：上面必须已把所有环境变量就位
    import uvicorn
    from app.main import app

    _mount_frontend(app)
    if not args.lan:
        # 默认：只绑 127.0.0.1，桌面模式绝不外露端口
        host = "127.0.0.1"
    else:
        # --lan：绑定 0.0.0.0 供同网段手机扫码配对；打印局域网地址提示
        host = "0.0.0.0"
        from app.modules.pairing.service import detect_local_ipv4

        lan_ip = detect_local_ipv4()
        if lan_ip:
            print(f"[standalone] LAN 模式已开启：手机可用 http://{lan_ip}:{port} 接入（同网段）")
        else:
            print(
                f"[standalone] LAN 模式已开启：已绑定 0.0.0.0:{port}"
                "（未探测到局域网 IP，请手动确认本机地址）"
            )
    uvicorn.run(app, host=host, port=port, log_level=os.environ.get("LOG_LEVEL", "info"))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # 让 Rust 壳的 stderr 尾部拿到完整上下文再退码
        import traceback

        traceback.print_exc()
        print(f"[standalone] 启动失败：{exc}", file=sys.stderr)
        sys.exit(1)
