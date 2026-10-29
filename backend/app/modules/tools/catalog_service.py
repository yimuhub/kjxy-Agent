"""可用工具目录服务（v3.25）：三类来源统一列表 + 详情 + 自定义工具增删改与启停。

三类来源：
- ``builtin``：内置工具（元数据行 + 内存注册表实现），平台级，可停用不可删；
- ``mcp``：MCP 服务器工具缓存快照，**只读**，启停回到 MCP 服务器页；
- ``custom``：本小院手填或一句话生成的工具，归属小院，启用后按 ``impl_kind`` 真实执行。
"""

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.audit.models import AuditLog
from app.modules.llm.supplier_crypto import decrypt_api_key, encrypt_api_key, mask_api_key
from app.modules.mcp.models import McpServer
from app.modules.mcp.tool_bridge import MCP_MAX_FAILURES, build_wire_name
from app.modules.tools.generator import normalize_spec, unique_code, validate_spec
from app.modules.tools.models import ToolDefinition

SOURCE_BUILTIN = "builtin"
SOURCE_CUSTOM = "custom"
SOURCE_MCP = "mcp"

#: 自定义工具执行时写审计的来源标记（详情抽屉「最近调用」据此回看）
AUDIT_RESOURCE_TYPE = "tool"


def _out(row: ToolDefinition) -> dict[str, Any]:
    return {
        "code": row.code,
        "version": row.version,
        "name": row.name,
        "description": row.description,
        "input_schema": row.input_schema_json,
        "output_schema": row.output_schema_json,
        "required_permissions": row.required_permissions_json or [],
        "risk_level": row.risk_level,
        "side_effect": bool(row.side_effect),
        "timeout_seconds": row.timeout_seconds,
        "enabled": bool(row.enabled),
        "source": row.source or SOURCE_BUILTIN,
        "impl_kind": row.impl_kind or "registry",
        "yard_id": row.yard_id,
        "readonly": (row.source or SOURCE_BUILTIN) == SOURCE_BUILTIN,
        "implementation": _impl_view(row),
    }


def _prepare_impl(kind: str, impl: dict[str, Any]) -> dict[str, Any]:
    """落库形态：HTTP 头值逐个 Fernet 加密，明文绝不进数据库、日志或审计。

    入参可以是「明文头」（新建/用户改了头）或「已存密文头」（编辑但没碰头）：
    后者原样保留，避免把密文当明文二次加密。
    """
    if kind == "prompt":
        return {"prompt_template": str(impl.get("prompt_template") or "").strip()[:4000]}
    prepared: dict[str, Any] = {
        "url": str(impl.get("url") or "").strip().rstrip("/"),
        "method": str(impl.get("method") or "POST").upper(),
    }
    if isinstance(impl.get("headers"), dict):
        prepared["headers_enc"] = {
            str(k): encrypt_api_key(str(v)) for k, v in impl["headers"].items()
        }
    elif isinstance(impl.get("headers_enc"), dict):
        prepared["headers_enc"] = dict(impl["headers_enc"])
    else:
        prepared["headers_enc"] = {}
    if impl.get("timeout_seconds"):
        prepared["timeout_seconds"] = int(impl["timeout_seconds"])
    return prepared


def _impl_view(row: ToolDefinition) -> dict[str, Any]:
    """执行方式的展示视图：只出结构，密钥/头值一律打码，绝不回显原文。"""
    kind = row.impl_kind or "registry"
    impl = row.impl_json or {}
    if kind == "prompt":
        return {"kind": "prompt", "prompt_template": str(impl.get("prompt_template") or "")}
    if kind == "http":
        raw_headers = impl.get("headers_enc")
        headers: dict[str, Any] = raw_headers if isinstance(raw_headers, dict) else {}
        return {
            "kind": "http",
            "url": str(impl.get("url") or ""),
            "method": str(impl.get("method") or "POST"),
            "headers": {
                str(name): mask_api_key(decrypt_api_key(str(cipher)))
                for name, cipher in headers.items()
            },
        }
    return {"kind": "registry"}


def list_catalog(
    db: Session, yard_id: str | None, *, include_disabled: bool = True
) -> list[dict[str, Any]]:
    """内置（全局）+ 本小院自定义工具的元数据行。"""
    query = db.query(ToolDefinition).filter(
        (ToolDefinition.yard_id.is_(None)) | (ToolDefinition.yard_id == yard_id)
        if yard_id
        else ToolDefinition.yard_id.is_(None)
    )
    if not include_disabled:
        query = query.filter(ToolDefinition.enabled.is_(True))
    rows = query.order_by(ToolDefinition.code.asc()).all()
    return [_out(r) for r in rows]


