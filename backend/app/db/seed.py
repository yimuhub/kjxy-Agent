"""开发环境演示数据。幂等：已存在则跳过。

用法：python -m app.db.seed（需先 alembic upgrade head）
生产环境禁止执行：非 development 环境必须显式设置 SEED_ALLOW_PRODUCTION=YES。
开发环境设 SEED_RANDOM_PASSWORDS=YES 时，每个账号生成随机密码并只打印一次。
"""

import os
import secrets
import sys

from sqlalchemy import select

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import SessionLocal

log = get_logger(__name__)

DEMO_YARD_NAME = "演示科技小院"

SEED_USERS = [
    # (username, password, display_name, yard_role|None)
    # admin 同时是演示小院负责人
    ("admin", "admin123", "平台管理员 / 小院负责人", "yard_owner"),
    ("yard_admin", "yard123", "小院管理员", "yard_admin"),
    ("expert", "expert123", "农业专家", "expert"),
    ("operator", "operator123", "科研人员", "operator"),
]

DEMO_DOC_TITLE = "水稻分蘖期管理技术要点（演示资料）"
DEMO_DOC_TEXT = """# 水稻分蘖期管理技术要点

## 一、分蘖期的观察重点
水稻分蘖期是决定有效穗数的关键时期。田间观察应重点关注：分蘖数动态、
叶色变化、病虫害迹象与田间水层管理。当全田分蘖数达到计划穗数的 80% 左右时，
应及时晒田控制无效分蘖。

## 二、肥水管理
分蘖期保持浅水勤灌，忌长期深水淹灌。分蘖肥宜早施，一般在栽后 7-10 天施用，
促进低位分蘖早生快发。晒田复水后视苗情补施穗肥。

## 三、病虫害防控
分蘖期注意二化螟、稻蓟马与纹枯病的田间监测。坚持"预防为主、综合防治"，
优先选用生物防治与高效低毒农药，严格遵守安全间隔期。

（本资料为平台演示用示例文本，不作为生产指导依据。）
"""


def _is_development() -> bool:
    return settings.app_env.strip().lower() == "development"


def _random_password() -> str:
    """随机演示密码：仅在开发随机模式下使用，明文只打印一次。"""
    return secrets.token_urlsafe(12)


def _ensure_demo_owner(db) -> None:
    """演示小院：admin 必须是负责人。

    历史库由 0030 回填时，多院环境会选中最早的 yard_admin 当负责人；
    演示环境统一纠正为 admin，并把其他 yard_owner 回退为管理员。
    """
    from app.modules.auth.repository import get_by_username
    from app.modules.yards.models import Yard, YardMembership
    from app.modules.yards.repository import add_membership, get_membership

    admin = get_by_username(db, "admin")
    yard = db.scalar(select(Yard).where(Yard.name == DEMO_YARD_NAME))
    if admin is None or yard is None:
        return
    yard.owner_user_id = admin.id
    membership = get_membership(db, yard.id, admin.id)
    if membership is None:
        add_membership(db, yard_id=yard.id, user_id=admin.id, role="yard_owner")
    else:
        membership.role = "yard_owner"
        membership.status = "active"
    for row in db.scalars(
        select(YardMembership).where(
            YardMembership.yard_id == yard.id,
            YardMembership.role == "yard_owner",
        )
    ).all():
        if row.user_id != admin.id:
            row.role = "yard_admin"
    from app.modules.market.agri_skills import ensure_builtin_skills

    ensure_builtin_skills(db, yard_id=yard.id, actor_id=admin.id, request_id="seed")
    db.commit()


def seed(random_passwords: bool = False) -> None:
    from app.modules.auth.repository import create_user, get_by_username
    from app.modules.knowledge.embedding import MockEmbeddingProvider
    from app.modules.knowledge.models import KnowledgeChunk, KnowledgeDocument
    from app.modules.yards.repository import (
        add_membership,
        create_yard,
    )

    db = SessionLocal()
    try:
        if get_by_username(db, "admin") is not None:
            _ensure_demo_owner(db)
            log.info("seed 数据已存在，已校正演示小院负责人")
            return

        yard = create_yard(
            db,
            name=DEMO_YARD_NAME,
            province="演示省",
            city="演示市",
            scene_type="试点小院",
        )
        db.flush()
        log.info("创建演示小院: %s", yard.id)

        users = {}
        passwords: dict[str, str] = {}
        for username, password, display_name, role in SEED_USERS:
            plain = _random_password() if random_passwords else password
            passwords[username] = plain
            user = create_user(db, username=username, password=plain, display_name=display_name)
            users[username] = user
            if role:
                add_membership(db, yard_id=yard.id, user_id=user.id, role=role)
        db.flush()
        # admin 是演示小院负责人：owner_user_id 决定负责人专属权限
        yard.owner_user_id = users["admin"].id
        db.flush()

        # 内置农业技能：直接出现在「专家审核 → Skill 管理」
        from app.modules.market.agri_skills import ensure_builtin_skills

        ensure_builtin_skills(db, yard_id=yard.id, actor_id=users["admin"].id, request_id="seed")

        # 演示知识文档（直接写入 ready 状态，免去解析流程）
        from app.modules.knowledge.chunking import split_text

        doc = KnowledgeDocument(
            yard_id=yard.id,
            title=DEMO_DOC_TITLE,
            storage_key="seed/demo-rice-tillering.txt",
            mime_type="text/plain",
            source="seed",
            status="ready",
            checksum="seed-demo",
            uploaded_by=users["yard_admin"].id,
            page_count=1,
        )
        db.add(doc)
        db.flush()

        embedder = MockEmbeddingProvider()
        chunks = split_text(DEMO_DOC_TEXT, chunk_size=400, overlap=50)
        vectors = embedder.embed(chunks)
        for i, (text, vec) in enumerate(zip(chunks, vectors, strict=False)):
            db.add(
                KnowledgeChunk(
                    yard_id=yard.id,
                    document_id=doc.id,
                    chunk_index=i,
                    content=text,
                    page_number=1,
                    title_path="水稻分蘖期管理技术要点",
                    trust_level="yard_material",
                    visibility="yard",
                    embedding=list(vec),
                )
            )
        db.commit()
        log.info("seed 完成：小院=%s 用户=%d 文档chunk=%d", yard.id, len(users), len(chunks))
        if random_passwords:
            # 随机口令只在此处明文打印一次，日志/数据库均不留副本；请立刻保存
            print("SEED_RANDOM_PASSWORDS=YES：以下密码仅打印一次，请立即保存：")
            for username, plain in passwords.items():
                print(f"{username} {plain}")
        else:
            log.info(
                "演示账号： admin/admin123 yard_admin/yard123 expert/expert123 operator/operator123"  # noqa: E501
            )
    finally:
        db.close()


def main() -> None:
    """入口守卫：非开发环境必须显式 SEED_ALLOW_PRODUCTION=YES 才允许执行。"""
    if not _is_development() and os.environ.get("SEED_ALLOW_PRODUCTION") != "YES":
        print(
            f"拒绝执行 seed：当前环境 app_env={settings.app_env} 非 development。"
            "如确需执行，请显式设置 SEED_ALLOW_PRODUCTION=YES。",
            file=sys.stderr,
        )
        sys.exit(1)
    seed(random_passwords=os.environ.get("SEED_RANDOM_PASSWORDS") == "YES")


if __name__ == "__main__":
    main()
