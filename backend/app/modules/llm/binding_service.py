"""模型绑定服务（v3.25）：三层绑定的读写 + 解析链 + 兜底候选。

解析顺序（前档不可用自动落到后档，永不因单个供应商故障中断业务）：
1. 助手自身指定的供应商（``LLMMetadata.supplier_id``，来自助手模型绑定）
2. 按智能体绑定（``scope_type=agent``）
3. 按能力绑定（``scope_type=purpose``）
4. 全局默认绑定（小院级 → 平台级，``scope_type=global``）
5. 小院「当前生效供应商」（既有 ``yard_active_suppliers``）
6. 平台供应商按 priority（既有兜底）
7. ``settings.llm_provider``（无 DB 供应商时的最后兜底，由 provider 层处理）

同一层内多条绑定按 ``priority`` 从大到小构成兜底顺序，最多 3 条
（再多只会掩盖配置错误）。供应商不可见的判定：跨小院、已删除、已禁用
（含运行时连续失败自动降级）。
"""

from dataclasses import dataclass

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.llm.binding_models import (
    MAX_FALLBACK_PER_SCOPE,
    SCOPE_AGENT,
    SCOPE_GLOBAL,
    SCOPE_PURPOSE,
    LLMBinding,
)
from app.modules.llm.binding_schemas import BindingEntry, BindingsReplace
from app.modules.llm.capabilities import capability_label, is_known_purpose
from app.modules.llm.supplier_models import Supplier, SupplierModel
from app.modules.llm.supplier_schemas import STATUS_ENABLED

#: 解析档位（前端「这次会用谁」按此顺序展示，值同时用于审计与提示）
LEVEL_AGENT_BOUND = "agent_bound"
LEVEL_AGENT_BINDING = "agent_binding"
LEVEL_PURPOSE_BINDING = "purpose_binding"
LEVEL_YARD_DEFAULT = "yard_default"
LEVEL_PLATFORM_DEFAULT = "platform_default"
LEVEL_YARD_ACTIVE = "yard_active"
LEVEL_PLATFORM_PRIORITY = "platform_priority"
LEVEL_SETTINGS = "settings_fallback"

LEVEL_LABELS: dict[str, str] = {
    LEVEL_AGENT_BOUND: "助手指定模型",
    LEVEL_AGENT_BINDING: "按智能体绑定",
    LEVEL_PURPOSE_BINDING: "按能力绑定",
    LEVEL_YARD_DEFAULT: "小院默认+兜底",
    LEVEL_PLATFORM_DEFAULT: "平台默认+兜底",
    LEVEL_YARD_ACTIVE: "小院生效供应商",
    LEVEL_PLATFORM_PRIORITY: "平台供应商优先级",
    LEVEL_SETTINGS: "环境变量兜底",
}

#: 档位顺序（既是解析顺序，也是前端展示顺序）
LEVEL_ORDER: tuple[str, ...] = (
    LEVEL_AGENT_BOUND,
    LEVEL_AGENT_BINDING,
    LEVEL_PURPOSE_BINDING,
    LEVEL_YARD_DEFAULT,
    LEVEL_PLATFORM_DEFAULT,
    LEVEL_YARD_ACTIVE,
    LEVEL_PLATFORM_PRIORITY,
    LEVEL_SETTINGS,
)


@dataclass(frozen=True)
class Candidate:
    """解析链上的一档候选：供应商行 + 可选模型覆盖 + 命中层级。

    v3.33 起 ``supplier_model`` 是真正命中的模型行（可为空 = 旧行为沿用
    ``supplier.model_id``）；``model_name`` 始终是**提交给上游的物理模型名**，
    保持旧调用方读取该字段的兼容性。
    """

    supplier: Supplier
    model_name: str | None
    level: str
    supplier_model: SupplierModel | None = None


class ScopeRef:
    """一条绑定的归属层：scope_type + scope_key（global 的 key 恒为空串）。"""

    def __init__(self, scope_type: str, scope_key: str) -> None:
        self.scope_type = scope_type
        self.scope_key = "" if scope_type == SCOPE_GLOBAL else (scope_key or "").strip()


