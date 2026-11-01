"""模型供应商服务：CRUD、连通性探测、小院 active 解析、Provider 构建。

- API Key 明文只在内存中短暂使用（加密落库 / 探测时），绝不打日志、绝不返回。
- 供应商的增删改、启用/禁用、active 切换都会失效 Provider 缓存，配置即时生效、无需重启。
"""

import csv
import io
import json
import re
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.llm.provider import invalidate_supplier_cache
from app.modules.llm.supplier_crypto import (
    decrypt_api_key,
    encrypt_api_key,
    mask_api_key,
)
from app.modules.llm.supplier_models import (
    CHANNEL_KIND_OFFICIAL,
    Supplier,
    SupplierModel,
    YardActiveSupplier,
)
from app.modules.llm.supplier_schemas import (
    STATUS_DISABLED,
    STATUS_ENABLED,
    ProbeModelItem,
    ProbeModelsResult,
    ProviderTemplate,
    SupplierCreate,
    SupplierImportBundle,
    SupplierImportPreview,
    SupplierImportPreviewItem,
    SupplierImportResult,
    SupplierModelCreate,
    SupplierModelOut,
    SupplierModelTestResult,
    SupplierModelUpdate,
    SupplierOut,
    SupplierTestResult,
    SupplierUpdate,
)

if TYPE_CHECKING:
    from app.modules.llm.providers_openai import OpenAICompatibleProvider

log = get_logger(__name__)

PROBE_TIMEOUT_SECONDS = 10
AUTO_DISABLE_FAILURES = 3  # 连续探测失败 3 次自动禁用
#: 运行时（真实业务调用）连续失败 5 次自动降级：与探测口径分开，
#: 避免一次上游抖动就把供应商踢出候选链（探测是主动的，运行时是被动的）。
RUNTIME_DEGRADE_FAILURES = 5

#: §A 模型列表探测：单次超时 8 秒、响应体硬上限 1MB（与 MCP 客户端同口径）
MODELS_PROBE_TIMEOUT_SECONDS = 8
MODELS_PROBE_MAX_BYTES = 1024 * 1024
#: 返回给前端的模型条目上限（诚实提示：上游再多也先截断）
MODELS_PROBE_MAX_ITEMS = 500
#: 无统一 OpenAI 兼容模型列表端点的类型（§A：直接 unsupported，不发外呼）
NO_MODEL_LIST_PROVIDERS = {
    "anthropic",
    "gemini",
    # 百炼原生图像接口与视频通道路径不同，不按 /models 探测，避免误报 404
    "dashscope_image",
    "openai_video",
}

#: 测试注入口：httpx.MockTransport 假扮上游 /models（全离线纪律，生产恒为 None）
_transport_override: httpx.AsyncBaseTransport | None = None


def set_test_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """测试专用：注入/清除 MockTransport（模块级单点，杜绝真实出网）。"""
    global _transport_override
    _transport_override = transport


def _validated_base_url(raw: str) -> str:
    """规范化并校验供应商 base_url。

    规则：必填、http(s) 协议、去掉尾部斜杠与空白；**不做 SSRF 判定**——
    出网校验统一由 check_egress_url 在探测/调用时执行（allow_local=True，
    放行本机与内网私有化部署，但元数据地址永远封死）。
    """
    url = (raw or "").strip().rstrip("/")
    if not url:
        raise AppError("INVALID_REQUEST", "基础地址不能为空。", http_status=400)
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise AppError(
            "INVALID_REQUEST",
            "基础地址必须是 http(s) 形式的完整 URL。",
            http_status=400,
        )
    return url


# ---------- 查询 ----------


def _visible_query(db: Session, yard_id: str | None):
    """小院可见供应商：全局共享 + 本小院。"""
    if yard_id:
        return db.query(Supplier).filter(
            or_(Supplier.yard_id.is_(None), Supplier.yard_id == yard_id)
        )
    return db.query(Supplier).filter(Supplier.yard_id.is_(None))


def get_supplier(db: Session, yard_id: str | None, supplier_id: str) -> Supplier:
    row = _visible_query(db, yard_id).filter(Supplier.id == supplier_id).first()
    if not row:
        raise AppError("SUPPLIER_NOT_FOUND", "供应商不存在。", http_status=404)
    return row


def _writable_supplier(
    db: Session, yard_id: str | None, supplier_id: str, *, is_platform_admin: bool
) -> Supplier:
    """写操作专用「角色+归属」双校验（ADR-0005）：

    - 先走可见性查询（全局 + 本小院），跨小院行天然不可见；
    - 非平台管理员不得改写平台全局行（yard_id 为 NULL）。
    越权与不存在统一 404（平台惯例：不向无权方泄露资源存在性）。
    """
    row = get_supplier(db, yard_id, supplier_id)
    if not is_platform_admin and row.yard_id is None:
        raise AppError("SUPPLIER_NOT_FOUND", "供应商不存在。", http_status=404)
    if not is_platform_admin and getattr(row, "channel_kind", "user") == CHANNEL_KIND_OFFICIAL:
        raise AppError(
            "SUPPLIER_READONLY",
            "官方模型通道由平台维护，小院只读；请在「官方模型」中开通权益使用。",
            http_status=403,
        )
    return row


def _writable_model_supplier(
    db: Session, yard_id: str | None, supplier_id: str, *, is_platform_admin: bool
) -> Supplier:
    """模型目录写操作的归属校验：与供应商写操作同权（官方通道小院只读）。"""
    return _writable_supplier(db, yard_id, supplier_id, is_platform_admin=is_platform_admin)


def get_active_supplier_id(db: Session, yard_id: str) -> str | None:
    row = db.query(YardActiveSupplier).filter_by(yard_id=yard_id).first()
    return row.supplier_id if row else None


def resolve_active_supplier(db: Session, yard_id: str) -> Supplier | None:
    """热更新解析：小院 active supplier → 全局 active（priority 最高）→ None（回退 settings）。"""
    active_id = get_active_supplier_id(db, yard_id)
    if active_id:
        row = db.query(Supplier).filter(Supplier.id == active_id).first()
        if row and row.status == STATUS_ENABLED and (row.yard_id is None or row.yard_id == yard_id):
            return row
        # active 指向的供应商不可用：继续走全局兜底
    return (
        db.query(Supplier)
        .filter(Supplier.yard_id.is_(None), Supplier.status == STATUS_ENABLED)
        .order_by(Supplier.priority.desc(), Supplier.created_at.asc())
        .first()
    )


