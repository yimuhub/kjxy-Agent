"""作物诊断 Agent（code=crop_diagnosis，version=1.0.0）。

统一运行接口：async run(input_data, context) -> AgentResult。
db 会话从 context.metadata["db"] 获取；所有知识查询经 service 层带 tenant_id 隔离。
"""

from __future__ import annotations

from typing import Any

from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.agents import get_global_registry
from app.modules.agents.definitions import get_definition
from app.modules.agents.schemas import AgentContext, AgentResult
from app.modules.crop_diagnosis.schemas import DiagnosisOutput
from app.modules.crop_diagnosis.service import diagnose

log = get_logger(__name__)


class CropDiagnosisAgent:
    """作物诊断辅助 Agent：症状/环境描述 -> 候选问题 + 证据 + 风险说明。"""

    code = "crop_diagnosis"
    version = "1.0.0"
    definition = get_definition("crop_diagnosis")

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
                warnings=["诊断辅助未能执行：运行上下文缺少数据库会话。"],
            )
        try:
            raw = diagnose(
                db,
                tenant_id=context.tenant_id,
                user_id=context.actor_id,
                input_data=input_data or {},
            )
        except AppError as exc:
            log.warning("crop_diagnosis failed: %s %s", exc.code, exc.message)
            return AgentResult(
                status="failed",
                output={"error_code": exc.code, "error": exc.message},
                warnings=["诊断辅助未能执行：输入不合法。"],
            )
        output = DiagnosisOutput.model_validate(raw)

        warnings: list[str] = []
        if output.needs_expert_review:
            warnings.append(
                "本结果为诊断辅助，非确定诊断；涉及用药或症状较重/证据不足的情形，"
                "请务必经专家复核后再做生产决策。"
            )

        return AgentResult(
            status="awaiting_review" if output.needs_expert_review else "succeeded",
            output=output.model_dump(),
            citations=[e.model_dump() for e in output.evidence],
            warnings=warnings,
            confidence=output.confidence,
        )


def _register_agent() -> None:
    get_global_registry().register(CropDiagnosisAgent.code, CropDiagnosisAgent())
    log.info("registered agent %s v%s", CropDiagnosisAgent.code, CropDiagnosisAgent.version)


_register_agent()