def _normalize_scope(scope_type: str, scope_key: str) -> str:
    if scope_type == SCOPE_GLOBAL:
        return ""
    key = (scope_key or "").strip()
    if not key:
        raise AppError(
            "INVALID_REQUEST",
            f"{scope_type} 绑定必须指定 scope_key。",
            http_status=400,
        )
    if scope_type == SCOPE_PURPOSE and not is_known_purpose(key):
        raise AppError(
            "INVALID_REQUEST",
            f"未知能力码：{key}（请从能力清单中选择）。",
            http_status=400,
        )
    return key


def _visible_query(db: Session, yard_id: str | None):
    """可见绑定 = 本小院的 + 平台级的（yard_id 为 NULL）。"""
    if yard_id:
        return db.query(LLMBinding).filter(
            or_(LLMBinding.yard_id.is_(None), LLMBinding.yard_id == yard_id)
        )
    return db.query(LLMBinding).filter(LLMBinding.yard_id.is_(None))


def list_bindings(
    db: Session,
    yard_id: str | None,
    *,
    scope_type: str | None = None,
    scope_key: str | None = None,
) -> list[LLMBinding]:
    query = _visible_query(db, yard_id)
    if scope_type:
        query = query.filter(LLMBinding.scope_type == scope_type)
        if scope_key is not None:
            query = query.filter(LLMBinding.scope_key == _normalize_scope(scope_type, scope_key))
    return query.order_by(LLMBinding.priority.desc(), LLMBinding.created_at.asc()).all()


def _supplier_for_write(db: Session, yard_id: str | None, supplier_id: str) -> Supplier:
    """绑定可选的供应商：必须在本小院可见域内（全局共享或本小院自有）。"""
    row = (
        db.query(Supplier)
        .filter(Supplier.id == supplier_id)
        .filter(or_(Supplier.yard_id.is_(None), Supplier.yard_id == yard_id))
        .first()
    )
    if not row:
        raise AppError("SUPPLIER_NOT_FOUND", "供应商不存在或对本小院不可见。", http_status=404)
    return row


def replace_bindings(
    db: Session,
    yard_id: str | None,
    payload: BindingsReplace,
    *,
    is_platform_admin: bool,
) -> list[LLMBinding]:
    """整层替换某 (yard_id, scope_type, scope_key) 下的绑定条目。

    - 平台级写入（yard_id=None）仅平台管理员；小院管理员写的恒为本小院层。
    - 校验：同层最多 3 条、供应商去重且必须可见、能力码必须已登记。
    """
    scope_key = _normalize_scope(payload.scope_type, payload.scope_key)
    if len(payload.entries) > MAX_FALLBACK_PER_SCOPE:
        raise AppError(
            "BINDING_TOO_MANY",
            f"同一层最多配置 {MAX_FALLBACK_PER_SCOPE} 条兜底供应商。",
            http_status=400,
        )
    if len({e.supplier_id for e in payload.entries}) != len(payload.entries):
        raise AppError("INVALID_REQUEST", "同一层内供应商不能重复绑定。", http_status=400)

    owner_yard = yard_id
    if owner_yard is None and not is_platform_admin:
        raise AppError("YARD_REQUIRED", "创建模型绑定需要小院上下文。", http_status=400)

    db.query(LLMBinding).filter(
        LLMBinding.yard_id == owner_yard,
        LLMBinding.scope_type == payload.scope_type,
        LLMBinding.scope_key == scope_key,
    ).delete(synchronize_session=False)

    created: list[LLMBinding] = []
    # priority 由前端给出；缺省按提交顺序倒排，保证「第一条=主用」直观成立
    total = len(payload.entries)
    for idx, entry in enumerate(payload.entries):
        supplier = _supplier_for_write(db, owner_yard, entry.supplier_id)
        created.append(
            _write_row(
                db,
                owner_yard,
                payload.scope_type,
                scope_key,
                entry,
                supplier,
                default_priority=(total - idx) * 10,
            )
        )
    db.flush()
    return created


def _write_row(
    db: Session,
    yard_id: str | None,
    scope_type: str,
    scope_key: str,
    entry: BindingEntry,
    supplier: Supplier,
    *,
    default_priority: int,
) -> LLMBinding:
    if entry.supplier_model_id:
        model_row = db.get(SupplierModel, entry.supplier_model_id)
        if model_row is None or model_row.supplier_id != supplier.id:
            raise AppError(
                "INVALID_REQUEST",
                "所选模型不属于该供应商，请刷新模型列表后重新选择。",
                http_status=400,
            )
    row = LLMBinding(
        yard_id=yard_id,
        scope_type=scope_type,
        scope_key=scope_key,
        supplier_id=supplier.id,
        supplier_model_id=entry.supplier_model_id or None,
        model_name=(entry.model_name or "").strip() or None,
        priority=entry.priority if entry.priority else default_priority,
        enabled=entry.enabled,
    )
    db.add(row)
    db.flush()
    return row


