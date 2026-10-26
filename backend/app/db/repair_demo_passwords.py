"""修复演示账号密码：把四个演示用户的密码重置为 seed.py 的文档值。

背景：seed() 是幂等的——只要 admin 已存在就整体跳过，永远不会修复
"密码漂移"（比如某次 seed 用的旧密码、或密码被人改过）。本脚本只做
一件事：把 admin / yard_admin / expert / operator 四个用户名的密码
哈希重置为 SEED_USERS 里记录的文档值，其余用户一律不动。

用法（项目根目录 / Mac 本机部署目录）：
    cd backend && ../.venv/bin/python -m app.db.repair_demo_passwords

只读检查模式（不写库）：
    ... python -m app.db.repair_demo_passwords --check
"""

import sys

from app.core.logging import get_logger
from app.core.security import hash_password, verify_password
from app.db.seed import SEED_USERS
from app.db.session import SessionLocal
from app.modules.auth.repository import get_by_username

log = get_logger(__name__)


def repair(check_only: bool = False) -> dict:
    """返回 {username: 状态}；状态 in ok(已修复)/already(已是文档密码)/missing(用户不存在)。"""
    from app.db import models_registry  # noqa: F401  # 确保所有模型已注册

    db = SessionLocal()
    result = {}
    try:
        for username, password, _display, _role in SEED_USERS:
            user = get_by_username(db, username)
            if user is None:
                result[username] = "missing"
                log.warning("用户 %s 不存在，跳过（可跑 python -m app.db.seed 补建）", username)
                continue
            if verify_password(password, user.password_hash):
                result[username] = "already"
                log.info("用户 %s 的密码已是文档值，无需改动", username)
                continue
            if not check_only:
                user.password_hash = hash_password(password)
                db.commit()
            result[username] = "ok"
            suffix = "（试运行，未写库）" if check_only else ""
            log.info("用户 %s 的密码已重置为文档值%s", username, suffix)
        return result
    finally:
        db.close()


def main() -> int:
    check_only = "--check" in sys.argv[1:]
    result = repair(check_only=check_only)
    print("演示账号密码状态：")
    for username, status in result.items():
        if check_only and status == "ok":
            mark = "需修复（试运行，未改动）"
        else:
            mark = {"ok": "已修复", "already": "正常", "missing": "不存在"}[status]
        print(f"  {username}: {mark}")
    if any(s == "missing" for s in result.values()):
        print("提示：缺失的账号请跑 `python -m app.db.seed` 补建（幂等）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
