from app.core.config import Settings


def _settings(**overrides) -> Settings:
    values = {
        "app_env": "production",
        "app_secret_key": "x" * 40,
        "database_url": "sqlite:///./prod.db",
        "platform_admin_usernames": "owner_abc123",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_production_rejects_sqlite_by_default():
    errors, _ = _settings().validate_for_runtime()
    assert any("SQLite" in e for e in errors)


def test_production_allows_sqlite_with_explicit_flag():
    errors, warnings = _settings(allow_sqlite_in_production=True).validate_for_runtime()
    assert not errors
    assert any("SQLite" in w for w in warnings)


def test_production_rejects_weak_admin_username():
    errors, _ = _settings(platform_admin_usernames="admin").validate_for_runtime()
    assert any("弱名单" in e for e in errors)


def test_production_accepts_strong_config():
    errors, _ = _settings(
        database_url="postgresql+psycopg://u:p@localhost/db"
    ).validate_for_runtime()
    assert not errors


def test_production_warns_on_mock_llm_and_embedding():
    _, warnings = _settings(
        database_url="postgresql+psycopg://u:p@localhost/db"
    ).validate_for_runtime()
    assert any("LLM_PROVIDER=fake" in w for w in warnings)
    assert any("EMBEDDING_PROVIDER" in w for w in warnings)


def test_production_silent_on_real_llm_and_embedding():
    _, warnings = _settings(
        database_url="postgresql+psycopg://u:p@localhost/db",
        llm_provider="openai_compatible",
        embedding_provider="openai_compatible",
    ).validate_for_runtime()
    assert not any("mock" in w for w in warnings)
