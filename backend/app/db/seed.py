"""开发环境演示数据。幂等：已存在则跳过。

用法：python -m app.db.seed（需先 alembic upgrade head）
生产环境禁止执行。
"""

from app.core.logging import get_logger
from app.db.session import SessionLocal

log = get_logger(__name__)

DEMO_TENANT_NAME = "演示科技小院"

SEED_USERS = [
    # (username, password, display_name, tenant_role|None)
    ("admin", "admin123", "平台管理员", None),  # 平台管理员，无租户归属
    ("tenant_admin", "tenant123", "租户管理员", "tenant_admin"),
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


def seed() -> None:
    from app.modules.auth.repository import create_user, get_by_username
    from app.modules.knowledge.embedding import MockEmbeddingProvider
    from app.modules.knowledge.models import KnowledgeChunk, KnowledgeDocument
    from app.modules.tenants.repository import (
        add_membership,
        create_tenant,
    )

    db = SessionLocal()
    try:
        if get_by_username(db, "admin") is not None:
            log.info("seed 数据已存在，跳过")
            return

        tenant = create_tenant(
            db,
            name=DEMO_TENANT_NAME,
            province="演示省",
            city="演示市",
            scene_type="试点小院",
        )
        db.flush()
        log.info("创建演示租户: %s", tenant.id)

        users = {}
        for username, password, display_name, role in SEED_USERS:
            user = create_user(db, username=username, password=password, display_name=display_name)
            users[username] = user
            if role:
                add_membership(db, tenant_id=tenant.id, user_id=user.id, role=role)
        db.flush()

        # 演示知识文档（直接写入 ready 状态，免去解析流程）
        from app.modules.knowledge.chunking import split_text

        doc = KnowledgeDocument(
            tenant_id=tenant.id,
            title=DEMO_DOC_TITLE,
            storage_key="seed/demo-rice-tillering.txt",
            mime_type="text/plain",
            source="seed",
            status="ready",
            checksum="seed-demo",
            uploaded_by=users["tenant_admin"].id,
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
                    tenant_id=tenant.id,
                    document_id=doc.id,
                    chunk_index=i,
                    content=text,
                    page_number=1,
                    title_path="水稻分蘖期管理技术要点",
                    trust_level="tenant_material",
                    visibility="tenant",
                    embedding=list(vec),
                )
            )
        db.commit()
        log.info("seed 完成：租户=%s 用户=%d 文档chunk=%d", tenant.id, len(users), len(chunks))
        log.info(
            "演示账号： admin/admin123 tenant_admin/tenant123 expert/expert123 operator/operator123"
        )
    finally:
        db.close()


if __name__ == "__main__":
    seed()
