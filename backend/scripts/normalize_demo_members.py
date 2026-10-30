"""整理演示小院的成员数据（仅开发环境）。

做两件事：
1. 移除 E2E / 压测脚本留下的假成员（join_*、e2e_*、m_*、curl_e2e_*、E2E 新成员…），
   只删「小院成员关系」，不删账号，避免破坏历史审计与消息引用；
2. 补齐演示团队真人成员（陈立群、李慧敏…），四个固定账号的展示名保持不变。

用法：
    python scripts/normalize_demo_members.py            # 预演，只打印将处理的对象
    python scripts/normalize_demo_members.py --apply    # 真正执行
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.db.seed import DEMO_ROSTER, DEMO_YARD_NAME, ensure_demo_roster  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.modules.auth.models import User  # noqa: E402
from app.modules.yards.models import Yard, YardMembership  # noqa: E402
from sqlalchemy import select  # noqa: E402

#: 测试脚本生成的用户名前缀 / 占位展示名
_TEST_USERNAME = re.compile(r"^(join_|e2e_|e2e|m_|curl_e2e|probe|e27_)", re.I)
_TEST_DISPLAY = {"E2E 新成员", "E2E", "e2e", "测试成员"}

#: 演示团队里必须保留的账号
_KEEP = {"admin", "yard_admin", "expert", "operator"} | {u for u, _, _ in DEMO_ROSTER}


def _is_test_member(username: str, display_name: str) -> bool:
    if username in _KEEP:
        return False
    if _TEST_USERNAME.match(username):
        return True
    if display_name in _TEST_DISPLAY:
        return True
    # 展示名就是用户名本身，且形如机器生成的短串（如 e2e_mvplzlcc）
    return display_name == username and bool(re.search(r"[_0-9]", username))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="真正写入数据库")
    args = parser.parse_args()

    if settings.app_env.strip().lower() != "development":
        print(f"拒绝执行：当前 app_env={settings.app_env} 非 development。", file=sys.stderr)
        sys.exit(1)

    db = SessionLocal()
    try:
        yard = db.scalar(select(Yard).where(Yard.name == DEMO_YARD_NAME))
        if yard is None:
            print(f"未找到演示小院「{DEMO_YARD_NAME}」，请先执行 make seed-dev。", file=sys.stderr)
            sys.exit(1)

        rows = db.execute(
            select(YardMembership, User)
            .join(User, User.id == YardMembership.user_id)
            .where(YardMembership.yard_id == yard.id)
        ).all()
        junk = [(m, u) for m, u in rows if _is_test_member(u.username, u.display_name or "")]
        keep = len(rows) - len(junk)
        print(f"演示小院成员共 {len(rows)} 人：保留 {keep}，清理测试成员 {len(junk)}")

        if args.apply:
            for membership, _user in junk:
                db.delete(membership)
            db.flush()
            created = ensure_demo_roster(db, yard)
            db.commit()
            print(f"已清理 {len(junk)} 条测试成员关系，补齐真人成员 {created} 人。")
        else:
            for _membership, user in junk[:10]:
                print(f"  将移除：{user.username}（{user.display_name or '-'}）")
            if len(junk) > 10:
                print(f"  … 其余 {len(junk) - 10} 个")
            print("预演结束；加 --apply 执行。")
    finally:
        db.close()


if __name__ == "__main__":
    main()
