"""模型绑定路由（v3.25）：``/llm/bindings``、``/llm/capabilities`` 与链路测试。

权限：``llm:manage``（小院负责人/管理员默认持有）。所有写操作记审计，
metadata 只放层级与供应商 id/名称，绝不含密钥。
"""

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_db_session, require_yard_permission
from app.core.responses import ok
from app.modules.audit.service import log_audit
from app.modules.llm import binding_service
from app.modules.llm.binding_models import (
    MAX_FALLBACK_PER_SCOPE,
    SCOPE_AGENT,
    SCOPE_GLOBAL,
    SCOPE_PURPOSE,
)
from app.modules.llm.binding_schemas import BindingsReplace
from app.modules.llm.capabilities import (
    CAPABILITY_CODES,
    SCOPE_TYPE_LABELS,
    VALID_PURPOSES,
    capability_payload,
)
from app.modules.llm.provider import invalidate_supplier_cache
from app.modules.llm.supplier_models import Supplier

router = APIRouter(tags=["llm-bindings"])

_manage = require_yard_permission("llm:manage")


def _audit(user: CurrentUser, db: Session, action: str, meta: dict[str, Any]) -> None:
    log_audit(
        db,
        yard_id=user.yard_id,
        actor_id=user.user_id,
        action=action,
        resource_type="llm_binding",
        resource_id=None,
        result="success",
        request_id=user.request_id,
        metadata=meta,
    )


def _enrich(db: Session, rows) -> list[dict[str, Any]]:
    """绑定行 + 供应商展示字段（名称/类型/默认模型），前端一行读完不用再查。"""
    out: list[dict[str, Any]] = []
    for row in rows:
        supplier = db.get(Supplier, row.supplier_id)
        payload = {
            "id": row.id,
            "yard_id": row.yard_id,
            "scope_type": row.scope_type,
            "scope_key": row.scope_key,
            "supplier_id": row.supplier_id,
            "supplier_name": supplier.name if supplier else "",
            "provider_type": supplier.provider_type if supplier else "",
            "model_id": supplier.model_id if supplier else "",
            "model_name": row.model_name,
            "priority": row.priority,
            "enabled": bool(row.enabled),
            # 可用性：停用/熔断的供应商标「不可用」，避免界面把绑定当成真的在用
            "supplier_status": getattr(supplier, "status", None) if supplier else None,
            "available": bool(supplier and int(getattr(supplier, "status", 0) or 0) == 1),
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }
        out.append(payload)
    return out


@router.get("/capabilities")
def list_capabilities(user: CurrentUser = Depends(_manage)):
    """能力清单（用途码 → 中文名/分组/说明）+ 三层绑定类型与合法用途码校验集。"""
    return ok(
        {
            "capabilities": capability_payload(),
            "purpose_codes": list(CAPABILITY_CODES),
            "valid_purposes": list(VALID_PURPOSES),
            "scope_types": [
                {"type": SCOPE_GLOBAL, "label": "全局默认", "need_key": False},
                {"type": SCOPE_PURPOSE, "label": "按能力", "need_key": True},
                {"type": SCOPE_AGENT, "label": "按智能体", "need_key": True},
            ],
            "levels": [
                {"level": level, "label": binding_service.LEVEL_LABELS[level]}
                for level in binding_service.LEVEL_ORDER
            ],
            "max_fallback": MAX_FALLBACK_PER_SCOPE,
        }
    )


@router.get("/bindings")
def get_bindings(
    request: Request,
    user: CurrentUser = Depends(_manage),
    db: Session = Depends(get_db_session),
    scope_type: str | None = Query(default=None),
    scope_key: str | None = Query(default=None),
):
    """读取本小院可见绑定（含平台级），可选按层过滤。"""
    rows = binding_service.list_bindings(
        db, user.yard_id, scope_type=scope_type, scope_key=scope_key
    )
    return ok({"items": _enrich(db, rows), **_scope_labels()}, request)


def _scope_labels() -> dict[str, Any]:
    return {"scope_type_labels": SCOPE_TYPE_LABELS}


#: 「智能体模型」页下方同时展示的视觉能力（图像/视频各一档默认绑定）
VISUAL_PURPOSES: tuple[str, ...] = ("image_generation", "video_generation")


def _head_of_summary(summary: dict[str, Any]) -> dict[str, Any] | None:
    hops = summary.get("hops") or []
    if not hops:
        return None
    head = hops[0]
    return {
        "supplier_id": head.get("supplier_id"),
        "supplier_name": head.get("supplier_name"),
        "model_name": head.get("model_name"),
        "level": head.get("level"),
        "level_label": head.get("level_label"),
    }


