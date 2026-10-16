"""全局配置。所有配置项必须有类型、默认值与说明；密钥只从环境变量读取。"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="development", description="development|production|test")
    app_name: str = Field(default="tech-yard-platform")
    app_secret_key: str = Field(default="replace-with-long-random-secret-min-32-chars")

    # 平台管理员 bootstrap：用户名在此名单中则自动授予 platform_admin 角色。
    # 生产环境请改为难以猜测的专有用户名。
    platform_admin_usernames: str = Field(default="admin")

    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    database_url: str = Field(
        default="sqlite:///./dev.db", description="生产使用 postgresql+psycopg"
    )
    redis_url: str = Field(default="redis://localhost:6379/0")
    celery_broker_url: str = Field(default="redis://localhost:6379/1")
    celery_result_backend: str = Field(default="redis://localhost:6379/2")

    file_storage_backend: str = Field(default="local")
    local_storage_path: str = Field(default="/tmp/techyard-files")
    upload_max_mb: int = 50

    llm_provider: str = Field(default="fake", description="fake|openai_compatible")
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: int = 60
    llm_max_tokens: int = 2048

    embedding_provider: str = Field(default="mock", description="mock|openai_compatible")
    embedding_model: str = ""
    embedding_dim: int = 1536

    chunk_size: int = 800
    chunk_overlap: int = 100
    retrieval_top_k: int = 5
    retrieval_min_score: float = Field(
        default=0.25, description="检索最低相似度阈值；低于此分数的片段视为知识不足"
    )
    retrieval_mode: str = Field(
        default="hybrid", description="检索模式：vector=纯向量检索，hybrid=向量+关键词混合"
    )
    retrieval_alpha: float = Field(
        default=0.7, description="混合检索权重：final = alpha*vector + (1-alpha)*keyword"
    )
    worker_sync_fallback: bool = Field(
        default=False,
        description="纯本机模式：Redis/Celery 不可用时在后台线程同步执行任务，不阻塞 API",
    )

    use_pgvector: bool = Field(
        default=True, description="PostgreSQL 下使用 pgvector 排序；sqlite/测试自动回退"
    )

    policy_module_enabled: bool = Field(
        default=True,
        description="政策模块开关：第三期（采集/匹配/申报）。设为 False 关闭入口。",
    )

    login_rate_limit_max_attempts: int = Field(
        default=10, description="登录限流：滑动窗口内允许的失败尝试次数"
    )
    login_rate_limit_window_seconds: int = Field(default=300, description="登录限流：滑动窗口秒数")

    tool_approval_ttl_hours: int = Field(
        default=24,
        description="高风险工具审批单有效期（小时）；0 表示不设有效期。批准后按此计算 expires_at。",
    )
    tool_approval_max_uses: int = Field(
        default=1,
        description="单张已批准审批单允许放行的最大次数；0 表示不限。防止一纸审批无限复用。",
    )

    log_level: str = "INFO"

    @property
    def platform_admin_username_list(self) -> list[str]:
        return [u.strip() for u in self.platform_admin_usernames.split(",") if u.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
