"""插件清单 v2：平台、权限、产物声明与授权范围指纹。

口径：
- v1 清单（声明式 workflow / knowledge_pack / domain_pack）继续可用，等价于
  「全平台、无本机能力」；
- v2 为 ``local_runtime``（本机能力桥）与 ``workspace``（工作区代码修改）
  的必需格式：必须声明 ``platforms``、``artifacts`` 与 ``permissions``；
- 权限只能取自目录 ``PERMISSION_CATALOG``，未知权限直接拒绝，避免「顺手多要」；
- ``scope_hash`` 是权限集合指纹：权限增减都会改变它，旧授权自动失效，必须重新确认。
"""

from __future__ import annotations

import hashlib
from typing import Any

from app.core.errors import AppError

SCHEMA_VERSIONS = (1, 2)
PLATFORMS = ("macos", "windows", "linux")
LOCAL_RUNTIME_TYPES = ("local_runtime", "workspace")

RISK_ORDER = ("low", "medium", "high", "critical")

PERMISSION_CATALOG: dict[str, dict[str, Any]] = {
    "network": {
        "label": "网络访问",
        "description": "以本机身份访问外部服务。",
        "risk": "low",
        "requires_ack": False,
    },
    "notifications": {
        "label": "系统通知",
        "description": "在桌面弹出提醒。",
        "risk": "low",
        "requires_ack": False,
    },
    "microphone": {
        "label": "麦克风",
        "description": "录音并转写。",
        "risk": "low",
        "requires_ack": False,
    },
    "filesystem_read": {
        "label": "读取文件",
        "description": "读取用户选定目录下的文件。",
        "risk": "medium",
        "requires_ack": True,
    },
    "clipboard": {
        "label": "剪贴板",
        "description": "读取或写入剪贴板内容。",
        "risk": "medium",
        "requires_ack": True,
    },
    "camera": {
        "label": "摄像头",
        "description": "拍照或连续画面采集。",
        "risk": "medium",
        "requires_ack": True,
    },
    "filesystem_write": {
        "label": "写入文件",
        "description": "创建、修改或删除用户选定目录下的文件。",
        "risk": "high",
        "requires_ack": True,
    },
    "screen_capture": {
        "label": "屏幕读取",
        "description": "截取屏幕画面并识别界面内容。",
        "risk": "high",
        "requires_ack": True,
    },
    "accessibility": {
        "label": "辅助功能",
        "description": "读取窗口与控件结构（无障碍树）。",
        "risk": "high",
        "requires_ack": True,
    },
    "input_control": {
        "label": "模拟操作",
        "description": "代替用户移动鼠标、点击与键盘输入。",
        "risk": "critical",
        "requires_ack": True,
    },
    "shell": {
        "label": "执行命令",
        "description": "在本机执行系统命令。",
        "risk": "critical",
        "requires_ack": True,
    },
}


def _invalid(message: str, *, details: dict | None = None) -> AppError:
    return AppError(
        "PLUGIN_MANIFEST_INVALID",
        message,
        http_status=422,
        details=details,
    )


def normalize(codes: Any) -> list[str]:
    """权限去重并排序；未知权限拒绝。"""
    if codes is None:
        return []
    if not isinstance(codes, list):
        raise _invalid("manifest.permissions 必须是数组。")
    cleaned: list[str] = []
    for raw in codes:
        code = str(raw or "").strip().lower()
        if not code:
            continue
        if code not in PERMISSION_CATALOG:
            raise _invalid(
                f"未知权限声明：{code}",
                details={"permission": code, "supported": sorted(PERMISSION_CATALOG)},
            )
        if code not in cleaned:
            cleaned.append(code)
    return sorted(cleaned)


def scope_hash(permissions: list[str]) -> str:
    """权限集合指纹（与版本无关）：权限集合不变则授权持续有效。"""
    canonical = ",".join(sorted(permissions))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def risk_level(permissions: list[str]) -> str:
    best = "low"
    for code in permissions:
        risk = str(PERMISSION_CATALOG.get(code, {}).get("risk") or "low")
        if RISK_ORDER.index(risk) > RISK_ORDER.index(best):
            best = risk
    return best


