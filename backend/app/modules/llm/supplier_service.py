"""模型供应商服务：CRUD、连通性探测、租户 active 解析、Provider 构建。

- API Key 明文只在内存中短暂使用（加密落库 / 探测时），绝不打日志、绝不返回。
- 供应商的增删改、启用/禁用、active 切换都会失效 Provider 缓存，配置即时生效、无需重启。
"""

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

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
from app.modules.llm.supplier_models import Supplier, TenantActiveSupplier
from app.modules.llm.supplier_schemas import (
    STATUS_DISABLED,
    STATUS_ENABLED,
    ProviderTemplate,
    SupplierCreate,
    SupplierOut,
    SupplierTestResult,
    SupplierUpdate,
)

if TYPE_CHECKING:
    from app.modules.llm.providers_openai import OpenAICompatibleProvider

log = get_logger(__name__)

PROBE_TIMEOUT_SECONDS = 10
AUTO_DISABLE_FAILURES = 3  # 连续失败 3 次自动禁用


# ---------- 查询 ----------


def _visible_query(db: Session, tenant_id: str | None):
    """租户可见供应商：全局共享 + 本租户。"""
    if tenant_id:
        return db.query(Supplier).filter(
            or_(Supplier.tenant_id.is_(None), Supplier.tenant_id == tenant_id)
        )
    return db.query(Supplier).filter(Supplier.tenant_id.is_(None))


def get_supplier(db: Session, tenant_id: str | None, supplier_id: str) -> Supplier:
    row = _visible_query(db, tenant_id).filter(Supplier.id == supplier_id).first()
    if not row:
        raise AppError("SUPPLIER_NOT_FOUND", "供应商不存在。", http_status=404)
    return row


def _writable_supplier(
    db: Session, tenant_id: str | None, supplier_id: str, *, is_platform_admin: bool
) -> Supplier:
    """写操作专用「角色+归属」双校验（ADR-0005）：

    - 先走可见性查询（全局 + 本租户），跨租户行天然不可见；
    - 非平台管理员不得改写平台全局行（tenant_id 为 NULL）。
    越权与不存在统一 404（平台惯例：不向无权方泄露资源存在性）。
    """
    row = get_supplier(db, tenant_id, supplier_id)
    if not is_platform_admin and row.tenant_id is None:
        raise AppError("SUPPLIER_NOT_FOUND", "供应商不存在。", http_status=404)
    return row


def get_active_supplier_id(db: Session, tenant_id: str) -> str | None:
    row = db.query(TenantActiveSupplier).filter_by(tenant_id=tenant_id).first()
    return row.supplier_id if row else None


def resolve_active_supplier(db: Session, tenant_id: str) -> Supplier | None:
    """热更新解析：租户 active supplier → 全局 active（priority 最高）→ None（回退 settings）。"""
    active_id = get_active_supplier_id(db, tenant_id)
    if active_id:
        row = db.query(Supplier).filter(Supplier.id == active_id).first()
        if (
            row
            and row.status == STATUS_ENABLED
            and (row.tenant_id is None or row.tenant_id == tenant_id)
        ):
            return row
        # active 指向的供应商不可用：继续走全局兜底
    return (
        db.query(Supplier)
        .filter(Supplier.tenant_id.is_(None), Supplier.status == STATUS_ENABLED)
        .order_by(Supplier.priority.desc(), Supplier.created_at.asc())
        .first()
    )


