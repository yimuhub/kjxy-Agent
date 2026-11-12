"""新小院的默认助手（v3.80，审查 D-01/D-14 的小白闭环）。

问题
----
全新安装后，对话第一次提问会被拦在::

    本小院还没有可用的已发布助手或班组——请先在 Agent 市场装载或创建助手。

审查报告在「小白用户」维度把它列为**第二次机会的断点**：装完系统的第一件
事本应是成功提一次问，而不是先去市场里造助手、还要面对 ``{{question}}``
占位符。

修法与红线
----------
这里播种的助手是**平台人工撰写**的模板（不是模型生成物），发布人是刚刚
完成向导的**平台管理员本人**——按 ADR-0005 §11 与权限矩阵，platform_admin
可直接发布（其本人即最高审核人，动作全量审计）。因此：

- 不违反「模型生成物不得低于人工审核直接进入可用态」的红线；
- 与内置技能同一口径（``ensure_skills`` 也是向导里直接铺 ``published``）；
- 每次落库都写审计（``agent_default_seeded``）。

只播种**低风险、只读**助手（``knowledge.search`` + 小院知识范围），不给任何
有副作用的工具权限。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

#: 平台撰写的默认助手模板（与技能目录一样是「人工撰写」的静态数据）
DEFAULT_AGENTS: tuple[dict[str, Any], ...] = (
    {
        "agent_code": "tech_advisor",
        "name": "技术顾问",
        "emoji": "🌾",
        "description": (
            "回答田间技术问题：病虫识别、灌溉施肥、采收贮藏等，依据本小院知识库并给出处。"
        ),
        "persona_prompt": (
            "你是本小院的技术顾问，面向一线种植户与农技员回答田间技术问题。"
            "先给结论，再给可执行步骤；依据来自本小院知识库与公开权威资料，"
            "必须标注来源；资料不足时如实说明缺什么，不要编造。"
        ),
        "task_prompt_template": (
            "请依据检索到的本小院资料，直接、具体地回答下面的问题，"
            "必要时给出分步操作要点与注意事项，并标明资料出处：{{question}}"
        ),
        "allowed_tools": ["knowledge.search"],
        "knowledge_scopes": ["yard"],
        "risk_level": "low",
    },
    {
        "agent_code": "yard_steward",
        "name": "小院管家",
        "emoji": "🧭",
        "description": "处理小院日常事务：台账记录、流程指引、资料整理与工作安排。",
        "persona_prompt": (
            "你是本小院的管家，负责日常事务性工作：台账与记录怎么填、"
            "流程走到哪一步、材料与资料怎么归档、这周该做什么。"
            "回答用清单式、编号式，简洁可执行；涉及小院内部资料时以检索到的内容为准。"
        ),
        "task_prompt_template": (
            "请依据本小院的实际资料，用简洁的步骤清单回答下面的问题；"
            "若涉及本小院的具体数据与流程，请说明来源：{{question}}"
        ),
        "allowed_tools": ["knowledge.search"],
        "knowledge_scopes": ["yard", "crop_public"],
        "risk_level": "low",
    },
)

_DEFAULT_VERSION = "1.0.0"


def seed_default_agents(
    db: Session,
    *,
    yard_id: str,
    actor_id: str,
    request_id: str = "",
) -> int:
    """为新小院播种默认助手（已发布、全院可见）；返回新建数量。

    幂等：同小院同 code 同版本只建一次，重复调用不会产生第二份。
    """
    from app.modules.agents.models import (
        STATUS_PUBLISHED,
        VISIBILITY_PUBLIC,
        YardAgent,
    )

    created = 0
    for spec in DEFAULT_AGENTS:
        code = str(spec["agent_code"])
        exists = db.scalar(
            select(YardAgent.id).where(
                YardAgent.yard_id == yard_id,
                YardAgent.agent_code == code,
                YardAgent.version == _DEFAULT_VERSION,
            )
        )
        if exists:
            continue
        db.add(
            YardAgent(
                yard_id=yard_id,
                agent_code=code,
                name=str(spec["name"]),
                description=str(spec["description"]),
                emoji=str(spec.get("emoji") or ""),
                persona_prompt=str(spec["persona_prompt"]),
                task_prompt_template=str(spec["task_prompt_template"]),
                allowed_tools=list(spec.get("allowed_tools") or []),
                bound_skills=[],
                knowledge_scopes=list(spec.get("knowledge_scopes") or []),
                supplier_id=None,
                model_name=None,
                group_name=None,
                risk_level=str(spec.get("risk_level") or "low"),
                status=STATUS_PUBLISHED,
                visibility=VISIBILITY_PUBLIC,
                version=_DEFAULT_VERSION,
                # 平台撰写（非模型生成），与技能目录同一口径
                created_from="manual",
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
        created += 1
    if created:
        db.flush()
        _audit_seed(db, yard_id=yard_id, actor_id=actor_id, request_id=request_id, created=created)
    return created


def _audit_seed(db: Session, *, yard_id: str, actor_id: str, request_id: str, created: int) -> None:
    """写审计：谁在什么时候为哪个小院铺了默认助手。"""
    try:
        from app.modules.audit.service import log_audit

        log_audit(
            db,
            yard_id=yard_id,
            actor_id=actor_id,
            action="agent_default_seeded",
            resource_type="yard_agent",
            resource_id=None,
            result="success",
            request_id=request_id,
            metadata={"count": created, "codes": [s["agent_code"] for s in DEFAULT_AGENTS]},
        )
    except Exception:  # noqa: BLE001 - 审计失败不阻断建院
        pass


__all__ = ["DEFAULT_AGENTS", "seed_default_agents"]
