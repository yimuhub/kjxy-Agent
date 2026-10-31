"""模型 Provider 协议与工厂（含 DB 驱动的供应商热更新）。

解析顺序（v3.25 起支持三层绑定与兜底链）：助手指定供应商 → 按智能体绑定 →
按能力绑定 → 小院/平台默认绑定 → 小院 active supplier → 全局 active（priority 最高）
→ 回退 settings.llm_provider。命中档位的供应商不可用（跨院/停用/运行时降级）自动落到下一档。

供应商 Provider 实例按 (yard_id, supplier_id, 模型覆盖) 缓存；供应商 CRUD/启停用、
active 切换或绑定变更时失效对应缓存，配置变更无需重启。

get_provider() 签名保持不变：小院上下文由调用方经 provider_resolution()
注入（contextvar），零参调用与旧行为兼容。需要兜底重试的调用方用
get_provider_candidates() 取整条候选链。
"""

import contextvars
import threading
from typing import Any, Protocol

from app.core.config import settings
from app.core.errors import AppError
from app.modules.llm.schemas import LLMRequest, LLMResponse


class LLMProvider(Protocol):
    async def complete(self, request: LLMRequest) -> LLMResponse: ...


# (yard_id, supplier_id, 模型覆盖, 模型行 id) -> Provider 实例
# v3.33：缓存键带上模型行/实际模型名，避免同一供应商的多模型互相串缓存
_provider_cache: dict[tuple[str, str, str, str], LLMProvider] = {}
_cache_lock = threading.Lock()

# 调用方注入的解析上下文：(yard_id, db_session, 绑定供应商 id, 能力码, 助手编码)
# db_session 为 SQLAlchemy Session；用 Any 避免与 provider 循环导入
_resolution_ctx: contextvars.ContextVar[
    tuple[str | None, Any | None, str | None, str | None, str | None]
] = (  # noqa: E501
    contextvars.ContextVar("llm_provider_resolution", default=(None, None, None, None, None))
)


class provider_resolution:
    """上下文管理器：为 get_provider() 注入小院与 DB 会话（可带绑定供应商/能力/助手）。"""

    def __init__(
        self,
        yard_id: str | None,
        db: Any,
        supplier_id: str | None = None,
        *,
        purpose: str | None = None,
        agent_code: str | None = None,
    ) -> None:
        self._token: contextvars.Token | None = None
        self._ctx = (yard_id, db, supplier_id, purpose, agent_code)

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


def _cached_supplier_provider(
    yard_id: str,
    supplier,
    model_override: str | None = None,
    model_row=None,
) -> LLMProvider:
    from app.modules.llm import supplier_service

    model_key = getattr(model_row, "id", "") or ""
    cache_key = (yard_id, supplier.id, (model_override or "").strip(), model_key)
    with _cache_lock:
        inst = _provider_cache.get(cache_key)
    if inst is None:
        build_kwargs: dict[str, object] = {"model_override": cache_key[2] or None}
        if model_row is not None:
            build_kwargs["model_row"] = model_row
        inst = supplier_service.build_provider_from_supplier(supplier, **build_kwargs)
        with _cache_lock:
            _provider_cache[cache_key] = inst
    return inst


def get_provider_candidates() -> list[tuple[LLMProvider, str]]:
    """按解析顺序返回 (Provider, 命中层级) 候选链；无 DB 供应商时退化为 settings 兜底。

    调用方（``complete_with_logging``）据此在主用失败时逐个兜底；
    不支持 DB 上下文（未注入 ``provider_resolution``）时返回单元素 settings 链。
    """
    yard_id, db, supplier_id, purpose, agent_code = _resolution_ctx.get()
    if yard_id is not None and db is not None:
        from app.modules.llm import binding_service
        from app.modules.llm.binding_service import LEVEL_SETTINGS

        chain = binding_service.resolve_chain(
            db,
            yard_id,
            purpose=purpose,
            agent_code=agent_code,
            bound_supplier_id=supplier_id,
        )
        if chain:
            return [
                (
                    _cached_supplier_provider(
                        yard_id, c.supplier, c.model_name, c.supplier_model
                    ),
                    c.level,
                )
                for c in chain
            ]
        return [(_legacy_provider(), LEVEL_SETTINGS)]
    from app.modules.llm.binding_service import LEVEL_SETTINGS

    return [(_legacy_provider(), LEVEL_SETTINGS)]


def get_provider() -> LLMProvider:
    """按 DB 供应商配置返回首个可用 Provider；无可用供应商时回退 settings。

    解析优先级见模块文档；需要兜底重试的调用方应改用 get_provider_candidates()。
    """
    return get_provider_candidates()[0][0]


def invalidate_supplier_cache(yard_id: str | None = None, supplier_id: str | None = None) -> None:
    """供应商或绑定变更后失效缓存；不传参则清空全部。"""
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