def supplier_to_out(
    db: Session, row: Supplier, tenant_id: str, *, is_platform_admin: bool = False
) -> SupplierOut:
    masked = mask_api_key(decrypt_api_key(row.api_key_enc))
    return SupplierOut(
        id=row.id,
        tenant_id=row.tenant_id,
        name=row.name,
        provider_type=row.provider_type,
        base_url=row.base_url,
        model_id=row.model_id,
        api_key_masked=masked,
        models=row.models,
        priority=row.priority,
        status=row.status,
        is_active=get_active_supplier_id(db, tenant_id) == row.id if tenant_id else False,
        # 租户管理员视角：全局共享行只读；平台管理员不受限
        readonly=(not is_platform_admin) and row.tenant_id is None,
        consecutive_failures=row.consecutive_failures or 0,
        test_time=row.test_time.isoformat() if row.test_time else None,
        response_time_ms=row.response_time_ms,
        last_error=row.last_error,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


# ---------- CRUD ----------


def list_suppliers(
    db: Session, tenant_id: str | None, *, is_platform_admin: bool = False
) -> list[SupplierOut]:
    rows = (
        _visible_query(db, tenant_id)
        .order_by(Supplier.priority.desc(), Supplier.created_at.asc())
        .all()
    )
    return [
        supplier_to_out(db, r, tenant_id or "", is_platform_admin=is_platform_admin) for r in rows
    ]


def create_supplier(
    db: Session,
    tenant_id: str | None,
    payload: SupplierCreate,
    *,
    is_platform_admin: bool,
) -> SupplierOut:
    scope_tenant_id: str | None
    if payload.scope == "global":
        if not is_platform_admin:
            raise AppError("FORBIDDEN", "只有平台管理员可以创建全局共享供应商。", http_status=403)
        scope_tenant_id = None
    else:
        if not tenant_id:
            raise AppError("TENANT_REQUIRED", "创建租户供应商需要租户上下文。", http_status=400)
        scope_tenant_id = tenant_id
    row = Supplier(
        tenant_id=scope_tenant_id,
        name=payload.name.strip(),
        provider_type=payload.provider_type,
        base_url=payload.base_url.strip().rstrip("/"),
        model_id=payload.model_id.strip(),
        api_key_enc=encrypt_api_key(payload.api_key),
        models=(payload.models.strip() or None) if payload.models else None,
        priority=payload.priority,
        status=STATUS_ENABLED,
    )
    db.add(row)
    db.flush()
    invalidate_supplier_cache()
    return supplier_to_out(db, row, tenant_id or "")


def update_supplier(
    db: Session,
    tenant_id: str | None,
    supplier_id: str,
    payload: SupplierUpdate,
    *,
    is_platform_admin: bool = False,
) -> SupplierOut:
    row = _writable_supplier(db, tenant_id, supplier_id, is_platform_admin=is_platform_admin)
    data = payload.model_dump(exclude_unset=True)
    api_key = data.pop("api_key", None)
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
    db.flush()
    invalidate_supplier_cache(supplier_id=row.id)
    return supplier_to_out(db, row, tenant_id or "")


def delete_supplier(
    db: Session, tenant_id: str | None, supplier_id: str, *, is_platform_admin: bool = False
) -> None:
    row = _writable_supplier(db, tenant_id, supplier_id, is_platform_admin=is_platform_admin)
    db.query(TenantActiveSupplier).filter(TenantActiveSupplier.supplier_id == row.id).delete()
    db.delete(row)
    db.flush()
    invalidate_supplier_cache(supplier_id=row.id)


def set_supplier_status(
    db: Session,
    tenant_id: str | None,
    supplier_id: str,
    status: int,
    *,
    is_platform_admin: bool = False,
) -> SupplierOut:
    row = _writable_supplier(db, tenant_id, supplier_id, is_platform_admin=is_platform_admin)
    row.status = status
    if status == STATUS_ENABLED:
        row.consecutive_failures = 0
    db.flush()
    invalidate_supplier_cache(supplier_id=row.id)
    return supplier_to_out(db, row, tenant_id or "")


def set_active_supplier(
    db: Session, tenant_id: str, supplier_id: str, *, is_platform_admin: bool = False
) -> SupplierOut:
    # 刻意用可见性查询（非 writable）：租户可选用平台全局行作为本租户生效供应商
    row = get_supplier(db, tenant_id, supplier_id)
    if row.status != STATUS_ENABLED:
        raise AppError("SUPPLIER_DISABLED", "该供应商已禁用，请先启用。", http_status=400)
    active = db.query(TenantActiveSupplier).filter_by(tenant_id=tenant_id).first()
    if active:
        active.supplier_id = row.id
    else:
        db.add(TenantActiveSupplier(tenant_id=tenant_id, supplier_id=row.id))
    db.flush()
    invalidate_supplier_cache()  # active 映射变更：整体失效，保证解析即时生效
    out = supplier_to_out(db, row, tenant_id, is_platform_admin=is_platform_admin)
    out.is_active = True
    return out


# ---------- 连通性探测 ----------


async def probe_upstream(
    base_url: str, api_key: str, model_id: str, timeout: int = PROBE_TIMEOUT_SECONDS
) -> SupplierTestResult:
    """先 GET {base}/models；失败则发一条最小 chat('hi')。key 只在内存使用。"""
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
        check_egress_url(base_url, context="supplier_probe")
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
    db: Session, tenant_id: str | None, supplier_id: str, *, is_platform_admin: bool = False
) -> tuple[SupplierOut, SupplierTestResult]:
    """对已存供应商探测，结果写回 test_time/response_time_ms/last_error；
    连续失败 3 次自动禁用。探测会改写供应商行，故与编辑同权：
    租户管理员对平台全局行不可探测（404）。"""
    row = _writable_supplier(db, tenant_id, supplier_id, is_platform_admin=is_platform_admin)
    api_key = decrypt_api_key(row.api_key_enc)
    try:
        result = await probe_upstream(row.base_url, api_key, row.model_id)
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
    return supplier_to_out(db, row, tenant_id or ""), result


# ---------- Provider 构建与模板 ----------


def build_provider_from_supplier(row: "Supplier") -> "OpenAICompatibleProvider":
    """由供应商行构建 OpenAI 兼容 Provider（各类型统一走兼容协议）。"""
    from app.modules.llm.providers_openai import OpenAICompatibleProvider

    api_key = decrypt_api_key(row.api_key_enc)
    try:
        provider = OpenAICompatibleProvider(
            base_url=row.base_url, api_key=api_key, model=row.model_id
        )
    finally:
        del api_key
    # 调用记录中的 provider 标签（llm_calls.provider 列最长 50）
    provider.provider_name = f"supplier:{row.name}"[:50]
    provider.supplier_id = row.id
    return provider


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
    ]
