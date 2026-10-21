"""LLM 调用服务：统一走 Provider，并在 llm_calls 表中记录调用（含失败）。"""

import hashlib
import time
import uuid

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.llm.models import LLMCall, LLMTrace
from app.modules.llm.provider import get_provider, provider_resolution
from app.modules.llm.schemas import LLMRequest, LLMResponse, LLMStatusOut
from app.modules.tenants.quota import check_and_consume

log = get_logger(__name__)

# provider 选择理由只打一次：进程内首次解析成功后 log.info，避免每次调用刷屏
_choice_logged = False


def _provider_choice_reason(provider) -> str:
    """为什么选中当前 provider 的人类可读理由（只含配置态描述，绝不含密钥值）。"""
    if getattr(provider, "provider_name", None):
        return "DB 供应商配置生效（租户/全局 active supplier）"
    if settings.llm_provider == "fake":
        return "settings.llm_provider=fake（开发/CI 兜底，不出网）"
    if settings.llm_provider == "openai_compatible":
        return "环境变量 LLM_BASE_URL/LLM_API_KEY 已配置"
    return f"settings.llm_provider={settings.llm_provider}"


def _log_provider_choice_once(provider) -> None:
    global _choice_logged
    if _choice_logged:
        return
    _choice_logged = True
    log.info(
        "选择了模型提供方 %s ，因为 %s",
        _provider_label(provider),
        _provider_choice_reason(provider),
    )


def _prompt_sha(request: LLMRequest) -> str:
    """只存 prompt 的 sha256 摘要，不存原文。"""
    text = "\n".join(f"{m.role}:{m.content}" for m in request.messages)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _prompt_digest(request: LLMRequest) -> str:
    """观测轨迹的提示词摘要（W4 隐私口径）：``sha256 前 12 位|前 200 字``。

    全文绝不入库——200 字之外的内容被截断丢弃，sha 前缀仍可跨行对账
    （同一 prompt 的 digest 稳定可比）。
    """
    text = "\n".join(f"{m.role}:{m.content or ''}" for m in request.messages)
    sha12 = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return f"{sha12}|{text[:200]}"


def _estimate_cost(provider, input_tokens: int | None, output_tokens: int | None) -> float:
    """(in+out)/1000 * 单价；单价取 provider 常量（fake 为 0）。"""
    unit_price = float(getattr(provider, "PRICE_PER_1K_USD", 0.0) or 0.0)
    total = (input_tokens or 0) + (output_tokens or 0)
    return round(total / 1000 * unit_price, 6)


def _provider_label(provider) -> str:
    """调用记录中的提供方标签：供应商模式用 supplier:<name>，否则回退 settings。"""
    return getattr(provider, "provider_name", settings.llm_provider) or settings.llm_provider


def _write_call(
    db: Session,
    *,
    provider,
    request: LLMRequest,
    resp: LLMResponse | None,
    status: str,
    error_category: str | None,
    latency_ms: int,
    request_id: str,
    tenant_id: str,
    error_text: str | None = None,
) -> None:
    from app.core.context import metrics

    metrics.observe_llm(status=status, provider=_provider_label(provider))
    db.add(
        LLMCall(
            tenant_id=tenant_id,
            task_id=request.metadata.task_id,
            provider=_provider_label(provider),
            model=(
                resp.model
                if resp
                else (request.model or getattr(provider, "model", "") or "unknown")
            ),
            input_tokens=resp.input_tokens if resp else None,
            output_tokens=resp.output_tokens if resp else None,
            estimated_cost=_estimate_cost(
                provider, resp.input_tokens if resp else None, resp.output_tokens if resp else None
            ),
            status=status,
            error_category=error_category,
            latency_ms=latency_ms,
            request_id=request_id,
            prompt_sha=_prompt_sha(request),
        )
    )
    # ⚑ observe_llm 指标点旁挂观测轨迹（W4）：fake provider 也落；purpose 由调用方
    # 经 LLMMetadata 透传，拿不到按 "platform"；token 取不到就 null，绝不编造。
    db.add(
        LLMTrace(
            tenant_id=tenant_id,
            provider_id=getattr(provider, "supplier_id", None),
            model=(
                resp.model
                if resp
                else (request.model or getattr(provider, "model", "") or "unknown")
            ),
            purpose=request.metadata.purpose or "platform",
            agent_ref=request.metadata.agent_ref,
            run_id=request.metadata.task_id,
            status="ok" if status == "success" else "error",
            input_tokens=resp.input_tokens if resp else None,
            output_tokens=resp.output_tokens if resp else None,
            latency_ms=latency_ms,
            prompt_digest=_prompt_digest(request),
            error_text=error_text[:300] if error_text else None,
        )
    )
    db.flush()


async def complete_with_logging(db: Session, request: LLMRequest) -> LLMResponse:
    """调用模型并记录 llm_calls 行；成功/失败都记，失败时原样抛出 AppError。"""
    tenant_id = request.metadata.tenant_id
    if not tenant_id:
        raise AppError("INVALID_REQUEST", "缺少租户信息，无法记录模型调用。", http_status=400)
    # 配额拦截：调用模型前先扣减 llm_calls，超限直接 429 拒绝，不消耗上游资源
    check_and_consume(db, tenant_id, "llm_calls")
    request_id = request.metadata.request_id or f"req_{uuid.uuid4().hex[:16]}"

    # 供应商热更新：注入租户上下文，get_provider() 按 DB 配置解析
    with provider_resolution(tenant_id, db):
        provider = get_provider()
    _log_provider_choice_once(provider)
    started = time.perf_counter()
    try:
        resp = await provider.complete(request)
    except AppError as e:
        latency_ms = int((time.perf_counter() - started) * 1000)
        category = e.details.get("category") if e.code == "LLM_ERROR" else None
        _write_call(
            db,
            provider=provider,
            request=request,
            resp=None,
            status="error",
            error_category=category,
            latency_ms=latency_ms,
            request_id=request_id,
            tenant_id=tenant_id,
            error_text=f"{e.code}: {e.message}",
        )
        raise

    latency_ms = int((time.perf_counter() - started) * 1000)
    _write_call(
        db,
        provider=provider,
        request=request,
        resp=resp,
        status="success",
        error_category=None,
        latency_ms=latency_ms,
        request_id=request_id,
        tenant_id=tenant_id,
    )
    return resp


def get_status() -> LLMStatusOut:
    """模型状态：只返回 provider/model/configured/mode/reason，绝不返回密钥。"""
    provider_name = settings.llm_provider
    if provider_name == "fake":
        return LLMStatusOut(
            provider=provider_name,
            model=settings.llm_model or "fake-mock-1",
            configured=True,
            mode="mock",
            reason="LLM_PROVIDER=fake（内置 mock，无需密钥，开发/CI 兜底）",
        )
    if provider_name == "openai_compatible":
        configured = bool(settings.llm_base_url and settings.llm_api_key)
        return LLMStatusOut(
            provider=provider_name,
            model=settings.llm_model or "",
            configured=configured,
            mode="live",
            reason=(
                "环境变量 LLM_BASE_URL/LLM_API_KEY 已配置"
                if configured
                else "LLM_PROVIDER=openai_compatible 但缺少 LLM_BASE_URL/LLM_API_KEY，调用将报 503"
            ),
        )
    return LLMStatusOut(
        provider=provider_name,
        model="",
        configured=False,
        mode="unknown",
        reason="未知 LLM_PROVIDER 取值，未选择任何提供方，请检查配置",
    )
