"""聚合全部 ORM 模型，供 Alembic env.py 与测试建表使用。

新增模块模型后，在此追加 import 即可。

这里必须聚合**全部**建表模块：漏掉一个，Alembic 就会把库里那张表看成"模型没有的表"，
下一次 --autogenerate 会生成 drop_table（供应商/MCP/插件/工坊/配额都曾漏过）。
"""

from app.modules.agents.factory_models import FactoryRun  # noqa: F401
from app.modules.agents.models import YardAgent  # noqa: F401
from app.modules.agents.run_steps import AgentRunStep  # noqa: F401
from app.modules.agents.teams.models import AgentTeam, TeamRun, TeamRunMember  # noqa: F401
from app.modules.audit.models import AuditLog  # noqa: F401
from app.modules.auth.models import User  # noqa: F401
from app.modules.auth.sso_models import (  # noqa: F401
    SsoExchangeCode,
    SsoIdentity,
    SsoProvider,
)
from app.modules.bots.models import (  # noqa: F401
    BotConfig,
    BotConversation,
    BotMessageLog,
)
from app.modules.chat.models import (  # noqa: F401
    ChatAttachment,
    ChatMessage,
    ChatParticipant,
    ChatSession,
)
from app.modules.cluster.models import ClusterNode  # noqa: F401
from app.modules.commander.asset_models import (  # noqa: F401
    CommanderFailureCase,
    CommanderSkillSnippet,
    CommanderWorkflow,
)
from app.modules.commander.evolution_models import CommanderIteration  # noqa: F401
from app.modules.commander.governance_models import (  # noqa: F401
    CommanderEvaluation,
    CommanderSnapshot,
)
from app.modules.commander.models import (  # noqa: F401
    CommanderRun,
    CommanderSubtask,
)
from app.modules.devices.models import (  # noqa: F401
    Device,
    DeviceAlert,
    DeviceAlertRule,
    DeviceAnalysis,
    Reading,
)
from app.modules.evolution.models import (  # noqa: F401
    EvalRun,
    Feedback,
    KnowledgeCandidate,
    Skill,
    SkillRollout,
)
from app.modules.farm.models import (  # noqa: F401
    CropDiagnosis,
    DiagnosisFollowUp,
    FarmAgentAction,
    FarmAgentRun,
    FarmAlert,
    FarmCycle,
    FarmPlot,
    FarmRecord,
    PesticideProduct,
    WorkOrder,
    WorkOrderEvent,
)
from app.modules.journal.models import YardJournal  # noqa: F401
from app.modules.knowledge.models import KnowledgeChunk, KnowledgeDocument  # noqa: F401
from app.modules.library.models import (  # noqa: F401
    LibraryInstall,
    LibraryItem,
    LibrarySource,
    LibrarySyncRun,
    PluginConsent,
)
from app.modules.llm.binding_models import LLMBinding  # noqa: F401
from app.modules.llm.models import LLMCall, LLMTrace  # noqa: F401
from app.modules.llm.supplier_models import (  # noqa: F401
    OfficialEntitlement,
    OfficialOrder,
    OfficialPlan,
    OfficialWebhookEvent,
    RelayChannel,
    Supplier,
    SupplierModel,
    YardActiveSupplier,
)
from app.modules.market.models import MarketItem  # noqa: F401
from app.modules.mcp.models import McpServer  # noqa: F401
from app.modules.memory.models import AgentMemory  # noqa: F401
from app.modules.org.models import OrgPosition, OrgProposal, OrgUnit  # noqa: F401
from app.modules.pairing.models import PairCode, PairDevice  # noqa: F401
from app.modules.plugins.models import PluginManifest  # noqa: F401
from app.modules.policy.models import Policy, PolicyApplication  # noqa: F401
from app.modules.push.models import PushChannel  # noqa: F401
from app.modules.self_improve.models import (  # noqa: F401
    SelfImproveMemory,
    YardJob,
    YardJobRun,
)
from app.modules.share.models import ShareLink  # noqa: F401
from app.modules.tasks.models import Task, TaskEvent  # noqa: F401
from app.modules.tools.models import ToolApproval, ToolDefinition  # noqa: F401
from app.modules.workspace.models import WorkspaceChange, WorkspaceProject  # noqa: F401
from app.modules.yards.models import (  # noqa: F401
    Yard,
    YardJoinRequest,
    YardMemberGrant,
    YardMembership,
    YardRole,
    YardSettings,
)
from app.modules.yards.quota import YardQuota  # noqa: F401
