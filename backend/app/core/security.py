"""密码哈希与 JWT。密钥只从配置读取；日志中永不输出令牌明文。"""

from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from app.core.config import settings

ALGORITHM = "HS256"

#: 小院声明的历史键名：tenant→yard 重命名前签发、已下发到浏览器/桌面端的令牌
#: 仍携带 tenant_id；只读新键会把旧令牌误判为"无小院上下文"（静默降权）。
LEGACY_YARD_CLAIM = "tenant_id"


def resolve_yard_id(payload: dict) -> str | None:
    """JWT payload → 小院 id 的唯一入口：先读 yard_id，缺失再回退历史 tenant_id。

    所有 decode_token 消费方（deps / auth refresh / pairing 等）必须经此解析，
    避免任何调用点漏写回退导致角色从 DB 查不到而降为空列表。
    """
    yard_id = payload.get("yard_id")
    if yard_id is None:
        yard_id = payload.get(LEGACY_YARD_CLAIM)
    return yard_id


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def _now() -> datetime:
    return datetime.now(UTC)


def create_access_token(
    *, user_id: str, yard_id: str | None, roles: list[str], token_version: int = 0
) -> str:
    payload = {
        "sub": user_id,
        "yard_id": yard_id,
        "roles": roles,
        "type": "access",
        "ver": token_version,
        "exp": _now() + timedelta(minutes=settings.access_token_expire_minutes),
        "iat": _now(),
    }
    return jwt.encode(payload, settings.app_secret_key, algorithm=ALGORITHM)


def create_refresh_token(
    *, user_id: str, yard_id: str | None, token_version: int = 0, jti: str | None = None
) -> str:
    payload = {
        "sub": user_id,
        "yard_id": yard_id,
        "type": "refresh",
        "ver": token_version,
        "exp": _now() + timedelta(days=settings.refresh_token_expire_days),
        "iat": _now(),
    }
    if jti:
        payload["jti"] = jti
    return jwt.encode(payload, settings.app_secret_key, algorithm=ALGORITHM)


def decode_token(token: str, expected_type: str = "access") -> dict:
    try:
        payload = jwt.decode(token, settings.app_secret_key, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as e:
        from app.core.errors import AppError

        raise AppError("UNAUTHORIZED", "登录已过期，请重新登录。", http_status=401) from e
    except jwt.InvalidTokenError as e:
        from app.core.errors import AppError

        raise AppError("UNAUTHORIZED", "身份凭证无效。", http_status=401) from e
    if payload.get("type") != expected_type:
        from app.core.errors import AppError

        raise AppError("UNAUTHORIZED", "身份凭证类型错误。", http_status=401)
    return payload
