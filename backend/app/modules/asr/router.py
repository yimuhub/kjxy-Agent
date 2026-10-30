"""语音转写路由（v3.30）。

- 状态查询对成员开放（对话页需要判断「本机/在线是否可用」）；
- 下载/取消/删除/测试会改动本机文件或消耗音频，按 ``settings:manage`` 收口。

模型文件按「这台机器」存储（见 ``asr.storage``），不是小院数据：
同一小院不同电脑各自下载，状态以本机实际文件与校验标记为准。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Request, UploadFile
from sqlalchemy.orm import Session

from app.core.deps import YardContext, get_db_session, get_yard_context, require_yard_permission
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.asr import downloader, service
from app.modules.asr.catalog import get_model, payload_list
from app.modules.audit.service import log_audit

router = APIRouter(prefix="/asr", tags=["asr"])


def _model_or_404(model_id: str):
    model = get_model(model_id)
    if model is None:
        raise AppError("ASR_MODEL_NOT_FOUND", "未知的语音模型。", http_status=404)
    return model


def _actor(ctx: YardContext) -> str:
    return ctx.user_id


@router.get("/status")
def asr_status(
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    """当前语音转写状态：模式、本机模型进度、在线通道、最终生效通道。"""
    return ok(service.status(db, ctx.yard_id), request)


@router.get("/models")
def asr_models(request: Request):
    """内置模型清单（含体积/许可/是否内置引擎）。"""
    return ok({"models": payload_list()}, request)


@router.post("/models/{model_id}/download")
def asr_download(
    model_id: str,
    request: Request,
    ctx: YardContext = Depends(require_yard_permission("settings:manage")),
    db: Session = Depends(get_db_session),
):
    """开始下载本机模型（幂等，可断点续传）。"""
    _model_or_404(model_id)
    try:
        state = downloader.start(model_id)
    except ValueError as e:
        raise AppError("ASR_DOWNLOAD_REJECTED", str(e), http_status=400) from e
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=_actor(ctx),
        action="asr_model_download_started",
        resource_type="asr_model",
        resource_id=model_id,
        result="success",
        request_id=ctx.request_id,
        metadata={"total_bytes": state.get("total")},
    )
    db.commit()
    return ok(state, request)


@router.post("/models/{model_id}/cancel")
def asr_cancel(
    model_id: str,
    request: Request,
    ctx: YardContext = Depends(require_yard_permission("settings:manage")),
):
    _model_or_404(model_id)
    return ok(downloader.cancel(model_id), request)


@router.delete("/models/{model_id}")
def asr_remove(
    model_id: str,
    request: Request,
    ctx: YardContext = Depends(require_yard_permission("settings:manage")),
    db: Session = Depends(get_db_session),
):
    """删除本机模型文件（释放磁盘；在线通道不受影响）。"""
    _model_or_404(model_id)
    try:
        downloader.remove(model_id)
    except OSError as e:
        raise AppError(
            "ASR_MODEL_IN_USE",
            "模型文件仍被占用，请稍后重试（若刚识别过语音，等几秒再删）。",
            http_status=409,
        ) from e
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=_actor(ctx),
        action="asr_model_removed",
        resource_type="asr_model",
        resource_id=model_id,
        result="success",
        request_id=ctx.request_id,
        metadata={},
    )
    db.commit()
    return ok({"removed": True, "model_id": model_id}, request)


@router.post("/test")
async def asr_test(
    request: Request,
    file: UploadFile = File(...),
    ctx: YardContext = Depends(require_yard_permission("settings:manage")),
    db: Session = Depends(get_db_session),
):
    """设置页「测试转写」：用一段短音频走当前生效通道，返回文本/引擎/耗时。"""
    return ok(await service.test_transcribe(db, ctx, file), request)


__all__ = ["router"]
