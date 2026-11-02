"""公网网页抓取：strict_public_only SSRF 防护 + 轻量正文提取。

与 ``app.core.net_guard`` 的区别：本模块只服务 ``web.fetch``，**任何**非公网地址
（loopback / 私有段 / 链路本地 / 保留 / 组播 / 未指定 / 云元数据）一律拒绝，
且每次重定向都重新解析并校验。校验通过后会把建连解析钉在这批地址上（S-2：
DNS 重绑定需要在校验与建连之间换答案，换了就失败关闭）。不引入 bs4，正则剥离脚本与标签。
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import ipaddress
import re
import socket
from html import unescape
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from app.core.errors import AppError
from app.core.logging import get_logger

log = get_logger(__name__)

MAX_REDIRECTS = 3
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 8.0
MAX_CHARS_LIMIT = 6000
ALLOWED_CONTENT_TYPES = ("text/html", "text/plain")
REDIRECT_STATUSES = {301, 302, 303, 307, 308}

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_SCRIPT_RE = re.compile(
    r"<(script|style|noscript|template|svg)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

_transport_override: httpx.AsyncBaseTransport | None = None


def set_test_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """测试专用：注入/清除 MockTransport（杜绝真实出网）。"""
    global _transport_override
    _transport_override = transport


def _blocked(message: str) -> AppError:
    return AppError("WEB_FETCH_BLOCKED", message, http_status=400)


def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


_REAL_GETADDRINFO = socket.getaddrinfo

#: 抓取期间的 DNS 钉（host, port, 已校验地址集合）；None 时解析直通，不影响其他调用方
_DNSPin = tuple[str, int, frozenset[str]]
_PINNED_DNS: contextvars.ContextVar[_DNSPin | None] = contextvars.ContextVar(
    "ky_web_fetch_dns_pin", default=None
)


def _host_text(host: Any) -> str:
    """asyncio 会把 host 以 bytes 传给解析器，端口也可能是服务名。"""
    return host.decode() if isinstance(host, bytes) else str(host)


def _pinned_getaddrinfo(host: Any, port: Any, *args: Any, **kwargs: Any) -> list:
    """命中当前抓取目标时，只允许连到刚才校验过的那批地址。

    校验与实际建连原本是两次独立解析：TTL=0 的域名可以在两次解析之间把 A 记录从公网
    切到内网（DNS 重绑定），把内网服务正文回传给任意登录用户。交集为空说明答案已经
    换了一批——失败关闭，而不是照着新答案连。
    """
    pin = _PINNED_DNS.get()
    try:
        wanted_port = int(port)
    except (TypeError, ValueError):
        wanted_port = -1
    if pin is None or pin[0] != _host_text(host) or pin[1] != wanted_port:
        return _REAL_GETADDRINFO(host, port, *args, **kwargs)
    answers = _REAL_GETADDRINFO(host, port, *args, **kwargs)
    kept = [item for item in answers if str(item[4][0]).split("%")[0] in pin[2]]
    if not kept:
        raise OSError("DNS 解析结果与校验时不一致，已中止连接")
    return kept


if socket.getaddrinfo is not _pinned_getaddrinfo:
    socket.getaddrinfo = _pinned_getaddrinfo


@contextlib.contextmanager
def _dns_pin(host: str, port: int, ips: frozenset[str]):
    token = _PINNED_DNS.set((host, port, ips))
    try:
        yield
    finally:
        _PINNED_DNS.reset(token)


def _resolve_ips(host: str, port: int) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    try:
        infos = _REAL_GETADDRINFO(host, port, type=socket.SOCK_STREAM)
    except OSError as e:
        raise _blocked("无法解析目标域名。") from e
    ips = []
    for info in infos:
        ip_str = str(info[4][0]).split("%")[0]
        try:
            ips.append(ipaddress.ip_address(ip_str))
        except ValueError as e:
            raise _blocked("目标地址解析结果异常。") from e
    if not ips:
        raise _blocked("无法解析目标域名。")
    return ips


async def _check_url(url: str) -> tuple[str, int, frozenset[str]]:
    """scheme + 主机名 + 全量 IP 校验；返回可用于钉住建连的 (host, port, 已校验地址)。"""
    try:
        parsed = urlparse(url)
    except ValueError as e:
        raise _blocked("目标地址格式不合法。") from e
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise _blocked("仅允许抓取公网 HTTP(S) 地址。")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    ips = await asyncio.to_thread(_resolve_ips, parsed.hostname, port)
    for ip in ips:
        if not _is_public(ip):
            log.warning("web.fetch blocked: %s -> %s", parsed.hostname, ip)
            raise _blocked("禁止访问内网、本机或云元数据地址。")
    return parsed.hostname, port, frozenset(str(ip) for ip in ips)


def _collapse(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def _extract_title(html: str) -> str:
    match = _TITLE_RE.search(html)
    return _collapse(unescape(match.group(1))) if match else ""


def _extract_text(html: str) -> str:
    cleaned = _COMMENT_RE.sub(" ", html)
    cleaned = _SCRIPT_RE.sub(" ", cleaned)
    cleaned = _TAG_RE.sub(" ", cleaned)
    return _collapse(unescape(cleaned))


def _charset(content_type: str) -> str:
    match = re.search(r"charset=([\w-]+)", content_type or "", re.IGNORECASE)
    if match:
        candidate = match.group(1).strip().lower()
        # 只接受常见安全编码，其余回落 utf-8
        if candidate in {"utf-8", "utf8", "gbk", "gb2312", "gb18030", "big5", "latin-1"}:
            return "gb18030" if candidate == "gb2312" else candidate
    return "utf-8"


def _decode(raw: bytes, content_type: str) -> str:
    try:
        return raw.decode(_charset(content_type), errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _clamp_chars(max_chars: int) -> int:
    try:
        value = int(max_chars)
    except (TypeError, ValueError):
        value = MAX_CHARS_LIMIT
    return max(1, min(value, MAX_CHARS_LIMIT))


async def _read_body(response: httpx.Response) -> bytes:
    declared = response.headers.get("content-length")
    if declared:
        try:
            if int(declared) > MAX_BYTES:
                raise _blocked("网页内容超过 2MB 上限。")
        except ValueError:
            pass
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > MAX_BYTES:
            raise _blocked("网页内容超过 2MB 上限。")
        chunks.append(chunk)
    return b"".join(chunks)


async def fetch_public_url(url: str, max_chars: int = MAX_CHARS_LIMIT) -> dict[str, Any]:
    """抓取公网网页正文；被拦截抛 WEB_FETCH_BLOCKED(400)。"""
    target = str(url or "").strip()
    limit = _clamp_chars(max_chars)
    headers = {
        "User-Agent": "TechYardBot/1.0 (+web.fetch)",
        "Accept": "text/html,text/plain;q=0.9",
    }

    async with httpx.AsyncClient(
        timeout=TIMEOUT_SECONDS,
        transport=_transport_override,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        for attempt in range(MAX_REDIRECTS + 1):
            host, port, ips = await _check_url(target)
            with _dns_pin(host, port, ips):
                try:
                    request = client.build_request("GET", target, headers=headers)
                    response = await client.send(request, stream=True)
                except httpx.HTTPError as e:
                    raise _blocked("目标站点不可达或请求失败。") from e
                try:
                    if response.status_code in REDIRECT_STATUSES:
                        location = response.headers.get("location")
                        if not location:
                            raise _blocked("重定向缺少目标地址。")
                        if attempt >= MAX_REDIRECTS:
                            raise _blocked("重定向次数过多。")
                        target = urljoin(target, location)
                        continue
                    if response.status_code >= 400:
                        raise _blocked(f"目标站点返回 {response.status_code}。")

                    content_type = response.headers.get("content-type", "")
                    base_type = content_type.split(";")[0].strip().lower()
                    if base_type not in ALLOWED_CONTENT_TYPES:
                        raise _blocked("仅支持抓取 HTML 或纯文本页面。")
                    raw = await _read_body(response)
                finally:
                    await response.aclose()
            break
        else:  # pragma: no cover - 循环内必然 break 或抛错
            raise _blocked("重定向次数过多。")

    body = _decode(raw, content_type)
    if base_type == "text/plain":
        title, text = "", _collapse(body)
    else:
        title, text = _extract_title(body), _extract_text(body)
    return {
        "title": title,
        "text": text[:limit],
        "url": target,
        "content_type": base_type,
    }


__all__ = ["MAX_BYTES", "MAX_REDIRECTS", "fetch_public_url", "set_test_transport"]