def supplier_to_out(
    db: Session, row: Supplier, yard_id: str, *, is_platform_admin: bool = False
) -> SupplierOut:
    masked = mask_api_key(decrypt_api_key(row.api_key_enc))
    return SupplierOut(
        id=row.id,
        yard_id=row.yard_id,
        name=row.name,
        provider_type=row.provider_type,
        base_url=row.base_url,
        model_id=row.model_id,
        api_key_masked=masked,
        models=row.models,
        priority=row.priority,
        status=row.status,
        is_active=get_active_supplier_id(db, yard_id) == row.id if yard_id else False,
        # 小院管理员视角：全局共享行/官方通道行只读；平台管理员不受限
        readonly=(not is_platform_admin)
        and (row.yard_id is None or getattr(row, "channel_kind", "user") == CHANNEL_KIND_OFFICIAL),
        default_model_id=getattr(row, "default_model_id", None),
        channel_kind=getattr(row, "channel_kind", "user") or "user",
        consecutive_failures=row.consecutive_failures or 0,
        test_time=row.test_time.isoformat() if row.test_time else None,
        response_time_ms=row.response_time_ms,
        last_error=row.last_error,
        supports_tools=bool(row.supports_tools),
        price_in_per_1k=row.price_in_per_1k,
        price_out_per_1k=row.price_out_per_1k,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


# ---------- CRUD ----------


def list_suppliers(
    db: Session, yard_id: str | None, *, is_platform_admin: bool = False
) -> list[SupplierOut]:
    rows = (
        _visible_query(db, yard_id)
        .order_by(Supplier.priority.desc(), Supplier.created_at.asc())
        .all()
    )
    return [
        supplier_to_out(db, r, yard_id or "", is_platform_admin=is_platform_admin) for r in rows
    ]


def _selected_model_ids(values: list[str] | None, default_model: str | None) -> list[str]:
    """默认模型置首、去重、截断；保证默认模型一定在可用列表里。"""
    out: list[str] = []
    for raw in [default_model or "", *(values or [])]:
        mid = str(raw or "").strip()
        if mid and mid not in out:
            out.append(mid[:200])
    return out[:200]


def _protocol_for_selected(supplier: Supplier, modality: str) -> str:
    default = {
        "openai_images": "openai_images",
        "dashscope_image": "dashscope_image",
        "openai_video": "openai_video",
    }.get(supplier.provider_type, "openai_chat")
    protocol = protocol_for_modality(modality, default=default)
    if supplier.provider_type == "dashscope_image" and modality == "image":
        return "dashscope_image"
    return protocol


def _apply_selected_models(
    db: Session,
    supplier: Supplier,
    selected_values: list[str] | None,
    default_model: str | None,
) -> None:
    """按「可用模型」勾选结果增补/停用 supplier_models 行，并校准默认模型。

    未勾选的模型只置停用、不删除：保留既有绑定与历史，绑定解析会自动跳过停用行。
    """
    selected = _selected_model_ids(selected_values, default_model)
    default_id = (default_model or (selected[0] if selected else "")).strip()
    forced_modality = {
        "openai_images": "image",
        "dashscope_image": "image",
        "openai_video": "video",
    }.get(supplier.provider_type)

    rows = (
        db.query(SupplierModel)
        .filter(SupplierModel.supplier_id == supplier.id)
        .order_by(SupplierModel.created_at.asc())
        .all()
    )
    by_name: dict[str, SupplierModel] = {}
    for row in rows:
        by_name[row.model_key] = row
        by_name[row.upstream_model_id] = row

    default_row: SupplierModel | None = None
    for mid in selected:
        selected_row = by_name.get(mid)
        if selected_row is None:
            modality = guess_modality(mid)
            if forced_modality and modality in ("text", "multimodal"):
                modality = forced_modality
            selected_row = SupplierModel(
                supplier_id=supplier.id,
                model_key=mid[:100],
                upstream_model_id=mid[:200],
                display_name=mid[:120],
                modality=modality,
                protocol=_protocol_for_selected(supplier, modality),
                source="probe",
                status=STATUS_ENABLED,
            )
            db.add(selected_row)
            db.flush()
            rows.append(selected_row)
            by_name[selected_row.model_key] = selected_row
            by_name[selected_row.upstream_model_id] = selected_row
        else:
            selected_row.status = STATUS_ENABLED
        if mid == default_id:
            default_row = selected_row

    keep = set(selected)
    for row in rows:
        if row.model_key not in keep and row.upstream_model_id not in keep:
            row.status = STATUS_DISABLED

    if default_row is None and selected:
        default_row = by_name.get(default_id)
    if default_row is not None:
        supplier.default_model_id = default_row.id
    _sync_supplier_defaults(db, supplier)


def create_supplier(
    db: Session,
    yard_id: str | None,
    payload: SupplierCreate,
    *,
    is_platform_admin: bool,
) -> SupplierOut:
    scope_yard_id: str | None
    if payload.scope == "global":
        if not is_platform_admin:
            raise AppError("FORBIDDEN", "只有平台管理员可以创建全局共享供应商。", http_status=403)
        scope_yard_id = None
    else:
        if not yard_id:
            raise AppError("YARD_REQUIRED", "创建小院供应商需要小院上下文。", http_status=400)
        scope_yard_id = yard_id
    row = Supplier(
        yard_id=scope_yard_id,
        name=payload.name.strip(),
        provider_type=payload.provider_type,
        base_url=_validated_base_url(payload.base_url),
        model_id=payload.model_id.strip(),
        api_key_enc=encrypt_api_key(payload.api_key),
        models=(payload.models.strip() or None) if payload.models else None,
        priority=payload.priority,
        status=STATUS_ENABLED,
        price_in_per_1k=payload.price_in_per_1k,
        price_out_per_1k=payload.price_out_per_1k,
    )
    db.add(row)
    db.flush()
    _apply_selected_models(db, row, payload.available_models, payload.model_id)
    invalidate_supplier_cache()
    return supplier_to_out(db, row, yard_id or "")


def update_supplier(
    db: Session,
    yard_id: str | None,
    supplier_id: str,
    payload: SupplierUpdate,
    *,
    is_platform_admin: bool = False,
) -> SupplierOut:
    row = _writable_supplier(db, yard_id, supplier_id, is_platform_admin=is_platform_admin)
    data = payload.model_dump(exclude_unset=True)
    api_key = data.pop("api_key", None)
    selected_models = data.pop("available_models", None)
    model_changed = "model_id" in data
    for field in ("name", "provider_type", "base_url", "model_id", "models", "priority"):
        if field in data and data[field] is not None:
            value = data[field]
            if isinstance(value, str):
                value = value.strip()
                if field == "base_url":
                    value = value.rstrip("/")
                if field == "models" and not value:
                    value = None
            setattr(row, field, value)
    if api_key:
        row.api_key_enc = encrypt_api_key(api_key)  # 密钥轮换
        row.consecutive_failures = 0
        row.last_error = None
    # function-calling 能力位（布尔位单独处理：不走上方字符串 strip 分支）
    if data.get("supports_tools") is not None:
        row.supports_tools = bool(data["supports_tools"])
    # v3.32 金额额度单价：显式传 null 表示清空（回退到 Provider 默认估算）
    for price_field in ("price_in_per_1k", "price_out_per_1k"):
        if price_field in data:
            row.__setattr__(price_field, data[price_field])
    if selected_models is not None or model_changed:
        if selected_models is None:
            selected_models = [
                m.upstream_model_id
                for m in db.query(SupplierModel)
                .filter(
                    SupplierModel.supplier_id == row.id,
                    SupplierModel.status == STATUS_ENABLED,
                )
                .order_by(SupplierModel.created_at.asc())
                .all()
            ]
        _apply_selected_models(db, row, selected_models, row.model_id)
    db.flush()
    invalidate_supplier_cache(supplier_id=row.id)
    return supplier_to_out(db, row, yard_id or "")


def delete_supplier(
    db: Session, yard_id: str | None, supplier_id: str, *, is_platform_admin: bool = False
) -> None:
    row = _writable_supplier(db, yard_id, supplier_id, is_platform_admin=is_platform_admin)
    db.query(YardActiveSupplier).filter(YardActiveSupplier.supplier_id == row.id).delete()
    db.delete(row)
    db.flush()
    invalidate_supplier_cache(supplier_id=row.id)


def set_supplier_status(
    db: Session,
    yard_id: str | None,
    supplier_id: str,
    status: int,
    *,
    is_platform_admin: bool = False,
) -> SupplierOut:
    row = _writable_supplier(db, yard_id, supplier_id, is_platform_admin=is_platform_admin)
    row.status = status
    if status == STATUS_ENABLED:
        row.consecutive_failures = 0
    db.flush()
    invalidate_supplier_cache(supplier_id=row.id)
    return supplier_to_out(db, row, yard_id or "")


def set_active_supplier(
    db: Session, yard_id: str, supplier_id: str, *, is_platform_admin: bool = False
) -> SupplierOut:
    # 刻意用可见性查询（非 writable）：小院可选用平台全局行作为本小院生效供应商
    row = get_supplier(db, yard_id, supplier_id)
    if row.status != STATUS_ENABLED:
        raise AppError("SUPPLIER_DISABLED", "该供应商已禁用，请先启用。", http_status=400)
    active = db.query(YardActiveSupplier).filter_by(yard_id=yard_id).first()
    if active:
        active.supplier_id = row.id
    else:
        db.add(YardActiveSupplier(yard_id=yard_id, supplier_id=row.id))
    db.flush()
    invalidate_supplier_cache()  # active 映射变更：整体失效，保证解析即时生效
    out = supplier_to_out(db, row, yard_id, is_platform_admin=is_platform_admin)
    out.is_active = True
    return out


# ---------- 模态推断（v3.33） ----------

#: 关键字 → 模态（顺序敏感：先命中先返回，避免 "qwen-vl" 被 "qwen" 误判为文本）
_MODALITY_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("rerank", "rerank"),
    ("embedding", "embedding"),
    ("bge", "embedding"),
    ("gte", "embedding"),
    ("tts", "tts"),
    ("whisper", "asr"),
    ("paraformer", "asr"),
    ("sensevoice", "asr"),
    ("asr", "asr"),
    ("dall-e", "image"),
    ("dalle", "image"),
    ("flux", "image"),
    ("wanx", "image"),
    ("seedream", "image"),
    ("stable-diffusion", "image"),
    ("sdxl", "image"),
    ("veo", "video"),
    ("kling", "video"),
    ("hailuo", "video"),
    ("sora", "video"),
    ("wan", "video"),
    ("vl", "multimodal"),
    ("vision", "multimodal"),
    ("omni", "multimodal"),
)