def permission_details(permissions: list[str]) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    for code in sorted(permissions):
        meta = PERMISSION_CATALOG[code]
        details.append(
            {
                "code": code,
                "label": meta["label"],
                "description": meta["description"],
                "risk": meta["risk"],
                "requires_ack": bool(meta["requires_ack"]),
            }
        )
    return details


def consent_required(permissions: list[str]) -> bool:
    return any(PERMISSION_CATALOG[code]["requires_ack"] for code in permissions)


def _normalize_platforms(raw: Any) -> list[str]:
    if raw is None:
        return list(PLATFORMS)
    if not isinstance(raw, list) or not raw:
        raise _invalid("manifest.platforms 必须是非空数组。")
    cleaned: list[str] = []
    for value in raw:
        platform = str(value or "").strip().lower()
        if platform not in PLATFORMS:
            raise _invalid(
                f"不支持的平台：{platform}",
                details={"platform": platform, "supported": list(PLATFORMS)},
            )
        if platform not in cleaned:
            cleaned.append(platform)
    return cleaned


def _normalize_artifacts(raw: Any, platforms: list[str]) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise _invalid("manifest.artifacts 必须是对象（平台 → 产物信息）。")
    artifacts: dict[str, Any] = {}
    for key, value in raw.items():
        platform = str(key).strip().lower()
        if platform not in PLATFORMS:
            raise _invalid(f"artifacts 含未知平台：{platform}")
        if not isinstance(value, dict):
            raise _invalid(f"artifacts.{platform} 必须是对象。")
        entry_path = str(value.get("entry") or "").strip()
        sha = str(value.get("sha256") or "").strip().lower()
        size = int(value.get("size_bytes") or 0)
        if not entry_path:
            raise _invalid(f"artifacts.{platform}.entry 不能为空。")
        if len(sha) != 64:
            raise _invalid(f"artifacts.{platform}.sha256 必须是 64 位十六进制。")
        artifacts[platform] = {
            "entry": entry_path,
            "sha256": sha,
            "size_bytes": size,
        }
    missing = [platform for platform in platforms if platform not in artifacts]
    if missing:
        raise _invalid(
            "声明了平台但缺少对应产物。",
            details={"missing": missing},
        )
    return artifacts


def normalize_manifest(raw: dict[str, Any], *, item_type: str = "plugin") -> dict[str, Any]:
    """把 v1/v2 清单统一成 v2 视图；不改动原始字段，只补派生字段。"""
    if not isinstance(raw, dict):
        raise _invalid("manifest 必须是 JSON 对象。")
    schema_version = int(raw.get("schema_version") or 1)
    if schema_version not in SCHEMA_VERSIONS:
        raise _invalid(f"不支持的清单版本 schema_version={schema_version}。")
    permissions = normalize(raw.get("permissions"))
    plugin_type = str(raw.get("type") or item_type or "").strip()
    is_local = plugin_type in LOCAL_RUNTIME_TYPES
    if schema_version == 1 and is_local:
        raise _invalid("本机能力插件必须使用 schema_version=2 清单。")
    platforms = _normalize_platforms(raw.get("platforms") if schema_version == 2 else None)
    artifacts = _normalize_artifacts(
        raw.get("artifacts") if schema_version == 2 else None, platforms
    )
    if is_local:
        if not permissions:
            raise _invalid("本机能力插件必须声明 permissions（至少一项）。")
        if not artifacts:
            raise _invalid("本机能力插件必须为每个平台提供 artifacts。")
    result = dict(raw)
    result["schema_version"] = schema_version
    result["platforms"] = platforms
    result["artifacts"] = artifacts
    result["permissions"] = permissions
    result["permission_details"] = permission_details(permissions)
    result["scope_hash"] = scope_hash(permissions)
    result["risk_level"] = risk_level(permissions)
    result["consent_required"] = consent_required(permissions)
    return result
