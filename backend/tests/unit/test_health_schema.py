from app.db import health
from sqlalchemy import create_engine, text


def _head() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(Config(str(health._ALEMBIC_INI))).get_current_head()


def test_development_does_not_block(monkeypatch):
    monkeypatch.setattr(health.settings, "app_env", "development")
    assert health.schema_ready(create_engine("sqlite://")) is True


def test_production_requires_alembic_head(monkeypatch):
    monkeypatch.setattr(health.settings, "app_env", "production")
    engine = create_engine("sqlite://")
    assert health.schema_ready(engine) is False

    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
        conn.execute(text("INSERT INTO alembic_version VALUES (:v)"), {"v": _head()})
    assert health.schema_ready(engine) is True

    with engine.begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num = 'stale'"))
    assert health.schema_ready(engine) is False