def guess_modality(model_id: str) -> str:
    """按模型名关键字猜测模态（仅作导入预填，不代替人工确认）。

    ``wan`` 既可能是通义万相（图像）也可能是视频；这里按 v3.33 口径归 video，
    因为 ``wanx``（文生图）已在前面的 image 规则命中。
    """
    name = (model_id or "").strip().lower()
    for keyword, modality in _MODALITY_KEYWORDS:
        if keyword in name:
            return modality
    return "text"


def protocol_for_modality(modality: str, default: str = "openai_chat") -> str:
    """模态 → 默认协议（显式给出协议时以显式值为准）。"""
    return {
        "image": "openai_images",
        "video": "openai_video",
        "embedding": "openai_embedding",
        "asr": "openai_audio",
        "tts": "openai_audio",
        "text": default,
        "multimodal": default,
        "audio": default,
        "rerank": default,
    }.get(modality, default)


# ---------- 连通性探测 ----------


async def probe_models(
    *,
    provider_type: str,
    base_url: str,
    api_key: str | None = None,
) -> ProbeModelsResult:
    """探测上游模型列表（DESIGN-v3 §A）。

    - openai 兼容族（openai/deepseek/moonshot/qwen/custom）→ GET {base}/models；
    - anthropic/gemini 无统一列表端点 → source='unsupported'（不发外呼，诚实说明）；
    - 失败 → source='error' + 中文原因，**不抛错**（前端据此给「手动输入」兜底）。

    api_key 只在本次调用内存中使用，绝不落库/日志/审计。
    """
    if provider_type in NO_MODEL_LIST_PROVIDERS:
        return ProbeModelsResult(
            models=[],
            source="unsupported",
            message=f"{provider_type} 没有统一的模型列表接口，请手动填写模型名。",
        )
    url = _validated_base_url(base_url)
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    from app.core.net_guard import check_egress_url

    try:
        check_egress_url(url, context="supplier_probe_models", allow_local=True)
    except AppError as e:
        return ProbeModelsResult(models=[], source="error", message=e.message)

    try:
        client_kwargs: dict = {"timeout": MODELS_PROBE_TIMEOUT_SECONDS}
        if _transport_override is not None:
            client_kwargs["transport"] = _transport_override
        async with httpx.AsyncClient(**client_kwargs) as client:
            resp = await client.get(f"{url}/models", headers=headers)
            if resp.status_code >= 400:
                return ProbeModelsResult(
                    models=[],
                    source="error",
                    message=f"上游返回 {resp.status_code}，请检查地址与密钥。",
                )
            if len(resp.content) > MODELS_PROBE_MAX_BYTES:
                return ProbeModelsResult(
                    models=[], source="error", message="上游响应过大，已中止解析。"
                )
            data = resp.json()
    except httpx.TimeoutException:
        return ProbeModelsResult(models=[], source="error", message="连接上游超时（8 秒）。")
    except httpx.HTTPError:
        return ProbeModelsResult(models=[], source="error", message="无法连接上游地址。")
    except ValueError:
        return ProbeModelsResult(models=[], source="error", message="上游返回的不是合法 JSON。")

    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return ProbeModelsResult(
            models=[], source="error", message="上游响应结构不符合 OpenAI 兼容格式。"
        )
    models: list[ProbeModelItem] = []
    for entry in items[:MODELS_PROBE_MAX_ITEMS]:
        if isinstance(entry, dict) and entry.get("id"):
            mid = str(entry["id"])
            models.append(ProbeModelItem(id=mid, label=mid, guessed_modality=guess_modality(mid)))
        elif isinstance(entry, str) and entry:
            models.append(
                ProbeModelItem(id=entry, label=entry, guessed_modality=guess_modality(entry))
            )
    if not models:
        return ProbeModelsResult(models=[], source="error", message="上游未返回任何模型。")
    return ProbeModelsResult(models=models, source="remote")


async def probe_upstream(
    base_url: str,
    api_key: str,
    model_id: str,
    timeout: int = PROBE_TIMEOUT_SECONDS,
    *,
    provider_type: str = "",
) -> SupplierTestResult:
    """先 GET {base}/models；失败则发一条最小 chat('hi')。key 只在内存使用。

    图像/视频类供应商（``provider_type`` 属于视觉域）没有 chat 端点，
    因此只做 /models 探测，不强塞一条会必然失败的 chat 请求。
    """
    from app.modules.llm.images import IMAGE_PROVIDER_TYPES, VIDEO_PROVIDER_TYPES

    visual_only = provider_type in (IMAGE_PROVIDER_TYPES | VIDEO_PROVIDER_TYPES)
    base = base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}"}
    started = time.perf_counter()

    def _fail(note: str) -> SupplierTestResult:
        return SupplierTestResult(
            ok=False, latency_ms=int((time.perf_counter() - started) * 1000), models=[], note=note
        )

    # SSRF 防护（规范 §15.1）：探测目标禁止指向回环/link-local/云元数据/保留地址
    from app.core.errors import AppError as _AppError
    from app.core.net_guard import check_egress_url

    try:
        check_egress_url(base_url, context="supplier_probe", allow_local=True)
    except _AppError as e:
        return _fail(e.message)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            models_resp = None
            try:
                models_resp = await client.get(f"{base}/models", headers=headers)
            except httpx.HTTPError:
                models_resp = None  # /models 不可达：降级走 chat 探测
            if models_resp is not None and models_resp.status_code == 200:
                try:
                    data = models_resp.json()
                except ValueError:
                    data = {}
                items = data.get("data") or []
                models = [str(m.get("id")) for m in items if isinstance(m, dict) and m.get("id")]
                return SupplierTestResult(
                    ok=True,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    models=models,
                    note="/models 探测成功",
                )
            # /models 失败/不可用：发一条最小 chat 做真实验证
            if visual_only:
                if models_resp is not None and models_resp.status_code in (401, 403):
                    return _fail("鉴权失败：请检查 API Key")
                return _fail(
                    "图像/视频供应商不支持 chat 探测：/models 未返回模型列表，请核对 base_url"
                )
            chat_started = time.perf_counter()
            chat_resp = await client.post(
                f"{base}/chat/completions",
                json={
                    "model": model_id,
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 4,
                },
                headers=headers,
            )
            latency = int((time.perf_counter() - chat_started) * 1000)
            if chat_resp.status_code == 200:
                return SupplierTestResult(
                    ok=True,
                    latency_ms=latency,
                    models=[],
                    note="chat 探测成功（/models 不可用，未取到模型列表）",
                )
            if chat_resp.status_code in (401, 403):
                return SupplierTestResult(
                    ok=False, latency_ms=latency, models=[], note="鉴权失败：请检查 API Key"
                )
            if chat_resp.status_code == 429:
                return SupplierTestResult(
                    ok=False, latency_ms=latency, models=[], note="上游限流（429），请稍后重试"
                )
            return SupplierTestResult(
                ok=False,
                latency_ms=latency,
                models=[],
                note=f"上游返回异常状态码 {chat_resp.status_code}",
            )
    except httpx.TimeoutException:
        return _fail(f"连接超时（{timeout} 秒）")
    except httpx.ConnectError:
        return _fail("无法连接上游地址，请检查 base_url")
    except httpx.HTTPError:
        return _fail("网络异常，请稍后重试")


