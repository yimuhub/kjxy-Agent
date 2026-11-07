"""套餐目录（v3.71）：代码定义，避免种子数据依赖。

定价为交付示例价，签约以商务报价为准——界面原样标注，不做假折扣。
每档套餐由一组「额度追加项」构成，兑换/确认到账时翻译成
``quota_grants``（作用域=全小院，窗口=自然月；加油包按天计有效期）。
"""

from dataclasses import dataclass

KIND_SUBSCRIPTION = "subscription"
KIND_BOOSTER = "booster"


@dataclass(frozen=True)
class PlanItem:
    """一笔额度追加：翻译成 QuotaGrant（tokens/calls 为个数，cost 为元）。"""

    metric: str  # tokens | calls | cost（与 quota 模块口径一致）
    window: str  # day | month
    amount: float
    valid_days: int  # 到账后有效天数；订阅档 0 = 跟随自然月窗口
    note: str


@dataclass(frozen=True)
class Plan:
    code: str
    name: str
    kind: str  # subscription | booster
    price_yuan: float
    tagline: str
    items: tuple[PlanItem, ...]


PLANS: tuple[Plan, ...] = (
    Plan(
        code="basic",
        name="基础版",
        kind=KIND_SUBSCRIPTION,
        price_yuan=1999.0,
        tagline="单小院起步：日常对话、知识库与工单闭环",
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
        tagline="多智能体重度使用：指挥官自动化与批量诊断",
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
        tagline="院级旗舰：全域自动化 + 大规模知识资产沉淀",
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
        tagline="急活专用：一次性 100 万 Token，30 天有效",
        items=(PlanItem("tokens", "month", 1_000_000, 30, "30 天内有效 100 万 Token"),),
    ),
    Plan(
        code="booster-cost",
        name="加油包 · 算力",
        kind=KIND_BOOSTER,
        price_yuan=199.0,
        tagline="贵模型专项：一次性 ¥100 算力额度，30 天有效",
        items=(PlanItem("cost", "month", 100.0, 30, "30 天内有效 ¥100 模型算力"),),
    ),
)

PLAN_LABELS: dict[str, str] = {plan.code: plan.name for plan in PLANS}


def get_plan(code: str) -> Plan | None:
    return next((plan for plan in PLANS if plan.code == code), None)
