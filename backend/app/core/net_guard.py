"""SSRF 出网防护（规范 §15.1，分级策略见 ADR-0003 + v1.3 设计契约 §B）。

对服务端发起的 HTTP(S) 目标做解析后 IP 校验：

1. **永远禁止（与开关无关）**：link-local（含云元数据 169.254.0.0/16、fe80::/10）、
   组播、保留段、未指定地址（0.0.0.0/::），以及落在「内网私有段」里但实际是云元数据的
   地址（AWS IMDS IPv6 ``fd00:ec2::254``、阿里云 ``100.100.100.200``、Oracle
   ``192.0.0.192``）——错误文案统一「禁止访问元数据或保留地址。」；
2. **本机段（127.0.0.0/8、::1）**：按调用方分级。
   `allow_local=True`（LLM 供应商域：保存 / 测试 / 运行时 / 模型列表探测）放行——
   小院管理员配置供应商属可信输入，Ollama / LM Studio 这类本机推理服务是正当场景；
   默认（设备连接器、推送通道、MCP 服务器）保持严格，本机地址一律拒绝；
3. **内网私有段（10/8、172.16/12、192.168/16、fc00::/7 等）**：沿用 ADR-0003 既有口径——
   内网私有化投放是科技小院的常见形态，默认放行（`allow_local=True` 时同样放行），
   部署总闸 `settings.enforce_model_egress_guard=true`（纯公网 SaaS）时一并阻断，
   文案维持原文「当前部署策略禁止访问内网地址。」；
4. DNS 解析后的每个 IP 都要校验，防止域名指向内网的 DNS 重绑定绕过。
"""

import ipaddress
import socket
from urllib.parse import urlparse

from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import get_logger

log = get_logger(__name__)

_ALLOWED_SCHEMES = {"http", "https"}

#: 元数据 / 链路本地 / 组播 / 保留段命中时的统一文案（v1.3 §B）
_MSG_META_RESERVED = "禁止访问元数据或保留地址。"
#: 默认严格调用方访问本机地址时的文案
_MSG_LOOPBACK_STRICT = "禁止访问本机地址；如需本机模型服务，请在「模型供应商」中配置。"
#: 部署总闸阻断内网时的文案（沿用既有口径，避免旧集成误判）
_MSG_ENFORCE_PRIVATE = "当前部署策略禁止访问内网地址。"

_IP = ipaddress.IPv4Address | ipaddress.IPv6Address

#: 云元数据地址：有的落在 ULA / 共享地址段，会被「内网私有段默认放行」规则误放行，
#: 因此在任何模式下都先于私网判定阻断。
#: - fd00:ec2::254：AWS IMDS IPv6（fc00::/7 ULA 内，is_private=True）
#: - 100.100.100.200：阿里云元数据（100.64.0.0/10 共享地址段）
#: - 192.0.0.192：Oracle Cloud 元数据
_METADATA_NETWORKS = (
    ipaddress.ip_network("fd00:ec2::/64"),
    ipaddress.ip_network("100.100.100.200/32"),
    ipaddress.ip_network("192.0.0.192/32"),
)


def _is_metadata(ip: _IP) -> bool:
    return any(ip.version == net.version and ip in net for net in _METADATA_NETWORKS)


def _is_always_blocked(ip: _IP) -> bool:
    """元数据 / 链路本地 / 组播 / 保留 / 未指定：任何开关都不放行。"""
    return bool(
        _is_metadata(ip)
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _is_private(ip: _IP) -> bool:
    return bool(ip.is_private)


def check_egress_url(url: str, *, context: str = "", allow_local: bool = False) -> None:
    """校验 URL 是否允许服务端访问；不允许时抛 AppError(SSRF_BLOCKED)。

    Args:
        url: 待访问的目标地址。
        context: 日志上下文（调用方标识）。
        allow_local: 是否额外放行本机段（127.0.0.0/8、::1）。仅 LLM 供应商域
            （保存/测试/运行时/模型探测）传 True；其余调用方保持默认严格。
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
            raise AppError("SSRF_BLOCKED", _MSG_META_RESERVED, http_status=400)
        if ip.is_loopback:
            if enforce_public:
                log.warning("SSRF blocked (loopback, enforce on): %s -> %s", context, ip)
                raise AppError("SSRF_BLOCKED", _MSG_ENFORCE_PRIVATE, http_status=400)
            if allow_local:
                # 供应商域显式放行：本机推理服务（Ollama / LM Studio）是正当场景
                log.info("loopback egress allowed (allow_local): %s -> %s", context or "egress", ip)
                continue
            log.warning("SSRF blocked (loopback, strict): %s -> %s", context, ip)
            raise AppError("SSRF_BLOCKED", _MSG_LOOPBACK_STRICT, http_status=400)
        if _is_private(ip):
            if enforce_public:
                log.warning("SSRF blocked (private, enforce on): %s -> %s", context, ip)
                raise AppError("SSRF_BLOCKED", _MSG_ENFORCE_PRIVATE, http_status=400)
            # ADR-0003：内网私有化投放默认放行（allow_local 调用方同样在此分支放行）
            log.info("private egress allowed by policy: %s -> %s", context or "egress", ip)
