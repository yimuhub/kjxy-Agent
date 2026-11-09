"""内置工具：视频生成（v3.37，code=video.generate）。

与 ``image.generate`` 同一个口径：能力由模型供应商供应，平台只做解析与适配。
`风险等级=external_side_effect`：视频生成真实计费，默认走人工审批；
会话授权模式为 ``full``（小院/平台管理员）或已批准时直接执行。

产物是上游返回的视频 URL（平台不转存，避免大文件占用磁盘与带宽）。
"""

from __future__ import annotations

from typing import Any

from app.core.errors import AppError
from app.core.logging import get_logger

log = get_logger(__name__)

TOOL_CODE = "video.generate"
TOOL_TIMEOUT_SECONDS = 240
DEFAULT_WAIT_SECONDS = 120

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string",
            "minLength": 4,
            "maxLength": 2000,
            "description": "视频内容描述（主体、动作、镜头、风格）",
        },
        "duration_seconds": {
            "type": "integer",
            "minimum": 1,
            "maximum": 30,
            "default": 5,
            "description": "视频时长（秒），越短越便宜",
        },
        "size": {
            "type": "string",
            "default": "1280x720",
            "description": "分辨率，如 1280x720",
        },
        "wait_seconds": {
            "type": "integer",
            "minimum": 0,
            "maximum": 200,
            "default": DEFAULT_WAIT_SECONDS,
            "description": "等待出片的最长秒数；超时返回任务 id，任务仍在上游继续",
        },
    },
    "required": ["prompt"],
    "additionalProperties": False,
}

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "accepted": {"type": "boolean", "description": "上游已受理但未在等待窗口内出片"},
        "url": {"type": "string", "description": "成品视频地址（已出片时）"},
        "task_id": {"type": "string", "description": "上游任务 id（生成中时用于追进度）"},
        "model": {"type": "string"},
        "provider": {"type": "string"},
        "latency_ms": {"type": "integer"},
        "message": {"type": "string"},
    },
    "required": ["ok", "message"],
}


async def generate_video_tool_func(params: dict, ctx: Any = None) -> dict[str, Any]:
    """工具入口：视频供应商解析要查库，自开一个会话，用完即关。"""
    if ctx is None or not getattr(ctx, "yard_id", None):
        raise AppError("YARD_REQUIRED", "视频生成需要小院上下文。", http_status=400)
    prompt = " ".join(str(params.get("prompt") or "").split())
    if len(prompt) < 4:
        raise AppError("INVALID_REQUEST", "视频描述过短（至少 4 个字）。", http_status=400)

    from app.db.session import SessionLocal
    from app.modules.llm.video import VideoRequest, resolve_video_provider

    db = SessionLocal()
    try:
        resolved = resolve_video_provider(
            db,
            ctx.yard_id,
            agent_code=getattr(ctx, "agent_code", None) or None,
        )
    finally:
        db.close()

    if resolved is None:
        raise AppError(
            "VIDEO_NOT_CONFIGURED",
            "还没有可用的视频供应商：请在「模型中心 → 供应商」接入视频模型，"
            "并在「智能体模型 → 视觉能力默认模型」里指定。",
            http_status=503,
        )

    provider, _level = resolved
    # 0 是合法值（提交后立刻返回 task_id，由后台轮询续查），不能被 `or` 吞成默认值
    raw_wait = params.get("wait_seconds")
    wait_seconds = DEFAULT_WAIT_SECONDS if raw_wait is None else int(raw_wait)
    batch = await provider.generate(
        VideoRequest(
            prompt=prompt[:2000],
            duration_seconds=int(params.get("duration_seconds") or 5),
            size=str(params.get("size") or "1280x720"),
        ),
        wait_seconds=max(0, min(200, wait_seconds)),
    )
    url = batch.assets[0].url if batch.assets else ""
    return {
        "ok": bool(url) or batch.accepted,
        "accepted": batch.accepted,
        "url": url,
        "task_id": batch.task_id or "",
        "model": batch.model,
        "provider": batch.provider_label,
        "latency_ms": batch.latency_ms,
        "message": batch.message or ("视频已生成。" if url else "上游已受理，视频仍在生成。"),
    }


def tool_meta() -> dict[str, Any]:
    return {
        "code": TOOL_CODE,
        "version": "1.0.0",
        "name": "视频生成",
        "description": (
            "按文字描述调用视频生成供应商产出短视频，返回可访问的视频地址；"
            "生成较慢时返回任务 id。会真实计费，默认需人工审批。"
        ),
        "input_schema_json": INPUT_SCHEMA,
        "output_schema_json": OUTPUT_SCHEMA,
        "required_permissions_json": [],
        "risk_level": "external_side_effect",
        "side_effect": True,
        "timeout_seconds": TOOL_TIMEOUT_SECONDS,
        "enabled": True,
    }


def register_video_tool() -> Any:
    """注册 code=video.generate。"""
    try:
        from app.modules.tools.registry import ToolDef, tool_registry
    except Exception as e:  # pragma: no cover - 注册表缺失时不影响 import
        log.warning("工具注册表不可用，video.generate 未注册: %s", e)
        return None
    meta = tool_meta()
    tool_def = ToolDef(
        code=TOOL_CODE,
        version=str(meta["version"]),
        name=str(meta["name"]),
        description=str(meta["description"]),
        input_schema=INPUT_SCHEMA,
        output_schema=OUTPUT_SCHEMA,
        required_permissions=[],
        risk_level="external_side_effect",
        side_effect=True,
        timeout_seconds=TOOL_TIMEOUT_SECONDS,
        func=generate_video_tool_func,
    )
    try:
        tool_registry.register(tool_def)
    except Exception as e:  # pragma: no cover
        log.warning("video.generate 注册失败: %s", e)
        return None
    log.info("video.generate 工具已注册")
    return tool_def


__all__ = [
    "TOOL_CODE",
    "generate_video_tool_func",
    "register_video_tool",
    "tool_meta",
]