def delete_binding(db: Session, yard_id: str | None, binding_id: str) -> None:
    row = _visible_query(db, yard_id).filter(LLMBinding.id == binding_id).first()
    if not row:
        raise AppError("BINDING_NOT_FOUND", "绑定不存在。", http_status=404)
    if row.yard_id is None:
        raise AppError(
            "BINDING_READONLY",
            "平台级绑定只能由平台管理员调整，小院可在本小院层覆盖。",
            http_status=403,
        )
    db.delete(row)
    db.flush()


# ---------- 解析 ----------


def _usable(db: Session, yard_id: str | None, supplier_id: str) -> Supplier | None:
    """供应商可用性：可见域内 + 存在 + 启用（禁用含运行时自动降级）。"""
    row = (
        db.query(Supplier)
        .filter(Supplier.id == supplier_id)
        .filter(or_(Supplier.yard_id.is_(None), Supplier.yard_id == yard_id))
        .first()
    )
    return row if row is not None and row.status == STATUS_ENABLED else None


def _bindings_of(
    db: Session, yard_id: str | None, scope_type: str, scope_key: str, *, platform_level: bool
) -> list[LLMBinding]:
    query = db.query(LLMBinding).filter(
        LLMBinding.scope_type == scope_type,
        LLMBinding.scope_key == scope_key,
        LLMBinding.enabled.is_(True),
    )
    query = query.filter(
        LLMBinding.yard_id.is_(None) if platform_level else LLMBinding.yard_id == yard_id
    )
    return query.order_by(LLMBinding.priority.desc(), LLMBinding.created_at.asc()).all()


def _append(
    chain: list[Candidate],
    seen: set[str],
    rows,
    level: str,
    *,
    yard_id: str | None,
) -> None:
    """按顺序追加可用供应商（同供应商只入链一次，先命中者优先）。"""
    for supplier, model_name, model_row in rows:
        if supplier is None or supplier.id in seen:
            continue
        seen.add(supplier.id)
        if model_row is not None and model_row.upstream_model_id:
            model_name = model_row.upstream_model_id
        chain.append(
            Candidate(
                supplier=supplier,
                model_name=model_name,
                level=level,
                supplier_model=model_row,
            )
        )


def _default_model_row(db: Session, supplier: Supplier | None) -> SupplierModel | None:
    """供应商未显式绑模型时：按 default_model_id → 默认模型行（启用态）。"""
    if supplier is None:
        return None
    from app.modules.llm import supplier_service

    return supplier_service.resolve_model_row(db, supplier)


def _binding_row(
    db: Session, supplier: Supplier | None, binding: LLMBinding
) -> tuple[Supplier | None, str | None, SupplierModel | None]:
    """一条绑定 → (供应商, 上游模型名, 模型行)。

    优先 ``supplier_model_id``；其次 ``model_name`` 匹配 supplier_models 的
    upstream_model_id/model_key；最后供应商默认模型；再落到 suppliers.model_id。
    """
    if supplier is None:
        return None, None, None
    from app.modules.llm import supplier_service

    model_row = supplier_service.resolve_model_row(
        db,
        supplier,
        supplier_model_id=getattr(binding, "supplier_model_id", None),
        model_name=binding.model_name,
    )
    if model_row is not None:
        return supplier, model_row.upstream_model_id, model_row
    return supplier, (binding.model_name or "").strip() or None, None


