"""科研辅助 Agent。

code=research_assistant，version=1.0.0。
对应开发文档 V3.0 §12.3：CSV 数据导入、字段映射、缺失值报告、描述统计、图表草案。

硬规则：
- 只做描述统计：不计算 p 值、不做显著性检验、不编造文献引用。
- 统计假设必须随输出标明（见 service.ASSUMPTIONS）。
- 输出恒带 needs_review=True：本分析为自动生成的草案，须经研究人员审核。

租户隔离：本 Agent 为纯计算、不读库；但 run() 仍校验 context.tenant_id 非空。
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.agents import get_definition, get_global_registry
from app.modules.agents.schemas import AgentResult
from app.modules.research_assistant.schemas import AnalysisInput, AnalysisOutput
from app.modules.research_assistant.service import analyze_csv

log = get_logger(__name__)


class ResearchAssistantAgent:
    """科研辅助 Agent：CSV 导入、缺失值报告、描述统计与图表草案（纯计算）。"""

    code = "research_assistant"
    version = "1.0.0"
    definition = get_definition("research_assistant")

    async def run(self, input_data: dict[str, Any], context: Any) -> AgentResult:
        tenant_id = str(getattr(context, "tenant_id", "") or "")
        if not tenant_id:
            return AgentResult(
                status="failed",
                output={},
                warnings=["tenant_id 不能为空：缺少租户上下文，拒绝执行。"],
            )

        try:
            req = AnalysisInput(**(input_data or {}))
        except ValidationError as e:
            details = "; ".join(
                f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()
            )
            return AgentResult(
                status="failed",
                output={},
                warnings=[f"输入参数不合法：{details}。"],
            )

        extra_warnings: list[str] = []
        if req.analysis_type != "descriptive":
            extra_warnings.append(
                f"analysis_type={req.analysis_type} 暂不支持，已按 descriptive（描述统计）执行。"
            )

        try:
            result_dict = analyze_csv(req.csv_text, req.field_mapping)
        except AppError as e:
            return AgentResult(status="failed", output={}, warnings=[e.message])

        output = AnalysisOutput(**result_dict)
        return AgentResult(
            status="succeeded",
            output=output.model_dump(),
            citations=[],
            warnings=extra_warnings + output.warnings,
        )


def _register_agent() -> None:
    try:
        get_global_registry().register(ResearchAssistantAgent.code, ResearchAssistantAgent())
        log.info(
            "registered agent %s v%s", ResearchAssistantAgent.code, ResearchAssistantAgent.version
        )
    except Exception as e:
        log.warning("agent registration skipped: %s", e)


_register_agent()