def _mcp_items(db: Session, yard_id: str) -> list[dict[str, Any]]:
    """MCP 工具并入列表：按小院已启用、未熔断服务器的缓存快照展开（只读）。"""
    servers = list(
        db.scalars(
            select(McpServer)
            .where(
                McpServer.yard_id == yard_id,
                McpServer.consecutive_failures < MCP_MAX_FAILURES,
            )
            .order_by(McpServer.created_at.asc())
        )
    )
    items: list[dict[str, Any]] = []
    for srv in servers:
        for name, entry in sorted((srv.tool_cache_json or {}).items()):
            if not isinstance(entry, dict):
                continue
            schema = entry.get("input_schema")
            items.append(
                {
                    "code": build_wire_name(srv.id, str(name)),
                    "version": f"mcp:{srv.id[:8]}",
                    "name": str(name),
                    "description": str(entry.get("description") or "")[:1024],
                    "input_schema": schema if isinstance(schema, dict) else {},
                    "output_schema": None,
                    "required_permissions": ["tools:execute"],
                    # 外部服务器行为不可控：统一按最高风险档展示与执行
                    "risk_level": "external_side_effect",
                    "side_effect": True,
                    "timeout_seconds": 20,
                    "enabled": bool(srv.enabled),
                    "source": SOURCE_MCP,
                    "impl_kind": "mcp",
                    "yard_id": yard_id,
                    "readonly": True,
                    "implementation": {
                        "kind": "mcp",
                        "server": srv.name,
                        "server_enabled": bool(srv.enabled),
                    },
                }
            )
    return items


def list_all(db: Session, yard_id: str, *, include_disabled: bool = True) -> dict[str, Any]:
    """「可用工具」页统一视图：三类来源合并 + 分组计数（前端只显示本小院有效权限内的入口）。"""
    catalog = list_catalog(db, yard_id, include_disabled=include_disabled)
    mcp_items = _mcp_items(db, yard_id)
    items = catalog + mcp_items
    if not include_disabled:
        items = [item for item in items if item["enabled"]]
    counts = {
        SOURCE_BUILTIN: sum(1 for i in items if i["source"] == SOURCE_BUILTIN),
        SOURCE_MCP: sum(1 for i in items if i["source"] == SOURCE_MCP),
        SOURCE_CUSTOM: sum(1 for i in items if i["source"] == SOURCE_CUSTOM),
    }
    return {
        "items": sorted(items, key=lambda i: (i["source"], i["code"])),
        "counts": counts,
        "risk_labels": RISK_LABELS,
        "source_labels": {
            SOURCE_BUILTIN: "内置",
            SOURCE_MCP: "来自 MCP 服务器",
            SOURCE_CUSTOM: "本小院添加",
        },
    }


RISK_LABELS: dict[str, str] = {
    "read_only": "只读：只查询，不改动任何数据",
    "draft": "起草：产出草稿，等待人工确认",
    "write_reversible": "可回滚写入：改动本系统内数据，可撤销",
    "external_side_effect": "外部副作用：改动外部世界，需人工审批",
}


def _row_or_error(db: Session, yard_id: str, code: str) -> ToolDefinition:
    row = (
        db.query(ToolDefinition)
        .filter(ToolDefinition.code == code)
        .filter((ToolDefinition.yard_id.is_(None)) | (ToolDefinition.yard_id == yard_id))
        .first()
    )
    if row is None:
        raise AppError("TOOL_NOT_FOUND", f"工具不存在：{code}。", http_status=404)
    return row


def get_detail(db: Session, yard_id: str, code: str) -> dict[str, Any]:
    """工具详情：定义 + 执行方式 + 最近调用记录（审计里按工具 code 回看，只给结论字段）。"""
    if code.startswith("mcp__"):
        items = {item["code"]: item for item in _mcp_items(db, yard_id)}
        item = items.get(code)
        if item is None:
            raise AppError(
                "TOOL_NOT_FOUND", f"MCP 工具不可用或服务器已停用：{code}。", http_status=404
            )
        return {**item, "recent_calls": _recent_calls(db, yard_id, code)}

    row = _row_or_error(db, yard_id, code)
    payload = _out(row)
    payload["definition_markdown"] = _definition_markdown(row)
    payload["recent_calls"] = _recent_calls(db, yard_id, row.code)
    return payload