def _agent_model_row(
    db: Session,
    yard_id: str,
    *,
    code: str,
    name: str,
    kind: str,
    agent: Any,
    bindings: list,
) -> dict[str, Any]:
    """一行 = 一个助手 + 它当前真正会用的模型（含旧设置与继承来源）。"""
    enriched = _enrich(db, bindings)
    legacy: dict[str, Any] | None = None
    if agent is not None and getattr(agent, "supplier_id", None):
        supplier = db.get(Supplier, agent.supplier_id)
        legacy = {
            "supplier_id": agent.supplier_id,
            "supplier_name": supplier.name if supplier else "",
            "model_id": supplier.model_id if supplier else "",
            "model_name": getattr(agent, "model_name", None),
        }
    mode = "custom" if enriched else ("legacy" if legacy else "follow")
    summary = binding_service.resolution_summary(
        db,
        yard_id,
        purpose="declarative",
        agent_code=code,
        bound_supplier_id=(legacy or {}).get("supplier_id"),
    )
    return {
        "code": code,
        "name": name,
        "kind": kind,
        "agent_id": getattr(agent, "id", None),
        "status": getattr(agent, "status", "published"),
        "mode": mode,
        "bindings": enriched,
        "legacy": legacy,
        "resolved": _head_of_summary(summary),
        "hops": [
            {
                "level_label": hop.get("level_label"),
                "supplier_name": hop.get("supplier_name"),
                "model_name": hop.get("model_name"),
            }
            for hop in (summary.get("hops") or [])[:4]
        ],
    }


@router.get("/bindings/agents")
def list_agent_model_overview(
    request: Request,
    user: CurrentUser = Depends(_manage),
    db: Session = Depends(get_db_session),
):
    """「模型中心 → 智能体模型」聚合：一次返回全部助手 + 解析结果 + 视觉能力档位。

    助手来源：平台内置助手（专业角色）+ 本小院已发布的自建助手；
    每行给 ``mode``（follow/custom/legacy）、绑定明细、当前真正会用的供应商与模型。
    """
    from app.modules.agents import declarative
    from app.modules.agents.definitions import platform_agent_definitions
    from app.modules.llm.capabilities import capability_label

    agent_rows = binding_service.list_bindings(db, user.yard_id, scope_type=SCOPE_AGENT)
    by_key: dict[str, list] = {}
    for row in agent_rows:
        by_key.setdefault(row.scope_key, []).append(row)

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in platform_agent_definitions():
        code = str(item["code"])
        if code in seen:
            continue
        seen.add(code)
        items.append(
            _agent_model_row(
                db,
                user.yard_id,
                code=code,
                name=str(item["name"]),
                kind="platform",
                agent=None,
                bindings=by_key.get(code, []),
            )
        )
    yard_agents = declarative.list_agents(db, yard_id=user.yard_id, user_id=user.user_id)
    for agent in yard_agents:
        if agent.status != "published" or agent.agent_code in seen:
            continue
        seen.add(agent.agent_code)
        items.append(
            _agent_model_row(
                db,
                user.yard_id,
                code=agent.agent_code,
                name=agent.name,
                kind="yard",
                agent=agent,
                bindings=by_key.get(agent.agent_code, []),
            )
        )
    # 先展示「自定义过」的，再按类型与名称；用户一眼看到需要关注的
    items.sort(key=lambda row: (row["mode"] == "follow", row["kind"] != "yard", row["name"]))

    visual: list[dict[str, Any]] = []
    for purpose in VISUAL_PURPOSES:
        rows = binding_service.list_bindings(
            db, user.yard_id, scope_type=SCOPE_PURPOSE, scope_key=purpose
        )
        summary = binding_service.resolution_summary(db, user.yard_id, purpose=purpose)
        visual.append(
            {
                "purpose": purpose,
                "label": capability_label(purpose),
                "bindings": _enrich(db, rows),
                "resolved": _head_of_summary(summary),
            }
        )
    return ok(
        {
            "items": items,
            "visual": visual,
            "max_fallback": MAX_FALLBACK_PER_SCOPE,
        },
        request,
    )


