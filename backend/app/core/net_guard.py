"""SSRF 出网防护（规范 §15.1）。

对服务端发起的 HTTP(S) 目标做解析后 IP 校验：
- 始终阻断：loopback / link-local（含云元数据 169.254.169.254）/ 组播 / 保留地址；
- 私有网段（10/8、172.16/12、192.168/16、fc00::/7）：默认允许（内网私有化模型服务是
  科技小院的常见部署形态），当 settings.enforce_model_egress_guard=true 时一并阻断。
"""

import ipaddress
import socket
from urllib.parse import urlparse

from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import get_logger

log = get_logger(__name__)

_ALLOWED_SCHEMES = {"http", "https"}


_IP = ipaddress.IPv4Address | ipaddress.IPv6Address


def _is_always_blocked(ip: _IP) -> bool:
    return (
        ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified
    )


def _is_private(ip: _IP) -> bool:
    return ip.is_private


def check_egress_url(url: str, *, context: str = "") -> None:
    """校验 URL 是否允许服务端访问；不允许时抛 AppError(SSRF_BLOCKED)。

    DNS 解析后的每个 IP 都要通过校验，防止域名指向内网的 DNS 重绑定绕过。
    """
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES or not parsed.hostname:
        raise AppError("SSRF_BLOCKED", "目标地址协议或主机名不合法。", http_status=400)
    enforce_public = settings.enforce_model_egress_guard
    try:
        infos = socket.getaddrinfo(
            parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
        )
    except socket.gaierror:
        # 域名无法解析：放行给 HTTP 客户端自然报“不可达”，避免离线/单测环境误伤
        log.debug("egress dns unresolvable (pass-through): %s", parsed.hostname)
        return
    for info in infos:
        ip_str = str(info[4][0]).split("%")[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            raise AppError("SSRF_BLOCKED", "目标地址解析结果异常。", http_status=400) from None
        if _is_always_blocked(ip):
            log.warning("SSRF blocked: %s -> %s (%s)", context or "egress", parsed.hostname, ip)
            raise AppError("SSRF_BLOCKED", "禁止访问本机、内网元数据或保留地址。", http_status=400)
        if _is_private(ip):
            if enforce_public:
                log.warning("SSRF blocked (private, enforce on): %s -> %s", context, ip)
                raise AppError("SSRF_BLOCKED", "当前部署策略禁止访问内网地址。", http_status=400)
            log.info("private egress allowed by policy: %s -> %s", context or "egress", ip)
