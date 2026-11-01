"""系统运行日志读取：脱敏、尾部截取、级别/关键词过滤。

日志来源：
- ``app``：应用自身写入的 ``<data_dir>/logs/app.log``（含 uvicorn 访问日志）；
- ``engine``：桌面 sidecar 的 stdout 落盘 ``<data_dir>/logs/engine.log``。

只在服务端读尾部窗口（默认 4MB），避免把历史大文件整份读进内存；
返回内容一律先脱敏（密钥/令牌/Bearer），再交给前端展示或下载。
"""

from __future__ import annotations

import re
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.logging import log_paths

VALID_SOURCES = ("app", "engine")
VALID_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
#: 单次扫描的最大尾部字节数
MAX_SCAN_BYTES = 4 * 1024 * 1024
#: 下载时最多导出的字节数
MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024
#: 单行最大长度（超长第三方堆栈截断）
MAX_LINE_CHARS = 4000

_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d{3})?)\s+"
    r"(?P<level>[A-Z]+)\s+"
    r"\[(?P<logger>[^\]]*)\]\s+"
    r"\[(?P<request_id>[^\]]*)\]\s?"
    r"(?P<message>.*)$"
)
_UVICORN_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s+"
    r"(?P<level>[A-Z]+):\s+(?P<message>.*)$"
)

_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;\"'}]+"),
        r"\1***",
    ),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}"), r"\1***"),
    (
        re.compile(
            r"(?i)((?:api[_-]?key|secret|token|password|passwd|access[_-]?key)"
            r"\s*[:=]\s*[\"']?)[^\s,;\"'}]+"
        ),
        r"\1***",
    ),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), "sk-***"),
    (re.compile(r"\b(ghp|gho|ghs|ghu)_[A-Za-z0-9]{16,}"), r"\1_***"),
)


def redact(text: str) -> str:
    """脱敏：密钥、令牌、Authorization/Bearer、GitHub token。"""
    out = text or ""
    for pattern, repl in _REDACTIONS:
        out = pattern.sub(repl, out)
    return out


def _source_path(source: str) -> Path:
    if source not in VALID_SOURCES:
        raise ValueError(f"未知日志来源：{source}")
    return log_paths()[source]


def _read_tail(path: Path, max_bytes: int) -> tuple[str, bool]:
    """读文件尾部；返回 (文本, 是否因窗口截断)。"""
    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > max_bytes:
            fh.seek(size - max_bytes)
            raw = fh.read()
            # 丢掉第一条不完整行
            _, _, raw = raw.partition(b"\n")
            return raw.decode("utf-8", errors="replace"), True
        return fh.read().decode("utf-8", errors="replace"), False


def _parse_line(line: str) -> dict[str, Any] | None:
    match = _LINE_RE.match(line)
    if match:
        return {
            "ts": match.group("ts"),
            "level": match.group("level").upper(),
            "logger": match.group("logger"),
            "request_id": match.group("request_id"),
            "message": match.group("message"),
        }
    match = _UVICORN_RE.match(line)
    if match:
        return {
            "ts": match.group("ts"),
            "level": match.group("level").upper(),
            "logger": "uvicorn",
            "request_id": "",
            "message": match.group("message"),
        }
    return None


def _parse_lines(text: str) -> list[dict[str, Any]]:
    """按行解析；无法解析的续行（堆栈等）并入上一条消息。"""
    items: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        parsed = _parse_line(line)
        if parsed is not None:
            parsed["message"] = parsed["message"][:MAX_LINE_CHARS]
            items.append(parsed)
        elif items and line.strip():
            items[-1]["message"] = (items[-1]["message"] + "\n" + line)[:MAX_LINE_CHARS]
    return items


def _matches(item: dict[str, Any], *, level: str | None, keyword: str | None) -> bool:
    if level:
        try:
            threshold = VALID_LEVELS.index(level.upper())
        except ValueError:
            threshold = 0
        try:
            current = VALID_LEVELS.index(str(item.get("level") or "INFO"))
        except ValueError:
            current = 1
        if current < threshold:
            return False
    if keyword:
        haystack = f"{item.get('logger')} {item.get('message')} {item.get('request_id')}".lower()
        if keyword.lower() not in haystack:
            return False
    return True


def _iso_mtime(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()
    except OSError:
        return None


def read_logs(
    *,
    source: str = "app",
    level: str | None = None,
    keyword: str | None = None,
    limit: int = 300,
    offset: int = 0,
) -> dict[str, Any]:
    """读取并过滤日志；返回 ``{items, meta}``，items 按时间新→旧。"""
    path = _source_path(source)
    sources = []
    for name, candidate in log_paths().items():
        try:
            stat = candidate.stat()
            sources.append(
                {
                    "name": name,
                    "exists": True,
                    "size_bytes": stat.st_size,
                    "mtime": _iso_mtime(candidate),
                }
            )
        except OSError:
            sources.append({"name": name, "exists": False, "size_bytes": 0, "mtime": None})

    meta: dict[str, Any] = {
        "source": source,
        "path": str(path),
        "exists": path.exists(),
        "size_bytes": 0,
        "mtime": None,
        "scanned_bytes": 0,
        "truncated": False,
        "matched": 0,
        "sources": sources,
    }
    if not path.exists():
        return {"items": [], "meta": meta}

    try:
        size = path.stat().st_size
        text, truncated = _read_tail(path, MAX_SCAN_BYTES)
    except OSError:
        return {"items": [], "meta": meta}

    keyword_clean = (keyword or "").strip()[:200] or None
    level_clean = (level or "").strip().upper() or None
    matched: deque[dict[str, Any]] = deque()
    total = 0
    for item in _parse_lines(text):
        if not _matches(item, level=level_clean, keyword=keyword_clean):
            continue
        item["message"] = redact(item["message"])
        matched.append(item)
        total += 1
        if len(matched) > 5000:
            matched.popleft()

    newest_first = list(reversed(matched))
    page = newest_first[offset : offset + limit]
    meta.update(
        {
            "size_bytes": size,
            "mtime": _iso_mtime(path),
            "scanned_bytes": len(text.encode("utf-8", errors="replace")),
            "truncated": truncated,
            "matched": total,
        }
    )
    return {"items": page, "meta": meta}


def export_text(*, source: str = "app") -> tuple[str, str]:
    """导出（脱敏后）的日志尾部，返回 ``(文件名, 文本)``。"""
    path = _source_path(source)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    filename = f"{source}-logs-{stamp}.log"
    if not path.exists():
        return filename, ""
    text, _ = _read_tail(path, MAX_DOWNLOAD_BYTES)
    return filename, redact(text)


__all__ = [
    "MAX_DOWNLOAD_BYTES",
    "MAX_SCAN_BYTES",
    "VALID_LEVELS",
    "VALID_SOURCES",
    "export_text",
    "read_logs",
    "redact",
]
