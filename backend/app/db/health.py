"""生产就绪检查：数据库 schema 是否已迁移到 Alembic head。"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import engine as default_engine

log = get_logger(__name__)

_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def schema_ready(engine: Engine | None = None) -> bool:
    """生产环境要求 alembic_version 等于脚本 head；开发/测试不阻断。"""
    if settings.app_env != "production":
        return True
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        head = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_current_head()
        with (engine or default_engine).connect() as conn:
            current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        ok = bool(head) and current == head
        if not ok:
            log.error("schema not ready: current=%s head=%s", current, head)
        return ok
    except Exception as exc:  # 数据库不可达/表缺失都视为未就绪
        log.error("schema check failed: %s", type(exc).__name__)
        return False
