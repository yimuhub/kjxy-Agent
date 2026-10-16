"""文件存储后端。原始文件名永不直接用作服务器路径；使用随机存储键并校验防路径穿越。"""

import re
import uuid
from pathlib import Path
from typing import BinaryIO, Protocol

from app.core.config import settings

_KEY_RE = re.compile(r"^[a-z0-9/._-]+$")


class StorageBackend(Protocol):
    def save(self, data: bytes, ext: str) -> str:
        """保存字节流，返回随机存储键。"""
        ...

    def open(self, key: str) -> BinaryIO:
        """按存储键打开只读二进制流。"""
        ...

    def delete(self, key: str) -> None:
        """删除存储键对应的文件；不存在时静默成功。"""
        ...


class LocalStorageBackend:
    """本地卷存储后端。键形如 ``ab/abcdef1234....pdf``，按前两位分散子目录。"""

    def __init__(self, root: str | Path | None = None) -> None:
        # resolve() 展开符号链接（macOS 的 /var/folders 临时目录实际位于 /private/var）；
        # 不展开会导致 _resolve 的包含校验误报"存储键越界"
        self.root = Path(root or settings.local_storage_path).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        if not _KEY_RE.match(key):
            raise ValueError(f"非法存储键: {key!r}")
        path = (self.root / key).resolve()
        if path != self.root and self.root not in path.parents:
            raise ValueError(f"存储键越界: {key!r}")
        return path

    def save(self, data: bytes, ext: str) -> str:
        ext = ext.lower()
        if not ext.startswith("."):
            ext = f".{ext}" if ext else ""
        name = f"{uuid.uuid4().hex}{ext}"
        key = f"{name[:2]}/{name}"
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def open(self, key: str) -> BinaryIO:
        return self._resolve(key).open("rb")

    def delete(self, key: str) -> None:
        try:
            self._resolve(key).unlink(missing_ok=True)
        except ValueError:
            # 非法键：视为已删除，不抛异常
            pass
