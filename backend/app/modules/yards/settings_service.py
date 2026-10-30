"""小院设置域：注册、安全、搜索与网络。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.audit.service import log_audit
from app.modules.yards.models import YardSettings
from app.modules.yards.permissions import ASSIGNABLE_ROLES

#: 默认值（未写入 yard_settings 时使用）
DEFAULTS: dict[str, dict[str, Any]] = {
    "registration": {
        "mode": "approval",  # closed | approval | open
        "default_role": "viewer",
        "require_email": False,
        "require_phone": False,
        "allow_new_yard": True,
    },
    "security": {
        "password_min_length": 8,
        "max_login_attempts": 10,
        "lockout_seconds": 300,
    },
    "search": {
        "enabled": True,
        "provider": "auto",  # auto | searxng | tavily | brave（旧值照常可用）
        "builtin_sources": ["bing", "sogou", "so360"],  # 免密钥内置源及其优先顺序
        "searxng_url": "http://127.0.0.1:8888",
        "tavily_api_key": "",
        "brave_api_key": "",
        "daily_limit": 500,
        "timeout_seconds": 8,
    },
    "skill_lab": {
        "enabled": True,
        "daily_limit": 20,
        "use_web": True,
        "max_sources": 6,
    },
    "self_improve": {
        "enabled": False,
        "schedule": {"preset": "daily", "custom_value": 1, "custom_unit": "days"},
        "scope": "per_member",  # yard | per_member
        "daily_budget_tokens": 200000,
        "auto_publish": False,  # 自动通过知识候选（技能提案始终人工审核）
        "retain_days": 90,
        "notify_owner": True,
    },
    "agent_generate": {
        "enabled": True,
        "daily_limit": 10,  # 每成员每日生成次数（0 = 不限）
        "use_web": True,  # 生成前先联网取证；关闭则直接按本地知识设计
    },
    "device_analysis": {
        "enabled": True,  # 有启用设备的小院默认每天分析一次
        "schedule": {"preset": "daily"},
        "explain_with_llm": True,  # LLM 只做解释；关闭则只出规则结论
        "daily_budget_tokens": 100000,
        "window_hours": 24,
    },
    # Agent 自动执行（v3.28）：设备/天气/台账 → 规则 → 告警/工单/诊断/台账/药剂
    "farm_agent": {
        "enabled": False,
        "schedule": {"preset": "daily"},
        "explain_with_llm": True,
        "daily_budget_tokens": 100000,
        "dedupe_hours": 24,
        "window_hours": 24,
        "actions": {
            "alert": "auto",
            "work_order": "confirm",
            "diagnosis": "confirm",
            "record": "auto",
            "pesticide": "confirm",
            "plot": "confirm",
        },
        "thresholds": {
            "soil_moisture_low": 20.0,
            "rain_no_spray_mm": 10.0,
            "wind_no_spray_ms": 10.0,
            "temp_drop_c": 8.0,
            "temp_min_c": 3.0,
            "temp_max_c": 35.0,
            "humidity_disease_pct": 85.0,
            "offline_hours": 6.0,
        },
        # 天气预报（可选）：none | qweather | openweather
        "weather_provider": "none",
        "weather_api_key": "",
        "weather_location": "",
        "weather_lat": None,
        "weather_lon": None,
    },
    # 语音转写（v3.30）：auto=本机就绪走本机、否则在线；local/online 强制单通道；off 关闭
    "asr": {
        "mode": "auto",
        "local_model_id": "paraformer-zh-small",
    },
    # 分级额度（v3.32）：环境模板 + 旧「调用次数」配额的处理方式。
    # legacy_calls_mode：enforce=超限即拒；warn=只告警放行（默认）；off=完全不看
    "quota": {
        "environment": "standard",
        "legacy_calls_mode": "warn",
        "default_grant_days": 7,
    },
    # 工作台视图：默认模式 + 按角色的默认视图 + 共享预设（成员仍可在本地 DIY）
    "menu": {
        "default_mode": "standard",  # minimal | standard | full
        "role_modes": {
            "yard_owner": "standard",
            "yard_admin": "standard",
            "expert": "standard",
            "operator": "minimal",
            "viewer": "minimal",
        },
        "profiles": [],
        "allow_member_customize": True,
    },
}

REGISTRATION_MODES = {"closed", "approval", "open"}
SEARCH_PROVIDERS = {"auto", "searxng", "tavily", "brave"}
#: 免密钥内置搜索源（与 app.modules.search.providers_builtin 的实现保持一致）
BUILTIN_SEARCH_SOURCES = ("bing", "sogou", "so360")
SI_PRESETS = {"daily", "hours12", "hours6", "days3", "weekly", "monthly", "custom"}
SI_UNITS = {"hours", "days", "months"}
SI_SCOPES = {"yard", "per_member"}
DA_PRESETS = {"hours12", "daily", "weekly"}
FA_PRESETS = {"hours6", "hours12", "daily"}
FA_POLICIES = {"auto", "confirm", "off"}
FA_ACTIONS = ("alert", "work_order", "diagnosis", "record", "pesticide", "plot")
FA_WEATHER_PROVIDERS = {"none", "qweather", "openweather"}
FA_THRESHOLD_RANGES: dict[str, tuple[float, float]] = {
    "soil_moisture_low": (1.0, 80.0),
    "rain_no_spray_mm": (0.0, 200.0),
    "wind_no_spray_ms": (0.0, 40.0),
    "temp_drop_c": (0.0, 30.0),
    "temp_min_c": (-30.0, 20.0),
    "temp_max_c": (15.0, 55.0),
    "humidity_disease_pct": (50.0, 100.0),
    "offline_hours": (0.5, 168.0),
}
MENU_MODES = {"minimal", "standard", "full"}
ASR_MODES = {"auto", "local", "online", "off"}


def _row(db: Session, yard_id: str, key: str) -> YardSettings | None:
    return db.scalar(
        select(YardSettings).where(YardSettings.yard_id == yard_id, YardSettings.key == key)
    )


def get_setting(db: Session, yard_id: str, key: str) -> dict[str, Any]:
    merged = deepcopy(DEFAULTS.get(key, {}))
    row = _row(db, yard_id, key)
    if row is not None and isinstance(row.value_json, dict):
        merged.update(row.value_json)
    return merged


def get_all(db: Session, yard_id: str) -> dict[str, dict[str, Any]]:
    return {key: get_setting(db, yard_id, key) for key in DEFAULTS}


def _validate_menu(merged: dict[str, Any]) -> None:
    """工作台视图配置：模式枚举 + 预设数量上限 + 结构最小校验。"""

    def bad(message: str) -> AppError:
        return AppError("MENU_PROFILE_INVALID", message, http_status=400)

    if merged.get("default_mode") not in MENU_MODES:
        raise bad("默认视图模式不合法。")
    role_modes = merged.get("role_modes")
    if not isinstance(role_modes, dict) or any(
        not isinstance(code, str) or mode not in MENU_MODES for code, mode in role_modes.items()
    ):
        raise bad("角色默认视图不合法。")
    profiles = merged.get("profiles")
    if not isinstance(profiles, list) or len(profiles) > 20:
        raise bad("预设视图最多 20 个。")
    for profile in profiles:
        if (
            not isinstance(profile, dict)
            or not isinstance(profile.get("id"), str)
            or not profile["id"]
            or not isinstance(profile.get("name"), str)
            or not profile["name"].strip()
            or len(profile["name"]) > 32
            or profile.get("mode") not in MENU_MODES
        ):
            raise bad("预设视图缺少合法的 id/名称/模式。")
        groups = profile.get("groups", [])
        if not isinstance(groups, list) or len(groups) > 12:
            raise bad("预设视图分组最多 12 个。")
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("label"), str):
                raise bad("预设视图分组格式不合法。")
            items = group.get("items", [])
            if not isinstance(items, list) or len(items) > 80:
                raise bad("单个分组最多 80 个菜单项。")
            if any(
                not isinstance(item, dict)
                or not isinstance(item.get("key"), str)
                or not isinstance(item.get("title"), str)
                for item in items
            ):
                raise bad("菜单项格式不合法。")
    if not isinstance(merged.get("allow_member_customize"), bool):
        raise bad("成员自定义开关必须是布尔值。")


def _validate(key: str, value: dict[str, Any], merged: dict[str, Any]) -> None:
    if key == "registration":
        if merged["mode"] not in REGISTRATION_MODES:
            raise AppError("INVALID_REQUEST", "注册模式不合法。", http_status=400)
        if merged["default_role"] not in ASSIGNABLE_ROLES:
            raise AppError("INVALID_REQUEST", "默认角色不合法。", http_status=400)
    elif key == "security":
        try:
            minimum = int(merged["password_min_length"])
            attempts = int(merged["max_login_attempts"])
            lockout = int(merged["lockout_seconds"])
        except (TypeError, ValueError) as e:
            raise AppError("INVALID_REQUEST", "安全策略必须是整数。", http_status=400) from e
        if not 6 <= minimum <= 64 or not 3 <= attempts <= 100 or not 30 <= lockout <= 86400:
            raise AppError("INVALID_REQUEST", "安全策略超出允许范围。", http_status=400)
    elif key == "search":
        if merged["provider"] not in SEARCH_PROVIDERS:
            raise AppError("INVALID_REQUEST", "搜索供应商不合法。", http_status=400)
        sources = merged.get("builtin_sources")
        if not isinstance(sources, list) or any(
            not isinstance(item, str) or item not in BUILTIN_SEARCH_SOURCES for item in sources
        ):
            raise AppError("INVALID_REQUEST", "内置搜索源不合法。", http_status=400)
        merged["builtin_sources"] = list(dict.fromkeys(sources))  # 去重保序
        try:
            limit = int(merged["daily_limit"])
            timeout = int(merged["timeout_seconds"])
        except (TypeError, ValueError) as e:
            raise AppError("INVALID_REQUEST", "搜索配额必须是整数。", http_status=400) from e
        if not 1 <= limit <= 100000 or not 2 <= timeout <= 30:
            raise AppError("INVALID_REQUEST", "搜索配额/超时超出允许范围。", http_status=400)
        parsed = urlparse(str(merged.get("searxng_url") or ""))
        searxng_in_chain = merged["provider"] == "searxng" or (
            merged["provider"] == "auto" and str(merged.get("searxng_url") or "").strip()
        )
        if searxng_in_chain and parsed.scheme not in {"http", "https"}:
            raise AppError("INVALID_REQUEST", "SearXNG 地址必须是 HTTP(S)。", http_status=400)
    elif key == "skill_lab":
        try:
            limit = int(merged["daily_limit"])
            max_sources = int(merged["max_sources"])
        except (TypeError, ValueError) as e:
            raise AppError("INVALID_REQUEST", "技能生成配额必须是整数。", http_status=400) from e
        if not 0 <= limit <= 1000 or not 0 <= max_sources <= 20:
            raise AppError("INVALID_REQUEST", "技能生成配额超出允许范围。", http_status=400)
    elif key == "self_improve":
        for flag in ("enabled", "auto_publish", "notify_owner"):
            if not isinstance(merged[flag], bool):
                raise AppError("INVALID_REQUEST", "自我总结开关必须是布尔值。", http_status=400)
        schedule = merged.get("schedule")
        if not isinstance(schedule, dict) or schedule.get("preset") not in SI_PRESETS:
            raise AppError("INVALID_REQUEST", "自我总结调度不合法。", http_status=400)
        if schedule.get("preset") == "custom":
            raw_value = schedule.get("custom_value")
            try:
                custom_value = int(raw_value) if raw_value is not None else 0
            except (TypeError, ValueError) as e:
                raise AppError(
                    "INVALID_REQUEST", "自定义周期数值必须是整数。", http_status=400
                ) from e
            if schedule.get("custom_unit") not in SI_UNITS or not 1 <= custom_value <= 720:
                raise AppError("INVALID_REQUEST", "自定义周期超出允许范围。", http_status=400)
        if merged.get("scope") not in SI_SCOPES:
            raise AppError("INVALID_REQUEST", "自我总结归纳口径不合法。", http_status=400)
        try:
            budget = int(merged["daily_budget_tokens"])
            retain = int(merged["retain_days"])
        except (TypeError, ValueError) as e:
            raise AppError(
                "INVALID_REQUEST", "自我总结预算/保留期必须是整数。", http_status=400
            ) from e
        if not 1000 <= budget <= 10_000_000 or not 7 <= retain <= 365:
            raise AppError("INVALID_REQUEST", "自我总结预算/保留期超出允许范围。", http_status=400)
    elif key == "agent_generate":
        for flag in ("enabled", "use_web"):
            if not isinstance(merged[flag], bool):
                raise AppError("INVALID_REQUEST", "智能体生成开关必须是布尔值。", http_status=400)
        try:
            limit = int(merged["daily_limit"])
        except (TypeError, ValueError) as e:
            raise AppError("INVALID_REQUEST", "智能体生成配额必须是整数。", http_status=400) from e
        if not 0 <= limit <= 200:
            raise AppError("INVALID_REQUEST", "智能体生成配额超出允许范围。", http_status=400)
    elif key == "device_analysis":
        for flag in ("enabled", "explain_with_llm"):
            if not isinstance(merged[flag], bool):
                raise AppError("INVALID_REQUEST", "设备分析开关必须是布尔值。", http_status=400)
        schedule = merged.get("schedule")
        if not isinstance(schedule, dict) or schedule.get("preset") not in DA_PRESETS:
            raise AppError("INVALID_REQUEST", "设备分析调度不合法。", http_status=400)
        try:
            budget = int(merged["daily_budget_tokens"])
            window = int(merged["window_hours"])
        except (TypeError, ValueError) as e:
            raise AppError(
                "INVALID_REQUEST", "设备分析预算/窗口必须是整数。", http_status=400
            ) from e
        if not 0 <= budget <= 10_000_000 or not 6 <= window <= 168:
            raise AppError("INVALID_REQUEST", "设备分析预算/窗口超出允许范围。", http_status=400)
    elif key == "farm_agent":
        for flag in ("enabled", "explain_with_llm"):
            if not isinstance(merged[flag], bool):
                raise AppError(
                    "INVALID_REQUEST", "Agent 自动执行开关必须是布尔值。", http_status=400
                )
        schedule = merged.get("schedule")
        if not isinstance(schedule, dict) or schedule.get("preset") not in FA_PRESETS:
            raise AppError("INVALID_REQUEST", "自动执行调度不合法。", http_status=400)
        actions = merged.get("actions")
        if not isinstance(actions, dict):
            raise AppError("INVALID_REQUEST", "动作策略必须是对象。", http_status=400)
        unknown = set(actions) - set(FA_ACTIONS)
        if unknown:
            raise AppError(
                "INVALID_REQUEST",
                f"未知动作类型：{', '.join(sorted(unknown))}",
                http_status=400,
            )
        if any(value not in FA_POLICIES for value in actions.values()):
            raise AppError("INVALID_REQUEST", "动作策略必须是 auto/confirm/off。", http_status=400)
        thresholds = merged.get("thresholds")
        if not isinstance(thresholds, dict):
            raise AppError("INVALID_REQUEST", "阈值必须是对象。", http_status=400)
        for name, raw in thresholds.items():
            if name not in FA_THRESHOLD_RANGES:
                raise AppError("INVALID_REQUEST", f"未知阈值：{name}", http_status=400)
            try:
                number = float(raw)
            except (TypeError, ValueError) as e:
                raise AppError(
                    "INVALID_REQUEST", f"阈值 {name} 必须是数字。", http_status=400
                ) from e
            low, high = FA_THRESHOLD_RANGES[name]
            if not low <= number <= high:
                raise AppError(
                    "INVALID_REQUEST",
                    f"阈值 {name} 超出允许范围（{low:g}~{high:g}）。",
                    http_status=400,
                )
        try:
            budget = int(merged["daily_budget_tokens"])
            dedupe = int(merged["dedupe_hours"])
            window = int(merged["window_hours"])
        except (TypeError, ValueError) as e:
            raise AppError(
                "INVALID_REQUEST", "自动执行预算/降噪/窗口必须是整数。", http_status=400
            ) from e
        if not 0 <= budget <= 10_000_000:
            raise AppError("INVALID_REQUEST", "自动执行预算超出允许范围。", http_status=400)
        if not 1 <= dedupe <= 168 or not 6 <= window <= 168:
            raise AppError("INVALID_REQUEST", "降噪时长或窗口超出允许范围。", http_status=400)
        provider = merged.get("weather_provider")
        if provider not in FA_WEATHER_PROVIDERS:
            raise AppError("INVALID_REQUEST", "天气预报供应商不合法。", http_status=400)
        if provider != "none":
            if not str(merged.get("weather_api_key") or "").strip():
                raise AppError(
                    "INVALID_REQUEST",
                    "启用天气预报需要填写 API Key（未填写时请保持供应商为 none）。",
                    http_status=400,
                )
            if provider == "qweather" and not str(merged.get("weather_location") or "").strip():
                raise AppError(
                    "INVALID_REQUEST", "和风天气需要填写 Location ID 或城市名。", http_status=400
                )
            if provider == "openweather":
                has_coords = isinstance(merged.get("weather_lat"), int | float) and isinstance(
                    merged.get("weather_lon"), int | float
                )
                if not has_coords and not str(merged.get("weather_location") or "").strip():
                    raise AppError(
                        "INVALID_REQUEST",
                        "OpenWeather 需要填写城市名或经纬度。",
                        http_status=400,
                    )
    elif key == "quota":
        from app.modules.quota.environments import ENVIRONMENTS

        if merged.get("environment") not in ENVIRONMENTS:
            raise AppError(
                "INVALID_REQUEST",
                "未知的额度环境，可选：" + "、".join(ENVIRONMENTS),
                http_status=400,
            )
        if merged.get("legacy_calls_mode") not in {"enforce", "warn", "off"}:
            raise AppError(
                "INVALID_REQUEST",
                "旧调用额度模式只能是 enforce / warn / off。",
                http_status=400,
            )
        days = merged.get("default_grant_days")
        if not isinstance(days, int) or isinstance(days, bool) or not 0 <= days <= 90:
            raise AppError(
                "INVALID_REQUEST",
                "默认临时额度有效期需为 0–90 的整数天。",
                http_status=400,
            )
    elif key == "asr":
        from app.modules.asr.catalog import get_model

        if merged.get("mode") not in ASR_MODES:
            raise AppError("INVALID_REQUEST", "语音转写模式不合法。", http_status=400)
        if get_model(str(merged.get("local_model_id") or "")) is None:
            raise AppError("INVALID_REQUEST", "未知的本机转写模型。", http_status=400)
    elif key == "menu":
        _validate_menu(merged)
    else:
        raise AppError("INVALID_REQUEST", f"未知设置项：{key}", http_status=400)


def update_setting(
    db: Session,
    *,
    yard_id: str,
    key: str,
    value: dict[str, Any],
    actor_id: str,
    request_id: str,
) -> dict[str, Any]:
    if key not in DEFAULTS:
        raise AppError("INVALID_REQUEST", f"未知设置项：{key}", http_status=400)
    merged = get_setting(db, yard_id, key)
    known_schedule_keys = set(DEFAULTS["self_improve"]["schedule"].keys())
    for k, v in value.items():
        if k not in merged:
            raise AppError("INVALID_REQUEST", f"未知配置字段：{k}", http_status=400)
        # 空字符串表示“保持原密钥不变”（前端不回显明文）
        if k.endswith("_api_key") and v == "":
            continue
        if k == "schedule":
            if not isinstance(v, dict):
                raise AppError("INVALID_REQUEST", "调度配置必须是对象。", http_status=400)
            unknown = set(v.keys()) - known_schedule_keys
            if unknown:
                joined = ", ".join(sorted(unknown))
                raise AppError("INVALID_REQUEST", f"未知调度字段：{joined}", http_status=400)
            merged[k] = {**merged.get(k, {}), **v}
        else:
            merged[k] = v
    _validate(key, value, merged)
    row = _row(db, yard_id, key)
    if row is None:
        row = YardSettings(yard_id=yard_id, key=key, value_json=merged, updated_by=actor_id)
        db.add(row)
    else:
        row.value_json = merged
        row.updated_by = actor_id
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=actor_id,
        action="yard_settings_updated",
        resource_type="yard_settings",
        resource_id=key,
        result="success",
        request_id=request_id,
        metadata={"key": key, "fields": sorted(value.keys())},
    )
    db.flush()
    return merged


def public_view(db: Session, yard_id: str) -> dict[str, dict[str, Any]]:
    """对外视图：密钥只暴露是否已配置。"""
    data = get_all(db, yard_id)
    search = data["search"]
    search["tavily_configured"] = bool(search.pop("tavily_api_key", ""))
    search["brave_configured"] = bool(search.pop("brave_api_key", ""))
    farm_agent = data.get("farm_agent")
    if farm_agent is not None:
        farm_agent["weather_api_key_configured"] = bool(farm_agent.pop("weather_api_key", ""))
    return data


__all__ = [
    "ASR_MODES",
    "BUILTIN_SEARCH_SOURCES",
    "DEFAULTS",
    "REGISTRATION_MODES",
    "SEARCH_PROVIDERS",
    "SI_PRESETS",
    "SI_SCOPES",
    "SI_UNITS",
    "get_all",
    "get_setting",
    "public_view",
    "update_setting",
]
