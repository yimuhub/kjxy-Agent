"""聚合全部 ORM 模型，供 Alembic env.py 与测试建表使用。

新增模块模型后，在此追加 import 即可。
"""

from app.modules.agents.models import TenantAgent  # noqa: F401
from app.modules.agents.run_steps import AgentRunStep  # noqa: F401
from app.modules.agents.teams.models import AgentTeam, TeamRun, TeamRunMember  # noqa: F401
from app.modules.audit.models import AuditLog  # noqa: F401
from app.modules.auth.models import User  # noqa: F401
from app.modules.devices.models import (  # noqa: F401
    Device,
    DeviceAlert,
    DeviceAlertRule,
    Reading,
)
from app.modules.evolution.models import (  # noqa: F401
    EvalRun,
    Feedback,
    KnowledgeCandidate,
    Skill,
    SkillRollout,
)
from app.modules.knowledge.models import KnowledgeChunk, KnowledgeDocument  # noqa: F401
from app.modules.llm.models import LLMCall  # noqa: F401
from app.modules.policy.models import Policy, PolicyApplication  # noqa: F401
from app.modules.tasks.models import Task, TaskEvent  # noqa: F401
from app.modules.tenants.models import Tenant, TenantMembership  # noqa: F401
from app.modules.tools.models import ToolApproval, ToolDefinition  # noqa: F401
