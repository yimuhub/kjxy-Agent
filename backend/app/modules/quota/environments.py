"""额度环境模板（v3.32）。

一套环境 = 一组开局策略。小院切换环境时只覆盖 ``source=template`` 的策略，
人工改过的（``source=manual``）保持不动，避免把现场调好的额度刷掉。

模板只放「小院级 Token 总额」这一条硬约束：Agent / 模型级额度按现场需要再加，
否则新建小院会被一堆用不上的策略淹没。
"""

from __future__ import annotations

from typing import Any

from app.modules.quota.models import (
    METRIC_TOKENS,
    ON_EXCEED_APPROVE,
    SCOPE_YARD,
    SOURCE_TEMPLATE,
    WINDOW_MONTH,
)

ENVIRONMENT_TRIAL = "trial"
ENVIRONMENT_STANDARD = "standard"
ENVIRONMENT_PRODUCTION = "production"

ENVIRONMENTS: dict[str, dict[str, Any]] = {
    ENVIRONMENT_TRIAL: {
        "label": "试点试用",
        "description": "50 万 Token/月，超限需申请；适合演示与小范围试用。",
        "policies": [
            {
                "scope_type": SCOPE_YARD,
                "scope_key": "",
                "metric": METRIC_TOKENS,
                "window": WINDOW_MONTH,
                "limit_value": 500_000,
                "on_exceed": ON_EXCEED_APPROVE,
                "note": "试用环境总额度",
            }
        ],
    },
    ENVIRONMENT_STANDARD: {
        "label": "标准运行",
        "description": "200 万 Token/月，超限需申请；日常生产够用。",
        "policies": [
            {
                "scope_type": SCOPE_YARD,
                "scope_key": "",
                "metric": METRIC_TOKENS,
                "window": WINDOW_MONTH,
                "limit_value": 2_000_000,
                "on_exceed": ON_EXCEED_APPROVE,
                "note": "标准环境总额度",
            }
        ],
    },
    ENVIRONMENT_PRODUCTION: {
        "label": "生产高负载",
        "description": "1000 万 Token/月，超限需申请；面向全天候自动执行的小院。",
        "policies": [
            {
                "scope_type": SCOPE_YARD,
                "scope_key": "",
                "metric": METRIC_TOKENS,
                "window": WINDOW_MONTH,
                "limit_value": 10_000_000,
                "on_exceed": ON_EXCEED_APPROVE,
                "note": "生产环境总额度",
            }
        ],
    },
}

DEFAULT_ENVIRONMENT = ENVIRONMENT_STANDARD


def template_policies(environment: str) -> list[dict[str, Any]]:
    item = ENVIRONMENTS.get(environment) or ENVIRONMENTS[DEFAULT_ENVIRONMENT]
    return [dict(p, source=SOURCE_TEMPLATE) for p in item["policies"]]


def payload() -> list[dict[str, Any]]:
    return [
        {
            "key": key,
            "label": item["label"],
            "description": item["description"],
            "policies": item["policies"],
        }
        for key, item in ENVIRONMENTS.items()
    ]
