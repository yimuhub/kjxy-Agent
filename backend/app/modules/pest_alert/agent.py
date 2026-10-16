"""虫情预警（code=pest_alert，version=1.0.0）。

统一运行接口：async run(input_data, context) -> AgentResult。
db 会话从 context.metadata["db"] 获取；所有知识查询经 service 层带 tenant_id 隔离。

本文件由脚手架 app.modules.agents.scaffold 自动生成：
- import 时先做"配置式注册"（register_agent_definition 登记元数据，
  定义已存在则直接复用），再向 global_registry 自注册实例——双保险，
  服务启动扫描模块即完成接入。
"""

from __future__ import annotations

from typing import Any

from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.agents import get_global_registry
from app.modules.agents.definitions import get_definition, register_agent_definition
from app.modules.agents.schemas import AgentContext, AgentResult
from app.modules.pest_alert.schemas import PestAlertOutput
from app.modules.pest_alert.service import run_pest_alert

log = get_logger(__name__)

_AGENT_CODE = "pest_alert"


def _ensure_definition() -> dict:
    """配置式注册：定义缺失时补登记，已存在则复用（import 幂等）。"""
    definition = get_definition(_AGENT_CODE)
    if definition is None:
        definition = register_agent_definition(
            code=_AGENT_CODE,
            name="虫情预警",
            description="根据田间监测数据生成虫害预警",
            version="1.0.0",
            allowed_tools=["knowledge.search"],
            allowed_knowledge_scopes=["tenant"],
        )
    return definition


class PestAlertAgent:
    """虫情预警：根据田间监测数据生成虫害预警（TODO：补充职责说明）。"""

    code = _AGENT_CODE
    version = "1.0.0"
    definition = _ensure_definition()

    async def run(self, input_data: dict[str, Any], context: AgentContext) -> AgentResult:
        metadata = getattr(context, "metadata", None) or {}
        db = metadata.get("db")
        if db is None:
            return AgentResult(
                status="failed",
                output={
                    "error_code": "MISSING_DB",
                    "error": "缺少数据库会话（context.metadata['db']）。",
                },
                warnings=["虫情预警未能执行：运行上下文缺少数据库会话。"],
            )
        try:
            raw = run_pest_alert(
                db,
                tenant_id=context.tenant_id,
                user_id=context.actor_id,
                input_data=input_data or {},
            )
        except AppError as exc:
            log.warning("%s failed: %s %s", self.code, exc.code, exc.message)
            return AgentResult(
                status="failed",
                output={"error_code": exc.code, "error": exc.message},
                warnings=[f"虫情预警执行失败：{exc.message}"],
            )
        output = PestAlertOutput(**raw)
        return AgentResult(
            status="succeeded",
            output=output.model_dump(),
            warnings=list(output.warnings),
            confidence=output.confidence,
        )


def _register_agent() -> None:
    try:
        get_global_registry().register(PestAlertAgent.code, PestAlertAgent())
        log.info("registered agent %s v%s", PestAlertAgent.code, PestAlertAgent.version)
    except Exception as e:
        log.warning("agent registration skipped: %s", e)


_register_agent()