def _definition_markdown(row: ToolDefinition) -> str:
    """人类可读定义文本（详情抽屉「能做什么」下方展示）。"""
    parts = [row.description.strip()]
    schema = row.input_schema_json or {}
    props = schema.get("properties") if isinstance(schema, dict) else None
    if isinstance(props, dict) and props:
        required = set(schema.get("required") or [])
        lines = [
            f"- `{key}`（{'必填' if key in required else '可选'}，{str(p.get('type') or 'any')}）"
            f"：{p.get('description') or ''}"
            if isinstance(p, dict)
            else f"- `{key}`"
            for key, p in props.items()
        ]
        parts.append("参数说明：\n" + "\n".join(lines))
    impl = _impl_view(row)
    if impl["kind"] == "prompt":
        parts.append(f"执行方式：调用小院已配置模型，提示词模板：\n{impl['prompt_template']}")
    elif impl["kind"] == "http":
        parts.append(f"执行方式：{impl['method']} {impl['url']}")
    return "\n\n".join(p for p in parts if p)


def _recent_calls(db: Session, yard_id: str, code: str, limit: int = 8) -> list[dict[str, Any]]:
    """最近**调用**记录：只取执行类审计（tool_executed），目录增删改不混进来。

    字段只给时间/结局/耗时/失败阶段，不含参数原文。
    """
    rows = list(
        db.query(AuditLog)
        .filter(
            AuditLog.yard_id == yard_id,
            AuditLog.resource_type == AUDIT_RESOURCE_TYPE,
            AuditLog.resource_id == code,
            AuditLog.action == "tool_executed",
        )
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
        .all()
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        meta = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        out.append(
            {
                "at": row.created_at.isoformat() if row.created_at else "",
                "action": row.action,
                "result": row.result,
                "latency_ms": meta.get("latency_ms"),
                "stage": meta.get("stage"),
                "actor_id": row.actor_id,
            }
        )
    return out


# ---------- 自定义工具写入 ----------


def create_custom(
    db: Session,
    yard_id: str,
    actor_id: str,
    payload: dict[str, Any],
    *,
    enabled: bool = False,
) -> dict[str, Any]:
    """新增自定义工具（手填或一句话确认后）：先校验，再以草稿/启用形态落库。"""
    errors = validate_spec(payload)
    if errors:
        raise AppError("TOOL_SPEC_INVALID", "工具定义不合规：" + "；".join(errors), http_status=400)
    spec = normalize_spec(payload, utterance=str(payload.get("name") or ""))
    if db.query(ToolDefinition).filter(ToolDefinition.code == spec["code"]).first():
        raise AppError(
            "DUPLICATE_TOOL_CODE",
            f"工具编码 {spec['code']} 已存在，请换一个。",
            http_status=409,
        )
    spec["code"] = unique_code(db, spec["code"])
    row = ToolDefinition(
        code=spec["code"],
        version=spec["version"],
        name=spec["name"],
        description=spec["description"],
        input_schema_json=spec["input_schema"],
        required_permissions_json=spec["required_permissions"],
        risk_level=spec["risk_level"],
        side_effect=spec["side_effect"],
        timeout_seconds=spec["timeout_seconds"],
        enabled=enabled,
        source=SOURCE_CUSTOM,
        impl_kind=spec["impl_kind"],
        impl_json=_prepare_impl(spec["impl_kind"], spec["impl"]),
        yard_id=yard_id,
        created_by=actor_id,
    )
    db.add(row)
    db.flush()
    return _out(row)


def _writable_custom(db: Session, yard_id: str, code: str) -> ToolDefinition:
    row = _row_or_error(db, yard_id, code)
    if (row.source or SOURCE_BUILTIN) != SOURCE_CUSTOM:
        raise AppError(
            "TOOL_READONLY",
            "内置工具不可编辑或删除，只能停用；自定义工具请在「添加可用工具」中新建。",
            http_status=403,
        )
    if row.yard_id != yard_id:
        raise AppError("TOOL_NOT_FOUND", f"工具不存在：{code}。", http_status=404)
    return row


def _merge_impl(row: ToolDefinition, payload: dict[str, Any]) -> dict[str, Any]:
    """编辑时的执行方式合并：未提交 impl 就原样保留（头值仍是密文）；
    提交了新 headers 才重新加密替换，绝不把已存密文当明文二次加密。"""
    if payload.get("impl") is None:
        return dict(row.impl_json or {})
    incoming = dict(payload["impl"])
    stored = dict(row.impl_json or {})
    if not isinstance(incoming.get("headers"), dict):
        # 前端回显的是打码头像：没改头就沿用库里密文，别把掩码当密钥存回去
        incoming.setdefault("headers_enc", stored.get("headers_enc") or {})
    return incoming


def update_custom(db: Session, yard_id: str, code: str, payload: dict[str, Any]) -> dict[str, Any]:
    row = _writable_custom(db, yard_id, code)
    merged = {
        "code": row.code,  # code 创建后不可变（模型与审批单按 code 引用）
        "version": payload.get("version") or row.version,
        "name": payload.get("name") or row.name,
        "description": payload.get("description")
        if payload.get("description") is not None
        else row.description,
        "input_schema": payload.get("input_schema") or row.input_schema_json,
        "impl_kind": payload.get("impl_kind") or row.impl_kind,
        "impl": _merge_impl(row, payload),
        "risk_level": payload.get("risk_level") or row.risk_level,
        "side_effect": payload.get("side_effect", row.side_effect),
        "timeout_seconds": payload.get("timeout_seconds") or row.timeout_seconds,
        "required_permissions": (
            payload.get("required_permissions") or row.required_permissions_json or []
        ),
    }
    errors = validate_spec(merged)
    if errors:
        raise AppError("TOOL_SPEC_INVALID", "工具定义不合规：" + "；".join(errors), http_status=400)
    spec = normalize_spec(merged, utterance=row.name)
    row.version = spec["version"]
    row.name = spec["name"]
    row.description = spec["description"]
    row.input_schema_json = spec["input_schema"]
    row.required_permissions_json = spec["required_permissions"]
    row.risk_level = spec["risk_level"]
    row.side_effect = spec["side_effect"]
    row.timeout_seconds = spec["timeout_seconds"]
    row.impl_kind = spec["impl_kind"]
    row.impl_json = _prepare_impl(spec["impl_kind"], spec["impl"])
    db.flush()
    return _out(row)


def set_enabled(db: Session, yard_id: str, code: str, enabled: bool) -> dict[str, Any]:
    """启停：内置行为平台级开关（影响所有小院）；MCP 工具请到 MCP 服务器页启停。"""
    if code.startswith("mcp__"):
        raise AppError(
            "TOOL_READONLY",
            "MCP 工具的启停在「MCP 服务」里按服务器整体控制。",
            http_status=403,
        )
    row = _row_or_error(db, yard_id, code)
    if enabled and (row.source or SOURCE_BUILTIN) == SOURCE_CUSTOM:
        # 启用前必须确有可执行实现：草稿允许存在，空实现不允许对模型可见
        impl = row.impl_json or {}
        kind = row.impl_kind or "prompt"
        usable = (
            bool(str(impl.get("prompt_template") or "").strip())
            if kind == "prompt"
            else str(impl.get("url") or "").startswith(("http://", "https://"))
        )
        if not usable:
            missing = "提示词模板" if kind == "prompt" else "接口地址"
            raise AppError(
                "TOOL_SPEC_INVALID",
                f"工具 {row.name} 还没有可执行实现（缺 {missing}），请补全后再启用。",
                http_status=400,
            )
    row.enabled = enabled
    db.flush()
    return _out(row)


def delete_custom(db: Session, yard_id: str, code: str) -> None:
    row = _writable_custom(db, yard_id, code)
    db.delete(row)
    db.flush()


def schema_json_hint(schema: dict[str, Any]) -> str:
    """详情抽屉「调用示例」：按 schema 生成一条**合法**的参数样例（不含猜测值语义）。"""
    props = schema.get("properties") if isinstance(schema, dict) else None
    sample: dict[str, Any] = {}
    if isinstance(props, dict):
        for key, spec in props.items():
            if isinstance(spec, dict):
                kind = str(spec.get("type") or "string")
                sample[key] = _sample_value(spec, kind)
    return json.dumps(sample, ensure_ascii=False, indent=2)


def _sample_value(spec: dict[str, Any], kind: str) -> Any:
    enum = spec.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]
    if kind == "integer":
        return 1
    if kind == "number":
        return 1.0
    if kind == "boolean":
        return True
    if kind == "array":
        return []
    if kind == "object":
        return {}
    return str(spec.get("description") or "示例值")[:24]
