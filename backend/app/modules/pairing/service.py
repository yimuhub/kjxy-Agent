"""配对服务：一次性配对码 + 设备令牌签发/管理。

安全口径：
- 配对码 ``secrets.token_urlsafe(16)``，库内只存 sha256；TTL 与并发未用码
  上限由 settings（``pairing_*``）控制，超限直接拒绝（防码堆积被慢爆破）；
- 交换用**原子 UPDATE**（``WHERE used_at IS NULL`` + rowcount 判定）标记
  已用：并发重复交换只有一个成功，SQLite/PostgreSQL 同口径；
- 无效/过期/已用三种失败共用同一错误（UNAUTHORIZED 401），不区分原因，
  防通过错误差异枚举有效码；
- 设备令牌：JWT（复用 app/core/security 的密钥与算法体系），claims 携带
  ``kind=pair`` 且 ``type=pair``，有效期 ``pairing_device_token_expire_days``
  天。**边界诚实说明**：现有 ``get_current_user`` 走
  ``decode_token(expected_type="access")``，对 ``type`` 做精确匹配——pair 令牌
  会被所有常规鉴权路由拒绝（401），当前只应由配对模块自己的
  ``verify_pair_token`` 消费；后续 wave 如需设备令牌访问常规 API，必须显式
  扩依赖，而不是放宽这里的 type 匹配；
- 审计：建码/交换/吊销各记一条；审计 metadata 绝不记配对码、哈希或令牌。
"""

