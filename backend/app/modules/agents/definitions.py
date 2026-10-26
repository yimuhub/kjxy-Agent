"""Agent 定义元数据（开发文档 §8.1 格式）。

agriculture_qa 的真实实现由 app/modules/agriculture/qa_agent.py 提供并自行注册；
此处仅保留其元数据声明，供注册表/文档引用。

`register_agent_definition()` 供 Agent 脚手架（app/modules/agents/scaffold.py）
做"配置式注册"：新 Agent 的 agent.py 在 import 时调用它登记元数据，
服务启动扫描模块自注册时即完成定义接入，无需手改本文件。
"""

from app.core.errors import AppError

AGENT_DEFINITIONS: list[dict] = [
    {
        "code": "pest_alert",
        "name": "虫情预警",
        "version": "1.0.0",
        "description": "根据田间监测数据生成虫害预警",
        "input_schema_ref": "",
        "output_schema_ref": "",
        "allowed_tools": ["knowledge.search"],
        "allowed_knowledge_scopes": ["yard"],
        "risk_level": "low",
        "max_steps": 8,
        "timeout_seconds": 90,
        "enabled": True,
    },
    {
        "code": "orchestrator",
        "name": "总控调度 Agent",
        "version": "1.0.0",
        "description": "接收任务、选择工作流/专业 Agent、汇总结果、处理中断",
        "input_schema_ref": "contracts/json-schema/qa-input.schema.json",
        "output_schema_ref": "contracts/json-schema/qa-output.schema.json",
        "allowed_tools": [],
        "allowed_knowledge_scopes": [],
        "risk_level": "low",
        "max_steps": 8,
        "timeout_seconds": 120,
        "enabled": True,
    },
    {
        "code": "agriculture_qa",
        "name": "农业知识问答 Agent",
        "version": "1.0.0",
        "description": "基于已授权农业知识提供带来源的辅助回答",
        "input_schema_ref": "contracts/json-schema/qa-input.schema.json",
        "output_schema_ref": "contracts/json-schema/qa-output.schema.json",
        "allowed_tools": ["knowledge.search"],
        "allowed_knowledge_scopes": ["yard", "crop_public"],
        "risk_level": "low",
        "max_steps": 8,
        "timeout_seconds": 60,
        "enabled": True,
    },
    {
        "code": "crop_diagnosis",
        "name": "作物诊断辅助 Agent",
        "version": "1.0.0",
        "description": "基于症状/环境描述给出候选问题、支持证据与风险说明的诊断辅助（非确定诊断）",
        "input_schema_ref": "contracts/json-schema/crop-diagnosis-input.schema.json",
        "output_schema_ref": "contracts/json-schema/crop-diagnosis-output.schema.json",
        "allowed_tools": ["knowledge.search"],
        "allowed_knowledge_scopes": ["yard", "crop_public"],
        "risk_level": "medium",
        "max_steps": 8,
        "timeout_seconds": 90,
        "enabled": True,
    },
    {
        "code": "farm_planner",
        "name": "农事规划 Agent",
        "version": "1.0.0",
        "description": "生成农事计划草案：任务时间窗、物资估算、依赖条件与风险；计划与工单明确区分",
        "input_schema_ref": "contracts/json-schema/farm-planner-input.schema.json",
        "output_schema_ref": "contracts/json-schema/farm-planner-output.schema.json",
        "allowed_tools": ["knowledge.search"],
        "allowed_knowledge_scopes": ["yard", "crop_public"],
        "risk_level": "medium",
        "max_steps": 8,
        "timeout_seconds": 90,
        "enabled": True,
    },
    {
        "code": "research_assistant",
        "name": "科研辅助 Agent",
        "version": "1.0.0",
        "description": "CSV 数据导入、缺失值报告、描述统计与图表草案；统计假设明确标注",
        "input_schema_ref": "contracts/json-schema/research-assistant-input.schema.json",
        "output_schema_ref": "contracts/json-schema/research-assistant-output.schema.json",
        "allowed_tools": [],
        "allowed_knowledge_scopes": ["yard"],
        "risk_level": "low",
        "max_steps": 8,
        "timeout_seconds": 90,
        "enabled": True,
    },
    {
        "code": "data_analyst",
        "name": "数据汇总 Agent",
        "version": "1.0.0",
        "description": "多源数据汇总与可回溯报表：每个关键数字可追溯到查询条件、时间范围与数据源",
        "input_schema_ref": "contracts/json-schema/data-analyst-input.schema.json",
        "output_schema_ref": "contracts/json-schema/data-analyst-output.schema.json",
        "allowed_tools": [],
        "allowed_knowledge_scopes": ["yard"],
        "risk_level": "low",
        "max_steps": 8,
        "timeout_seconds": 90,
        "enabled": True,
    },
]


def get_definition(code: str) -> dict | None:
    for definition in AGENT_DEFINITIONS:
        if definition.get("code") == code:
            return definition
    return None


def register_agent_definition(
    *,
    code: str,
    name: str,
    description: str,
    version: str = "1.0.0",
    allowed_tools: list[str] | None = None,
    allowed_knowledge_scopes: list[str] | None = None,
    risk_level: str = "low",
    max_steps: int = 8,
    timeout_seconds: int = 90,
    enabled: bool = True,
) -> dict:
    """配置式注册：向 AGENT_DEFINITIONS 追加一条 Agent 定义（脚手架自动调用）。

    与手写条目格式完全一致；code 已存在时抛 AppError（不静默覆盖，
    幂等性由调用方保证：脚手架生成前会先检查目录与定义是否已存在）。
    """
    if get_definition(code) is not None:
        raise AppError(
            "AGENT_ALREADY_REGISTERED",
            f"Agent 定义已存在：{code}，不重复追加。",
            http_status=409,
        )
    definition = {
        "code": code,
        "name": name,
        "version": version,
        "description": description,
        "input_schema_ref": "",
        "output_schema_ref": "",
        "allowed_tools": list(allowed_tools or []),
        "allowed_knowledge_scopes": list(allowed_knowledge_scopes or ["yard"]),
        "risk_level": risk_level,
        "max_steps": max_steps,
        "timeout_seconds": timeout_seconds,
        "enabled": enabled,
    }
    AGENT_DEFINITIONS.append(definition)
    return definition
