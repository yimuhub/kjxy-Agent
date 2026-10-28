"""跨院只读分享服务（v3.12）：创建、列出、撤销与匿名解析。

快照内容一律在服务端生成：
- 报表：按请求参数重新生成，且**隐去操作人姓名**（对外不暴露院内人员）；
- 诊断：只保留作物/品种/生育期/症状/候选/专家结论与免责声明，
  不含地块名、图片、创建人与内部 ID。

安全边界：
- 原始 token 只返回一次，库里只存 SHA-256；
- 撤销/过期/不存在的 token 统一 404，不区分原因（防枚举）；
- 公开读取只递增访问计数，不写审计（避免匿名访问刷审计表）。
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.audit.service import log_audit
from app.modules.farm import diagnosis_service
from app.modules.farm import service as farm_service
from app.modules.farm.schemas import ReportRequest
from app.modules.share.models import SHARE_MAX_DAYS, ShareLink
from app.modules.share.schemas import PublicShare, ShareCreate


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    """SQLite 读回来的时间不带时区：统一按 UTC 解释后再比较。"""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _out(row: ShareLink) -> dict:
    return {
        "share_id": row.id,
        "resource_type": row.resource_type,
        "resource_id": row.resource_id,
        "title": row.title,
        "expires_at": row.expires_at,
        "revoked_at": row.revoked_at,
        "view_count": row.view_count,
        "last_viewed_at": row.last_viewed_at,
        "created_at": row.created_at,
    }


def _report_snapshot(db: Session, yard_id: str, req: ShareCreate) -> tuple[str, str, dict]:
    report = farm_service.build_report(
        db,
        yard_id,
        ReportRequest(
            kind=req.report_kind or "log",
            start=req.start,  # type: ignore[arg-type]  # 已由 schema 保证
            end=req.end,  # type: ignore[arg-type]
            plot_id=req.plot_id,
        ),
        redact_operators=True,
    )
    content = (
        report["content_md"].rstrip()
        + "\n\n> 本页为只读快照，已隐去操作人姓名；数据以院内台账为准。\n"
    )
    return (
        f"农事报表分享 · {report['start']} 至 {report['end']}",
        report["title"],
        {
            "content_md": content,
            "records": report["records"],
            "plots": report["plots"],
            "kind": report["kind"],
        },
    )


def _diagnosis_snapshot(db: Session, yard_id: str, diagnosis_id: str) -> tuple[str, str, dict]:
    data = diagnosis_service.get_diagnosis(db, yard_id, diagnosis_id)
    output = data.get("output") or {}
    candidates = output.get("candidates") or []
    lines = [
        f"# 作物诊断摘要 · {data['crop']}",
        "",
        f"- 生长阶段：{data.get('growth_stage') or '未填写'}",
        f"- 品种：{data.get('variety') or '未填写'}",
        f"- 状态：{data.get('status')}",
        f"- 综合置信度：{round(float(data.get('confidence') or 0) * 100)}%",
        "",
        "## 症状描述",
        "",
        data.get("symptoms") or "（未填写）",
        "",
        "## 候选结论",
        "",
    ]
    if candidates:
        for item in candidates[:5]:
            name = item.get("name") or "未命名"
            conf = round(float(item.get("confidence") or 0) * 100)
            note = (item.get("notes") or "").strip()
            lines.append(f"- **{name}**（{conf}%）{'：' + note if note else ''}")
    else:
        lines.append("- 暂无候选结论")
    final = (data.get("final_diagnosis") or "").strip()
    expert_note = (data.get("expert_note") or "").strip()
    if final or expert_note:
        lines += ["", "## 专家结论", ""]
        if final:
            lines.append(f"**{final}**")
        if expert_note:
            lines.append(expert_note)
    lines += [
        "",
        "> 本页为只读快照，仅作物与症状信息；地块、图片与人员信息已隐去。",
        "> 诊断结果为辅助判断，涉及用药请以专家复核与登记标签为准。",
    ]
    title = f"作物诊断分享 · {data['crop']}"
    return title, title, {"content_md": "\n".join(lines) + "\n", "status": data.get("status")}


def create_share(db: Session, yard_id: str, user_id: str, req: ShareCreate) -> dict:
    days = min(req.expires_days, SHARE_MAX_DAYS)
    if req.resource_type == "farm_report":
        resource_id = f"report:{req.report_kind}:{req.start}:{req.end}:{req.plot_id or ''}"
        title, snapshot_title, payload = _report_snapshot(db, yard_id, req)
    else:
        assert req.diagnosis_id is not None  # schema 已保证
        resource_id = req.diagnosis_id
        title, snapshot_title, payload = _diagnosis_snapshot(db, yard_id, req.diagnosis_id)

    token = secrets.token_urlsafe(32)
    row = ShareLink(
        yard_id=yard_id,
        created_by=user_id,
        resource_type=req.resource_type,
        resource_id=resource_id[:36],
        title=title[:200],
        token_hash=_hash(token),
        payload_json={**payload, "snapshot_title": snapshot_title},
        expires_at=_utcnow() + timedelta(days=days),
    )
    db.add(row)
    db.flush()
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user_id,
        action="share_link_created",
        resource_type="share_link",
        resource_id=row.id,
        result="success",
        request_id="",
        metadata={"resource_type": req.resource_type, "expires_days": days},
    )
    db.commit()
    return {**_out(row), "token": token, "path": f"/share/{token}"}


def list_shares(db: Session, yard_id: str, *, include_revoked: bool = False) -> list[dict]:
    stmt = select(ShareLink).where(ShareLink.yard_id == yard_id)
    if not include_revoked:
        stmt = stmt.where(ShareLink.revoked_at.is_(None))
    rows = db.scalars(stmt.order_by(ShareLink.created_at.desc())).all()
    return [_out(r) for r in rows]


def revoke_share(db: Session, yard_id: str, share_id: str, user_id: str) -> dict:
    row = db.scalar(select(ShareLink).where(ShareLink.yard_id == yard_id, ShareLink.id == share_id))
    if row is None:
        raise AppError("SHARE_NOT_FOUND", "分享不存在或无权访问。", http_status=404)
    if row.revoked_at is None:
        row.revoked_at = _utcnow()
        log_audit(
            db,
            yard_id=yard_id,
            actor_id=user_id,
            action="share_link_revoked",
            resource_type="share_link",
            resource_id=row.id,
            result="success",
            request_id="",
            metadata={"resource_type": row.resource_type},
        )
        db.commit()
    return _out(row)


def resolve_public(db: Session, token: str) -> dict:
    token = (token or "").strip()
    if not token or len(token) > 200:
        raise AppError("SHARE_NOT_FOUND", "分享不存在或已失效。", http_status=404)
    row = db.scalar(select(ShareLink).where(ShareLink.token_hash == _hash(token)))
    if row is None or row.revoked_at is not None or _as_utc(row.expires_at) <= _utcnow():
        raise AppError("SHARE_NOT_FOUND", "分享不存在或已失效。", http_status=404)
    row.view_count += 1
    row.last_viewed_at = _utcnow()
    db.commit()
    payload = row.payload_json or {}
    return PublicShare(
        title=row.title,
        resource_type=row.resource_type,
        content_md=str(payload.get("content_md") or ""),
        created_at=row.created_at,
        expires_at=row.expires_at,
        view_count=row.view_count,
    ).model_dump(mode="json")


__all__ = [
    "create_share",
    "list_shares",
    "resolve_public",
    "revoke_share",
]
