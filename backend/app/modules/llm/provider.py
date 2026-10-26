"""模型 Provider 协议与工厂（含 DB 驱动的供应商热更新）。

解析顺序：绑定供应商（助手模型绑定，需通过可见+启用双检）→
小院 active supplier → 全局 active（priority 最高）→ 回退 settings.llm_provider。
供应商 Provider 实例按 (yard_id, supplier_id) 缓存；供应商 CRUD/启停用/active
切换时失效对应缓存，配置变更无需重启。

get_provider() 签名保持不变：小院上下文由调用方经 provider_resolution()
注入（contextvar），零参调用与旧行为兼容。
"""

import contextvars
import threading
from typing import Any, Protocol

from app.core.config import settings
from app.core.errors import AppError
from app.modules.llm.schemas import LLMRequest, LLMResponse


class LLMProvider(Protocol):
    async def complete(self, request: LLMRequest) -> LLMResponse: ...


# (yard_id, supplier_id) -> Provider 实例
_provider_cache: dict[tuple[str, str], LLMProvider] = {}
_cache_lock = threading.Lock()

# 调用方注入的解析上下文：(yard_id, db_session, 绑定供应商 id 或 None)
# db_session 为 SQLAlchemy Session；用 Any 避免与 provider 循环导入
_resolution_ctx: contextvars.ContextVar[tuple[str | None, Any | None, str | None]] = (
    contextvars.ContextVar("llm_provider_resolution", default=(None, None, None))
)


class provider_resolution:
    """上下文管理器：为 get_provider() 注入小院与 DB 会话（可带绑定供应商）。"""

    def __init__(self, yard_id: str | None, db: Any, supplier_id: str | None = None) -> None:
        self._token: contextvars.Token | None = None
        self._ctx = (yard_id, db, supplier_id)

    def __enter__(self):
        self._token = _resolution_ctx.set(self._ctx)
        return self

    def __exit__(self, *exc_info):
        if self._token is not None:
            _resolution_ctx.reset(self._token)
        return False


def _legacy_provider() -> LLMProvider:
    """原 settings 驱动逻辑：fake | openai_compatible。"""
    if settings.llm_provider == "fake":
        from app.modules.llm.providers_fake import FakeProvider

        return FakeProvider()
    if settings.llm_provider == "openai_compatible":
        from app.modules.llm.providers_openai import OpenAICompatibleProvider

        # 缺 base_url/key 时构造即抛 LLM_NOT_CONFIGURED(503)
        return OpenAICompatibleProvider()
    raise AppError(
        "LLM_NOT_CONFIGURED",
        f"不支持的模型提供方：{settings.llm_provider}，请检查配置。",
        http_status=503,
    )


def _cached_supplier_provider(yard_id: str, supplier) -> LLMProvider:
    from app.modules.llm import supplier_service

    cache_key = (yard_id, supplier.id)
    with _cache_lock:
        inst = _provider_cache.get(cache_key)
    if inst is None:
        inst = supplier_service.build_provider_from_supplier(supplier)
        with _cache_lock:
            _provider_cache[cache_key] = inst
    return inst


def _bound_supplier(yard_id: str, db: Any, supplier_id: str):
    """绑定供应商权限双检：可见域 = 全局共享 + 本小院（他院/已删天然不可见），
    且必须处于启用态。任何一条不满足返回 None，由调用方回退既有解析链——
    供应商问题绝不向聊天用户抛错。
    """
    from app.modules.llm import supplier_service
    from app.modules.llm.supplier_schemas import STATUS_ENABLED

    try:
        row = supplier_service.get_supplier(db, yard_id, supplier_id)
    except AppError:
        return None
    return row if row.status == STATUS_ENABLED else None


def get_provider() -> LLMProvider:
    """按 DB 供应商配置返回 Provider；无可用供应商时回退 settings。

    解析优先级：绑定供应商（模型绑定，调用方经 provider_resolution 注入）
    → 小院 active supplier → 全局 active（priority 最高）→ settings 兜底。
    """
    yard_id, db, supplier_id = _resolution_ctx.get()
    if yard_id is not None and db is not None:
        from app.modules.llm import supplier_service

        if supplier_id:
            bound = _bound_supplier(yard_id, db, supplier_id)
            if bound is not None:
                return _cached_supplier_provider(yard_id, bound)
        supplier = supplier_service.resolve_active_supplier(db, yard_id)
        if supplier is not None:
            return _cached_supplier_provider(yard_id, supplier)
    return _legacy_provider()


def invalidate_supplier_cache(yard_id: str | None = None, supplier_id: str | None = None) -> None:
    """供应商变更后失效缓存；不传参则清空全部。"""
    with _cache_lock:
        if yard_id is None and supplier_id is None:
            _provider_cache.clear()
            return
        for key in [
            k
            for k in _provider_cache
            if (yard_id is None or k[0] == yard_id) and (supplier_id is None or k[1] == supplier_id)
        ]:
            _provider_cache.pop(key, None)
