"""LLM 调用服务：统一走 Provider，并在 llm_calls 与 llm_traces 表中记录调用（含失败）。"""

import hashlib
import time
import uuid

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.knowledge.chunking import estimate_token_count
from app.modules.llm.binding_service import LEVEL_LABELS
from app.modules.llm.models import LLMCall, LLMTrace
from app.modules.llm.provider import get_provider_candidates, provider_resolution
from app.modules.llm.schemas import LLMRequest, LLMResponse, LLMStatusOut
from app.modules.yards.quota import check_and_consume

log = get_logger(__name__)

# provider 选择理由只打一次：进程内首次解析成功后 log.info，避免每次调用刷屏
_choice_logged = False


def _provider_choice_reason(provider) -> str:
    """为什么选中当前 provider 的人类可读理由（只含配置态描述，绝不含密钥值）。"""
    if getattr(provider, "provider_name", None):
        return "DB 供应商配置生效（小院/全局 active supplier）"
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
    """观测轨迹的提示词摘要（W4 隐私口径）：``sha256 前 12 位|前 200 字``。"""
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


def _ensure_token_counts(request: LLMRequest, resp: LLMResponse) -> LLMResponse:
    """若成功响应的上游 provider 未返回 input_tokens / output_tokens，用分词器保底估算，
    确保所有成功调用场景都有非空 Token 统计。
    """
    in_tok = resp.input_tokens
    out_tok = resp.output_tokens
    if in_tok is None:
        prompt_text = "\n".join(f"{m.role}:{m.content or ''}" for m in request.messages)
        in_tok = estimate_token_count(prompt_text)
    if out_tok is None:
        out_tok = estimate_token_count(resp.content or "")
    if in_tok != resp.input_tokens or out_tok != resp.output_tokens:
        resp = resp.model_copy(update={"input_tokens": in_tok, "output_tokens": out_tok})
    return resp


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
    yard_id: str,
    error_text: str | None = None,
) -> None:
    from app.core.context import metrics

    metrics.observe_llm(status=status, provider=_provider_label(provider))
    db.add(
        LLMCall(
            yard_id=yard_id,
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
            supplier_id=getattr(provider, "supplier_id", None),
            purpose=request.metadata.purpose or "platform",
        )
    )
    db.add(
        LLMTrace(
            yard_id=yard_id,
            user_id=request.metadata.user_id,
            session_id=request.metadata.session_id,
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


def _note_runtime_success(db: Session, provider) -> None:
    """真实调用成功 → 供应商连续失败计数清零（降级后的正向恢复路径）。"""
    from app.modules.llm import supplier_service

    supplier_service.record_runtime_success(db, getattr(provider, "supplier_id", None))


def _note_runtime_failure(db: Session, provider, note: str) -> None:
    """真实调用失败 → 累计失败数，达阈值自动降级并记一条中文日志。"""
    from app.modules.llm import supplier_service

    degraded = supplier_service.record_runtime_failure(
        db, getattr(provider, "supplier_id", None), note
    )
    if degraded:
        log.warning(
            "供应商 %s 运行时连续失败达到阈值，已自动降级（不再参与解析），"
            "可在模型中心确认后重新启用",
            _provider_label(provider),
        )


async def complete_with_logging(db: Session, request: LLMRequest) -> LLMResponse:
    """调用模型并记录 llm_calls 行；按解析链逐档兜底，全部失败才抛出最后一次错误。

    兜底纪律（v3.25）：
    - 只有 provider 层报错（超时/连接/鉴权/限流/上游 4xx·5xx）才换下一档，
      每档失败都单独写一条 error 记录，成功档再写一条 success；
    - 失败供应商累计连续失败数，达阈值由供应商服务自动降级（踢出候选链）；
    - 配额只在入口扣一次，兜底重试不重复计费。
    """
    yard_id = request.metadata.yard_id
    if not yard_id:
        raise AppError("INVALID_REQUEST", "缺少小院信息，无法记录模型调用。", http_status=400)
    # 配额拦截：调用模型前先扣减 llm_calls，超限直接 429 拒绝，不消耗上游资源
    check_and_consume(db, yard_id, "llm_calls")
    request_id = request.metadata.request_id or f"req_{uuid.uuid4().hex[:16]}"

    with provider_resolution(
        yard_id,
        db,
        request.metadata.supplier_id,
        purpose=request.metadata.purpose,
        agent_code=request.metadata.agent_code,
    ):
        candidates = get_provider_candidates()
    _log_provider_choice_once(candidates[0][0])

    last_error: AppError | None = None
    for index, (provider, level) in enumerate(candidates):
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
                yard_id=yard_id,
                error_text=f"{e.code}: {e.message}",
            )
            _note_runtime_failure(db, provider, f"{e.code}: {e.message}")
            last_error = e
            if index + 1 < len(candidates):
                nxt = candidates[index + 1][0]
                log.warning(
                    "模型兜底切换：第 %d 档 %s 调用失败（%s），改用 %s",
                    index + 1,
                    _provider_label(provider),
                    category or e.code,
                    _provider_label(nxt),
                )
                continue
            raise
        latency_ms = int((time.perf_counter() - started) * 1000)
        resp = _ensure_token_counts(request, resp)
        _write_call(
            db,
            provider=provider,
            request=request,
            resp=resp,
            status="success",
            error_category=None,
            latency_ms=latency_ms,
            request_id=request_id,
            yard_id=yard_id,
        )
        _note_runtime_success(db, provider)
        if index:
            log.info(
                "模型兜底成功：主用档位失败后由「%s」档（%s）完成调用",
                LEVEL_LABELS.get(level, level),
                _provider_label(provider),
            )
        return resp

    # 候选链为空在这里不可达（get_provider_candidates 至少返回 settings 兜底档）
    assert last_error is not None  # noqa: S101 - 防御性断言
    raise last_error


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
