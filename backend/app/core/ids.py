"""ID 与短哈希摘要生成公共工具。"""

import hashlib
import uuid


def new_id() -> str:
    return str(uuid.uuid4())


def sha256_12(text: str) -> str:
    """生成 UTF-8 文本的 SHA-256 前 12 位十六进制摘要（用于机器人消息账本等）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def sha1_12(text: str) -> str:
    """生成 UTF-8 文本的 SHA-1 前 12 位十六进制摘要（用于自进化/工作流指纹等）。"""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
