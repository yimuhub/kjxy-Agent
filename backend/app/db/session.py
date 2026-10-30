"""数据库会话。"""

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


def ensure_sqlite_parent_dir(database_url: str) -> None:
    """SQLite 文件型数据库：自动创建父目录。

    SQLite 自己不会创建不存在的父目录，否则首次建表/连接会报
    "unable to open database file"。幂等，目录已存在时无操作。
    """

    if not database_url.startswith("sqlite:"):
        return
    # 去掉 sqlite:/// 前缀，剩下就是文件路径（相对/绝对/:memory:）
    _, _, path = database_url.partition("sqlite:///")
    path = path.split("?", 1)[0].strip()
    if not path or path == ":memory:":
        return
    parent = Path(path).expanduser().parent
    if str(parent) not in ("", "."):
        parent.mkdir(parents=True, exist_ok=True)


ensure_sqlite_parent_dir(settings.database_url)

_IS_SQLITE = settings.database_url.startswith("sqlite")
connect_args = {"check_same_thread": False} if _IS_SQLITE else {}
engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args=connect_args)

if _IS_SQLITE:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
        """WAL + NORMAL：读写并发不互相阻塞，显著降低并发请求下的锁等待。

        ``journal_mode`` 对内存库自动降级为 memory（无副作用），因此可无条件执行。
        """
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=5000")
        finally:
            cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
