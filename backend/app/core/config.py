"""全局配置。所有配置项必须有类型、默认值与说明；密钥只从环境变量读取。"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 明文默认占位值：一旦在非 development 环境仍在使用，必须拒绝启动。
DEFAULT_APP_SECRET_KEY = "replace-with-long-random-secret-min-32-chars"
# 难以猜测的专有用户名是生产 bootstrap 前提；默认 admin 仅限开发。
WEAK_PLATFORM_ADMIN_USERNAMES = {"admin", "root", "system", "platform_admin", "administrator"}


class StartupSecurityError(RuntimeError):
    """生产启动期安全校验失败：以异常强制 fail-fast，禁止带着不安全配置上线。"""


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
    tasks_inline_mode: bool = Field(
        default=False,
        description="桌面单机模式（TASKS_INLINE_MODE）：true 时创建/重试任务跳过 Celery 投递，"
        "直接走本机后台线程兜底路径执行；语义等同强制开启 worker_sync_fallback 的执行分支",
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

    # --- 运行安全与运营（整改升级新增） ---
    signup_enabled: bool = Field(
        default=True, description="是否开放租户自助注册；试运行/公网部署建议 false（邀请制）"
    )
    password_min_length: int = Field(
        default=8, ge=6, le=64, description="创建/注册场景密码最小长度；生产环境强制不低于 10"
    )
    alert_webhook_url: str = Field(
        default="",
        description="运维告警 webhook（POST JSON）；为空时通知降级为带 [ALERT] 标记的日志",
    )
    metrics_enabled: bool = Field(
        default=True, description="是否在 /metrics 暴露 Prometheus 文本指标"
    )
    enforce_model_egress_guard: bool = Field(
        default=False,
        description="true 时模型/供应商端点也执行公网 SSRF 校验（会阻断内网模型服务，"
        "适用于纯公网 SaaS；内网私有化保持 false，但 link-local/云元数据地址始终阻断）",
    )
    task_queued_timeout_minutes: int = Field(
        default=30,
        description="排队超时：queued 超过该时长的任务由回收任务置 failed(QUEUED_TIMEOUT)",
    )
    task_running_timeout_minutes: int = Field(
        default=120,
        description="执行超时：running 超过该时长且 updated_at 无推进的任务置 failed",
    )
    llm_precheck_enabled: bool = Field(
        default=False,
        description="候选知识 LLM 深检开关：true 时单条审核响应附带 deep_precheck 判定"
        "（展示性预筛，不阻断审核；机器预检不受此开关影响，始终执行）",
    )

    @property
    def platform_admin_username_list(self) -> list[str]:
        return [u.strip() for u in self.platform_admin_usernames.split(",") if u.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() == "production"

    def validate_for_runtime(self) -> tuple[list[str], list[str]]:
        """启动期安全校验。返回 (必须拒绝启动的错误, 仅告警的提示)。

        规范 §5.1/§15：密钥与凭证属 L3；生产环境使用公开占位密钥等于任意伪造令牌。
        """
        errors: list[str] = []
        warnings: list[str] = []
        if self.is_production:
            if self.app_secret_key == DEFAULT_APP_SECRET_KEY:
                errors.append("APP_SECRET_KEY 仍为公开占位值，任何人都可伪造令牌，生产环境必须更换")
            if len(self.app_secret_key) < 32:
                errors.append("APP_SECRET_KEY 长度不足 32 位，不满足生产最低强度")
            weak = [
                u
                for u in self.platform_admin_username_list
                if u.lower() in WEAK_PLATFORM_ADMIN_USERNAMES
            ]
            if weak:
                errors.append(
                    f"PLATFORM_ADMIN_USERNAMES 含易猜弱名单 {weak}：生产必须改为专有用户名"
                )
            if self.signup_enabled:
                warnings.append(
                    "SELF-SERVICE 注册在生产环境保持开放，建议试运行期设置 SIGNUP_ENABLED=false"
                )
            if self.database_url.startswith("sqlite"):
                errors.append("生产环境禁止使用 SQLite，必须配置 PostgreSQL")
        else:
            if self.app_secret_key == DEFAULT_APP_SECRET_KEY:
                warnings.append("APP_SECRET_KEY 使用默认占位值（仅限开发/测试环境）")
        return errors, warnings

    def enforce_runtime_safety(self) -> list[str]:
        """非生产返回告警列表并放行；生产发现阻断项直接抛 StartupSecurityError。"""
        errors, warnings = self.validate_for_runtime()
        if errors:
            raise StartupSecurityError("生产启动安全校验失败: " + "；".join(errors))
        return warnings


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
