"""助手卡片首图：上传/取图/清除（复用对话附件的本地存储与文件头嗅探）。

与附件同一条存储管道（``LocalStorageBackend`` 随机键，不落原始文件名），只是
这里不抽取文本、不留元数据表——首图是助手配置的一个可选属性，随助手一起
删除即可，不需要独立的生命周期。

口径：
- 只收图片（PNG/JPG/WEBP），文件头必须与扩展名一致，沿用附件的拒收规则；
- 单图上限 2MB：卡片首图是装饰性小图，不该按 25MB 的附件上限收；
- 取图受可见性约束：private 助手只有创建者与管理员拿得到图，越权 404
  （与 ``declarative.get_agent`` 的"不泄露存在性"口径一致）；
- 出参只给同院可用的取图路径，存储键永不出仓（见 ``declarative.to_out``）。
"""

from __future__ import annotations

import os

from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.agents import declarative
from app.modules.agents.models import VISIBILITY_PRIVATE, YardAgent
from app.modules.chat.attachments import EXT_FAMILY, IMAGE_MIMES, _sniff
from app.modules.knowledge.storage import LocalStorageBackend

#: 单张首图上限（MB）——独立于附件上限，卡片图不该按几十 MB 收
COVER_MAX_MB = 2

log = get_logger(__name__)


def _is_admin(roles: list[str]) -> bool:
    return bool({"platform_admin", "yard_admin"} & set(roles))


def _require_visible(agent: YardAgent, ctx: YardContext) -> None:
    """取图权限：公开助手全院可取；私有助手仅创建者与管理员，越权按 404 处理。"""
    if agent.visibility != VISIBILITY_PRIVATE:
        return
    if _is_admin(ctx.roles) or agent.created_by == ctx.user_id:
        return
    raise AppError("AGENT_NOT_FOUND", f"声明式 Agent 不存在：{agent.id}。", http_status=404)


def set_cover(db: Session, *, ctx: YardContext, agent_id: str, file: UploadFile) -> YardAgent:
    """替换首图：校验 → 落新文件 → 写键 → 删旧文件（顺序保证失败不留孤儿）。"""
    filename = os.path.basename(str(file.filename or "")).strip()[:255]
    ext = os.path.splitext(filename)[1].lower()
    if ext not in IMAGE_MIMES:
        raise AppError(
            "UNSUPPORTED_FILE_TYPE",
            f"首图只支持图片（PNG/JPG/WEBP），收到：{ext or '未知'}。",
            http_status=415,
        )
    data = file.file.read()
    if not data:
        raise AppError("INVALID_REQUEST", "上传的文件为空。", http_status=400)
    if len(data) > COVER_MAX_MB * 1024 * 1024:
        raise AppError(
            "FILE_TOO_LARGE",
            f"首图过大，上限为 {COVER_MAX_MB}MB。",
            http_status=413,
        )
    signature = _sniff(data)
    if signature is None or signature.family != EXT_FAMILY.get(ext):
        raise AppError(
            "UNSUPPORTED_FILE_TYPE",
            "文件内容与扩展名不一致，已拒绝上传。",
            http_status=415,
        )

    agent = declarative.get_agent(db, yard_id=ctx.yard_id, agent_id=agent_id)
    declarative.require_manage_access(agent, user_id=ctx.user_id, roles=ctx.roles)

    storage = LocalStorageBackend()
    old_key = agent.cover_image
    agent.cover_image = storage.save(data, ext)
    agent.cover_mime = IMAGE_MIMES[ext]
    agent.updated_by = ctx.user_id
    if old_key:
        # 旧图删除失败只记日志：新键已生效，不能因清理失败回滚整次上传
        try:
            storage.delete(old_key)
        except Exception as e:  # noqa: BLE001 —— 清理是尽力而为
            log.warning("旧首图删除失败 key=%s: %s", old_key, e)
    db.flush()
    return agent


def read_cover(db: Session, *, ctx: YardContext, agent_id: str) -> tuple[bytes, str]:
    """取首图字节与 MIME；助手未设首图时返回 404（前端据此回落 emoji）。"""
    agent = declarative.get_agent(db, yard_id=ctx.yard_id, agent_id=agent_id)
    _require_visible(agent, ctx)
    if not agent.cover_image:
        raise AppError("AGENT_COVER_MISSING", "该助手尚未设置首图。", http_status=404)
    try:
        with LocalStorageBackend().open(agent.cover_image) as fh:
            data = fh.read()
    except FileNotFoundError as e:
        # 键在文件没了（如手工清理过存储目录）：如实报缺失，让前端回落，不 500
        raise AppError("AGENT_COVER_MISSING", "该助手的首图文件已丢失。", http_status=404) from e
    return data, agent.cover_mime or "application/octet-stream"


def clear_cover(db: Session, *, ctx: YardContext, agent_id: str) -> YardAgent:
    """清除首图并删文件。"""
    agent = declarative.get_agent(db, yard_id=ctx.yard_id, agent_id=agent_id)
    declarative.require_manage_access(agent, user_id=ctx.user_id, roles=ctx.roles)
    if agent.cover_image:
        try:
            LocalStorageBackend().delete(agent.cover_image)
        except Exception as e:  # noqa: BLE001 —— 行已清空，文件残留只是垃圾
            log.warning("首图删除失败 key=%s: %s", agent.cover_image, e)
    agent.cover_image = None
    agent.cover_mime = None
    agent.updated_by = ctx.user_id
    db.flush()
    return agent


__all__ = ["COVER_MAX_MB", "clear_cover", "read_cover", "set_cover"]
