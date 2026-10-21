#!/usr/bin/env python3
"""演示班组种子脚本（W2）：为租户创建「棚室管家班组」并可真跑一次接力。

班组结构（顺序接力）：解读员（装载 sensor.query 工具）→ 诊断员 → 审核员
（is_critic=true）。三个成员都是最小声明式助手：低风险（只读工具 + tenant
知识范围）走 tenant_admin 自批路径直接 publish。

幂等：agent_code 与班组名按租户查找，已存在即复用（发布状态不满足时补发
布）；重复执行只打印复用结果，不产生重复行。

用法（backend/ 目录）：
    ../.venv/bin/python scripts/seed_demo_team.py                 # 只建/复用班组
    ../.venv/bin/python scripts/seed_demo_team.py --run "棚室夜间温度低怎么办"  # 并真跑一次
    ../.venv/bin/python scripts/seed_demo_team.py --tenant-id <uuid>  # 指定租户（默认第一个）

LLM 走 .env 的 LLM_PROVIDER=fake（离线确定性模板文），可验证接力拼接与
critic 意见并入。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

# 与演示目标一致：无网络确定性模型（已配置真实供应商时不覆盖显式设置）
os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ.setdefault("EMBEDDING_PROVIDER", "mock")

from app.core.deps import TenantContext  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.modules.agents import declarative  # noqa: E402
from app.modules.agents.declarative import STATUS_PUBLISHED, DeclarativeAgentCreate  # noqa: E402
from app.modules.agents.teams import service as teams  # noqa: E402
from app.modules.tenants.models import Tenant  # noqa: E402
from sqlalchemy import select  # noqa: E402

ACTOR_ID = "seed-demo-team"
TEAM_NAME = "棚室管家班组"

#: 演示成员：(agent_code, 角色名, instruction, is_critic, allowed_tools, persona, 模板)
_DEMO_MEMBERS: list[dict[str, object]] = [
    {
        "agent_code": "demo_greenhouse_reader",
        "role_name": "解读员",
        "instruction": "用 sensor.query 查询棚室最近读数，"
        "把数据读成人能看懂的事实清单，不做推断。",
        "is_critic": False,
        "allowed_tools": ["sensor.query"],
        "persona_prompt": "你是棚室数据解读员：只依据传感器读数陈述事实"
        "（温度/湿度/窗口统计），表达简洁直白。",
        "task_prompt_template": "请结合班组接力上下文（含读数查询结果）"
        "完成你的任务：{{question}}",
    },
    {
        "agent_code": "demo_greenhouse_diagnoser",
        "role_name": "诊断员",
        "instruction": "基于解读员的事实清单给出问题诊断与处置建议，标明依据与不确定性。",
        "is_critic": False,
        "allowed_tools": [],
        "persona_prompt": "你是设施农业诊断员：依据前序事实做病虫害/环境"
        "胁迫判断，给出可执行的处置建议。",
        "task_prompt_template": "请基于班组接力上下文进行诊断并给出建议：{{question}}",
    },
    {
        "agent_code": "demo_greenhouse_critic",
        "role_name": "审核员",
        "instruction": "审视前序成员全部输出，指出事实与可行性问题，给出修订后的最终结论。",
        "is_critic": True,
        "allowed_tools": [],
        "persona_prompt": "你是棚室生产审核员：负责校对事实、核查建议可行性，输出审慎的最终结论。",
        "task_prompt_template": "请以审核员视角处理下面的班组接力内容：{{question}}",
    },
]


def _resolve_tenant_id(db, explicit: str | None) -> str:
    if explicit:
        tenant = db.get(Tenant, explicit)
        if tenant is None:
            raise SystemExit(f"租户不存在：{explicit}")
        return tenant.id
    tenant = db.scalar(select(Tenant).order_by(Tenant.created_at.asc()))
    if tenant is None:
        raise SystemExit("库中没有任何租户，请先初始化租户再跑本脚本。")
    return tenant.id


def _ensure_published_agent(db, tenant_id: str, spec: dict[str, object]) -> str:
    """按 agent_code 找/建演示助手，并确保 published（低风险 tenant_admin 自批）。"""
    agent = db.scalar(
        select(declarative.TenantAgent)
        .where(
            declarative.TenantAgent.tenant_id == tenant_id,
            declarative.TenantAgent.agent_code == str(spec["agent_code"]),
        )
        .order_by(declarative.TenantAgent.created_at.desc())
    )
    if agent is not None:
        if agent.status != STATUS_PUBLISHED:
            # 半程崩溃残留（draft）：补走低风险自批发布，保证幂等可重入
            agent = declarative.publish_agent(
                db,
                tenant_id=tenant_id,
                actor_id=ACTOR_ID,
                agent_id=agent.id,
                roles=["tenant_admin"],
                request_id="seed-demo-team",
            )
            db.commit()
        print(f"  复用助手 {spec['agent_code']}（id={agent.id}, status={agent.status}）")
        return agent.id
    req = DeclarativeAgentCreate(
        agent_code=str(spec["agent_code"]),
        name=str(spec["role_name"]),
        description=f"演示班组「{TEAM_NAME}」成员：{spec['role_name']}",
        persona_prompt=str(spec["persona_prompt"]),
        task_prompt_template=str(spec["task_prompt_template"]),
        allowed_tools=list(spec["allowed_tools"]),
        knowledge_scopes=["tenant"],
    )
    agent = declarative.create_agent(
        db, tenant_id=tenant_id, actor_id=ACTOR_ID, req=req, request_id="seed-demo-team"
    )
    # 只读工具/无工具 + tenant 范围 → risk=low → tenant_admin 自批 published
    agent = declarative.publish_agent(
        db,
        tenant_id=tenant_id,
        actor_id=ACTOR_ID,
        agent_id=agent.id,
        roles=["tenant_admin"],
        request_id="seed-demo-team",
    )
    db.commit()
    print(
        f"  创建并发布助手 {spec['agent_code']}（risk={agent.risk_level}, status={agent.status}）"
    )
    return agent.id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", default=None, help="租户 id（默认取库中第一个租户）")
    parser.add_argument("--run", default=None, metavar="问题", help="建队后真跑一次班组接力")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        tenant_id = _resolve_tenant_id(db, args.tenant_id)
        print(f"租户：{tenant_id}")

        print("确保三个演示成员助手（幂等）：")
        members: list[dict[str, object]] = []
        for spec in _DEMO_MEMBERS:
            agent_id = _ensure_published_agent(db, tenant_id, spec)
            members.append(
                {
                    "agent_id": agent_id,
                    "role_name": str(spec["role_name"]),
                    "instruction": str(spec["instruction"]),
                    "is_critic": bool(spec["is_critic"]),
                }
            )

        existing = db.scalar(
            select(teams.AgentTeam).where(
                teams.AgentTeam.tenant_id == tenant_id, teams.AgentTeam.name == TEAM_NAME
            )
        )
        if existing is not None:
            print(f"复用既有班组「{TEAM_NAME}」（id={existing.id}）")
            team = existing
        else:
            team = teams.create_team(
                db,
                tenant_id=tenant_id,
                actor_id=ACTOR_ID,
                name=TEAM_NAME,
                description="棚室生产多 Agent 接力班组：解读 → 诊断 → 审核。",
                members=members,
                request_id="seed-demo-team",
            )
            db.commit()
            print(f"创建班组「{TEAM_NAME}」（id={team.id}）")

        if not args.run:
            print("完成（未指定 --run，仅建/复用班组）。")
            return 0

        ctx = TenantContext(
            tenant_id=tenant_id,
            user_id=ACTOR_ID,
            roles=["tenant_admin"],
            request_id="seed-demo-run",
        )
        teams.require_members_runnable(db, tenant_id=tenant_id, members=list(team.members_json))
        run = teams.create_team_run(db, team=team, user_input=args.run, ctx=ctx)
        run = asyncio.run(teams.continue_team_run(db, run=run, ctx=ctx))
        print(f"\n班组运行 {run.id}：status={run.status}")
        for row in teams.members_of_run(db, run):
            preview = (row.output or row.error_text or "").replace("\n", " ")[:80]
            print(
                f"  [{row.index}] {row.role_name:<4} status={row.status:<7} task_id={row.task_id}"
            )
            print(f"      接力输入片段：{(row.input_preview or '')[:60]}…")
            print(f"      输出片段：{preview}…")
        print(f"\n最终答案（{len(run.answer or '')} 字）：\n{(run.answer or '')[:500]}")
        return 0 if run.status == "completed" else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
