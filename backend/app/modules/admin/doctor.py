"""Doctor 体检：平台依赖与配置态自检（仅 platform_admin，GET /admin/doctor）。

借鉴 agency-orchestrator「失败即教学」规范：每个非 ok 项必须给一条可直接照做的
fix_hint（中文一句话），运维照着做就能推进下一步，而不是只报错不指路。

安全红线：所有 check 的 detail 只允许出现"是否已配置"级别的配置态描述，
绝不回显密钥/token/连接串密码等任何凭证值。
"""

from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.workers.heartbeat import worker_heartbeat_status

log = get_logger(__name__)

# backend/migrations：doctor.py 位于 backend/app/modules/admin/ 下，向上三级即 backend/
_MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"


def _check(name: str, status: str, detail: str, fix_hint: str = "") -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail, "fix_hint": fix_hint}


# ---------------------------------------------------------------- 各项检查
def _check_database(db: Session) -> dict[str, str]:
    """数据库连通：借请求会话执行 SELECT 1。"""
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        return _check(
            "database",
            "fail",
            f"数据库不可用：{type(e).__name__}",
            "检查 DATABASE_URL 配置与数据库服务状态，确认能执行 SELECT 1 后重跑体检。",
        )
    return _check(
        "database",
        "ok",
        f"连通正常（方言 {settings.database_url.split('://', 1)[0]}）",
    )


def _alembic_head() -> str | None:
    """读取 migrations/ 目录里的版本头（不连库）。"""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    return ScriptDirectory.from_config(cfg).get_current_head()


def _check_alembic(db: Session) -> dict[str, str]:
    """迁移版本：库内 alembic_version 是否已到 migrations 的版本头。"""
    db_version: str | None
    try:
        db_version = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:
        # 查询失败（多为表不存在）会让会话进入失败态，回滚保证后续检查可用
        db.rollback()
        db_version = None
    try:
        head = _alembic_head()
    except Exception as e:
        return _check(
            "alembic_version",
            "warn",
            f"无法读取迁移版本头：{type(e).__name__}",
            "确认 backend/migrations 目录随部署完整发布后重跑体检。",
        )
    if db_version is None:
        return _check(
            "alembic_version",
            "warn",
            f"库内无 alembic_version 记录（版本头={head or '未知'}）",
            "在 backend/ 目录执行 `alembic upgrade head` 把迁移推进到最新版本。",
        )
    if head is not None and db_version != head:
        return _check(
            "alembic_version",
            "warn",
            f"库内版本 {db_version} 落后于版本头 {head}",
            "在 backend/ 目录执行 `alembic upgrade head` 后再发布新代码。",
        )
    return _check("alembic_version", "ok", f"库内版本 {db_version} 与版本头一致")


def _redis_client():
    import redis

    return redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)


def _check_redis() -> dict[str, str]:
    """Redis ping：与 workers heartbeat 相同的即用即建模式（无共享单例）。"""
    try:
        _redis_client().ping()
    except Exception as e:
        return _check(
            "redis",
            "fail",
            f"Redis 不可达：{type(e).__name__}",
            "启动 Redis 服务或修正 REDIS_URL（注意密码与库号），恢复后重跑体检。",
        )
    return _check("redis", "ok", "ping 正常")


def _check_worker() -> dict[str, str]:
    """Worker 心跳新鲜度：直接复用 workers/heartbeat 的读取逻辑。"""
    status = worker_heartbeat_status()
    worker = str(status.get("worker"))
    if worker == "alive":
        if status.get("usable"):
            return _check("worker_heartbeat", "ok", f"心跳新鲜（{status.get('age_seconds')} 秒前）")
        # 心跳有，但这个 worker 消费不了本 API 的消息：多见于 worker 与 API
        # 连了不同的数据库——消息会被取走后直接丢弃，文档永远停在 queued。
        reason = str(status.get("reason") or "unknown")
        if reason == "worker_on_other_database":
            # worker 的库指纹在心跳载荷里；API 侧指纹由本进程现场计算
            # （worker_heartbeat_status 的返回不含 api_db，直接取会显示 None）。
            from app.workers.heartbeat import current_db_fingerprint

            api_db = current_db_fingerprint()
            return _check(
                "worker_heartbeat",
                "warn",
                f"worker 连的数据库与 API 不一致（worker={status.get('db')} vs api={api_db}）",
                "把 worker 与 API 指向同一个库；否则队列消息会被取走后丢弃，知识库文档永久卡在 queued。",  # noqa: E501
            )
        return _check(
            "worker_heartbeat",
            "warn",
            f"心跳来源不是 worker 进程（role={status.get('role')}, reason={reason}）",
            "确认 Celery worker 进程在跑并能访问同一 Redis 与同一个数据库。",
        )
    if worker == "stale":
        return _check(
            "worker_heartbeat",
            "warn",
            "3 分钟 TTL 内没有 worker 心跳（native 单进程模式属预期）",
            "生产形态请启动 Celery worker（celery -A app.workers.celery_app worker）并确认 beat 在跑。",  # noqa: E501
        )
    return _check(
        "worker_heartbeat",
        "warn",
        f"心跳状态 unknown(reason={status.get('reason') or 'unknown'})，无法判定 worker 存活",
        "先修复 Redis 连通性或检查 worker 进程内 heartbeat 键写入，再��看本项是否恢复。",
    )


