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
    # 服务器版（免 Docker）默认 SQLite；生产必须显式开启，启动时仍会告警。
    allow_sqlite_in_production: bool = Field(
        default=False, description="生产环境允许 SQLite（仅服务器单机版显式开启）"
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
    # 供应商 API Key 加密主密钥（Fernet key）；建议生产显式配置
    supplier_key_secret: str = ""
    # 技能签名私钥种子（base64url 编码的 32 字节，Ed25519）；留空则由 app_secret_key
    # 域分离派生。显式配置可独立于登录密钥轮换；更换后旧签名会如实判为无效。
    skill_signing_seed: str = ""

    # SSO/OIDC（v3.18）：平台总开关默认关；每个小院在「单点登录」里单独配置 IdP。
    sso_enabled: bool = Field(default=False, description="是否启用 SSO/OIDC 登录")
    sso_state_ttl_seconds: int = Field(
        default=300, ge=60, le=900, description="授权请求 state 有效期（秒）"
    )
    sso_exchange_ttl_seconds: int = Field(
        default=60, ge=30, le=300, description="回调换取登录态的一次性短码有效期（秒）"
    )
    sso_http_timeout_seconds: int = Field(
        default=10, ge=3, le=30, description="访问 IdP discovery/token/jwks 的超时（秒）"
    )

    # A2A 互通（v3.20）：平台总开关默认关；开启后对外开放 Agent Card 与入站 RPC。
    a2a_enabled: bool = Field(default=False, description="是否启用 A2A 协作")
    a2a_http_timeout_seconds: int = Field(
        default=15, ge=3, le=60, description="访问协作方 Agent Card / RPC 的超时（秒）"
    )
    llm_model: str = ""
    llm_timeout_seconds: int = 60
    llm_max_tokens: int = 2048

    # 对话内方案合成（v3.14）：输出上限必须显式给，否则供应商默认 max_tokens 会把
    # 一份完整方案截成一段话；知识库证据够多时不再联网，省搜索配额也省时延。
    plan_max_tokens: int = Field(
        default=6000, ge=512, le=32000, description="方案成稿单次生成的输出 Token 上限"
    )
    plan_timeout_seconds: int = Field(
        default=180,
        ge=30,
        le=600,
        description="方案成稿单次生成超时（整份方案产出远超全局 60 秒默认值）",
    )
    plan_kb_min_hits: int = Field(
        default=3, ge=1, le=20, description="知识库命中达到该条数即视为证据充足，跳过联网检索"
    )
    plan_repair_passes: int = Field(default=1, ge=0, le=2, description="缺失章节自动补写的最大轮数")

    # 多模态（v3.11）：均走当前生效供应商的 OpenAI 兼容端点。
    # 供应商模型不支持对应能力时返回明确错误，绝不伪造转写/识别结果。
    asr_model: str = Field(
        default="whisper-1", description="语音转写模型名（POST {base_url}/audio/transcriptions）"
    )
    asr_language: str = Field(default="zh", description="语音转写语言提示")
    transcribe_max_mb: int = Field(default=10, ge=1, le=100, description="语音文件大小上限（MB）")
    # 本机离线模型（v3.30）：sherpa-onnx 的 ONNX 模型按机器存放，不随安装包分发。
    # 留空则按操作系统应用数据目录解析（macOS/Windows/Linux 各走各的约定，见 asr.storage）。
    asr_model_dir: str = Field(
        default="", description="本机语音模型目录（留空=操作系统默认应用数据目录）"
    )
    vision_model: str = Field(default="", description="图片理解模型名；留空则复用当前供应商的模型")
    vision_max_mb: int = Field(default=8, ge=1, le=50, description="图片理解原图大小上限（MB）")

    embedding_provider: str = Field(default="mock", description="mock|openai_compatible")
    embedding_model: str = ""
    embedding_dim: int = 1536
    embedding_batch_size: int = Field(
        default=64,
        ge=1,
        le=512,
        description="单次向量化请求的文本条数上限：大文档按批切分，避免超出供应商请求体限制",
    )

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
    retrieval_scan_limit: int = Field(
        default=2000,
        description="检索候选扫描上限：SQLite 全量扫描条数 / pgvector 过采样上限；"
        "大知识库可按内存与延迟调高（2000 条 chunk 约数 MB 内存）",
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

    #: 受信反代链长度（审计 S-8）：0=直连部署取 socket 对端；N=前面有 N 台自家
    #: 反代/负载均衡，登录限流键取 X-Forwarded-For 倒数第 N 跳（不盲信整条 XFF）
    trusted_proxy_count: int = Field(
        default=0,
        description="受信反代/负载均衡层数；反代部署设为实际层数，登录限流才按真实客户端 IP 计数",
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
    tool_approval_self_decide: str = Field(
        default="role",
        description=(
            "高风险工具审批的自批策略：role（默认）=具备审批角色（空间管理员/专家）即可批"
            "本空间全部审批单，含自己发起的，审计标 self_approved；solo=仅当发起人之外无其他"
            "可审批人时允许自批；never=一律禁止自批（严格四眼合规场景）。"
            "普通成员无论如何都无审批权，只能发起。"
        ),
    )

    log_level: str = "INFO"

    # --- 运行安全与运营（整改升级新增） ---
    signup_enabled: bool = Field(
        default=True, description="是否开放小院自助注册；试运行/公网部署建议 false（邀请制）"
    )
    signup_rate_limit_max_attempts: int = Field(
        default=5, ge=1, description="自助注册：同一 IP 窗口内最多注册次数（含成功）"
    )
    signup_rate_limit_window_seconds: int = Field(
        default=3600, ge=60, description="自助注册限流窗口（秒）"
    )
    password_min_length: int = Field(
        default=8, ge=6, le=64, description="创建/注册场景密码最小长度；生产环境强制不低于 10"
    )
    alert_webhook_url: str = Field(
        default="",
        description="运维告警 webhook（POST JSON）；为空时通知降级为带 [ALERT] 标记的日志",
    )
    metrics_enabled: bool = Field(
        default=False,
        description=(
            "是否在 /metrics 暴露 Prometheus 文本指标。默认关闭：未鉴权时可读出 QPS、"
            "耗时分布与部署形态；内网抓取时再显式打开"
        ),
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

    # --- 手机配对（扫码接入局域网，Wave B） ---
    pairing_code_ttl_seconds: int = Field(
        default=300, description="配对码有效期（秒）：过期自动作废，需重新生成"
    )
    pairing_max_active_codes: int = Field(
        default=5, description="单小院未用配对码上限：超限拒绝新建，防止码堆积被慢爆破"
    )
    pairing_device_token_expire_days: int = Field(
        default=30, description="配对成功签发的设备令牌有效期（天）；过期需重新扫码配对"
    )
    pairing_advertise_port: int = Field(
        default=8000,
        description="pair_url 携带的对外服务端口；必须与 API 实际监听端口一致"
        "（nginx/桌面 sidecar 部署需按对外端口覆盖默认值）",
    )

    # --- 机器人网关（IM 出向长连接接入，Wave C） ---
    bot_manager_enabled: bool = Field(
        default=True,
        description="进程内机器人连接器管家开关：桌面单机模式保持 true；"
        "多实例部署由 Redis 选主保证只有 leader 建连（企微单机器人单连接限制）",
    )
    bot_leader_lock_ttl_seconds: int = Field(
        default=20, description="连接器选主锁的 Redis TTL（秒）：leader 崩溃后最多这么久可接管"
    )
    bot_leader_renew_seconds: int = Field(
        default=15, description="选主锁续租间隔（秒）：必须小于 TTL，否则锁会被抢走"
    )
    bot_context_window_turns: int = Field(
        default=6,
        ge=0,
        description="入站消息拼接的多轮上下文滑窗轮数（0=不带上下文）；"
        "明文只存 bot_conversations.context_cache_json，用途仅限上下文拼接",
    )
    bot_context_ttl_days: int = Field(
        default=30,
        description="上下文滑窗保留天数：超过该时长无活动的会话清空 context_cache_json",
    )
    bot_idempotency_cache_size: int = Field(
        default=512, ge=1, description="每个机器人入站消息幂等 LRU 容量（防平台重推重复回答）"
    )
    bot_wecom_ws_url: str = Field(
        default="wss://openws.work.weixin.qq.com", description="企微智能机器人长连接入口地址"
    )
    bot_dingtalk_open_url: str = Field(
        default="https://api.dingtalk.com/v1.0/gateway/connections/open",
        description="钉钉 Stream 模式建连凭证接口（POST 换 endpoint+ticket）",
    )
    bot_stream_min_interval_seconds: float = Field(
        default=2.0,
        ge=0,
        description="流式回复推平台的节流间隔（秒）：企微 30 条/分/会话限频，宁可少推",
    )
    bot_consecutive_failure_limit: int = Field(
        default=5,
        ge=2,
        description="机器人连接连续失败熔断阈值：达到后自动 enabled=false，与 push 通道同口径",
    )
    bot_wecom_ping_seconds: float = Field(
        default=30.0, gt=0, description="企微长连接空闲心跳间隔（秒），官方要求 30s"
    )

    # --- 声明式 Agent 工具循环护栏（P0 内核） ---
    agent_tool_loop_max_iterations: int = Field(
        default=6,
        ge=1,
        description="单次 run 的工具循环 LLM 调用轮数上限；超限诚实标注 truncated=true",
    )
    agent_tool_loop_max_tokens: int = Field(
        default=60000,
        ge=1000,
        description="单次 run 的输入+输出 token 预算（跨 checkpoint 续跑累计）；超限标注 truncated",
    )
    agent_tool_loop_max_seconds: int = Field(
        default=180,
        ge=5,
        description="单次 run 的墙钟时长预算（步耗时累计）；超限标注 truncated",
    )

    # --- GitHub 市场源（国内网络降级，v3.2） ---
    # api.github.com 在国内常无法直连：API 基址可整体替换为路径兼容的网关；
    # raw/zip 走「官方 → 镜像按序降级」，镜像健康状态由 market.github_source 缓存。
    github_api_base: str = Field(
        default="https://api.github.com",
        description=(
            "GitHub API 基址（/search/repositories、/repos/{owner}/{repo} 等路径保持不变）；"
            "国内可整体替换为路径兼容的反代网关"
        ),
    )
    github_raw_mirrors: str = Field(
        default=(
            # 前缀式加速器（拼接规则：镜像前缀 + "/" + 完整原始 URL）：
            "https://ghfast.top,"
            "https://gh-proxy.com,"
            # 整站改写型镜像（拼接规则：host 替换 raw.githubusercontent.com，路径不变）：
            "https://raw.gitmirror.com{path}"
        ),
        description=(
            "raw.githubusercontent.com 镜像（逗号分隔，官方失败后按序尝试）。"
            "两种拼接写法：①前缀式——镜像前缀后直接拼完整原始 URL；"
            "②模板式——含 {url}（完整原始 URL）或 {path}（路径部分）占位符。"
            "详见根 .env.example 注释"
        ),
    )
    github_zip_mirrors: str = Field(
        default="https://ghfast.top,https://gh-proxy.com",
        description="仓库 zip 下载镜像（逗号分隔前缀式：镜像前缀 + 完整原始 zip URL）",
    )

    @property
    def platform_admin_username_list(self) -> list[str]:
        return [u.strip() for u in self.platform_admin_usernames.split(",") if u.strip()]

    @property
    def github_raw_mirror_list(self) -> list[str]:
        return [m.strip() for m in self.github_raw_mirrors.split(",") if m.strip()]

    @property
    def github_zip_mirror_list(self) -> list[str]:
        return [m.strip() for m in self.github_zip_mirrors.split(",") if m.strip()]

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
            if self.llm_provider == "fake":
                warnings.append("LLM_PROVIDER=fake：模型回答来自内置 mock，生产环境应接入真实模型")
            if self.embedding_provider != "openai_compatible":
                warnings.append(
                    "EMBEDDING_PROVIDER 仍为 mock：向量检索不具备真实语义能力，"
                    "生产环境应配置 OpenAI 兼容向量模型"
                )
            if self.database_url.startswith("sqlite"):
                if self.allow_sqlite_in_production:
                    warnings.append(
                        "生产环境使用 SQLite（ALLOW_SQLITE_IN_PRODUCTION=true）：仅适合单机小规模，"
                        "并发升高前请迁移到 PostgreSQL"
                    )
                else:
                    errors.append(
                        "生产环境禁止使用 SQLite，必须配置 PostgreSQL；"
                        "仅服务器单机版可显式设置 ALLOW_SQLITE_IN_PRODUCTION=true"
                    )
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