async def test_saved_supplier(
    db: Session, yard_id: str | None, supplier_id: str, *, is_platform_admin: bool = False
) -> tuple[SupplierOut, SupplierTestResult]:
    """对已存供应商探测，结果写回 test_time/response_time_ms/last_error；
    连续失败 3 次自动禁用。探测会改写供应商行，故与编辑同权：
    小院管理员对平台全局行不可探测（404）。"""
    row = _writable_supplier(db, yard_id, supplier_id, is_platform_admin=is_platform_admin)
    api_key = decrypt_api_key(row.api_key_enc)
    try:
        result = await probe_upstream(
            row.base_url, api_key, row.model_id, provider_type=row.provider_type
        )
    finally:
        del api_key  # 明文 key 用完即弃
    row.test_time = datetime.now(UTC)
    row.response_time_ms = result.latency_ms
    if result.ok:
        row.consecutive_failures = 0
        row.last_error = None
    else:
        row.consecutive_failures = (row.consecutive_failures or 0) + 1
        row.last_error = result.note
        if row.consecutive_failures >= AUTO_DISABLE_FAILURES:
            row.status = STATUS_DISABLED
            log.warning(
                "供应商 %s 连续探测失败 %d 次，已自动禁用",
                row.id,
                row.consecutive_failures,
            )
    db.flush()
    invalidate_supplier_cache(supplier_id=row.id)
    return supplier_to_out(db, row, yard_id or ""), result


# ---------- 运行时健康记账（v3.25 兜底与降级） ----------


def record_runtime_success(db: Session, supplier_id: str | None) -> None:
    """真实调用成功：清零连续失败计数（降级恢复的正向路径）。"""
    if not supplier_id:
        return
    row = db.get(Supplier, supplier_id)
    if row is None:
        return
    if row.consecutive_failures:
        row.consecutive_failures = 0
        row.last_error = None
        db.flush()


def record_runtime_failure(db: Session, supplier_id: str | None, note: str) -> bool:
    """真实调用失败：累计连续失败数，达到阈值自动降级（禁用并从候选链剔除）。

    返回是否触发了本次降级，供调用方打一条「已自动降级」日志。
    """
    if not supplier_id:
        return False
    row = db.get(Supplier, supplier_id)
    if row is None:
        return False
    row.consecutive_failures = (row.consecutive_failures or 0) + 1
    row.last_error = (note or "")[:300]
    degraded = False
    if row.consecutive_failures >= RUNTIME_DEGRADE_FAILURES and row.status == STATUS_ENABLED:
        row.status = STATUS_DISABLED
        degraded = True
    db.flush()
    invalidate_supplier_cache(supplier_id=row.id)
    return degraded


# ---------- Provider 构建与模板 ----------


def build_provider_from_supplier(
    row: "Supplier",
    *,
    model_override: str | None = None,
    model_row: SupplierModel | None = None,
) -> "OpenAICompatibleProvider":
    """由供应商行构建 OpenAI 兼容 Provider（各类型统一走兼容协议）。

    ``model_override`` 来自模型绑定（v3.25）：绑定行指定物理模型时覆盖供应商默认值。
    ``model_row``（v3.33）优先级最高：模型行的 ``upstream_model_id`` 是真正提交给
    上游的名字，其 ``pricing`` 覆盖供应商级单价，``capabilities.tool_calling``
    与供应商 ``supports_tools`` 取或。
    """
    from app.modules.llm.providers_openai import OpenAICompatibleProvider

    api_key = decrypt_api_key(row.api_key_enc)
    model = (
        (getattr(model_row, "upstream_model_id", None) or "").strip()
        or (model_override or "").strip()
        or row.model_id
    )
    try:
        provider = OpenAICompatibleProvider(
            base_url=row.base_url,
            api_key=api_key,
            model=model,
        )
    finally:
        del api_key
    # 调用记录中的 provider 标签（llm_calls.provider 列最长 50）
    provider.provider_name = f"supplier:{row.name}"[:50]
    provider.supplier_id = row.id
    # 调用记录里带上模型行 id：同一供应商多模型时成本/额度可按模型归属
    provider.supplier_model_id = getattr(model_row, "id", None)  # type: ignore[attr-defined]
    # function-calling 能力位：供应商声明 或 模型行能力位（任一为真即开启）
    capabilities = (model_row.capabilities or {}) if model_row is not None else {}
    provider.supports_tools = bool(
        getattr(row, "supports_tools", False) or capabilities.get("tool_calling")
    )
    # v3.32 金额额度：模型行单价优先，其次供应商单价，未填则沿用 Provider 默认估算
    provider.price_in_per_1k = getattr(row, "price_in_per_1k", None)
    provider.price_out_per_1k = getattr(row, "price_out_per_1k", None)
    if model_row is not None:
        pricing = model_row.pricing or {}
        if isinstance(pricing, dict):
            if pricing.get("input_per_1k") is not None:
                provider.price_in_per_1k = float(pricing["input_per_1k"])
            if pricing.get("output_per_1k") is not None:
                provider.price_out_per_1k = float(pricing["output_per_1k"])
    return provider


# ---------- 供应商多模型目录（v3.33） ----------

#: URL 导入：响应体硬上限 2MB、最多 3 次重定向（每跳都做 SSRF 校验）
IMPORT_MAX_BYTES = 2 * 1024 * 1024
IMPORT_MAX_REDIRECTS = 3
IMPORT_TIMEOUT_SECONDS = 15

#: 模型行可写字段（导入 upsert 的差集计算口径）
_MODEL_WRITABLE_FIELDS: tuple[str, ...] = (
    "model_key",
    "upstream_model_id",
    "display_name",
    "modality",
    "protocol",
    "input_modalities",
    "output_modalities",
    "capabilities",
    "context_window",
    "max_output_tokens",
    "endpoint_path",
    "pricing",
    "default_params",
    "limits_json",
    "status",
)


