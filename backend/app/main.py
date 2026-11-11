"""FastAPI 应用入口。"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.router import router as v1_router
from app.core.config import settings
from app.core.context import metrics
from app.core.errors import (
    AppError,
    app_error_handler,
    http_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestIDMiddleware

log = get_logger(__name__)


def _startup_wiring() -> None:
    """应用启动时装配：工具元数据 seed + knowledge.search / workorder.dispatch /
    sensor.query / skill.search / skill.generate / skill.install / agent.generate 工具注册。"""
    try:
        from app.db.session import SessionLocal
        from app.modules.agents.agent_tools import register_agent_tools
        from app.modules.devices.agent_tool import register_sensor_query_tool
        from app.modules.docs.tool import register_doc_generate_tool
        from app.modules.growth.compliance import register_compliance_tool
        from app.modules.growth.poster import register_image_tool
        from app.modules.growth.toolseed import seed_growth_tools
        from app.modules.knowledge.service import register_knowledge_search_tool
        from app.modules.llm.video_tool import register_video_tool
        from app.modules.local_runtime.tool import register_computer_use_tool
        from app.modules.market.skill_tools import register_skill_agent_tools
        from app.modules.search.tools import register_search_tools
        from app.modules.tools.registry import seed_builtin_tools
        from app.modules.tools.workorder_tool import register_workorder_tool
        from app.modules.workspace.tool import register_workspace_tool

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
                from app.modules.commander.evaluator_tasks import (
                    start_inline_scheduler as start_eval_poll,
                )
                from app.modules.commander.iterations_tasks import (
                    start_inline_scheduler as start_iteration_poll,
                )
                from app.modules.commander.learning_tasks import (
                    start_inline_scheduler as start_learning_poll,
                )
                from app.modules.devices.worker_tasks import (
                    start_inline_scheduler as start_devices_poll,
                )
                from app.modules.farm.worker_tasks import (
                    start_inline_scheduler as start_workorder_escalate,
                )
                from app.modules.media.tasks import (
                    start_inline_scheduler as start_media_poll,
                )
                from app.modules.memory.tasks import (
                    start_inline_scheduler as start_memory_poll,
                )
                from app.modules.self_improve.worker_tasks import (
                    start_inline_scheduler as start_si_poll,
                )
                from app.workers.heartbeat_tasks import (
                    start_inline_scheduler as start_ops_maintenance,
                )

                start_devices_poll()
                start_si_poll()
                start_memory_poll()
                start_eval_poll()
                start_learning_poll()
                start_iteration_poll()
                # 对话内视频续查（v3.73）：5 秒一轮，桌面与 beat 同口径
                start_media_poll()
                # 逾期工单升级（v3.71）：Celery beat 每 6h 一次，桌面 inline 同口径补齐
                start_workorder_escalate()
                # 超时任务回收 + legacy 配额月清零（v3.72 / 审查 O6）：
                # 这两项原来只挂在 beat 上，桌面没有 worker/beat 就成了永久缺口
                start_ops_maintenance()
        except Exception as e:
            log.warning("inline schedulers start failed: %s", e)

        db = SessionLocal()
        try:
            seed_builtin_tools(db)
            seed_growth_tools(db)
            from app.modules.market.formal_writing_skills import (
                bind_formal_skills_to_agents,
                ensure_formal_writing_skills_for_all_yards,
                seed_formal_writing_skills,
            )
            from app.modules.market.office_skills import (
                bind_office_skills_to_office_agents,
                ensure_office_skills_for_all_yards,
                seed_office_skills,
            )

            seed_office_skills(db)
            seed_formal_writing_skills(db)
            ensure_office_skills_for_all_yards(db)
            ensure_formal_writing_skills_for_all_yards(db)
            bind_office_skills_to_office_agents(db)
            bind_formal_skills_to_agents(db)
            db.commit()
        except Exception as e:
            db.rollback()
            log.warning("seed builtin tools failed: %s", e)
        finally:
            db.close()

        if settings.is_production:
            # S-6：seed/repair 已经挡住在生产执行，但存量库里可能本来就躺着演示账号，
            # 启动时扫一遍并高声提醒——弱口令的风险不在脚本，而在库里那批账号。
            from app.db.seed import active_demo_usernames

            scan_db = SessionLocal()
            try:
                weak = active_demo_usernames(scan_db)
            finally:
                scan_db.close()
            if weak:
                log.warning(
                    "生产库仍有演示账号可用文档弱口令登录：%s —— 请立即改密或停用该账号",
                    ", ".join(weak),
                )

        # 官方技能库同步放到后台线程：没有配置源时立即返回，断网/超时都不阻塞启动
        def _sync_official_library() -> None:
            from app.db.session import SessionLocal
            from app.modules.library.service import run_due_library_syncs

            lib_db = SessionLocal()
            try:
                run_due_library_syncs(lib_db)
            except Exception as e:  # noqa: BLE001 —— 库同步失败不得影响主流程
                lib_db.rollback()
                log.warning("official library sync failed: %s", e)
            finally:
                lib_db.close()

        try:
            import threading

            threading.Thread(
                target=_sync_official_library, name="official-library-sync", daemon=True
            ).start()
        except Exception as e:
            log.warning("official library sync scheduler start failed: %s", e)

        register_knowledge_search_tool()
        register_workorder_tool()
        register_sensor_query_tool()
        register_search_tools()
        register_skill_agent_tools()
        register_agent_tools()
        register_compliance_tool()
        register_image_tool()
        register_video_tool()
        register_doc_generate_tool()
        register_computer_use_tool()
        register_workspace_tool()
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
    app = FastAPI(title="科技小院多智能体平台", version="3.78.0", lifespan=lifespan)
    app.add_middleware(RequestIDMiddleware)
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    # 框架自带的 404/405/403 也套统一信封：否则前端拿到 {"detail": ...} 会误报「网络连接失败」
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
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
        # 桌面壳只接管回显归属令牌一致的引擎；服务器部署不设置该环境变量，不额外暴露信息
        instance = os.environ.get("TECHYARD_INSTANCE_TOKEN", "").strip()
        headers = {"X-TechYard-Instance": instance} if instance else None
        # 未就绪时返回 503，供编排器/探针判断
        return JSONResponse(
            status_code=200 if ready else 503,
            content={"status": "ready" if ready else "not_ready", "checks": checks},
            headers=headers,
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

    @app.get("/.well-known/agent.json", include_in_schema=False)
    @app.get("/.well-known/agent-card.json", include_in_schema=False)
    def a2a_agent_card(request: Request):
        """A2A 公开名片：平台能力与技能清单（不暴露任何小院数据）。"""
        from app.modules.a2a.service import build_agent_card

        if not settings.a2a_enabled:
            raise AppError("A2A_DISABLED", "本平台未开启 A2A 协作。", http_status=404)
        return build_agent_card(str(request.base_url), request.app.version)

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
