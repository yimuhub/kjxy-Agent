"""对话内媒体生成的资产、额度与视频任务生命周期（v3.73）。

职责边界：

- **授权**：图片默认全员（可按小院关）；视频按「管理员 / 指挥官 / 小院显式开放」
  放行，授权通过 ``ctx.preauthorized_tools`` 交给工具风险闸，执行后照常留痕；
- **计量**：按业务时区自然日统计每人张数/秒数，额度不依赖 Redis（桌面单机可用）；
- **产物**：图片沿用海报目录与海报签名 URL；视频转存到 ``media/`` 并支持 Range；
- **异步**：视频提交后只拿 task_id，后台调度器续查、转存、回填消息 payload。

所有生成都走 ``tool_registry.execute``，不另开第二条执行路径。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.net_guard import check_egress_url
from app.core.timezone import business_today, day_bounds
from app.modules.chat.models import ChatMessage
from app.modules.growth.poster import poster_url
from app.modules.media import store
from app.modules.media.models import (
    KIND_IMAGE,
    KIND_VIDEO,
    STATUS_FAILED,
    STATUS_READY,
    STATUS_RUNNING,
    MediaAsset,
)
from app.modules.yards import settings_service as yard_settings

log = get_logger(__name__)

IMAGE_TOOL = "image.generate"
VIDEO_TOOL = "video.generate"

ADMIN_ROLES = {"yard_admin", "yard_owner", "platform_admin"}

#: 产物上限：图片 25MB、视频 100MB（超出直接拒绝，不写盘）
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_VIDEO_BYTES = 100 * 1024 * 1024
#: 视频转存超时（大文件慢网）
VIDEO_DOWNLOAD_TIMEOUT = 300
#: 轮询连续失败上限：超过即判失败，避免一个坏任务永久占着调度
POLL_MAX_ERRORS = 5

_IMAGE_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "member_access": True,
    "daily_limit": 20,
    "max_per_request": 4,
    "default_size": "1024x1024",
}
_VIDEO_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "member_access": False,
    "daily_limit": 3,
    "max_seconds": 10,
    "admin_max_seconds": 30,
    "retention_days": 30,
}


def _merge(raw: Any, defaults: dict[str, Any]) -> dict[str, Any]:
    merged = dict(defaults)
    if isinstance(raw, dict):
        for key, value in raw.items():
            if key in merged and value is not None:
                merged[key] = value
    return merged


def settings_for(db: Session, yard_id: str) -> dict[str, dict[str, Any]]:
    raw = yard_settings.get_setting(db, yard_id, "media")
    return {
        "image": _merge(raw.get("image"), _IMAGE_DEFAULTS),
        "video": _merge(raw.get("video"), _VIDEO_DEFAULTS),
    }


def is_admin(ctx: YardContext) -> bool:
    return bool(ADMIN_ROLES & set(ctx.roles or []))


def video_authorized(ctx: YardContext, cfg: dict[str, Any], *, executor_is_commander: bool) -> bool:
    """视频三选一：管理员 / 指挥官执行 / 小院显式对成员开放。"""
    if is_admin(ctx):
        return True
    if executor_is_commander:
        return True
    return bool(cfg["video"].get("member_access"))


def _used_today(db: Session, yard_id: str, user_id: str, kind: str) -> tuple[int, int]:
    start, end = day_bounds(business_today())
    # 提交上游之前就失败的（未产生供应商调用）不占额度；SQL 端聚合，
    # 不把当日所有资产行拉回 Python 再数（v3.78 性能清理）。
    counts_before_submit = db.execute(
        select(
            func.count(MediaAsset.id),
            func.coalesce(func.sum(func.coalesce(MediaAsset.duration_seconds, 0)), 0),
        ).where(
            MediaAsset.yard_id == yard_id,
            MediaAsset.created_by == user_id,
            MediaAsset.kind == kind,
            MediaAsset.created_at >= start,
            MediaAsset.created_at < end,
            ~((MediaAsset.status == STATUS_FAILED) & MediaAsset.upstream_task_id.is_(None)),
        )
    ).one()
    return int(counts_before_submit[0]), int(counts_before_submit[1])


def quota_snapshot(db: Session, ctx: YardContext, kind: str, cfg: dict[str, Any]) -> dict[str, Any]:
    used_count, used_seconds = _used_today(db, ctx.yard_id, ctx.user_id, kind)
    group = cfg["image"] if kind == KIND_IMAGE else cfg["video"]
    limit = int(group.get("daily_limit") or 0)
    seconds_limit = int(group.get("daily_limit") or 0) * int(group.get("max_seconds") or 0)
    return {
        "kind": kind,
        "limit": limit,
        "used": used_count,
        "remaining": max(0, limit - used_count) if limit > 0 else None,
        "seconds_used": used_seconds,
        "seconds_limit": seconds_limit if kind == KIND_VIDEO and seconds_limit > 0 else None,
        "reset_at": day_bounds(business_today())[1].isoformat(),
    }


def enforce_quota(
    db: Session, ctx: YardContext, kind: str, cfg: dict[str, Any], *, count: int = 1
) -> None:
    snapshot = quota_snapshot(db, ctx, kind, cfg)
    limit = snapshot["limit"]
    if limit <= 0:
        return
    remaining = snapshot["remaining"]
    if remaining is not None and remaining < max(1, count):
        label = "张图片" if kind == KIND_IMAGE else "条视频"
        raise AppError(
            "MEDIA_QUOTA_EXCEEDED",
            f"今天的{label}额度不足（已用 {snapshot['used']}/{limit}，本次需要 {count}）。"
            "明天自动重置；管理员可在用量额度里调整或清零。",
            http_status=429,
            details=snapshot,
        )


def _new_asset(
    db: Session,
    ctx: YardContext,
    *,
    kind: str,
    prompt: str,
    status: str,
    spec: dict[str, Any],
    agent_code: str,
    notice: str = "",
) -> MediaAsset:
    asset = MediaAsset(
        yard_id=ctx.yard_id,
        created_by=ctx.user_id,
        kind=kind,
        status=status,
        prompt=(prompt or "")[:2000],
        meta_json={
            "spec": spec,
            "agent_code": agent_code,
            "notice": notice,
            "submitted_at": datetime.now(UTC).isoformat(),
        },
    )
    db.add(asset)
    db.flush()
    return asset


def budget_seconds(cfg: dict[str, Any], ctx: YardContext) -> int:
    """单次视频时长上限：管理员 30 秒、成员 10 秒（均可由小院设置收紧）。"""
    group = cfg["video"]
    ceiling = (
        int(group.get("admin_max_seconds") or 30)
        if is_admin(ctx)
        else int(group.get("max_seconds") or 10)
    )
    return max(1, min(30, ceiling))


def _asset_filename(asset: MediaAsset) -> str:
    stamp = asset.created_at.strftime("%Y%m%d-%H%M")
    suffix = ""
    for key in (asset.poster_key, asset.storage_key):
        if key:
            suffix = Path(key).suffix
            break
    if not suffix:
        suffix = ".png" if asset.kind == KIND_IMAGE else ".mp4"
    label = "海报" if asset.kind == KIND_IMAGE else "视频"
    return f"{label}-{stamp}-{asset.id[:6]}{suffix}"


def asset_view(asset: MediaAsset) -> dict[str, Any]:
    meta = asset.meta_json or {}
    return {
        "id": asset.id,
        "kind": asset.kind,
        "status": asset.status,
        "url": asset.url or "",
        "mime": asset.mime,
        "bytes": int(asset.size_bytes or 0),
        "width": asset.width,
        "height": asset.height,
        "duration_seconds": asset.duration_seconds,
        "filename": _asset_filename(asset),
        "provider": asset.provider,
        "model": asset.model,
        "error_code": asset.error_code,
        "error_message": asset.error_message,
        "expires_at": asset.expires_at.isoformat() if asset.expires_at else None,
        "cached": bool(meta.get("cached", asset.kind == KIND_IMAGE)),
        "fallback": bool(meta.get("fallback")),
        "overlaid": bool(meta.get("overlaid")),
    }


def merge_statuses(statuses) -> str:
    """多资产卡片聚合口径：有在途算在途，否则有成品算成品，其余一律失败。

    ``card_payload``（落库时）与 ``sync_message_payload``（回填时）共用，
    避免两处各自维护同一套规则。
    """
    items = set(statuses)
    if STATUS_RUNNING in items:
        return STATUS_RUNNING
    if STATUS_READY in items:
        return STATUS_READY
    return STATUS_FAILED


def _card_status(assets: list[MediaAsset]) -> str:
    return merge_statuses(asset.status for asset in assets)


def card_payload(
    assets: list[MediaAsset],
    *,
    prompt: str,
    notice: str = "",
    hint: str = "",
) -> dict[str, Any]:
    kind = assets[0].kind if assets else KIND_IMAGE
    payload: dict[str, Any] = {
        "type": "media",
        "kind": kind,
        "status": _card_status(assets),
        "assets": [asset_view(asset) for asset in assets],
        "prompt": prompt[:500],
        "notice": notice,
    }
    if kind == KIND_VIDEO and payload["status"] == STATUS_RUNNING:
        payload["hint"] = hint or "视频生成中，通常 1–3 分钟；完成后这张卡片会自动出现成品。"
    video_assets = [asset for asset in assets if asset.kind == KIND_VIDEO]
    if video_assets:
        payload["retention_days"] = int(
            (video_assets[0].meta_json or {}).get("retention_days")
            or _VIDEO_DEFAULTS["retention_days"]
        )
    return payload


def sync_message_payload(db: Session, asset: MediaAsset) -> None:
    """视频完成后原地刷新消息卡片（历史消息刷新也能看到最新状态）。"""
    if not asset.message_id:
        return
    message = db.get(ChatMessage, asset.message_id)
    if message is None or not isinstance(message.payload_json, dict):
        return
    payload = dict(message.payload_json)
    if payload.get("type") != "media":
        return
    items = [dict(item) for item in (payload.get("assets") or []) if isinstance(item, dict)]
    replaced = False
    for index, item in enumerate(items):
        if item.get("id") == asset.id:
            items[index] = asset_view(asset)
            replaced = True
    if not replaced:
        items.append(asset_view(asset))
    payload["assets"] = items
    # 转存失败等结局说明要以最新一次为准，不能停留在提交时的空 notice
    latest_notice = str((asset.meta_json or {}).get("notice") or "")
    if latest_notice:
        payload["notice"] = latest_notice
    payload["status"] = merge_statuses(str(item.get("status") or "") for item in items)
    message.payload_json = payload


# ---------------------------------------------------------------------------
# 图像
# ---------------------------------------------------------------------------


def _image_params(spec: dict[str, Any], prompt: str) -> dict[str, Any]:
    params: dict[str, Any] = {
        "prompt": str(spec.get("image_prompt") or prompt)[:2000],
        "size": str(spec.get("size") or "1024x1024"),
        "n": max(1, min(4, int(spec.get("n") or 1))),
    }
    if spec.get("poster"):
        params["title"] = str(spec.get("title") or "")[:40]
        params["subtitle"] = str(spec.get("subtitle") or "")[:120]
        params["footer"] = str(spec.get("footer") or "")[:120]
        params["theme"] = str(spec.get("theme") or "harvest")
    negative = str(spec.get("negative_prompt") or "").strip()
    if negative:
        params["negative_prompt"] = negative[:400]
    return params


async def generate_image(
    db: Session,
    ctx: YardContext,
    *,
    prompt: str,
    spec: dict[str, Any],
    agent_code: str,
) -> dict[str, Any]:
    from app.modules.tools.registry import tool_registry

    exec_ctx = replace(ctx, agent_code=agent_code or "")
    result = await tool_registry.execute(
        db, IMAGE_TOOL, _image_params(spec, prompt), exec_ctx, ctx.request_id
    )
    raw_assets = [item for item in (result.get("assets") or []) if isinstance(item, dict)]
    if not raw_assets:
        raise AppError("MEDIA_EMPTY", "图像生成没有返回可用文件，请稍后重试。", http_status=502)

    rows: list[MediaAsset] = []
    notice = str(result.get("notice") or "")
    for item in raw_assets:
        data_bytes = int(item.get("bytes") or 0)
        if data_bytes > MAX_IMAGE_BYTES:
            raise AppError("MEDIA_TOO_LARGE", "图片超过 25MB，已拒绝保存。", http_status=413)
        asset = _new_asset(
            db,
            ctx,
            kind=KIND_IMAGE,
            prompt=prompt,
            status=STATUS_READY,
            spec=spec,
            agent_code=agent_code,
            notice=notice,
        )
        asset.poster_key = str(item.get("key") or "")
        asset.url = str(item.get("url") or poster_url(asset.poster_key))
        asset.mime = str(item.get("mime") or "image/png")
        asset.size_bytes = data_bytes
        asset.width = item.get("width")
        asset.height = item.get("height")
        asset.provider = result.get("provider")
        asset.model = result.get("model")
        asset.meta_json = {
            **(asset.meta_json or {}),
            "fallback": bool(result.get("fallback")),
            "overlaid": bool(result.get("overlaid")),
            "notice": notice,
            "cached": True,
        }
        rows.append(asset)
    db.flush()
    return {
        "assets": rows,
        "notice": notice,
        "provider": result.get("provider"),
        "model": result.get("model"),
        "fallback": bool(result.get("fallback")),
        "overlaid": bool(result.get("overlaid")),
    }


# ---------------------------------------------------------------------------
# 视频
# ---------------------------------------------------------------------------


def _video_params(spec: dict[str, Any], prompt: str) -> dict[str, Any]:
    return {
        "prompt": str(spec.get("video_prompt") or prompt)[:2000],
        "duration_seconds": int(spec.get("duration_seconds") or 5),
        "size": str(spec.get("size") or "1280x720"),
        # 0 = 提交后立即返回 task_id，由后台轮询续查，不占住对话连接
        "wait_seconds": 0,
    }


def _sniff_video_mime(data: bytes, header: str) -> str:
    header = (header or "").split(";")[0].strip().lower()
    if header.startswith("video/"):
        return header
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "video/mp4"
    if data[:4] == b"\x1aE\xdf\xa3":
        return "video/webm"
    return "video/mp4"


async def download_video(url: str) -> tuple[bytes, str]:
    """把上游成品拉回本地；手动跟 3 次重定向，每一跳都过 net_guard。"""
    current = url
    async with httpx.AsyncClient(
        timeout=VIDEO_DOWNLOAD_TIMEOUT, trust_env=False, follow_redirects=False
    ) as client:
        for _ in range(4):
            check_egress_url(current, context="media_video_cache", allow_local=False)
            async with client.stream("GET", current) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location") or ""
                    if not location:
                        raise AppError("MEDIA_CACHE_FAILED", "视频下载地址跳转缺少目标。")
                    current = urljoin(current, location)
                    continue
                if resp.status_code != 200:
                    raise AppError(
                        "MEDIA_CACHE_FAILED",
                        f"视频下载失败（HTTP {resp.status_code}），请稍后重试。",
                    )
                declared = int(resp.headers.get("content-length") or 0)
                if declared > MAX_VIDEO_BYTES:
                    raise AppError("MEDIA_TOO_LARGE", "视频超过 100MB，已拒绝保存。", 413)
                buffer = bytearray()
                async for chunk in resp.aiter_bytes(64 * 1024):
                    buffer.extend(chunk)
                    if len(buffer) > MAX_VIDEO_BYTES:
                        raise AppError("MEDIA_TOO_LARGE", "视频超过 100MB，已拒绝保存。", 413)
                return bytes(buffer), _sniff_video_mime(
                    bytes(buffer[:32]), resp.headers.get("content-type", "")
                )
    raise AppError("MEDIA_CACHE_FAILED", "视频下载重定向次数过多。")


async def _finish_video(
    db: Session, asset: MediaAsset, upstream_url: str, *, notice: str = ""
) -> None:
    retention_days = int((asset.meta_json or {}).get("retention_days") or 30)
    cached = False
    try:
        data, mime = await download_video(upstream_url)
        key = store.save_bytes(data, mime)
        asset.storage_key = key
        asset.url = store.media_url(key)
        asset.mime = mime
        asset.size_bytes = len(data)
        cached = True
    except Exception as e:  # noqa: BLE001 - 转存失败仍可播临时链接，但必须如实标记
        log.warning("视频转存失败，保留上游临时链接: %s", e)
        asset.url = upstream_url
        asset.mime = "video/mp4"
        notice = (notice + " " if notice else "") + (
            "视频未能转存到本地，当前为供应商临时链接，可能过期。"
        )
        asset.error_message = str(e)[:300]
    asset.status = STATUS_READY
    asset.error_code = None
    # 未转存的临时链接同样按保留期收口：到期后消息明确显示「已清理」，不留死链
    asset.expires_at = datetime.now(UTC) + timedelta(days=retention_days)
    asset.meta_json = {
        **(asset.meta_json or {}),
        "cached": cached,
        "notice": notice,
    }
    db.flush()
    sync_message_payload(db, asset)


async def submit_video(
    db: Session,
    ctx: YardContext,
    *,
    prompt: str,
    spec: dict[str, Any],
    agent_code: str,
    authorized: bool,
    retention_days: int,
    notice: str = "",
) -> dict[str, Any]:
    """提交视频任务；authorized=True 时以「小院策略预授权」放行风险闸。"""
    from app.modules.tools.registry import tool_registry

    params = _video_params(spec, prompt)
    exec_ctx = replace(ctx, agent_code=agent_code or "")
    if authorized:
        exec_ctx = replace(exec_ctx, preauthorized_tools=(VIDEO_TOOL,))
    result = await tool_registry.execute(db, VIDEO_TOOL, params, exec_ctx, ctx.request_id)
    task_id = str(result.get("task_id") or "")
    url = str(result.get("url") or "")

    asset = _new_asset(
        db,
        ctx,
        kind=KIND_VIDEO,
        prompt=prompt,
        status=STATUS_RUNNING,
        spec=spec,
        agent_code=agent_code,
        notice=notice,
    )
    asset.upstream_task_id = task_id or None
    asset.provider = result.get("provider")
    asset.model = result.get("model")
    asset.duration_seconds = int(params["duration_seconds"])
    asset.meta_json = {
        **(asset.meta_json or {}),
        "retention_days": retention_days,
        "provider_message": str(result.get("message") or "")[:300],
    }
    db.flush()

    if url:
        await _finish_video(db, asset, url, notice=notice)
    elif not task_id:
        asset.status = STATUS_FAILED
        asset.error_code = "VIDEO_NO_TASK"
        asset.error_message = "上游没有返回任务 id，无法继续查询进度。"
        db.flush()
        raise AppError(
            "VIDEO_NO_TASK",
            asset.error_message,
            http_status=502,
        )
    return {
        "assets": [asset],
        "notice": notice,
        "provider": asset.provider,
        "model": asset.model,
        "accepted": not bool(url),
    }


def _video_error_code(message: str) -> str:
    for key, code in (
        ("鉴权", "VIDEO_AUTH"),
        ("限流", "VIDEO_RATE_LIMIT"),
        ("超时", "VIDEO_TIMEOUT"),
        ("无法连接", "VIDEO_CONNECTION"),
        ("没有", "VIDEO_NOT_CONFIGURED"),
        ("未配置", "VIDEO_NOT_CONFIGURED"),
        ("超过", "VIDEO_TOO_LARGE"),
    ):
        if key in message:
            return code
    return "VIDEO_FAILED"


async def poll_pending(db: Session, *, limit: int = 4) -> int:
    """续查在途视频：成功则转存并回填消息卡片，连续失败 5 次判失败。"""
    from app.modules.llm.video import resolve_video_provider

    now = datetime.now(UTC)
    rows = db.scalars(
        select(MediaAsset)
        .where(
            MediaAsset.kind == KIND_VIDEO,
            MediaAsset.status == STATUS_RUNNING,
            MediaAsset.upstream_task_id.is_not(None),
        )
        .order_by(MediaAsset.updated_at)
        .limit(max(1, limit))
    ).all()
    processed = 0
    for asset in rows:
        # 租约：先推进 updated_at，避免多进程/多线程重复拉同一任务
        db.execute(
            update(MediaAsset)
            .where(MediaAsset.id == asset.id, MediaAsset.status == STATUS_RUNNING)
            .values(updated_at=now)
        )
        db.commit()
        db.refresh(asset)
        processed += 1

        meta = dict(asset.meta_json or {})
        resolved = None
        try:
            resolved = resolve_video_provider(
                db, asset.yard_id, agent_code=str(meta.get("agent_code") or "") or None
            )
        except Exception as e:  # noqa: BLE001 - 供应商解析失败按可重试错误处理
            log.warning("视频轮询解析供应商失败: %s", e)
        if resolved is None:
            asset.status = STATUS_FAILED
            asset.error_code = "VIDEO_NOT_CONFIGURED"
            asset.error_message = "视频供应商已被移除或不可用，无法继续查询进度。"
            db.commit()
            sync_message_payload(db, asset)
            db.commit()
            continue

        provider, _level = resolved
        query = getattr(provider, "query_task", None)
        if query is None:
            asset.status = STATUS_FAILED
            asset.error_code = "VIDEO_PROVIDER_UNSUPPORTED"
            asset.error_message = "当前视频供应商不支持按任务 id 续查，请重新发起生成。"
            db.commit()
            sync_message_payload(db, asset)
            db.commit()
            continue

        try:
            batch = await query(str(asset.upstream_task_id), wait_seconds=0)
        except AppError as e:
            errors = int(meta.get("poll_errors") or 0) + 1
            meta["poll_errors"] = errors
            asset.meta_json = meta
            if errors >= POLL_MAX_ERRORS:
                asset.status = STATUS_FAILED
                asset.error_code = _video_error_code(e.message)
                asset.error_message = e.message[:500]
            db.commit()
            if asset.status == STATUS_FAILED:
                sync_message_payload(db, asset)
                db.commit()
            continue
        except Exception as e:  # noqa: BLE001 - 单次异常不判死刑
            errors = int(meta.get("poll_errors") or 0) + 1
            meta["poll_errors"] = errors
            asset.meta_json = meta
            if errors >= POLL_MAX_ERRORS:
                asset.status = STATUS_FAILED
                asset.error_code = "VIDEO_POLL_FAILED"
                asset.error_message = f"查询视频进度多次失败：{type(e).__name__}"
            db.commit()
            continue

        if batch.assets:
            meta["poll_errors"] = 0
            asset.meta_json = meta
            await _finish_video(db, asset, batch.assets[0].url, notice=str(batch.message or ""))
            db.commit()
            continue

        # 仍在上游生成：清掉错误计数，等下一轮
        meta["poll_errors"] = 0
        asset.meta_json = meta
        db.commit()
    return processed


def mark_message(db: Session, assets: list[MediaAsset], message_id: str) -> None:
    for asset in assets:
        asset.message_id = message_id
        sync_message_payload(db, asset)
    db.flush()


def expire_media(db: Session) -> int:
    """按保留期清理视频文件；行保留并标记过期，历史消息仍能如实显示「已清理」。"""
    now = datetime.now(UTC)
    rows = db.scalars(
        select(MediaAsset).where(
            MediaAsset.expires_at.is_not(None),
            MediaAsset.expires_at < now,
            MediaAsset.status == STATUS_READY,
        )
    ).all()
    removed = 0
    for asset in rows:
        if asset.storage_key:
            try:
                store.delete(asset.storage_key)
            except Exception as e:  # noqa: BLE001 - 文件已丢也算清理完成
                log.warning("媒体文件清理失败 %s: %s", asset.storage_key, e)
        asset.storage_key = None
        asset.url = None
        asset.status = STATUS_FAILED
        asset.error_code = "MEDIA_EXPIRED"
        asset.error_message = "视频已按保留期自动清理，如需请重新生成。"
        removed += 1
        sync_message_payload(db, asset)
    if rows:
        db.flush()
    return removed


def public_asset(db: Session, ctx: YardContext, asset_id: str) -> dict[str, Any]:
    """按 id 取资产：只允许本小院 + 请求者本人（管理员可看本小院全部）。"""
    asset = db.get(MediaAsset, asset_id)
    if asset is None or asset.yard_id != ctx.yard_id:
        raise AppError("MEDIA_NOT_FOUND", "媒体不存在或已清理。", http_status=404)
    if asset.created_by != ctx.user_id and not is_admin(ctx):
        raise AppError("MEDIA_NOT_FOUND", "媒体不存在或已清理。", http_status=404)
    return asset_view(asset)


def usage_summary(db: Session, ctx: YardContext) -> dict[str, Any]:
    cfg = settings_for(db, ctx.yard_id)
    return {
        "image": quota_snapshot(db, ctx, KIND_IMAGE, cfg),
        "video": quota_snapshot(db, ctx, KIND_VIDEO, cfg),
    }


__all__ = [
    "IMAGE_TOOL",
    "VIDEO_TOOL",
    "asset_view",
    "card_payload",
    "download_video",
    "enforce_quota",
    "expire_media",
    "generate_image",
    "is_admin",
    "mark_message",
    "poll_pending",
    "public_asset",
    "quota_snapshot",
    "settings_for",
    "submit_video",
    "sync_message_payload",
    "usage_summary",
    "video_authorized",
]
