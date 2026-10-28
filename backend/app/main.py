"""FastAPI 应用入口。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.v1.router import router as v1_router
from app.core.config import settings
from app.core.context import metrics
from app.core.errors import (
    AppError,
    app_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestIDMiddleware

log = get_logger(__name__)


def _startup_wiring() -> None:
    """应用启动时装配：工具元数据 seed + knowledge.search / workorder.dispatch /
    sensor.query / skill.search / skill.generate / skill.install 工具注册。"""
    try:
        from app.db.session import SessionLocal
        from app.modules.devices.agent_tool import register_sensor_query_tool
        from app.modules.knowledge.service import register_knowledge_search_tool
        from app.modules.market.skill_tools import register_skill_agent_tools
        from app.modules.search.tools import register_search_tools
        from app.modules.tools.registry import seed_builtin_tools
        from app.modules.tools.workorder_tool import register_workorder_tool

        # 注册各模块的任务 handler（native 模式无 Celery worker 时，API 进程的
        # 同步降级线程依赖这里完成注册；spec §7.4 的 knowledge_qa 必须可用）
        try:
            import app.modules.agriculture.worker_tasks  # noqa: F401
            import app.modules.devices.worker_tasks  # noqa: F401
            import app.modules.knowledge.worker_tasks  # noqa: F401
            import app.modules.self_improve.worker_tasks  # noqa: F401
        except Exception as e:
            log.warning("worker task registration import failed: %s", e)
        # 桌面单机模式：standalone 永不启动 beat，连接器轮询/自我总结巡检走进程内 daemon 线程
        try:
            if settings.tasks_inline_mode:
                from app.modules.devices.worker_tasks import (
                    start_inline_scheduler as start_devices_poll,
                )
                from app.modules.self_improve.worker_tasks import (
                    start_inline_scheduler as start_si_poll,
                )

                start_devices_poll()
                start_si_poll()
        except Exception as e:
            log.warning("inline schedulers start failed: %s", e)

        db = SessionLocal()
        try:
            seed_builtin_tools(db)
            db.commit()
        except Exception as e:
            db.rollback()
            log.warning("seed builtin tools failed: %s", e)
        finally:
            db.close()
        register_knowledge_search_tool()
        register_workorder_tool()
        register_sensor_query_tool()
        register_search_tools()
        register_skill_agent_tools()
    except Exception as e:
        log.warning("startup wiring failed: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.log_level)
    # 生产 fail-fast：默认密钥/弱管理员名单/SQLite 等阻断项直接拒绝启动（规范 §5.1/§15）
    for warning in settings.enforce_runtime_safety():
        log.warning("startup safety warning: %s", warning)
    log.info("tech-yard-platform starting, env=%s", settings.app_env)
    _startup_wiring()
    # IM 机器人管家：拉起 enabled 机器人长连接（测试环境/显式关闭时内部自行跳过）
    import asyncio

    from app.modules.bots.manager import manager as bot_manager

    _bot_resume_task = asyncio.create_task(bot_manager.resume_enabled_bots())  # noqa: RUF006
    yield
    log.info("tech-yard-platform stopping")


def create_app() -> FastAPI:
    app = FastAPI(title="科技小院多智能体平台", version="3.7.0", lifespan=lifespan)
    app.add_middleware(RequestIDMiddleware)
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    @app.get("/health/live", tags=["health"])
    def live():
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    def ready():
        # 桌面单机模式（TASKS_INLINE_MODE）：任务在 API 进程内执行、不需要 broker，
        # redis 不属于就绪依赖（standalone 契约，见 backend/standalone.py）
        checks = {"database": _check_db()}
        if not settings.tasks_inline_mode:
            checks["redis"] = _check_redis()
        checks["schema"] = _check_schema()
        ready = all(checks.values())
        # 未就绪时返回 503，供编排器/探针判断
        return JSONResponse(
            status_code=200 if ready else 503,
            content={"status": "ready" if ready else "not_ready", "checks": checks},
        )

    @app.get("/health/worker", tags=["health"])
    def worker_health():
        """Worker 心跳新鲜度（规范 §18.4：不得用 API 健康代替 Worker 健康）。"""
        from app.workers.heartbeat import worker_heartbeat_status

        return worker_heartbeat_status()

    @app.get("/metrics", include_in_schema=False)
    def metrics_endpoint():
        """Prometheus 文本格式最小实现（进程内计数）。"""
        if not settings.metrics_enabled:
            return Response(status_code=404)
        return Response(metrics.exposition(), media_type="text/plain; version=0.0.4; charset=utf-8")

    app.include_router(v1_router, prefix="/api/v1", tags=["v1"])
    return app


def _check_db() -> bool:
    try:
        from sqlalchemy import text

        from app.db.session import engine

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def _check_schema() -> bool:
    from app.db.health import schema_ready

    return schema_ready()


def _check_redis() -> bool:
    try:
        import redis

        client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2)
        client.ping()
        return True
    except Exception:
        return False


app = create_app()
