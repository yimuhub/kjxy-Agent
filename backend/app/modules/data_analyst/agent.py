"""数据汇总 Agent（data_analyst v1.0.0）。

多源数据汇总与可回溯报表：仅从授权数据源（tasks / knowledge_documents /
llm_calls）读取租户内数据，报表中每个关键数字可回溯到查询条件、
时间范围和数据源（Metric.trace），报表输出可重复生成
（report_id / generated_at / parameters / version 均记录在输出中）。

无状态，不建新表。
"""

from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.agents import AgentContext, AgentResult, get_definition, get_global_registry
from app.modules.data_analyst.schemas import ReportInput
from app.modules.data_analyst.service import build_report

log = get_logger(__name__)


class DataAnalystAgent:
    """数据汇总 Agent。"""

    code = "data_analyst"
    version = "1.0.0"
    definition = get_definition("data_analyst")

    async def run(self, input_data: dict, context: AgentContext) -> AgentResult:
        db = (context.metadata or {}).get("db")
        if db is None:
            raise AppError(
                "INVALID_REQUEST",
                "缺少数据库会话（context.metadata['db']）。",
                http_status=400,
            )
        report_input = ReportInput(**(input_data or {}))
        report = build_report(db, context.tenant_id, report_input)
        return AgentResult(
            status="succeeded",
            output=report,
            warnings=list(report.get("warnings", [])),
            confidence=1.0,
        )


def _register_agent() -> None:
    try:
        get_global_registry().register(DataAnalystAgent.code, DataAnalystAgent())
        log.info("registered agent %s v%s", DataAnalystAgent.code, DataAnalystAgent.version)
    except Exception as e:
        log.warning("agent registration skipped: %s", e)


_register_agent()