def _check_storage() -> dict[str, str]:
    """文件存储可写：local 后端做真实写入探针，其余后端只报告。"""
    if settings.file_storage_backend != "local":
        return _check(
            "storage_writable",
            "warn",
            f"当前 FILE_STORAGE_BACKEND={settings.file_storage_backend}，doctor 不做磁盘探针",
            "对非本地存储，请用对应云厂商 CLI 验证桶可写后人工确认本项。",
        )
    path = Path(settings.local_storage_path)
    probe = path / ".doctor_probe"
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        return _check(
            "storage_writable",
            "fail",
            f"本地存储目录不可写：{type(e).__name__}",
            "修正 LOCAL_STORAGE_PATH 指向目录的属主/权限（或改用对象存储后端）后重跑体检。",
        )
    return _check("storage_writable", "ok", "本地存储目录可写")


def _mask(endpoint: str) -> str:
    """连接串脱敏：只保留 host:port/db 定位信息，绝不带密码。"""
    return endpoint.split("@")[-1].split("?")[0]


def _check_llm() -> dict[str, str]:
    """LLM provider 配置态：复用 get_status（其契约本身保证不含密钥值）。"""
    from app.modules.llm.service import get_status

    status = get_status()
    detail = (
        f"provider={status.provider}, mode={status.mode}, "
        f"密钥{'已配置' if status.configured else '未配置'}（按安全约定不回显值）"
    )
    if not status.configured:
        return _check(
            "llm_provider",
            "fail",
            detail,
            "配置 LLM_BASE_URL 与 LLM_API_KEY（或开发环境显式设 LLM_PROVIDER=fake）后重跑体检。",
        )
    return _check("llm_provider", "ok", detail)


def _check_embedding() -> dict[str, str]:
    """Embedding provider 配置态：mock 属开发占位，生产需真实提供方。"""
    provider = settings.embedding_provider
    if provider == "mock":
        return _check(
            "embedding_provider",
            "warn",
            "当前为 mock embedding（开发/CI 占位，不代表真实语义质量）",
            "需要真实向量检索时设置 EMBEDDING_PROVIDER=openai_compatible，"
            "并配置 LLM_BASE_URL/LLM_API_KEY 与 EMBEDDING_MODEL。",
        )
    configured = bool(settings.llm_base_url and settings.llm_api_key and settings.embedding_model)
    if not configured:
        return _check(
            "embedding_provider",
            "fail",
            "embedding=openai_compatible 但 LLM_BASE_URL/LLM_API_KEY/EMBEDDING_MODEL 未配齐"
            f"（端点 {_mask(settings.llm_base_url or '未配置')}）",
            "补齐 LLM_BASE_URL、LLM_API_KEY、EMBEDDING_MODEL 三项环境变量后重启服务。",
        )
    return _check(
        "embedding_provider", "ok", f"openai_compatible（模型 {settings.embedding_model}）已配置"
    )


def _check_signup() -> dict[str, str]:
    """自助注册开关提示：生产开放注册是风险项。"""
    if not settings.signup_enabled:
        return _check("signup_enabled", "ok", "自助注册已关闭（邀请制）")
    if settings.is_production:
        return _check(
            "signup_enabled",
            "warn",
            "生产环境仍开放小院自助注册",
            "试运行/公网部署请设置 SIGNUP_ENABLED=false 改为邀请制。",
        )
    return _check("signup_enabled", "ok", "开发/测试环境开放自助注册（生产建议关闭）")


def _check_startup_safety() -> dict[str, str]:
    """生产 fail-fast 项复核：复用启动期同一套 validate_for_runtime 口径。"""
    errors, warnings = settings.validate_for_runtime()
    if errors:
        return _check(
            "startup_fail_fast",
            "fail",
            "存在启动阻断项：" + "；".join(errors),
            "按 detail 逐项修正环境变量（密钥/管理员名单/数据库地址）后重启。",
        )
    if warnings:
        return _check(
            "startup_fail_fast",
            "warn",
            "安全提示：" + "；".join(warnings),
            "按 detail 提示收紧对应配置（默认密钥仅限开发环境）。",
        )
    return _check("startup_fail_fast", "ok", "启动安全项全部通过")


def _check_metrics() -> dict[str, str]:
    if settings.metrics_enabled:
        return _check("metrics", "ok", "/metrics 已暴露 Prometheus 指标")
    return _check(
        "metrics",
        "warn",
        "METRICS_ENABLED=false，/metrics 返回 404，无 Prometheus 观测",
        "需要可观测性时设置 METRICS_ENABLED=true 并让抓取端指向 /metrics。",
    )


# ---------------------------------------------------------------- 汇总
def run_doctor(db: Session) -> dict[str, Any]:
    """执行全部体检项并汇总：任一 fail → fail；否则任一 warn → warn；全过 → ok。"""
    checks = [
        _check_database(db),
        _check_alembic(db),
        _check_redis(),
        _check_worker(),
        _check_storage(),
        _check_llm(),
        _check_embedding(),
        _check_signup(),
        _check_startup_safety(),
        _check_metrics(),
    ]
    statuses = {str(c["status"]) for c in checks}
    overall = "fail" if "fail" in statuses else ("warn" if "warn" in statuses else "ok")
    log.info("doctor 体检完成：overall=%s，检查项 %d 个", overall, len(checks))
    return {"overall": overall, "checks": checks}
