"""指挥官设置（``yard_settings.key="commander"``）。

严格口径（与报告 §5.11 一致）：
- 写入只接受已声明字段（``extra=forbid``），未知字段直接 400；
- 类型严格（``strict=True``）：布尔字段不接受 1/0，整数不接受 3.0/字符串；
- 读取永远返回「默认值 + 已存值」的完整对象，便于前端直接渲染表单。

默认值与 ``yards.settings_service.DEFAULTS["commander"]`` 同源（由本模块导出），
避免两处各写一份导致漂移。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from app.core.errors import AppError

DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "display_name": "指挥官",
    "emoji": "🧭",
    # 新会话默认进入指挥官模式（默认关，避免小问题也付出多 Agent 成本）
    "default_mode": False,
    "max_parallel": 3,
    "fallback_parallel": 2,
    "max_subtasks": 6,
    "subtask_timeout_seconds": 180,
    "run_timeout_seconds": 600,
    # 一句话目标直接开跑，不再等用户点「开始执行」；需要确认可单独打开
    "preview_plan": False,
    "auto_create_agent": True,
    # 失败后自主补救：换方法/换助手/新建助手/指挥官带工具亲自执行
    "auto_remediate": True,
    # 单个子任务最多自主尝试轮数；轮尽才向用户报告能力缺口
    "remediate_rounds": 5,
    "auto_publish_agent": False,
    # 信息不足时弹表单问清楚（受众/范围/格式），而不是猜一个接着跑；
    # 「完全控制」模式下仍不问，按最合理假设直接执行
    "clarify_enabled": True,
    # 每小院每天自动创建助手的上限，超出后回退到最接近的已有助手
    "daily_agent_limit": 10,
    "critic_enabled": False,
    "token_budget": 120000,
    # 规划/汇总模型绑定（空 = 跟随模型中心全局默认）
    "supplier_id": None,
    "supplier_model_id": None,
    # 人格：由周度测评判定，可手动锁定；影响规划口吻与自主补救轮数
    "persona": "balanced",
    "persona_locked": False,
    # 进化锁：暂停人格自动调整与后续自动进化，测评仍可只读运行
    "evolution_lock": False,
    # 监督测评：每周自动出报告；可按开关决定是否允许报告调整人格
    "evaluator_enabled": True,
    "evaluator_auto_persona": True,
    # 测评模型单独绑定（空 = 跟随规划模型，保持可独立审计）
    "evaluator_supplier_id": None,
    "evaluator_supplier_model_id": None,
    # 经验资产：成功运行自动沉淀工作流、从 Worker 样本萃取技能
    "auto_learn_workflows": True,
    "auto_extract_skills": True,
    # 同场景同错误累计多少次后进入失败黑名单（1–10）
    "failure_blacklist_threshold": 2,
    # 规划提示词可选模块与字符预算（核心契约与人格段始终保留）
    "prompt_modules": ["delivery", "cost", "concise", "risk"],
    "prompt_max_chars": 4500,
}


class CommanderSettings(BaseModel):
    """指挥官设置契约：严格类型 + 禁止未知字段。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    enabled: bool
    display_name: str = Field(min_length=1, max_length=40)
    emoji: str = Field(min_length=1, max_length=8)
    default_mode: bool
    max_parallel: int = Field(ge=1, le=10)
    fallback_parallel: int = Field(ge=1, le=10)
    max_subtasks: int = Field(ge=2, le=12)
    subtask_timeout_seconds: int = Field(ge=30, le=600)
    run_timeout_seconds: int = Field(ge=60, le=1800)
    preview_plan: bool
    auto_create_agent: bool
    auto_remediate: bool
    remediate_rounds: int = Field(ge=1, le=10)
    auto_publish_agent: bool
    clarify_enabled: bool
    daily_agent_limit: int = Field(ge=1, le=50)
    critic_enabled: bool
    token_budget: int = Field(ge=1000, le=2_000_000)
    supplier_id: str | None = Field(default=None, max_length=36)
    supplier_model_id: str | None = Field(default=None, max_length=100)
    persona: Literal["aggressive", "steady", "balanced"]
    persona_locked: bool
    evolution_lock: bool
    evaluator_enabled: bool
    evaluator_auto_persona: bool
    evaluator_supplier_id: str | None = Field(default=None, max_length=36)
    evaluator_supplier_model_id: str | None = Field(default=None, max_length=100)
    auto_learn_workflows: bool
    auto_extract_skills: bool
    failure_blacklist_threshold: int = Field(ge=1, le=10)
    prompt_modules: list[Literal["delivery", "cost", "concise", "risk"]]
    prompt_max_chars: int = Field(ge=2000, le=8000)


def defaults() -> dict[str, Any]:
    return deepcopy(DEFAULT_SETTINGS)


def validate_settings(value: dict[str, Any]) -> dict[str, Any]:
    """校验完整设置对象；失败统一抛 400 INVALID_REQUEST。"""
    try:
        parsed = CommanderSettings.model_validate(value)
    except ValidationError as exc:
        errors = exc.errors()
        first: dict[str, Any] = dict(errors[0]) if errors else {}
        field = ".".join(str(part) for part in first.get("loc", ())) or "commander"
        message = str(first.get("msg") or "设置项不合法")
        raise AppError(
            "INVALID_REQUEST",
            f"指挥官设置不合法：{field} {message}",
            http_status=400,
            details={"field": field},
        ) from exc
    return parsed.model_dump()


def _stored(db: Session, yard_id: str) -> dict[str, Any]:
    from app.modules.yards import settings_service

    return settings_service.get_setting(db, yard_id, "commander")


def settings_for(db: Session, yard_id: str) -> dict[str, Any]:
    """读取合并默认值后的完整设置；历史脏值退回默认值而不是让整页 500。"""
    merged = _stored(db, yard_id)
    try:
        return validate_settings(merged)
    except AppError:
        return defaults()


def update_settings(
    db: Session,
    *,
    yard_id: str,
    payload: dict[str, Any],
    actor_id: str,
    request_id: str,
) -> dict[str, Any]:
    """部分更新：先按默认值合并，再整体严格校验（含未知字段拒绝）。"""
    from app.modules.yards import settings_service

    unknown = sorted(set(payload) - set(DEFAULT_SETTINGS))
    if unknown:
        raise AppError(
            "INVALID_REQUEST",
            f"未知配置字段：{', '.join(unknown)}",
            http_status=400,
            details={"unknown_fields": unknown},
        )
    merged = {**_stored(db, yard_id), **payload}
    validated = validate_settings(merged)
    settings_service.update_setting(
        db,
        yard_id=yard_id,
        key="commander",
        value=validated,
        actor_id=actor_id,
        request_id=request_id,
    )
    db.flush()
    return validated


__all__ = [
    "DEFAULT_SETTINGS",
    "CommanderSettings",
    "defaults",
    "settings_for",
    "update_settings",
    "validate_settings",
]
