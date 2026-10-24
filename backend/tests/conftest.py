"""测试公共 fixture。

默认使用 SQLite 内存库跑单元/安全测试；需要 pgvector 的用例自行标记跳过。
集成测试需要真实 PostgreSQL 时通过环境变量 DATABASE_URL 覆盖。

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

os.environ["APP_SECRET_KEY"] = "test-secret-key-min-32-chars-for-unit-tests"
os.environ["LLM_PROVIDER"] = "fake"
os.environ["EMBEDDING_PROVIDER"] = "mock"
os.environ["POLICY_MODULE_ENABLED"] = "true"
os.environ["CELERY_BROKER_URL"] = "memory://"
os.environ["CELERY_RESULT_BACKEND"] = "cache+memory://"
os.environ["LOCAL_STORAGE_PATH"] = str(_TEST_STORAGE_DIR)
os.environ["TASK_ENQUEUE_TIMEOUT_SECONDS"] = "0.5"
os.environ["WORKER_SYNC_FALLBACK"] = "false"
os.environ["BOT_MANAGER_DISABLED"] = "1"  # 测试永不真起机器人长连接

from app.core.deps import get_db_session  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.main import create_app  # noqa: E402


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
