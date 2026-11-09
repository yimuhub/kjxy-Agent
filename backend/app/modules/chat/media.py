"""对话内媒体编排（v3.73）：一句话 → 图片/视频成品。

为什么用确定性意图而不是全靠模型自觉调工具：

- 普通问答链路不保证把 ``image.generate`` 放进工具清单，模型也不会每次都记得；
- 出图/出片有成本，必须能在进入模型前就做权限与额度判断；
- 产物要落库、要能刷新后仍在，必须有稳定的消息 payload，而不是模型随手写的
  Markdown 图片链接。

因此这里做三件事：规则识别媒体意图 → 一次轻量 LLM 把口语整理成结构化规格
（模型不可用时确定性兜底）→ 走 ``tool_registry`` 生成、``media.service`` 落库。
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.agents import declarative
from app.modules.agents.models import STATUS_PUBLISHED, VISIBILITY_PUBLIC, YardAgent
from app.modules.llm.images import ALLOWED_SIZES
from app.modules.llm.schemas import ChatMessage, LLMMetadata, LLMRequest
from app.modules.llm.service import complete_with_logging
from app.modules.media import service as media_service
from app.modules.media.models import KIND_IMAGE, KIND_VIDEO, STATUS_READY

log = get_logger(__name__)

_PHASE_GENERATING = "generating"
_STEP_RUNNING = "running"
_STEP_DONE = "done"

IMAGE_NOUNS: tuple[str, ...] = (
    "海报",
    "图片",
    "配图",
    "插画",
    "封面",
    "主视觉",
    "宣传图",
    "易拉宝",
    "展板",
    "头像",
    "壁纸",
    "示意图",
)
VIDEO_NOUNS: tuple[str, ...] = (
    "视频",
    "短片",
    "短视频",
    "宣传片",
    "动画",
    "视频片段",
)
POSTER_NOUNS: tuple[str, ...] = (
    "海报",
    "封面",
    "主视觉",
    "宣传图",
    "易拉宝",
    "展板",
)
MEDIA_VERBS: tuple[str, ...] = (
    "生成",
    "做",
    "制作",
    "设计",
    "来一张",
    "来一个",
    "来一段",
    "出图",
    "出片",
    "画一张",
    "画个",
    "我需要",
    "我要",
    "帮我",
    "给我",
)
#: 命中这些词说明用户要的是文字材料/方法，不是成品图（"写个海报文案"）
NON_MEDIA_NOUNS: tuple[str, ...] = (
    "文案",
    "脚本",
    "提示词",
    "方案",
    "思路",
    "排版建议",
    "设计说明",
    "分镜",
)
QUESTION_WORDS: tuple[str, ...] = (
    "怎么",
    "如何",
    "为什么",
    "能不能",
    "可不可以",
    "是否",
    "什么",
    "哪些",
    "有没有",
    "吗",
    "呢",
)

_JSON_RE = re.compile(r"\{.*\}", re.S)
_DURATION_RE = re.compile(r"(\d{1,2})\s*(?:秒|s\b|秒钟)")
_COUNT_RE = re.compile(r"([1-4])\s*张")

_IMAGE_SIZES = set(ALLOWED_SIZES)


@dataclass(frozen=True)
class MediaIntent:
    kind: str
    poster: bool
    subject: str
    duration_seconds: int
    size: str
    explicit: bool
    count: int = 1


def _has(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def _pick_size(text: str, kind: str) -> str:
    if kind == KIND_VIDEO:
        return "720x1280" if _has(text, ("竖版", "竖屏", "9:16")) else "1280x720"
    if _has(text, ("竖版", "竖屏", "9:16")):
        return "1024x1536" if "1024x1536" in _IMAGE_SIZES else "1024x1024"
    if _has(text, ("横版", "横屏", "16:9")):
        return "1536x1024" if "1536x1024" in _IMAGE_SIZES else "1024x1024"
    return "1024x1024"


def _clean_subject(text: str) -> str:
    clean = re.sub(r"^/(图|图片|视频)\s*", "", (text or "").strip())
    clean = re.sub(r"^(我需要|我要|帮我|请|麻烦|给我)\s*", "", clean)
    return " ".join(clean.split())[:600]


def detect(text: str) -> MediaIntent | None:
    """规则识别：明确要成品图/视频才算媒体意图；疑问句与非成品名词不劫持。"""
    raw = (text or "").strip()
    if not raw:
        return None
    explicit = raw.startswith(("/图", "/图片", "/视频"))
    if not explicit:
        if _has(raw, QUESTION_WORDS) or _has(raw, NON_MEDIA_NOUNS):
            return None
        if not _has(raw, MEDIA_VERBS):
            return None

    if explicit:
        kind = KIND_VIDEO if raw.startswith("/视频") else KIND_IMAGE
    elif _has(raw, VIDEO_NOUNS):
        kind = KIND_VIDEO
    elif _has(raw, IMAGE_NOUNS):
        kind = KIND_IMAGE
    else:
        return None
    if not explicit and not _has(raw, IMAGE_NOUNS + VIDEO_NOUNS):
        return None

    duration = 5
    match = _DURATION_RE.search(raw)
    if match:
        duration = max(1, min(30, int(match.group(1))))
    poster = kind == KIND_IMAGE and _has(raw, POSTER_NOUNS)
    count = 1
    if kind == KIND_IMAGE:
        count_match = _COUNT_RE.search(raw)
        if count_match:
            count = int(count_match.group(1))
    return MediaIntent(
        kind=kind,
        poster=poster,
        subject=_clean_subject(raw),
        duration_seconds=duration,
        size=_pick_size(raw, kind),
        explicit=explicit,
        count=count,
    )


def _fallback_spec(intent: MediaIntent) -> dict[str, Any]:
    subject = intent.subject or "主题画面"
    if intent.kind == KIND_IMAGE:
        prompt = (
            f"为「{subject}」创作一张高质量宣传图。要求：主体明确、构图完整、"
            "光线自然、色彩干净，农业科技小院场景要真实可信；画面下方预留文字区域，"
            "画面中不要出现任何文字、字母、水印或二维码。"
        )
        return {
            "kind": KIND_IMAGE,
            "poster": intent.poster,
            "image_prompt": prompt,
            "title": subject[:20],
            "subtitle": "",
            "footer": "",
            "negative_prompt": "错别字, 乱码文字, 水印, 低清晰度, 变形的手指",
            "size": intent.size,
            "n": intent.count,
        }
    prompt = (
        f"为「{subject}」创作一段 {intent.duration_seconds} 秒的短视频。"
        "要求：镜头稳定、动作自然、光线真实，农业场景可信，不要出现文字与水印。"
    )
    return {
        "kind": KIND_VIDEO,
        "video_prompt": prompt,
        "duration_seconds": intent.duration_seconds,
        "size": intent.size,
    }


def _spec_prompt(intent: MediaIntent) -> str:
    if intent.kind == KIND_IMAGE:
        return (
            "你是农业科技小院的视觉设计助手。把用户需求整理成海报/配图生成规格，只输出 JSON："
            '{"image_prompt":"给图像模型的结构化提示词（主体/场景/风格/构图/光线/文字区留白，'
            '并明确不要画面文字）","title":"海报大标题（不超过20字）",'
            '"subtitle":"副标题（不超过40字，没有就空字符串）",'
            '"footer":"页脚（小院名/电话，没有就空字符串）",'
            '"negative_prompt":"不希望出现的元素","size":"1024x1024|1024x1536|1536x1024"}。'
            "不要输出 JSON 以外的任何内容。"
        )
    return (
        "你是农业科技小院的短视频助手。把用户需求整理成视频生成规格，只输出 JSON："
        '{"video_prompt":"给视频模型的描述（主体/动作/镜头/光线/风格，不要文字与水印）",'
        '"duration_seconds":5,"size":"1280x720|720x1280"}。'
        "不要输出 JSON 以外的任何内容。"
    )


async def build_spec(
    db: Session,
    ctx: YardContext,
    intent: MediaIntent,
    request_text: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """一次轻量 LLM 整理规格；失败/不合规一律回落确定性模板。"""
    spec = _fallback_spec(intent)
    usage: dict[str, Any] | None = None
    try:
        resp = await complete_with_logging(
            db,
            LLMRequest(
                messages=[
                    ChatMessage(role="system", content=_spec_prompt(intent)),
                    ChatMessage(role="user", content=f"用户需求：{request_text[:800]}"),
                ],
                max_tokens=700,
                temperature=0.4,
                metadata=LLMMetadata(
                    yard_id=ctx.yard_id,
                    user_id=ctx.user_id,
                    request_id=ctx.request_id,
                    task_id=f"chat-media-{uuid.uuid4().hex[:12]}",
                    purpose="chat_media",
                    agent_ref="chat_media",
                ),
            ),
        )
        usage = {
            "input_tokens": int(getattr(resp, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(resp, "output_tokens", 0) or 0),
            "total_tokens": int(getattr(resp, "total_tokens", 0) or 0),
            "model": getattr(resp, "model", "") or "",
        }
    except Exception as e:  # noqa: BLE001 - 规格整理失败不阻断出图
        log.warning("媒体规格整理失败，使用兜底模板：%s", e)
        return spec, None

    match = _JSON_RE.search(resp.content or "")
    if not match:
        return spec, usage
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return spec, usage
    if not isinstance(data, dict):
        return spec, usage

    if intent.kind == KIND_IMAGE:
        for key in ("image_prompt", "title", "subtitle", "footer", "negative_prompt"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                spec[key] = value.strip()[:600]
        size = str(data.get("size") or "")
        if size in _IMAGE_SIZES:
            spec["size"] = size
        try:
            spec["n"] = max(1, min(4, int(data.get("n") or intent.count)))
        except (TypeError, ValueError):
            spec["n"] = intent.count
        if not spec.get("title"):
            spec["title"] = (intent.subject or "宣传海报")[:20]
    else:
        for key in ("video_prompt",):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                spec[key] = value.strip()[:800]
        try:
            duration = int(data.get("duration_seconds") or intent.duration_seconds)
            spec["duration_seconds"] = max(1, min(30, duration))
        except (TypeError, ValueError):
            pass
        size = str(data.get("size") or "")
        if size in {"1280x720", "720x1280"}:
            spec["size"] = size
    return spec, usage


def choose_executor(
    db: Session,
    ctx: YardContext,
    kind: str,
    *,
    binding: tuple[str, str] | None,
    target_code: str | None,
) -> tuple[YardAgent | None, str]:
    """按「锁定/点名 → 指挥官 → 任意已发布且有该工具的助手」选执行者。"""
    tool = media_service.IMAGE_TOOL if kind == KIND_IMAGE else media_service.VIDEO_TOOL
    codes: list[str] = []
    if binding and binding[0] == "agent":
        codes.append(binding[1])
    if target_code:
        codes.append(target_code)
    for code in codes:
        agent = declarative.get_published_by_code(
            db, yard_id=ctx.yard_id, agent_code=code, user_id=ctx.user_id
        )
        if agent is not None and tool in (agent.allowed_tools or []):
            return agent, "bound"

    commander = declarative.get_published_by_code(
        db, yard_id=ctx.yard_id, agent_code="commander", user_id=ctx.user_id
    )
    if commander is not None and tool in (commander.allowed_tools or []):
        return commander, "commander"

    rows = db.scalars(
        select(YardAgent)
        .where(YardAgent.yard_id == ctx.yard_id, YardAgent.status == STATUS_PUBLISHED)
        .limit(100)
    ).all()
    for agent in rows:
        if tool not in (agent.allowed_tools or []):
            continue
        if agent.visibility != VISIBILITY_PUBLIC and agent.created_by != ctx.user_id:
            continue
        return agent, "agent"
    return None, ""


def _text_outcome(
    text: str,
    *,
    reason: str,
    choice: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "content": text,
        "routed_by": "local",
        "choice": choice or {"kind": "agent", "id": "", "name": "媒体助手"},
        "reason": reason,
        "payload": None,
        "citations": [],
        "token_usage": usage,
    }


def _notice_text(notice: str) -> str:
    return f"（{notice}）" if notice else ""


async def run_turn(
    db: Session,
    *,
    ctx: YardContext,
    session_id: str,
    content: str,
    intent: MediaIntent,
    binding: tuple[str, str] | None,
    target_code: str | None,
    on_step: Any | None = None,
) -> dict[str, Any]:
    """媒体意图的一轮执行；失败返回可操作的中文说明，不抛 500。"""
    cfg = media_service.settings_for(db, ctx.yard_id)
    group = cfg[intent.kind]

    def step(detail: str, status: str = _STEP_RUNNING) -> None:
        if on_step is not None:
            on_step(_PHASE_GENERATING, detail, status)

    agent, executor_kind = choose_executor(
        db, ctx, intent.kind, binding=binding, target_code=target_code
    )
    executor_is_commander = executor_kind == "commander"
    choice = {
        "kind": "agent",
        "id": str(getattr(agent, "id", "") or ""),
        "name": str(getattr(agent, "name", "") or "媒体助手"),
    }

    if not group.get("enabled"):
        label = "图片" if intent.kind == KIND_IMAGE else "视频"
        return _text_outcome(
            f"本小院的对话内{label}生成已关闭。管理员可在「系统设置 → 对话内媒体」开启。",
            reason="媒体能力已关闭",
            choice=choice,
        )

    if intent.kind == KIND_IMAGE:
        if not media_service.is_admin(ctx) and not group.get("member_access"):
            return _text_outcome(
                "对话内图片生成目前只对管理员开放。请联系小院管理员在"
                "「系统设置 → 对话内媒体」打开「成员可用图片」。",
                reason="图片生成未对成员开放",
                choice=choice,
            )
    else:
        if not media_service.video_authorized(
            ctx, cfg, executor_is_commander=executor_is_commander
        ):
            return _text_outcome(
                "视频生成默认只对指挥官、绑定了视频生成技能的助手和小院管理员开放。"
                "需要成员也能用的话，请管理员在「系统设置 → 对话内媒体」打开「成员可用视频」"
                "（会按每人每日额度计费）。",
                reason="视频生成未授权",
                choice=choice,
            )

    if intent.kind == KIND_IMAGE:
        per_request = max(1, int(group.get("max_per_request") or 4))
        intent = replace(intent, count=max(1, min(per_request, intent.count)))

    try:
        media_service.enforce_quota(db, ctx, intent.kind, cfg, count=intent.count)
    except AppError as e:
        return _text_outcome(e.message, reason="媒体额度不足", choice=choice)

    step("正在整理画面需求")
    spec, usage = await build_spec(db, ctx, intent, content)

    if intent.kind == KIND_IMAGE:
        spec["n"] = max(1, min(intent.count, int(spec.get("n") or intent.count)))
        step("正在生成图片" + ("（底图完成后由系统叠加中文标题）" if intent.poster else ""))
        try:
            result = await media_service.generate_image(
                db,
                ctx,
                prompt=content,
                spec=spec,
                agent_code=str(getattr(agent, "agent_code", "") or ""),
            )
        except AppError as e:
            return _text_outcome(e.message, reason="图片生成失败", choice=choice, usage=usage)
        assets = result["assets"]
        notice = str(result.get("notice") or "")
        step(f"图片已生成（{len(assets)} 张）", _STEP_DONE)
        if result.get("fallback"):
            text = f"还没有可用的图像供应商，已用本地模板出一张示意图。{_notice_text(notice)}"
        else:
            text = f"海报已生成。{_notice_text(notice)}点击卡片可放大预览或下载原图。"
            if intent.poster and result.get("overlaid"):
                text = "海报已生成：AI 底图 + 系统叠加中文标题。点击卡片可预览或下载原图。"
        payload = media_service.card_payload(assets, prompt=content, notice=notice)
        return {
            "content": text,
            "routed_by": "local",
            "choice": choice,
            "reason": "对话直办：图片生成",
            "payload": payload,
            "citations": [],
            "token_usage": usage,
        }

    # 视频
    budget = media_service.budget_seconds(cfg, ctx)
    spec["duration_seconds"] = min(int(spec.get("duration_seconds") or 5), budget)
    step(f"正在提交视频任务（{spec['duration_seconds']} 秒）")
    retention = int(group.get("retention_days") or 30)
    try:
        result = await media_service.submit_video(
            db,
            ctx,
            prompt=content,
            spec=spec,
            agent_code=str(getattr(agent, "agent_code", "") or ""),
            authorized=True,
            retention_days=retention,
            notice="",
        )
    except AppError as e:
        return _text_outcome(e.message, reason="视频提交失败", choice=choice, usage=usage)
    assets = result["assets"]
    accepted = bool(result.get("accepted"))
    step(
        "视频任务已提交，等待上游出片" if accepted else "视频已生成",
        _STEP_DONE,
    )
    video_notice = str((assets[0].meta_json or {}).get("notice") or "")
    text = (
        f"视频任务已提交（{spec['duration_seconds']} 秒），通常 1–3 分钟。"
        "完成后这张卡片会自动变成可播放、可下载的成品；期间可以继续聊别的。"
        if accepted
        else f"视频已生成。点击卡片可播放或下载 MP4。{_notice_text(video_notice)}"
    )
    payload = media_service.card_payload(assets, prompt=content, notice=video_notice)
    return {
        "content": text,
        "routed_by": "local",
        "choice": choice,
        "reason": "对话直办：视频生成",
        "payload": payload,
        "citations": [],
        "token_usage": usage,
    }


def pending_asset_ids(payload: dict[str, Any] | None) -> list[str]:
    """从媒体卡片里取出需要绑定消息/轮询的资产 id。"""
    if not isinstance(payload, dict) or payload.get("type") != "media":
        return []
    ids: list[str] = []
    for item in payload.get("assets") or []:
        if isinstance(item, dict) and item.get("id"):
            ids.append(str(item["id"]))
    return ids


def needs_poll(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict) or payload.get("type") != "media":
        return False
    return any(
        isinstance(item, dict) and item.get("status") not in {STATUS_READY, "failed"}
        for item in payload.get("assets") or []
    )


__all__ = [
    "IMAGE_NOUNS",
    "VIDEO_NOUNS",
    "MediaIntent",
    "build_spec",
    "choose_executor",
    "detect",
    "needs_poll",
    "pending_asset_ids",
    "run_turn",
]
