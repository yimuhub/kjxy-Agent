"""模型供应商服务：CRUD、连通性探测、小院 active 解析、Provider 构建。

- API Key 明文只在内存中短暂使用（加密落库 / 探测时），绝不打日志、绝不返回。
- 供应商的增删改、启用/禁用、active 切换都会失效 Provider 缓存，配置即时生效、无需重启。
"""

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from urllib.parse import urlparse

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
from app.modules.llm.supplier_models import Supplier, YardActiveSupplier
from app.modules.llm.supplier_schemas import (
    STATUS_DISABLED,
    STATUS_ENABLED,
    ProbeModelItem,
    ProbeModelsResult,
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
AUTO_DISABLE_FAILURES = 3  # 连续探测失败 3 次自动禁用
#: 运行时（真实业务调用）连续失败 5 次自动降级：与探测口径分开，
#: 避免一次上游抖动就把供应商踢出候选链（探测是主动的，运行时是被动的）。
RUNTIME_DEGRADE_FAILURES = 5

#: §A 模型列表探测：单次超时 8 秒、响应体硬上限 1MB（与 MCP 客户端同口径）
MODELS_PROBE_TIMEOUT_SECONDS = 8
MODELS_PROBE_MAX_BYTES = 1024 * 1024
#: 返回给前端的模型条目上限（诚实提示：上游再多也先截断）
MODELS_PROBE_MAX_ITEMS = 500
#: 无统一模型列表端点的类型（§A：直接 unsupported，不发外呼）
NO_MODEL_LIST_PROVIDERS = {"anthropic", "gemini"}

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
    return row


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
        # 小院管理员视角：全局共享行只读；平台管理员不受限
        readonly=(not is_platform_admin) and row.yard_id is None,
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
            models.append(ProbeModelItem(id=mid, label=mid))
        elif isinstance(entry, str) and entry:
            models.append(ProbeModelItem(id=entry, label=entry))
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
    row: "Supplier", *, model_override: str | None = None
) -> "OpenAICompatibleProvider":
    """由供应商行构建 OpenAI 兼容 Provider（各类型统一走兼容协议）。

    ``model_override`` 来自模型绑定（v3.25）：绑定行指定物理模型时覆盖供应商默认值，
    白名单校验交给上游（绑错模型会以真实报错暴露，不做本地猜测）。
    """
    from app.modules.llm.providers_openai import OpenAICompatibleProvider

    api_key = decrypt_api_key(row.api_key_enc)
    try:
        provider = OpenAICompatibleProvider(
            base_url=row.base_url,
            api_key=api_key,
            model=(model_override or "").strip() or row.model_id,
        )
    finally:
        del api_key
    # 调用记录中的 provider 标签（llm_calls.provider 列最长 50）
    provider.provider_name = f"supplier:{row.name}"[:50]
    provider.supplier_id = row.id
    # function-calling 能力位由供应商行声明（默认 false → 工具循环关闭，零回归）
    provider.supports_tools = bool(getattr(row, "supports_tools", False))
    # v3.32 金额额度：供应商自带单价（元/千 token），未填则沿用 Provider 默认估算
    provider.price_in_per_1k = getattr(row, "price_in_per_1k", None)
    provider.price_out_per_1k = getattr(row, "price_out_per_1k", None)
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
