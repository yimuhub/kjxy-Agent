"""测试公共 fixture。

默认使用 SQLite 内存库跑单元/安全测试；需要 pgvector 的用例自行标记跳过。
集成测试需要真实 PostgreSQL 时通过环境变量 TEST_DATABASE_URL 覆盖
（**必须指向专用测试库**：这些用例会 TRUNCATE 全部业务表）。

测试环境确定性（与 CWD / .env 无关）：
本文件在 import app（实例化 Settings）之前无条件设置一批环境变量。进程环境
变量的优先级高于 pydantic-settings 的 .env 文件，因此无论 pytest 从仓库根目录
还是 backend/ 目录运行，配置都一致：
- POLICY_MODULE_ENABLED=true（测试覆盖政策模块开启路径）
- CELERY_BROKER_URL=memory://（进程内 broker，不发真实网络请求）
- LOCAL_STORAGE_PATH=<临时目录>（避免 root .env 里的容器路径 /data/files）
- WORKER_SYNC_FALLBACK=false：API 测试不产生后台线程竞态；需要 worker 行为的
  用例直接调用 worker_tasks.execute_task / run_task_inline。
"""

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# ---------------------------------------------------------------------------
# 必须在 import app（会实例化 Settings）之前设置
# ---------------------------------------------------------------------------
_TEST_STORAGE_DIR = Path(tempfile.mkdtemp(prefix="techyard-test-files-"))
_TEST_DB_PATH = Path(tempfile.mkdtemp(prefix="techyard-test-db-")) / "test.db"

os.environ["APP_SECRET_KEY"] = "test-secret-key-min-32-chars-for-unit-tests"
os.environ["LLM_PROVIDER"] = "fake"
os.environ["EMBEDDING_PROVIDER"] = "mock"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TEST_DB_PATH}")
os.environ["POLICY_MODULE_ENABLED"] = "true"
os.environ["CELERY_BROKER_URL"] = "memory://"
os.environ["CELERY_RESULT_BACKEND"] = "cache+memory://"
os.environ["LOCAL_STORAGE_PATH"] = str(_TEST_STORAGE_DIR)
os.environ["TASK_ENQUEUE_TIMEOUT_SECONDS"] = "0.5"
os.environ["WORKER_SYNC_FALLBACK"] = "false"
os.environ["KNOWLEDGE_PARSE_WATCHDOG_ENABLED"] = "false"  # 测试不拉起看门狗线程
os.environ["BOT_MANAGER_DISABLED"] = "1"  # 测试永不真起机器人长连接
os.environ["FACTORY_RESEARCH_DISABLED"] = "1"  # 工厂联网情报默认关闭，防测试打公网

# 供应商密钥加解密需要 Fernet 主密钥；不依赖某个测试模块先设置（曾导致
# 机器人类测试单独运行时因环境变量缺失而失败，属于测试顺序耦合）。
if not os.environ.get("SUPPLIER_KEY_SECRET"):
    from cryptography.fernet import Fernet

    os.environ["SUPPLIER_KEY_SECRET"] = Fernet.generate_key().decode()

from app.core.deps import get_db_session  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import engine as _global_engine  # noqa: E402
from app.main import create_app  # noqa: E402

# 本地默认把应用级 SessionLocal 指向临时 SQLite，避免测试写入 backend/dev.db；
# CI 显式提供 PostgreSQL 时沿用 CI 库（alembic 已建表）。
if _global_engine.url.get_backend_name() == "sqlite":
    Base.metadata.create_all(_global_engine)


def _pg_test_engine():
    """PG 真实链路测试专用引擎（**绝不拿主库开刀**）。

    选库规则：
    1. ``TEST_DATABASE_URL`` 优先——CI 的 postgres-integration job 会建一个
       专用库 ``techyard_test`` 并指到这里；
    2. 否则接受 ``DATABASE_URL``，但**仅当库名以 ``_test`` 结尾**；
    3. 其余一律返回 None（用例 skip）。

    为什么要这么严：这些用例会 TRUNCATE 全部业务表。若直接连开发者/走查
    用的主库，一次测试就会把管理员账号、小院、文档与调用记录全清空。
    """
    url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not url:
        url = os.environ.get("DATABASE_URL", "").strip()
        name = url.rsplit("/", 1)[-1].split("?")[0].strip()
        if not name.endswith("_test"):
            return None
    if not url.startswith("postgres"):
        return None
    return create_engine(url, pool_pre_ping=True)


def _pg_truncate(engine) -> None:
    """清空 PostgreSQL 公共 schema 下的全部业务表。

    直接问 PG 要表名（而不是读 ORM metadata）：迁移可能建了模型里没有的表，
    漏清会让上一个用例的数据渗进下一个。
    """
    with engine.begin() as conn:
        rows = conn.exec_driver_sql(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
        ).fetchall()
    if not rows:
        return
    tables = ", ".join(f'"{row[0]}"' for row in rows)
    with engine.begin() as conn:
        conn.exec_driver_sql(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE")


@pytest.fixture()
def pg_db_session():
    """真实 PostgreSQL + pgvector 会话（审查 D-01/D-05/D-22 的根因护栏）。

    背景：三个 P0 缺陷都**只在 PostgreSQL 上复现**——
    - D-01：pgvector 列读回 numpy.ndarray，`if not vec:` 抛 ValueError；
    - D-05/D-22：GROUP BY 里的常量若渲染成绑定参数，PG 直接报
      "column must appear in the GROUP BY clause"，指挥官总览 500；
    - 检索排序：pgvector 的 ``<=>`` 距离算子路径与 SQLite 全量扫描路径完全不同。

    而此前 CI 的所有单测都跑在 SQLite 内存库上——**生产库从未被测过**。
    这里提供绑定到**专用 PG 测试库**的会话：非专用库环境直接 skip（本地
    SQLite 与开发者主库都不受影响），每个用例前后 TRUNCATE 全部表互不污染。
    """
    engine = _pg_test_engine()
    if engine is None:
        pytest.skip(
            "需要专用 PostgreSQL 测试库：设置 TEST_DATABASE_URL，"
            "或让 DATABASE_URL 指向库名以 _test 结尾的库"
        )
    _pg_truncate(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        _pg_truncate(engine)
        engine.dispose()


@pytest.fixture()
def pg_client(pg_db_session):
    """绑定到真实 PostgreSQL 的 API 客户端。"""
    app = create_app()

    def override_db():
        try:
            yield pg_db_session
        finally:
            pass

    app.dependency_overrides[get_db_session] = override_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def db_session_factory():
    """可注入 worker_tasks._session_factory 的会话工厂（与 API 测试共用同一内存库）。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    yield TestingSession
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture()
def client(db_session):
    app = create_app()

    def override_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db_session] = override_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture(autouse=True)
def _offline_team_web(monkeypatch):
    """班组运行会主动联网取证：测试必须离线，单测里显式替换 search_web 验证降级。"""

    async def no_web(*args, **kwargs):
        return {"results": []}

    monkeypatch.setattr("app.modules.agents.teams.research.search_web", no_web)
