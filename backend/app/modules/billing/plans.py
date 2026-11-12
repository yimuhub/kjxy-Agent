"""套餐目录（v3.71 → v3.80，审查 D-18/D-21）。

**目录以数据库为准**：``billing_plans`` 表是运营可改的真相源（价格、卖点、
额度项、排序、上下架）；本文件的 ``PLANS`` 只是首次部署的种子值 + 空表兜底。

额度口径统一到配额模块的三个指标（``tokens`` / ``calls`` / ``cost``），
保证"卖什么"与"限什么"是同一套账（审查 D-21）：

- ``items``：本套餐**追加**的额度（追加到小院所选环境的基础额度之上）；
- ``entitlement``：客户端展示用的**总额度** = 环境基础额度 + 追加额度，
  由服务端按小院当前环境算好后下发 —— 付费页与配额页用同一套数字，
  运营报出"¥1999 含多少 Token"才有确定答案。

``unit`` 是展示用的量纲（"Token"/"次"/"元"），只做可读性，不参与计算。
"""

from dataclasses import dataclass

KIND_SUBSCRIPTION = "subscription"
KIND_BOOSTER = "booster"

#: 额度指标 → 展示量纲与单位（与 quota.METRIC_LABELS 同口径）
METRIC_UNITS: dict[str, tuple[str, str]] = {
    "tokens": ("模型 Token", "Token"),
    "calls": ("模型调用", "次"),
    "cost": ("模型算力", "元"),
}


@dataclass(frozen=True)
class PlanItem:
    """一笔额度追加：翻译成 QuotaGrant（tokens/calls 为个数，cost 为元）。"""

    metric: str  # tokens | calls | cost（与 quota 模块口径一致）
    window: str  # day | month
    amount: float
    valid_days: int  # 到账后有效天数；订阅档 0 = 跟随自然月窗口
    note: str

    @property
    def unit(self) -> str:
        return METRIC_UNITS.get(self.metric, ("", ""))[1]

    @property
    def metric_label(self) -> str:
        return METRIC_UNITS.get(self.metric, (self.metric, ""))[0]


@dataclass(frozen=True)
class Plan:
    code: str
    name: str
    kind: str  # subscription | booster
    price_yuan: float
    tagline: str
    items: tuple[PlanItem, ...]
    #: 加油包等一次性档：单独说明有效期口径
    valid_days: int = 0
    #: 卖点（运营可改，用于付费页对比表）
    highlights: tuple[str, ...] = ()
    #: 是否在售（下架后不对用户展示，但历史订单仍可查）
    active: bool = True
    #: 付费页展示顺序（数值小的在前）
    sort_order: int = 0


PLANS: tuple[Plan, ...] = (
    Plan(
        code="basic",
        name="基础版",
        kind=KIND_SUBSCRIPTION,
        price_yuan=1999.0,
        sort_order=10,
        tagline="单小院起步：日常对话、知识库与工单闭环",
        highlights=(
            "单小院日常运营全流程",
            "含 200 万 Token/月 追加额度",
            "含 5,000 次模型调用/月",
            "知识库与工单闭环",
            "超限走审批申请，不中断服务",
        ),
        items=(
            PlanItem("tokens", "month", 2_000_000, 0, "每月追加 200 万 Token"),
            PlanItem("calls", "month", 5_000, 0, "每月追加 5,000 次调用"),
        ),
    ),
    Plan(
        code="pro",
        name="专业版",
        kind=KIND_SUBSCRIPTION,
        price_yuan=4999.0,
        sort_order=20,
        tagline="多智能体重度使用：指挥官自动化与批量诊断",
        highlights=(
            "多智能体并发与指挥官自动化",
            "含 600 万 Token/月 追加额度",
            "含 15,000 次模型调用/月",
            "含 ¥200 模型算力额度/月（可跑贵模型）",
            "批量诊断与批量导出",
        ),
        items=(
            PlanItem("tokens", "month", 6_000_000, 0, "每月追加 600 万 Token"),
            PlanItem("calls", "month", 15_000, 0, "每月追加 15,000 次调用"),
            PlanItem("cost", "month", 200.0, 0, "每月追加 ¥200 模型算力额度"),
        ),
    ),
    Plan(
        code="flagship",
        name="旗舰版",
        kind=KIND_SUBSCRIPTION,
        price_yuan=9999.0,
        sort_order=30,
        tagline="院级旗舰：全域自动化 + 大规模知识资产沉淀",
        highlights=(
            "院级全域自动化与多小院协同",
            "含 2,000 万 Token/月 追加额度",
            "含 50,000 次模型调用/月",
            "含 ¥600 模型算力额度/月",
            "大规模知识资产沉淀与监督测评",
        ),
        items=(
            PlanItem("tokens", "month", 20_000_000, 0, "每月追加 2,000 万 Token"),
            PlanItem("calls", "month", 50_000, 0, "每月追加 50,000 次调用"),
            PlanItem("cost", "month", 600.0, 0, "每月追加 ¥600 模型算力额度"),
        ),
    ),
    Plan(
        code="booster-tokens",
        name="加油包 · Token",
        kind=KIND_BOOSTER,
        price_yuan=299.0,
        sort_order=40,
        tagline="急活专用：一次性 100 万 Token，30 天有效",
        highlights=(
            "一次性 100 万 Token",
            "30 天内有效，不占自然月额度",
            "适合临时汇报、批量整理",
        ),
        items=(PlanItem("tokens", "month", 1_000_000, 30, "30 天内有效 100 万 Token"),),
        valid_days=30,
    ),
    Plan(
        code="booster-cost",
        name="加油包 · 算力",
        kind=KIND_BOOSTER,
        price_yuan=199.0,
        sort_order=50,
        tagline="贵模型专项：一次性 ¥100 算力额度，30 天有效",
        highlights=(
            "一次性 ¥100 模型算力额度",
            "30 天内有效，不占自然月额度",
            "按上游成本价折算，专治贵模型跑不动",
        ),
        items=(PlanItem("cost", "month", 100.0, 30, "30 天内有效 ¥100 模型算力"),),
        valid_days=30,
    ),
)

PLAN_LABELS: dict[str, str] = {plan.code: plan.name for plan in PLANS}


def get_plan(code: str) -> Plan | None:
    return next((plan for plan in PLANS if plan.code == code), None)