def resolve_chain(
    db: Session,
    yard_id: str | None,
    *,
    purpose: str | None = None,
    agent_code: str | None = None,
    bound_supplier_id: str | None = None,
) -> list[Candidate]:
    """按 7 档顺序产出候选链（已剔除不可用供应商，同供应商去重）。"""
    from app.modules.llm import supplier_service

    chain: list[Candidate] = []
    seen: set[str] = set()

    if bound_supplier_id:
        row = _usable(db, yard_id, bound_supplier_id)
        _append(
            chain,
            seen,
            [(row, None, _default_model_row(db, row))],
            LEVEL_AGENT_BOUND,
            yard_id=yard_id,
        )

    if agent_code:
        for platform_level in (False, True):
            rows = [
                _binding_row(db, _usable(db, yard_id, b.supplier_id), b)
                for b in _bindings_of(
                    db, yard_id, SCOPE_AGENT, agent_code, platform_level=platform_level
                )
            ]
            _append(chain, seen, rows, LEVEL_AGENT_BINDING, yard_id=yard_id)

    if purpose:
        for platform_level in (False, True):
            rows = [
                _binding_row(db, _usable(db, yard_id, b.supplier_id), b)
                for b in _bindings_of(
                    db, yard_id, SCOPE_PURPOSE, purpose, platform_level=platform_level
                )
            ]
            _append(chain, seen, rows, LEVEL_PURPOSE_BINDING, yard_id=yard_id)

    for platform_level in (False, True):
        rows = [
            _binding_row(db, _usable(db, yard_id, b.supplier_id), b)
            for b in _bindings_of(db, yard_id, SCOPE_GLOBAL, "", platform_level=platform_level)
        ]
        _append(
            chain,
            seen,
            rows,
            LEVEL_PLATFORM_DEFAULT if platform_level else LEVEL_YARD_DEFAULT,
            yard_id=yard_id,
        )

    if yard_id:
        active_id = supplier_service.get_active_supplier_id(db, yard_id)
        if active_id:
            row = _usable(db, yard_id, active_id)
            _append(
                chain,
                seen,
                [(row, None, _default_model_row(db, row))],
                LEVEL_YARD_ACTIVE,
                yard_id=yard_id,
            )
        # 平台供应商按 priority 兜底：取启用态里优先级最高的若干个
        priority_rows = [
            (s, None, _default_model_row(db, s))
            for s in db.query(Supplier)
            .filter(Supplier.yard_id.is_(None), Supplier.status == STATUS_ENABLED)
            .order_by(Supplier.priority.desc(), Supplier.created_at.asc())
            .all()
        ]
        _append(chain, seen, priority_rows, LEVEL_PLATFORM_PRIORITY, yard_id=yard_id)

    return chain


def resolution_summary(
    db: Session,
    yard_id: str | None,
    *,
    purpose: str | None = None,
    agent_code: str | None = None,
    bound_supplier_id: str | None = None,
) -> dict:
    """「这次调用会用谁」：候选链 + 每档说明，供模型中心实时展示。"""
    chain = resolve_chain(
        db,
        yard_id,
        purpose=purpose,
        agent_code=agent_code,
        bound_supplier_id=bound_supplier_id,
    )
    hops = [
        {
            "level": c.level,
            "level_label": LEVEL_LABELS.get(c.level, c.level),
            "reason": _hop_reason(c, purpose),
            "supplier_id": c.supplier.id,
            "supplier_name": c.supplier.name,
            "model_name": c.model_name or c.supplier.model_id,
            "model_key": getattr(c.supplier_model, "model_key", None),
            "modality": getattr(c.supplier_model, "modality", None),
            "usable": True,
        }
        for c in chain
    ]
    head = chain[0] if chain else None
    return {
        "purpose": purpose,
        "purpose_label": capability_label(purpose) if purpose else None,
        "agent_code": agent_code,
        "candidate_supplier_ids": [c.supplier.id for c in chain],
        "resolved_supplier_id": head.supplier.id if head else None,
        "resolved_model": (head.model_name or head.supplier.model_id) if head else None,
        "resolved_model_key": getattr(head.supplier_model, "model_key", None) if head else None,
        "resolved_level": LEVEL_LABELS.get(head.level, head.level)
        if head
        else LEVEL_LABELS[LEVEL_SETTINGS],
        "hops": hops,
    }


def _hop_reason(candidate: Candidate, purpose: str | None) -> str:
    label = LEVEL_LABELS.get(candidate.level, candidate.level)
    if candidate.level == LEVEL_PURPOSE_BINDING and purpose:
        return f"{label}：{capability_label(purpose)} 的主用/兜底供应商"
    if candidate.supplier_model is not None:
        return (
            f"{label}：指定模型 {candidate.supplier_model.model_key}"
            f"（{candidate.supplier_model.upstream_model_id}）"
        )
    if candidate.model_name:
        return f"{label}：指定模型 {candidate.model_name}"
    return f"{label}：沿用供应商默认模型 {candidate.supplier.model_id}"