@router.put("/bindings")
def put_bindings(
    payload: BindingsReplace,
    request: Request,
    user: CurrentUser = Depends(_manage),
    db: Session = Depends(get_db_session),
):
    """整层替换：一次提交某层的全部兜底条目（顺序即优先级）。

    助手层特殊口径：写入即由统一定义接管——同步清空该自建助手上「旧设置」的
    ``supplier_id`` / ``model_name``（助手编辑页遗留字段），消灭两套入口互相覆盖。
    """
    try:
        rows = binding_service.replace_bindings(
            db,
            user.yard_id,
            payload,
            is_platform_admin="platform_admin" in user.roles,
        )
        cleared_legacy = False
        if payload.scope_type == SCOPE_AGENT and payload.scope_key:
            from app.modules.agents.models import YardAgent

            agent = (
                db.query(YardAgent)
                .filter(
                    YardAgent.yard_id == user.yard_id,
                    YardAgent.agent_code == payload.scope_key,
                )
                .first()
            )
            if agent is not None and (agent.supplier_id or agent.model_name):
                agent.supplier_id = None
                agent.model_name = None
                db.flush()
                cleared_legacy = True
        _audit(
            user,
            db,
            "llm_binding_replace",
            {
                "scope_type": payload.scope_type,
                "scope_key": payload.scope_key,
                "supplier_ids": [r.supplier_id for r in rows],
                "cleared_legacy_agent_model": cleared_legacy,
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    # 绑定变更立即影响解析：失效 Provider 缓存，配置无需重启即生效
    invalidate_supplier_cache()
    return ok({"items": _enrich(db, rows)}, request)


@router.delete("/bindings/{binding_id}")
def delete_binding(
    binding_id: str,
    request: Request,
    user: CurrentUser = Depends(_manage),
    db: Session = Depends(get_db_session),
):
    try:
        binding_service.delete_binding(db, user.yard_id, binding_id)
        _audit(user, db, "llm_binding_delete", {"binding_id": binding_id})
        db.commit()
    except Exception:
        db.rollback()
        raise
    invalidate_supplier_cache()
    return ok({"deleted": binding_id}, request)


@router.get("/bindings/resolution")
def resolve_preview(
    request: Request,
    user: CurrentUser = Depends(_manage),
    db: Session = Depends(get_db_session),
    purpose: str | None = Query(default=None),
    agent_code: str | None = Query(default=None),
):
    """「这次调用会用谁」：按解析链给出候选顺序与首个可用项。"""
    summary = binding_service.resolution_summary(
        db, user.yard_id, purpose=purpose, agent_code=agent_code
    )
    return ok(summary, request)


@router.post("/bindings/test")
async def test_binding_scope(
    request: Request,
    payload: dict[str, Any] = Body(...),
    user: CurrentUser = Depends(_manage),
    db: Session = Depends(get_db_session),
):
    """按层试跑一条最小请求：验证绑定与兜底链路真的能出话（会真实消耗一次调用）。"""
    scope_type = str(payload.get("scope_type") or SCOPE_GLOBAL)
    scope_key = str(payload.get("scope_key") or "")
    prompt = str(payload.get("prompt") or "连通性测试，请回复 ok。")[:200]
    if scope_type not in (SCOPE_GLOBAL, SCOPE_PURPOSE, SCOPE_AGENT):
        return ok({"ok": False, "note": f"未知绑定层：{scope_type}"}, request)

    from app.modules.llm.schemas import ChatMessage, LLMMetadata, LLMRequest

    llm_request = LLMRequest(
        messages=[ChatMessage(role="user", content=prompt)],
        metadata=LLMMetadata(
            yard_id=user.yard_id,
            user_id=user.user_id,
            request_id=getattr(user, "request_id", None),
            purpose=scope_key if scope_type == SCOPE_PURPOSE else "platform",
            agent_code=scope_key if scope_type == SCOPE_AGENT else None,
        ),
    )
    try:
        resp = await supplier_complete_for_test(db, llm_request)
    except Exception as e:  # noqa: BLE001 - 测试端点把任何失败如实回给操作者
        return ok({"ok": False, "note": str(getattr(e, "message", None) or e)[:300]}, request)
    _audit(user, db, "llm_binding_test", {"scope_type": scope_type, "scope_key": scope_key})
    db.commit()
    return ok(
        {
            "ok": True,
            "model": resp.model,
            "latency_ms": resp.latency_ms,
            "reply": (resp.content or "")[:80],
        },
        request,
    )


async def supplier_complete_for_test(db: Session, llm_request):
    """走与业务完全相同的解析与记账路径（含兜底），保证「测的就是用的」。"""
    from app.modules.llm.service import complete_with_logging

    return await complete_with_logging(db, llm_request)
