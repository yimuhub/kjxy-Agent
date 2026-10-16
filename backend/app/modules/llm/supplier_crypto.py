"""供应商 API Key 加解密。

- 用 Fernet 对称加密，密钥来自环境变量 SUPPLIER_KEY_SECRET（44 字符 urlsafe base64）。
- 缺失/非法时抛 AppError，提示运维配置（不在此硬中断应用启动，
  而是在使用供应商加密功能时给出明确报错）。
- mask：API 返回一律打码，只露前后各 4 位（sk-****abcd）。
- 明文 key 绝不打日志、绝不返回明文。
"""

import os

from app.core.errors import AppError
from app.core.logging import get_logger

log = get_logger(__name__)

SECRET_ENV = "SUPPLIER_KEY_SECRET"
_SECRET_MISSING_HINT = (
    "缺少 SUPPLIER_KEY_SECRET 环境变量，无法加解密供应商 API Key。"
    '请在 .env 中配置（用 `python -c "from cryptography.fernet import Fernet; '
    'print(Fernet.generate_key().decode())"` 生成）。'
)

_fernet = None  # 延迟初始化，类型为 Fernet（懒加载）


def _load_secret() -> str:
    secret = (os.environ.get(SECRET_ENV) or "").strip()
    if not secret:
        raise AppError("SUPPLIER_KEY_NOT_CONFIGURED", _SECRET_MISSING_HINT, http_status=500)
    return secret


def get_fernet():
    """取 Fernet 实例；密钥缺失或非法时抛带配置提示的 AppError。"""
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        raise AppError(
            "SUPPLIER_CRYPTO_MISSING",
            "缺少 cryptography 依赖，无法加解密供应商 API Key。"
            "请运行 `pip install cryptography` 安装后重启。",
            http_status=500,
        ) from None
    global _fernet
    if _fernet is None:
        secret = _load_secret()
        try:
            _fernet = Fernet(secret.encode("utf-8"))
        except Exception as e:
            raise AppError(
                "SUPPLIER_KEY_INVALID",
                "SUPPLIER_KEY_SECRET 不是合法的 Fernet 密钥（应为 44 字符 urlsafe base64），"
                "请重新生成并配置。",
                http_status=500,
            ) from e
    return _fernet


def reset_fernet_cache() -> None:
    """测试用：清空缓存的 Fernet 实例。"""
    global _fernet
    _fernet = None


def encrypt_api_key(plaintext: str) -> str:
    if not plaintext:
        raise AppError("INVALID_REQUEST", "API Key 不能为空。", http_status=400)
    token = get_fernet().encrypt(plaintext.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_api_key(ciphertext: str) -> str:
    try:
        plain = get_fernet().decrypt(ciphertext.encode("utf-8"))
    except Exception as e:
        # cryptography 未安装时 get_fernet 已抛 AppError；这里只处理解密失败
        if "SUPPLIER_" in str(getattr(e, "code", "")):
            raise
        raise AppError(
            "SUPPLIER_KEY_DECRYPT_FAILED",
            "供应商密钥解密失败（密钥可能已被轮换），请重新录入该供应商的 API Key。",
            http_status=500,
        ) from e
    return plain.decode("utf-8")


def mask_api_key(plaintext: str) -> str:
    """只露前后各 4 位，如 sk-****abcd；过短则整体打码。"""
    key = plaintext or ""
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}****{key[-4:]}"