import hashlib
import secrets
import socket
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.core.security import ALGORITHM, decode_token
from app.modules.audit.service import log_audit
from app.modules.pairing.models import PairCode, PairDevice


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc(dt: datetime) -> datetime:
    """SQLite 读回的 datetime 无 tzinfo：统一补 UTC 再比较/序列化。"""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _sha256(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _iso(dt: datetime | None) -> str:
    if dt is None:
        return ""
    return _as_utc(dt).isoformat()


# ---------------------------------------------------------------------------
# 局域网地址探测（pair_url 的 host 部分）
# ---------------------------------------------------------------------------


def detect_local_ipv4() -> str | None:
    """UDP connect 探测局域网源 IP：**不发任何包**，只查本机路由表选源地址。

    无可用出网路由（离线容器等）返回 None，调用方回退请求 Host 头。
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return str(sock.getsockname()[0])
    except OSError:
        return None
    finally:
        sock.close()


def build_pair_url(request: Request, code: str) -> str:
    """拼装 ``kjxy://pair?host=<IPv4>:<port>&code=<码>``。

    host 优先取局域网源 IP（手机能扫到才有意义），端口取
    ``settings.pairing_advertise_port``（必须与 API 实际对外端口一致）；
    探测失败回退请求 Host 头（Host 自带端口时沿用，否则补 advertise 端口）。
    """
    ip = detect_local_ipv4()
    if ip:
        host_part = f"{ip}:{settings.pairing_advertise_port}"
    else:
        host_header = (request.headers.get("host") or "").strip()
        if not host_header:
            host_part = f"localhost:{settings.pairing_advertise_port}"
        elif ":" in host_header and host_header.rsplit(":", 1)[1].isdigit():
            # Host 头自带端口：沿用（回退场景说明服务就在该地址上被访问）
            host_part = host_header
        else:
            host_part = f"{host_header}:{settings.pairing_advertise_port}"
    return f"kjxy://pair?host={host_part}&code={code}"


# ---------------------------------------------------------------------------
# 配对码：生成 / 交换
# ---------------------------------------------------------------------------


def create_pair_code(
    db: Session, *, tenant_id: str, actor_id: str, request_id: str
) -> tuple[str, PairCode]:
    """生成一次性配对码；同租户未用码超过上限直接拒绝（RATE_LIMITED 429）。"""
    now = _utcnow()
    # 配额口径：未用且未过期的码数（已用/已过期的码不占额度）
    active_count = _active_code_count(db, tenant_id=tenant_id, now=now)
    if active_count >= settings.pairing_max_active_codes:
        raise AppError(
            "RATE_LIMITED",
            f"未使用的配对码已达上限（{settings.pairing_max_active_codes} 个），"
            "请先完成配对或等待过期。",
            http_status=429,
        )
    code = secrets.token_urlsafe(16)
    row = PairCode(
        tenant_id=tenant_id,
        code_hash=_sha256(code),
        expires_at=now + timedelta(seconds=settings.pairing_code_ttl_seconds),
    )
    db.add(row)
    db.flush()
    # 审计只记动作本身：配对码/哈希绝不入审计
    log_audit(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="pairing_code_create",
        resource_type="pair_code",
        resource_id=row.id,
        result="success",
        request_id=request_id,
        metadata={"ttl_seconds": settings.pairing_code_ttl_seconds},
    )
    db.commit()
    return code, row


def _active_code_count(db: Session, *, tenant_id: str, now: datetime) -> int:
    rows = db.scalars(
        select(PairCode.id).where(
            PairCode.tenant_id == tenant_id,
            PairCode.used_at.is_(None),
            PairCode.expires_at > now,
        )
    ).all()
    return len(rows)


def exchange(
    db: Session, *, code: str, device_name: str, request_id: str
) -> tuple[PairDevice, str]:
    """配对码交换设备令牌：校哈希 → 原子占用 → 签发 30 天 JWT + 建档。

    无效/过期/已用统一 UNAUTHORIZED 401（同一文案），不给枚举者可乘之机。
    """
    now = _utcnow()
    row = db.scalars(select(PairCode).where(PairCode.code_hash == _sha256(code))).first()
    invalid = AppError("UNAUTHORIZED", "配对码无效或已过期。", http_status=401)
    if row is None:
        raise invalid
    if _as_utc(row.expires_at) <= now:
        raise invalid
    # 原子占用：并发重复交换只有 rowcount==1 的赢家能继续
    result = db.execute(
        update(PairCode)
        .where(PairCode.id == row.id, PairCode.used_at.is_(None))
        .values(used_at=now)
    )
    if result.rowcount != 1:  # type: ignore[attr-defined]
        raise invalid
    device = PairDevice(
        tenant_id=row.tenant_id,
        created_by=None,
        name=device_name.strip() or "未命名设备",
        # 先占位建形拿到 id，再以真实 device_id 签发令牌回填哈希
        token_hash="pending",
        expires_at=now + timedelta(days=settings.pairing_device_token_expire_days),
    )
    db.add(device)
    db.flush()
    token = create_pair_token(device_id=device.id, tenant_id=row.tenant_id)
    device.token_hash = _sha256(token)
    log_audit(
        db,
        tenant_id=row.tenant_id,
        actor_id=None,
        action="pairing_device_pair",
        resource_type="pair_device",
        resource_id=device.id,
        result="success",
        request_id=request_id,
        metadata={"device_name": device.name},
    )
    db.commit()
    return device, token


# ---------------------------------------------------------------------------
# 设备令牌（JWT：复用 app/core/security 的密钥与算法体系）
# ---------------------------------------------------------------------------


def create_pair_token(*, device_id: str, tenant_id: str) -> str:
    """签发设备令牌：``kind=pair`` + ``type=pair``，30 天（settings 可调）。

    ``type`` 必须与现有 ``decode_token(expected_type="access")`` 明确区分：
    常规鉴权依赖精确匹配 ``type == "access"``，pair 令牌天然进不了任何
    需登录接口（防越权），只能由 ``verify_pair_token`` 消费。
    """
    now = _utcnow()
    payload = {
        "sub": device_id,
        "tenant_id": tenant_id,
        "device_id": device_id,
        "kind": "pair",
        "type": "pair",
        "exp": now + timedelta(days=settings.pairing_device_token_expire_days),
        "iat": now,
    }
    return jwt.encode(payload, settings.app_secret_key, algorithm=ALGORITHM)


def verify_pair_token(token: str) -> dict:
    """校验设备令牌（预期 type=pair）：过期/类型错误均 UNAUTHORIZED 401。"""
    payload = decode_token(token, expected_type="pair")
    if payload.get("kind") != "pair":
        raise AppError("UNAUTHORIZED", "身份凭证类型错误。", http_status=401)
    return payload


# ---------------------------------------------------------------------------
# 设备台账
# ---------------------------------------------------------------------------


def list_devices(db: Session, tenant_id: str) -> list[PairDevice]:
    return list(
        db.scalars(
            select(PairDevice)
            .where(PairDevice.tenant_id == tenant_id)
            .order_by(PairDevice.created_at.desc())
        )
    )


def get_device_or_404(db: Session, tenant_id: str, device_id: str) -> PairDevice:
    row = db.scalars(
        select(PairDevice).where(PairDevice.id == device_id, PairDevice.tenant_id == tenant_id)
    ).first()
    if row is None:
        raise AppError("NOT_FOUND", "设备不存在或无权访问。", http_status=404)
    return row


def revoke_device(
    db: Session, *, tenant_id: str, device_id: str, actor_id: str, request_id: str
) -> PairDevice:
    """吊销设备（幂等）：打标 revoked_at；审计只记设备 id 与名称。"""
    device = get_device_or_404(db, tenant_id, device_id)
    if device.revoked_at is None:
        device.revoked_at = _utcnow()
        log_audit(
            db,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="pairing_device_revoke",
            resource_type="pair_device",
            resource_id=device.id,
            result="success",
            request_id=request_id,
            metadata={"device_name": device.name},
        )
        db.commit()
    return device


def device_to_out(d: PairDevice) -> dict:
    """设备出参：绝不回显 token_hash（哈希也不行，设备识别用 id）。"""
    return {
        "id": d.id,
        "name": d.name,
        "created_at": _iso(d.created_at),
        "expires_at": _iso(d.expires_at),
        "last_seen_at": _iso(d.last_seen_at) if d.last_seen_at else None,
        "revoked_at": _iso(d.revoked_at) if d.revoked_at else None,
    }