def supplier_model_to_out(row: SupplierModel) -> SupplierModelOut:
    return SupplierModelOut(
        id=row.id,
        supplier_id=row.supplier_id,
        model_key=row.model_key,
        upstream_model_id=row.upstream_model_id,
        display_name=row.display_name or row.model_key,
        modality=row.modality or "text",
        protocol=row.protocol or "openai_chat",
        input_modalities=row.input_modalities,
        output_modalities=row.output_modalities,
        capabilities=row.capabilities,
        context_window=row.context_window,
        max_output_tokens=row.max_output_tokens,
        endpoint_path=row.endpoint_path,
        pricing=row.pricing,
        default_params=row.default_params,
        limits_json=row.limits_json,
        status=row.status,
        source=row.source or "manual",
        external_id=row.external_id,
        test_ok=row.test_ok,
        latency_ms=row.latency_ms,
        last_error=row.last_error,
        last_test_at=row.last_test_at.isoformat() if row.last_test_at else None,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def _validate_pricing(pricing: Any) -> dict[str, Any] | None:
    """单价字典校验：除 ``currency`` 外必须是 >=0 的数字（不做任何隐式转换容错）。"""
    if pricing is None:
        return None
    if not isinstance(pricing, dict):
        raise AppError("INVALID_REQUEST", '价格必须是对象（如 {"input_per_1k": 0.001}）。', 400)
    out: dict[str, Any] = {}
    for key, value in pricing.items():
        name = str(key).strip()
        if not name or len(name) > 40:
            raise AppError("INVALID_REQUEST", "价格字段名不合法。", 400)
        if name == "currency":
            out[name] = str(value)[:8]
            continue
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise AppError("INVALID_REQUEST", f"价格字段「{name}」必须是数字。", http_status=400)
        if float(value) < 0:
            raise AppError("INVALID_REQUEST", f"价格字段「{name}」不能为负数。", http_status=400)
        out[name] = float(value)
    return out or None


def _model_values(payload: SupplierModelCreate) -> dict[str, Any]:
    """把创建契约收敛成可落库字段，并做服务端硬校验。"""
    data = payload.model_dump()
    # source 由调用方显式决定（手工 / 导入 / 官方供应商同步 / 官方），不随行数据写入
    data.pop("source", None)
    data["model_key"] = (data.get("model_key") or "").strip()
    data["upstream_model_id"] = (data.get("upstream_model_id") or "").strip()
    if not data["model_key"]:
        raise AppError("INVALID_REQUEST", "模型标识（model_key）不能为空。", 400)
    if not data["upstream_model_id"]:
        raise AppError("INVALID_REQUEST", "上游模型名（upstream_model_id）不能为空。", 400)
    data["display_name"] = (data.get("display_name") or data["model_key"]).strip()[:120] or data[
        "model_key"
    ]
    data["pricing"] = _validate_pricing(data.get("pricing"))
    if data.get("endpoint_path"):
        data["endpoint_path"] = str(data["endpoint_path"]).strip()[:200] or None
    return data


def _ensure_model_key_free(
    db: Session, supplier_id: str, model_key: str, *, exclude_id: str | None = None
) -> None:
    query = db.query(SupplierModel).filter(
        SupplierModel.supplier_id == supplier_id,
        SupplierModel.model_key == model_key,
    )
    if exclude_id:
        query = query.filter(SupplierModel.id != exclude_id)
    if query.first() is not None:
        raise AppError(
            "MODEL_KEY_EXISTS",
            f"模型标识「{model_key}」在该供应商下已存在，请换一个。",
            http_status=409,
        )


def get_supplier_model(db: Session, supplier_id: str, model_id: str) -> SupplierModel:
    row = (
        db.query(SupplierModel)
        .filter(SupplierModel.id == model_id, SupplierModel.supplier_id == supplier_id)
        .first()
    )
    if row is None:
        raise AppError("MODEL_NOT_FOUND", "模型不存在。", http_status=404)
    return row


def list_supplier_models(
    db: Session, yard_id: str | None, supplier_id: str, *, is_platform_admin: bool = False
) -> list[SupplierModelOut]:
    get_supplier(db, yard_id, supplier_id)  # 可见性校验（跨小院 404）
    rows = (
        db.query(SupplierModel)
        .filter(SupplierModel.supplier_id == supplier_id)
        .order_by(SupplierModel.modality.asc(), SupplierModel.created_at.asc())
        .all()
    )
    return [supplier_model_to_out(row) for row in rows]


def create_supplier_model(
    db: Session,
    yard_id: str | None,
    supplier_id: str,
    payload: SupplierModelCreate,
    *,
    is_platform_admin: bool = False,
    source: str | None = None,
) -> SupplierModelOut:
    supplier = _writable_model_supplier(
        db, yard_id, supplier_id, is_platform_admin=is_platform_admin
    )
    data = _model_values(payload)
    _ensure_model_key_free(db, supplier.id, data["model_key"])
    data["source"] = source or payload.source
    row = SupplierModel(supplier_id=supplier.id, **data)
    db.add(row)
    db.flush()
    _sync_supplier_defaults(db, supplier)
    invalidate_supplier_cache(supplier_id=supplier.id)
    return supplier_model_to_out(row)


def update_supplier_model(
    db: Session,
    yard_id: str | None,
    supplier_id: str,
    model_id: str,
    payload: SupplierModelUpdate,
    *,
    is_platform_admin: bool = False,
) -> SupplierModelOut:
    supplier = _writable_model_supplier(
        db, yard_id, supplier_id, is_platform_admin=is_platform_admin
    )
    row = get_supplier_model(db, supplier.id, model_id)
    data = payload.model_dump(exclude_unset=True)
    if "model_key" in data and data["model_key"] is not None:
        key = str(data["model_key"]).strip()
        if not key:
            raise AppError("INVALID_REQUEST", "模型标识（model_key）不能为空。", 400)
        _ensure_model_key_free(db, supplier.id, key, exclude_id=row.id)
        row.model_key = key[:100]
    if "upstream_model_id" in data and data["upstream_model_id"] is not None:
        upstream = str(data["upstream_model_id"]).strip()
        if not upstream:
            raise AppError("INVALID_REQUEST", "上游模型名（upstream_model_id）不能为空。", 400)
        row.upstream_model_id = upstream[:200]
    if "display_name" in data and data["display_name"] is not None:
        row.display_name = str(data["display_name"]).strip()[:120] or row.model_key
    for field in (
        "modality",
        "protocol",
        "input_modalities",
        "output_modalities",
        "capabilities",
        "context_window",
        "max_output_tokens",
        "endpoint_path",
        "default_params",
        "limits_json",
        "status",
    ):
        if field in data:
            value = data[field]
            if isinstance(value, str):
                value = value.strip() or None
            setattr(row, field, value)
    if "pricing" in data:
        row.pricing = _validate_pricing(data["pricing"])
    db.flush()
    _sync_supplier_defaults(db, supplier)
    invalidate_supplier_cache(supplier_id=supplier.id)
    return supplier_model_to_out(row)


def delete_supplier_model(
    db: Session,
    yard_id: str | None,
    supplier_id: str,
    model_id: str,
    *,
    is_platform_admin: bool = False,
) -> None:
    from app.modules.llm.binding_models import LLMBinding

    supplier = _writable_model_supplier(
        db, yard_id, supplier_id, is_platform_admin=is_platform_admin
    )
    row = get_supplier_model(db, supplier.id, model_id)
    refs = db.query(LLMBinding).filter(LLMBinding.supplier_model_id == row.id).count()
    # 旧绑定（只有 model_name）同样算引用：删了会让它在运行时静默回落到默认模型
    legacy_refs = (
        db.query(LLMBinding)
        .filter(
            LLMBinding.supplier_id == supplier.id,
            LLMBinding.supplier_model_id.is_(None),
            LLMBinding.model_name.in_([row.model_key, row.upstream_model_id]),
        )
        .count()
    )
    if refs or legacy_refs:
        raise AppError(
            "MODEL_IN_USE",
            f"模型「{row.model_key}」仍被 {refs + legacy_refs} 条模型绑定引用，"
            "请先在「模型绑定」中解除或改绑后再删除。",
            http_status=409,
        )
    used_as_default = supplier.default_model_id == row.id
    db.delete(row)
    db.flush()
    if used_as_default:
        supplier.default_model_id = None
    _sync_supplier_defaults(db, supplier)
    invalidate_supplier_cache(supplier_id=supplier.id)


def _sync_supplier_defaults(db: Session, supplier: Supplier) -> None:
    """模型增删改后同步旧字段：``models`` 白名单、``default_model_id``、``model_id``。"""
    rows = (
        db.query(SupplierModel)
        .filter(SupplierModel.supplier_id == supplier.id)
        .order_by(SupplierModel.created_at.asc())
        .all()
    )
    if not rows:
        supplier.default_model_id = None
        supplier.models = None
        return
    supplier.models = ",".join(row.model_key for row in rows)[:2000] or None
    by_id = {row.id: row for row in rows}
    if supplier.default_model_id not in by_id:
        enabled = [row for row in rows if int(row.status or 0) == STATUS_ENABLED]
        preferred = next((row for row in enabled if row.modality in ("text", "multimodal")), None)
        supplier.default_model_id = (preferred or (enabled or rows)[0]).id
    default_row = by_id[supplier.default_model_id]
    if default_row.modality in ("text", "multimodal", "embedding", "rerank"):
        # 旧单模型调用方读 suppliers.model_id：让它与默认模型保持一致
        if default_row.upstream_model_id:
            supplier.model_id = default_row.upstream_model_id[:100]


def sync_supplier_model_defaults(db: Session, supplier: Supplier) -> None:
    """公开入口：官方供应商/官方同步在批量改动模型后校准默认模型与旧字段。"""
    _sync_supplier_defaults(db, supplier)


def resolve_model_row(
    db: Session,
    supplier: Supplier,
    *,
    supplier_model_id: str | None = None,
    model_name: str | None = None,
) -> SupplierModel | None:
    """绑定解析：模型行 id → model_name（upstream/model_key）→ 供应商默认模型。

    只返回启用态模型；匹配不到返回 None，由调用方回落到 ``supplier.model_id``。
    """
    if supplier_model_id:
        row = db.get(SupplierModel, supplier_model_id)
        if (
            row is not None
            and row.supplier_id == supplier.id
            and int(row.status or 0) == STATUS_ENABLED
        ):
            return row
    name = (model_name or "").strip()
    if name:
        base = db.query(SupplierModel).filter(
            SupplierModel.supplier_id == supplier.id,
            SupplierModel.status == STATUS_ENABLED,
        )
        row = base.filter(SupplierModel.upstream_model_id == name).first()
        if row is None:
            row = base.filter(SupplierModel.model_key == name).first()
        if row is not None:
            return row
    if supplier.default_model_id:
        row = db.get(SupplierModel, supplier.default_model_id)
        if (
            row is not None
            and row.supplier_id == supplier.id
            and int(row.status or 0) == STATUS_ENABLED
        ):
            return row
    return None


# ---------- 模型导入（JSON / YAML / CSV） ----------


def _detect_format(content: str, declared: str | None) -> str:
    if declared:
        return declared
    stripped = content.lstrip()
    if stripped.startswith(("{", "[")):
        return "json"
    first_line = stripped.splitlines()[0] if stripped else ""
    if "," in first_line and "model_key" in first_line.lower():
        return "csv"
    if any(line.strip().endswith(":") for line in stripped.splitlines()[:5]):
        return "yaml"
    return "json"


def _split_tokens(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[;,|/\s]+", value) if part.strip()]
    return []


def _to_float(value: Any) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as e:
        raise AppError("IMPORT_ROW_INVALID", f"价格「{value}」不是合法数字。", 400) from e


def _normalize_import_row(raw: dict[str, Any]) -> dict[str, Any]:
    """把外部目录行（含 CSV 平铺列）归一化成 SupplierModelCreate 的字段。"""
    model_key = str(raw.get("model_key") or raw.get("id") or raw.get("name") or "").strip()
    upstream = str(
        raw.get("upstream_model_id") or raw.get("upstream") or raw.get("model") or model_key
    ).strip()
    modality = str(raw.get("modality") or guess_modality(upstream or model_key)).strip()
    protocol = str(raw.get("protocol") or protocol_for_modality(modality)).strip()
    row: dict[str, Any] = {
        "model_key": model_key,
        "upstream_model_id": upstream,
        "display_name": str(raw.get("display_name") or raw.get("label") or model_key).strip(),
        "modality": modality,
        "protocol": protocol,
    }
    for field in ("endpoint_path", "external_id"):
        if raw.get(field):
            row[field] = str(raw[field])[:200]
    for field in ("context_window", "max_output_tokens"):
        if raw.get(field) not in (None, ""):
            try:
                row[field] = int(raw[field])
            except (TypeError, ValueError) as e:
                raise AppError("IMPORT_ROW_INVALID", f"{field} 必须是整数。", 400) from e
    for field in ("input_modalities", "output_modalities"):
        if raw.get(field):
            row[field] = _split_tokens(raw[field])
    capabilities = raw.get("capabilities")
    if isinstance(capabilities, dict):
        row["capabilities"] = capabilities
    elif capabilities:
        row["capabilities"] = {token: True for token in _split_tokens(capabilities)}
    pricing = raw.get("pricing")
    if isinstance(pricing, dict):
        row["pricing"] = pricing
    else:
        prices: dict[str, Any] = {}
        for src_key, dst_key in (
            ("input_price", "input_per_1k"),
            ("input_per_1k", "input_per_1k"),
            ("output_price", "output_per_1k"),
            ("output_per_1k", "output_per_1k"),
        ):
            value = _to_float(raw.get(src_key))
            if value is not None:
                prices[dst_key] = value
        if raw.get("currency"):
            prices["currency"] = str(raw["currency"])[:8]
        if prices:
            row["pricing"] = prices
    if isinstance(raw.get("default_params"), dict):
        row["default_params"] = raw["default_params"]
    return row


def parse_import_content(content: str, fmt: str | None = None) -> list[dict[str, Any]]:
    """解析导入文本（JSON / YAML / CSV）为原始行列表；结构错直接给中文原因。"""
    text = (content or "").strip()
    if not text:
        raise AppError("IMPORT_EMPTY", "导入内容为空。", 400)
    resolved = _detect_format(text, fmt)
    try:
        if resolved == "json":
            payload = json.loads(text)
            rows = payload.get("models") if isinstance(payload, dict) else payload
            if not isinstance(rows, list):
                raise AppError(
                    "IMPORT_INVALID",
                    'JSON 导入需要模型数组（或 {"models": [...]}）。',
                    400,
                )
        elif resolved == "yaml":
            try:
                import yaml  # type: ignore[import-untyped]  # noqa: PLC0415 - 可选依赖，缺失时给出明确提示
            except ImportError as e:
                raise AppError(
                    "IMPORT_YAML_UNSUPPORTED",
                    "服务器未启用 YAML 解析（缺少 PyYAML），请改用 JSON 或 CSV 导入。",
                    http_status=400,
                ) from e
            payload = yaml.safe_load(text)
            rows = payload.get("models") if isinstance(payload, dict) else payload
            if not isinstance(rows, list):
                raise AppError(
                    "IMPORT_INVALID",
                    "YAML 导入需要模型数组（或 models: [...]）。",
                    400,
                )
        elif resolved == "csv":
            reader = csv.DictReader(io.StringIO(text))
            rows = [dict(item) for item in reader]
        else:  # pragma: no cover - 枚举外的值由上层契约拦下
            raise AppError("IMPORT_INVALID", f"不支持的导入格式：{resolved}。", 400)
    except AppError:
        raise
    except Exception as e:  # noqa: BLE001 - 解析失败统一转中文可读错误
        raise AppError("IMPORT_INVALID", f"导入内容解析失败：{e}", 400) from e
    out: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            raise AppError("IMPORT_INVALID", "导入内容包含非对象行。", 400)
        out.append(_normalize_import_row(item))
    return out


def _bundle_rows(
    bundle: SupplierImportBundle,
) -> list[tuple[dict[str, Any] | None, str, str | None]]:
    """返回 (行数据, 展示 key, 错误)；结构化与文本模式共用同一分类逻辑。"""
    from pydantic import ValidationError

    if bundle.content:
        raw_rows = parse_import_content(bundle.content, bundle.format)
        out: list[tuple[dict[str, Any] | None, str, str | None]] = []
        for index, raw in enumerate(raw_rows):
            display = str(raw.get("model_key") or f"第 {index + 1} 行")
            try:
                payload = SupplierModelCreate(**raw)
            except ValidationError as e:
                first = e.errors()[0]
                field = ".".join(str(part) for part in first.get("loc", ()))
                out.append((None, display, f"{field}: {first.get('msg', '校验失败')}"))
                continue
            out.append((payload.model_dump(), payload.model_key, None))
        return out
    return [(item.model_dump(), item.model_key, None) for item in bundle.models]


def _diff_model(row: SupplierModel, data: dict[str, Any]) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for field in _MODEL_WRITABLE_FIELDS:
        if field not in data:
            continue
        value = data[field]
        if field == "display_name" and not value:
            value = data.get("model_key")
        if getattr(row, field) != value:
            changes[field] = value
    return changes


def import_models(
    db: Session,
    yard_id: str | None,
    supplier_id: str | None,
    bundle: SupplierImportBundle,
    *,
    dry_run: bool = False,
    is_platform_admin: bool = False,
    source: str = "import",
) -> SupplierImportPreview | SupplierImportResult:
    """严格校验、逐行分类 create/update/skip/conflict/error 的幂等导入。

    - 匹配口径：``supplier_id + model_key``，其次 ``upstream_model_id``（改逻辑名不变更
      物理名也能幂等更新）；
    - ``dry_run=True`` 不写库；正式导入由调用方在**单事务**内提交；
    - ``api_key`` 只用于创建供应商，不进入任何返回值。
    """
    supplier: Supplier | None = None
    supplier_action = "existing"
    if supplier_id:
        supplier = _writable_model_supplier(
            db, yard_id, supplier_id, is_platform_admin=is_platform_admin
        )
    else:
        meta = bundle.supplier
        if not (meta and meta.name and meta.provider_type and meta.base_url and meta.api_key):
            raise AppError(
                "INVALID_REQUEST",
                "导入需要指定供应商，或在导入包中提供 name/provider_type/base_url/api_key "
                "以创建新供应商。",
                http_status=400,
            )
        supplier_action = "create"

    existing_rows: list[SupplierModel] = []
    if supplier is not None:
        existing_rows = (
            db.query(SupplierModel).filter(SupplierModel.supplier_id == supplier.id).all()
        )
    by_key = {row.model_key: row for row in existing_rows}
    by_upstream: dict[str, SupplierModel] = {}
    for existing in existing_rows:
        by_upstream.setdefault(existing.upstream_model_id, existing)

    items: list[SupplierImportPreviewItem] = []
    plans: list[tuple[str, dict[str, Any], SupplierModel | None]] = []
    seen_keys: set[str] = set()
    seen_upstream: set[str] = set()

    for raw, display_key, parse_error in _bundle_rows(bundle):
        if parse_error or raw is None:
            items.append(
                SupplierImportPreviewItem(
                    action="error", model_key=display_key, error=parse_error or "行数据无效"
                )
            )
            continue
        try:
            payload = SupplierModelCreate(**raw)
            data = _model_values(payload)
        except AppError as e:
            items.append(
                SupplierImportPreviewItem(action="error", model_key=display_key, error=e.message)
            )
            continue
        except Exception as e:  # noqa: BLE001 - 单行异常不拖垮整包
            items.append(
                SupplierImportPreviewItem(action="error", model_key=display_key, error=str(e)[:200])
            )
            continue

        key = data["model_key"]
        upstream = data["upstream_model_id"]
        if key in seen_keys or upstream in seen_upstream:
            items.append(
                SupplierImportPreviewItem(
                    action="conflict",
                    model_key=key,
                    error="导入包内 model_key / upstream_model_id 重复。",
                )
            )
            continue
        seen_keys.add(key)
        seen_upstream.add(upstream)

        row = by_key.get(key)
        if row is None:
            row = by_upstream.get(upstream)
            if row is not None and row.model_key != key:
                if key in by_key and by_key[key] is not row:
                    items.append(
                        SupplierImportPreviewItem(
                            action="conflict",
                            model_key=key,
                            error="model_key 已被该供应商的另一条模型占用。",
                        )
                    )
                    continue
        if row is None:
            plans.append(("create", data, None))
            items.append(SupplierImportPreviewItem(action="create", model_key=key))
            continue
        changes = _diff_model(row, data)
        if changes:
            plans.append(("update", changes, row))
            items.append(SupplierImportPreviewItem(action="update", model_key=key))
        else:
            items.append(SupplierImportPreviewItem(action="skip", model_key=key))

    summary = {"create": 0, "update": 0, "skip": 0, "conflict": 0, "error": 0}
    for item in items:
        summary[item.action] += 1

    if dry_run:
        return SupplierImportPreview(
            items=items,
            summary=summary,
            supplier_action=supplier_action,
            supplier_id=supplier.id if supplier is not None else None,
        )

    if supplier is None and not plans:
        raise AppError("IMPORT_EMPTY", "导入包没有可用的模型行，未创建供应商。", 400)

    if supplier is None:
        meta = bundle.supplier
        if meta is None:  # 上方已校验：无 supplier_id 时导入包必须带供应商信息
            raise AppError("INVALID_REQUEST", "导入包缺少供应商信息。", http_status=400)
        first_upstream = next(
            (plan[1]["upstream_model_id"] for plan in plans if plan[0] == "create"),
            "placeholder",
        )
        out = create_supplier(
            db,
            yard_id,
            SupplierCreate(
                name=(meta.name or "imported")[:40],
                provider_type=meta.provider_type,  # type: ignore[arg-type]
                base_url=meta.base_url or "",
                model_id=first_upstream[:100],
                api_key=meta.api_key or "",
                models=None,
                scope=meta.scope,
            ),
            is_platform_admin=is_platform_admin,
        )
        supplier = db.get(Supplier, out.id)
        if supplier is None:  # pragma: no cover - create_supplier 刚写入，理论不可达
            raise AppError("SUPPLIER_NOT_FOUND", "供应商创建失败。", http_status=500)

    created = updated = 0
    errors: list[str] = []
    for action, data, row in plans:
        if action == "create":
            db.add(
                SupplierModel(
                    supplier_id=supplier.id,
                    source=data.pop("source", source),
                    **data,
                )
            )
            created += 1
        elif row is not None:
            for field, value in data.items():
                setattr(row, field, value)
            updated += 1
    for item in items:
        if item.action == "error":
            errors.append(f"{item.model_key}: {item.error or '校验失败'}")
        elif item.action == "conflict":
            errors.append(f"{item.model_key}: {item.error or '冲突'}")
    if bundle.supplier and bundle.supplier.models:
        supplier.models = bundle.supplier.models.strip()[:2000] or None
    db.flush()
    _sync_supplier_defaults(db, supplier)
    invalidate_supplier_cache(supplier_id=supplier.id)
    return SupplierImportResult(
        supplier_id=supplier.id,
        created=created,
        updated=updated,
        skipped=summary["skip"],
        errors=errors,
    )


async def fetch_import_text(url: str) -> str:
    """按 URL 拉取导入内容：只允许 http/https、每跳 SSRF 校验、2MB 上限。"""
    from app.core.net_guard import check_egress_url

    current = (url or "").strip()
    parsed = urlparse(current)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise AppError("INVALID_REQUEST", "导入地址必须是 http(s) 完整 URL。", 400)
    for hop in range(IMPORT_MAX_REDIRECTS + 1):
        # URL 导入属于「别人给的地址」：不放行本机段（与供应商直连的 allow_local 区分）
        check_egress_url(current, context="supplier_import_url", allow_local=False)
        try:
            async with httpx.AsyncClient(
                timeout=IMPORT_TIMEOUT_SECONDS, follow_redirects=False
            ) as client:
                resp = await client.get(
                    current,
                    headers={"Accept": "application/json, text/yaml, text/csv, text/plain, */*"},
                )
        except httpx.TimeoutException as e:
            raise AppError("IMPORT_FETCH_FAILED", "拉取导入地址超时。", 502) from e
        except httpx.HTTPError as e:
            raise AppError("IMPORT_FETCH_FAILED", "无法连接导入地址。", 502) from e
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("location")
            if not location:
                raise AppError("IMPORT_FETCH_FAILED", "重定向缺少 Location。", 502)
            if hop >= IMPORT_MAX_REDIRECTS:
                raise AppError(
                    "IMPORT_TOO_MANY_REDIRECTS", "导入地址重定向超过 3 次，已中止。", 400
                )
            current = urljoin(current, location)
            continue
        if resp.status_code >= 400:
            raise AppError("IMPORT_FETCH_FAILED", f"导入地址返回 {resp.status_code}。", 502)
        if len(resp.content) > IMPORT_MAX_BYTES:
            raise AppError("IMPORT_TOO_LARGE", "导入内容超过 2MB 上限，已中止。", 413)
        return resp.text
    raise AppError(  # pragma: no cover - 循环内必 return 或 raise
        "IMPORT_TOO_MANY_REDIRECTS", "导入地址重定向超过 3 次，已中止。", 400
    )


# ---------- 单模型连通性测试（v3.33） ----------


async def _post_model_probe(
    url: str, api_key: str, payload: dict[str, Any], *, timeout: float
) -> httpx.Response:
    from app.core.net_guard import check_egress_url

    check_egress_url(url, context="supplier_model_test", allow_local=True)
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.post(url, json=payload, headers={"Authorization": f"Bearer {api_key}"})


def _status_message(status_code: int) -> str:
    if status_code in (401, 403):
        return "鉴权失败：请检查 API Key。"
    if status_code == 429:
        return "上游限流（429），请稍后重试。"
    return f"上游返回异常状态码 {status_code}。"


async def _run_model_test(
    supplier: Supplier, row: SupplierModel
) -> tuple[SupplierModelTestResult, dict[str, Any]]:
    """按模态选择真实可执行的探测；没有内置工具的模态如实返回未测试。"""
    from app.modules.llm.images import DEFAULT_SIZE, ImageRequest, build_image_provider

    started = time.perf_counter()
    modality = (row.modality or "text").lower()
    protocol = (row.protocol or "openai_chat").lower()
    api_key = decrypt_api_key(supplier.api_key_enc)
    base = (supplier.base_url or "").rstrip("/")
    detail: dict[str, Any] = {"model_key": row.model_key, "modality": modality}
    try:
        if modality in ("image",) or protocol in ("openai_images", "dashscope_image"):
            provider = build_image_provider(supplier, model_row=row)
            batch = await provider.generate(ImageRequest(prompt="ping", size=DEFAULT_SIZE, n=1))
            latency = int((time.perf_counter() - started) * 1000)
            detail.update({"model": batch.model, "images": len(batch.assets)})
            return (
                SupplierModelTestResult(
                    ok=True,
                    latency_ms=latency,
                    message="图像生成连通（1 张最小请求）。",
                    detail=detail,
                ),
                detail,
            )
        if modality in ("embedding", "rerank"):
            path = row.endpoint_path or ("/embeddings" if modality == "embedding" else "/rerank")
            resp = await _post_model_probe(
                f"{base}{path}",
                api_key,
                {"model": row.upstream_model_id, "input": "ping"},
                timeout=20,
            )
            latency = int((time.perf_counter() - started) * 1000)
            if resp.status_code != 200:
                return (
                    SupplierModelTestResult(
                        ok=False,
                        latency_ms=latency,
                        message=_status_message(resp.status_code),
                        detail={**detail, "status": resp.status_code},
                    ),
                    detail,
                )
            dimension = None
            try:
                data = resp.json()
                entries = data.get("data") if isinstance(data, dict) else None
                if isinstance(entries, list) and entries and isinstance(entries[0], dict):
                    vector = entries[0].get("embedding")
                    if isinstance(vector, list):
                        dimension = len(vector)
            except ValueError:
                dimension = None
            return (
                SupplierModelTestResult(
                    ok=True,
                    latency_ms=latency,
                    message=(
                        f"向量接口连通，维度 {dimension}。"
                        if dimension
                        else "向量接口连通（未返回维度）。"
                    ),
                    detail={**detail, "dimension": dimension},
                ),
                detail,
            )
        if modality in ("asr", "tts", "audio"):
            return (
                SupplierModelTestResult(
                    ok=False,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    message="未提供音频样例，未测试",
                    detail=detail,
                ),
                detail,
            )
        if modality == "video" or protocol == "openai_video":
            return (
                SupplierModelTestResult(
                    ok=False,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    message="视频通道已登记，暂无内置调用工具",
                    detail=detail,
                ),
                detail,
            )
        if modality in ("text", "multimodal") or protocol in ("openai_chat", "custom"):
            path = row.endpoint_path or "/chat/completions"
            resp = await _post_model_probe(
                f"{base}{path}",
                api_key,
                {
                    "model": row.upstream_model_id,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                },
                timeout=20,
            )
            latency = int((time.perf_counter() - started) * 1000)
            ok = resp.status_code == 200
            return (
                SupplierModelTestResult(
                    ok=ok,
                    latency_ms=latency,
                    message=(
                        "对话连通（max_tokens=1）。" if ok else _status_message(resp.status_code)
                    ),
                    detail={**detail, "status": resp.status_code},
                ),
                detail,
            )
        return (
            SupplierModelTestResult(
                ok=False,
                latency_ms=int((time.perf_counter() - started) * 1000),
                message=f"模态 {modality} 暂无内置测试工具，已登记通道。",
                detail=detail,
            ),
            detail,
        )
    except AppError as e:
        return (
            SupplierModelTestResult(
                ok=False,
                latency_ms=int((time.perf_counter() - started) * 1000),
                message=e.message,
                detail=detail,
            ),
            detail,
        )
    except (httpx.TimeoutException, httpx.HTTPError):
        return (
            SupplierModelTestResult(
                ok=False,
                latency_ms=int((time.perf_counter() - started) * 1000),
                message="无法连接上游或请求超时。",
                detail=detail,
            ),
            detail,
        )
    finally:
        del api_key


async def test_supplier_model(
    db: Session,
    yard_id: str | None,
    supplier_id: str,
    model_id: str,
    *,
    is_platform_admin: bool = False,
) -> SupplierModelTestResult:
    """单模型测试并把结果写回模型行（last_test_at/test_ok/latency_ms/last_error）。"""
    supplier = _writable_model_supplier(
        db, yard_id, supplier_id, is_platform_admin=is_platform_admin
    )
    row = get_supplier_model(db, supplier.id, model_id)
    result, _detail = await _run_model_test(supplier, row)
    row.last_test_at = datetime.now(UTC)
    row.test_ok = result.ok
    row.latency_ms = result.latency_ms
    row.last_error = None if result.ok else (result.message or "")[:2000]
    db.flush()
    invalidate_supplier_cache(supplier_id=supplier.id)
    return result


async def test_supplier_models_batch(
    db: Session,
    yard_id: str | None,
    supplier_id: str,
    model_ids: list[str] | None = None,
    *,
    is_platform_admin: bool = False,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """批量测试：默认按启用模型顺序，最多 20 个（避免一次打满上游限流）。"""
    supplier = _writable_model_supplier(
        db, yard_id, supplier_id, is_platform_admin=is_platform_admin
    )
    if model_ids:
        rows = [get_supplier_model(db, supplier.id, mid) for mid in model_ids[:limit]]
    else:
        rows = (
            db.query(SupplierModel)
            .filter(
                SupplierModel.supplier_id == supplier.id,
                SupplierModel.status == STATUS_ENABLED,
            )
            .order_by(SupplierModel.created_at.asc())
            .limit(limit)
            .all()
        )
    results: list[dict[str, Any]] = []
    for row in rows:
        result = await test_supplier_model(
            db, yard_id, supplier_id, row.id, is_platform_admin=is_platform_admin
        )
        results.append({"model_id": row.id, **result.model_dump()})
    return results


def get_provider_templates() -> list[ProviderTemplate]:
    return [
        ProviderTemplate(
            provider_type="deepseek",
            label="DeepSeek",
            base_url="https://api.deepseek.com",
            model_id="deepseek-chat",
            note="OpenAI 兼容接口",
        ),
        ProviderTemplate(
            provider_type="qwen",
            label="通义千问（Qwen）",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model_id="qwen-max",
            note="阿里云百炼 OpenAI 兼容模式",
        ),
        ProviderTemplate(
            provider_type="moonshot",
            label="Moonshot（Kimi）",
            base_url="https://api.moonshot.cn/v1",
            model_id="moonshot-v1-8k",
            note="OpenAI 兼容接口",
        ),
        ProviderTemplate(
            provider_type="openai",
            label="OpenAI",
            base_url="https://api.openai.com/v1",
            model_id="gpt-4o-mini",
            note="官方接口",
        ),
        ProviderTemplate(
            provider_type="openai_compatible",
            label="自定义（OpenAI 兼容）",
            base_url="",
            model_id="",
            note="任意兼容 POST {base_url}/chat/completions 的服务",
        ),
        # ---- v3.26 视觉域：图像/视频供应商与对话供应商分开配置 ----
        ProviderTemplate(
            provider_type="openai_images",
            label="图像生成（OpenAI 兼容）",
            base_url="https://api.openai.com/v1",
            model_id="gpt-image-1",
            note="兼容 POST {base_url}/images/generations；海报/配图走这里，不占用对话模型",
        ),
        ProviderTemplate(
            provider_type="dashscope_image",
            label="图像生成（阿里云百炼）",
            base_url="https://dashscope.aliyuncs.com",
            model_id="wanx2.1-t2i-turbo",
            note="百炼原生异步文生图接口（提交任务后轮询取图），base_url 不要带 /compatible-mode",
        ),
        ProviderTemplate(
            provider_type="openai_video",
            label="视频生成（通道预留）",
            base_url="",
            model_id="",
            note="协议位已登记，当前无内置调用工具；配置后可用于后续视频技能",
        ),
    ]
