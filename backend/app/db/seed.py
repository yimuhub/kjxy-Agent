"""开发环境演示数据。幂等：已存在则跳过。

用法：python -m app.db.seed（需先 alembic upgrade head）
生产环境禁止执行：非 development 环境必须显式设置 SEED_ALLOW_PRODUCTION=YES。
开发环境设 SEED_RANDOM_PASSWORDS=YES 时，每个账号生成随机密码并只打印一次。
"""

import hashlib
import os
import secrets
import sys

from sqlalchemy import select

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import SessionLocal

log = get_logger(__name__)

DEMO_YARD_NAME = "演示科技小院"

#: 四个固定演示账号：展示名保持「角色名」口径不变（负责人/管理员/专家/科研人员）
SEED_USERS = [
    # (username, password, display_name, yard_role|None)
    # admin 同时是演示小院负责人
    ("admin", "admin123", "平台管理员 / 小院负责人", "yard_owner"),
    ("yard_admin", "yard123", "小院管理员", "yard_admin"),
    ("expert", "expert123", "农业专家", "expert"),
    ("operator", "operator123", "科研人员", "operator"),
]

#: 演示团队其余成员：用真人姓名，列表里看起来像真实小院团队。
#: 口令统一 demo123（仅开发演示库）；正式部署不会执行 seed。
DEMO_MEMBER_PASSWORD = "demo123"
DEMO_ROSTER = [
    ("chen_liqun", "陈立群", "operator"),
    ("li_huimin", "李慧敏", "expert"),
    ("wang_wenbo", "王文博", "operator"),
    ("zhao_mengqi", "赵梦琪", "viewer"),
    ("zhou_zhenyu", "周振宇", "operator"),
    ("sun_xiaotong", "孙晓彤", "operator"),
    ("liu_guoqiang", "刘国强", "operator"),
    ("wu_jiayi", "吴佳怡", "expert"),
    ("zheng_haitao", "郑海涛", "operator"),
    ("he_yutong", "何雨桐", "viewer"),
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


DEMO_GROWTH_DOC_TITLE = "示范小院产品与产销档案（演示资料）"
DEMO_GROWTH_DOC_TEXT = """# 示范小院产品与产销档案

## 一、主推产品与产量
赣南脐橙（纽荷尔），树龄 8 年，山地果园、红壤；亩产约 3000 斤，
可售果以 70-80mm 为主，另有少量 80mm 以上大果做礼盒。

## 二、栽培与可核实的品质信息
以有机肥为主、水肥一体化；2025 年 11 月抽检糖度 13.5°Bx（记录在本院台账）。
不使用"零农残""绿色认证"等未经证实的说法——如需对外宣称，必须先取得检测报告。

## 三、销售与活动安排
主要走批发与社区团购；计划中秋礼盒预售、11 月采摘节，
目标客群为本地家庭、单位团购与周边城市礼赠。

（本资料为平台演示用示例文本，不是真实检测报告或销售数据。）
"""


def _is_development() -> bool:
    return settings.app_env.strip().lower() == "development"


def guard_demo_data_writes(purpose: str) -> None:
    """演示数据写库守卫：非开发环境必须显式 SEED_ALLOW_PRODUCTION=YES。

    seed 与 repair_demo_passwords 都会把口令写成文档里的公开弱值，因此共用同一道闸——
    生产库被误跑一次，等于对外发放一批可猜账号。
    """
    if _is_development() or os.environ.get("SEED_ALLOW_PRODUCTION") == "YES":
        return
    print(
        f"拒绝执行 {purpose}：当前环境 app_env={settings.app_env} 非 development。"
        "如确需执行，请显式设置 SEED_ALLOW_PRODUCTION=YES。",
        file=sys.stderr,
    )
    sys.exit(1)


def active_demo_usernames(db) -> list[str]:
    """仍能用文档演示口令登录的用户名（生产启动自查用）。"""
    from app.core.security import verify_password
    from app.modules.auth.repository import get_by_username

    documented = {username: password for username, password, _display, _role in SEED_USERS}
    documented.update({username: DEMO_MEMBER_PASSWORD for username, _display, _role in DEMO_ROSTER})
    hits: list[str] = []
    for username, plain in documented.items():
        user = get_by_username(db, username)
        if user is not None and verify_password(plain, user.password_hash):
            hits.append(username)
    return hits


def _admin_username() -> str:
    """允许服务器安装使用非弱口令用户名（默认 admin，供开发使用）。"""
    return os.environ.get("SEED_PLATFORM_ADMIN_USERNAME", "").strip() or "admin"


def _random_password() -> str:
    """随机演示密码：仅在开发随机模式下使用，明文只打印一次。"""
    return secrets.token_urlsafe(12)


def _add_demo_doc(db, *, yard_id: str, actor_id: str, title: str, text: str) -> int:
    """把一段演示文本写成 ready 状态的知识文档；返回切片数。"""
    from app.modules.knowledge.chunking import split_text
    from app.modules.knowledge.embedding import MockEmbeddingProvider
    from app.modules.knowledge.models import KnowledgeChunk, KnowledgeDocument

    doc = KnowledgeDocument(
        yard_id=yard_id,
        title=title,
        storage_key=f"seed/{secrets.token_hex(8)}.txt",
        mime_type="text/plain",
        source="seed",
        status="ready",
        checksum=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        uploaded_by=actor_id,
        page_count=1,
    )
    db.add(doc)
    db.flush()
    embedder = MockEmbeddingProvider()
    chunks = split_text(text, chunk_size=400, overlap=50)
    vectors = embedder.embed(chunks)
    for i, (content, vec) in enumerate(zip(chunks, vectors, strict=False)):
        db.add(
            KnowledgeChunk(
                yard_id=yard_id,
                document_id=doc.id,
                chunk_index=i,
                content=content,
                page_number=1,
                title_path=title,
                trust_level="yard_material",
                visibility="yard",
                embedding=list(vec),
            )
        )
    db.flush()
    return len(chunks)


def _ensure_demo_growth_doc(db, *, yard_id: str, actor_id: str) -> bool:
    """增长线演示资料（幂等，按标题判重）：让品牌/卖点/海报技能有可引用的事实素材。"""
    from app.modules.knowledge.models import KnowledgeDocument

    exists = db.scalar(
        select(KnowledgeDocument.id).where(
            KnowledgeDocument.yard_id == yard_id,
            KnowledgeDocument.title == DEMO_GROWTH_DOC_TITLE,
        )
    )
    if exists is not None:
        return False
    _add_demo_doc(
        db,
        yard_id=yard_id,
        actor_id=actor_id,
        title=DEMO_GROWTH_DOC_TITLE,
        text=DEMO_GROWTH_DOC_TEXT,
    )
    return True


def ensure_demo_roster(db, yard) -> int:
    """补齐演示团队真人成员（幂等）。返回本次新建人数。

    四个固定演示账号保持「角色名」展示，其余成员一律用真人姓名，
    让成员列表看起来像真实小院团队而不是测试占位。
    """
    from app.modules.auth.repository import create_user, get_by_username
    from app.modules.yards.repository import add_membership, get_membership

    created = 0
    for username, display_name, role in DEMO_ROSTER:
        user = get_by_username(db, username)
        if user is None:
            user = create_user(
                db,
                username=username,
                password=DEMO_MEMBER_PASSWORD,
                display_name=display_name,
            )
            created += 1
        elif user.display_name != display_name:
            user.display_name = display_name
        membership = get_membership(db, yard.id, user.id)
        if membership is None:
            add_membership(db, yard_id=yard.id, user_id=user.id, role=role)
        else:
            membership.role = role
            membership.status = "active"
    db.flush()
    return created


def _ensure_demo_owner(db) -> None:
    """演示小院：平台管理员必须是负责人。

    历史库由 0030 回填时，多院环境会选中最早的 yard_admin 当负责人；
    演示环境统一纠正为平台管理员，并把其他 yard_owner 回退为管理员。
    """
    from app.modules.auth.repository import get_by_username
    from app.modules.yards.models import Yard, YardMembership
    from app.modules.yards.repository import add_membership, get_membership

    admin = get_by_username(db, _admin_username())
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
    from app.modules.growth.skills import ensure_growth_skills
    from app.modules.market.agri_skills import ensure_builtin_skills
    from app.modules.market.formal_writing_skills import ensure_formal_writing_skills
    from app.modules.market.office_skills import ensure_office_skills

    ensure_builtin_skills(db, yard_id=yard.id, actor_id=admin.id, request_id="seed")
    ensure_growth_skills(db, yard_id=yard.id, actor_id=admin.id, request_id="seed")
    ensure_office_skills(db, yard_id=yard.id, actor_id=admin.id, request_id="seed")
    ensure_formal_writing_skills(db, yard_id=yard.id, actor_id=admin.id, request_id="seed")
    _ensure_demo_growth_doc(db, yard_id=yard.id, actor_id=admin.id)
    ensure_demo_roster(db, yard)
    db.commit()


def seed(random_passwords: bool = False) -> None:
    from app.modules.auth.repository import create_user, get_by_username
    from app.modules.yards.repository import (
        add_membership,
        create_yard,
    )

    db = SessionLocal()
    try:
        admin_username = _admin_username()
        if get_by_username(db, admin_username) is not None:
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
        login_names: dict[str, str] = {}
        for username, password, display_name, role in SEED_USERS:
            actual = admin_username if username == "admin" else username
            login_names[username] = actual
            plain = _random_password() if random_passwords else password
            passwords[username] = plain
            user = create_user(db, username=actual, password=plain, display_name=display_name)
            users[username] = user
            if role:
                add_membership(db, yard_id=yard.id, user_id=user.id, role=role)
        db.flush()
        # admin 是演示小院负责人：owner_user_id 决定负责人专属权限
        yard.owner_user_id = users["admin"].id
        db.flush()

        # 内置农业技能：直接出现在「专家审核 → Skill 管理」
        from app.modules.growth.skills import ensure_growth_skills
        from app.modules.market.agri_skills import ensure_builtin_skills
        from app.modules.market.formal_writing_skills import ensure_formal_writing_skills
        from app.modules.market.office_skills import ensure_office_skills

        ensure_builtin_skills(db, yard_id=yard.id, actor_id=users["admin"].id, request_id="seed")
        ensure_growth_skills(db, yard_id=yard.id, actor_id=users["admin"].id, request_id="seed")
        ensure_office_skills(db, yard_id=yard.id, actor_id=users["admin"].id, request_id="seed")
        ensure_formal_writing_skills(
            db, yard_id=yard.id, actor_id=users["admin"].id, request_id="seed"
        )

        # 演示知识文档（直接写入 ready 状态，免去解析流程）
        chunk_count = _add_demo_doc(
            db,
            yard_id=yard.id,
            actor_id=users["yard_admin"].id,
            title=DEMO_DOC_TITLE,
            text=DEMO_DOC_TEXT,
        )
        # 增长线演示资料：品牌/卖点/海报技能都能引用到可核实的事实
        _ensure_demo_growth_doc(db, yard_id=yard.id, actor_id=users["yard_admin"].id)
        # 演示团队其余成员：真人姓名，成员列表更像真实小院
        ensure_demo_roster(db, yard)
        db.commit()
        log.info("seed 完成：小院=%s 用户=%d 文档chunk=%d", yard.id, len(users), chunk_count)
        if random_passwords:
            # 随机口令只在此处明文打印一次，日志/数据库均不留副本；请立刻保存
            print("SEED_RANDOM_PASSWORDS=YES：以下密码仅打印一次，请立即保存：")
            for username, plain in passwords.items():
                print(f"{login_names[username]} {plain}")
        else:
            log.info(
                "演示账号： %s/%s yard_admin/yard123 expert/expert123 operator/operator123",
                login_names["admin"],
                passwords["admin"],
            )
    finally:
        db.close()


def main() -> None:
    """入口守卫：非开发环境必须显式 SEED_ALLOW_PRODUCTION=YES 才允许执行。"""
    guard_demo_data_writes("python -m app.db.seed")
    seed(random_passwords=os.environ.get("SEED_RANDOM_PASSWORDS") == "YES")


if __name__ == "__main__":
    main()
