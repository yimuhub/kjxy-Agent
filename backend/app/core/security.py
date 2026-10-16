"""密码哈希与 JWT。密钥只从配置读取；日志中永不输出令牌明文。"""

from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from app.core.config import settings

ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def _now() -> datetime:
    return datetime.now(UTC)


def create_access_token(*, user_id: str, tenant_id: str | None, roles: list[str]) -> str:
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "roles": roles,
        "type": "access",
        "exp": _now() + timedelta(minutes=settings.access_token_expire_minutes),
        "iat": _now(),
    }
    return jwt.encode(payload, settings.app_secret_key, algorithm=ALGORITHM)


def create_refresh_token(*, user_id: str, tenant_id: str | None) -> str:
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "type": "refresh",
        "exp": _now() + timedelta(days=settings.refresh_token_expire_days),
        "iat": _now(),
    }
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
