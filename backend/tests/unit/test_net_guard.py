"""出网防护单测：云元数据永久阻断、环回分级、私网默认放行。"""

import socket

import pytest
from app.core import net_guard
from app.core.config import settings
from app.core.errors import AppError


def _resolve_to(monkeypatch, ip: str) -> None:
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    sockaddr = (ip, 443, 0, 0) if family == socket.AF_INET6 else (ip, 443)

    def fake(host, port, *args, **kwargs):  # type: ignore[no-untyped-def]
        return [(family, socket.SOCK_STREAM, 6, "", sockaddr)]

    monkeypatch.setattr(socket, "getaddrinfo", fake)


def _assert_blocked(url: str, *, allow_local: bool = False) -> AppError:
    with pytest.raises(AppError) as exc:
        net_guard.check_egress_url(url, context="test", allow_local=allow_local)
    assert exc.value.code == "SSRF_BLOCKED"
    return exc.value


@pytest.mark.parametrize(
    "ip",
    [
        "169.254.169.254",  # IPv4 链路本地元数据
        "fe80::1",  # IPv6 链路本地
        "fd00:ec2::254",  # AWS IMDS IPv6（ULA 内，allow_local 也不得放行）
        "100.100.100.200",  # 阿里云元数据
        "192.0.0.192",  # Oracle Cloud 元数据
        "0.0.0.0",
        "ff02::1",  # 组播
    ],
)
def test_metadata_and_reserved_always_blocked(monkeypatch, ip):
    _resolve_to(monkeypatch, ip)
    error = _assert_blocked("http://probe.example.com/meta", allow_local=True)
    assert "元数据" in error.message


def test_private_allowed_by_default(monkeypatch):
    _resolve_to(monkeypatch, "10.1.2.3")
    net_guard.check_egress_url("http://internal.example.com/meta")


def test_ipv6_ula_non_metadata_allowed_by_default(monkeypatch):
    _resolve_to(monkeypatch, "fd00::1")
    net_guard.check_egress_url("http://ula.example.com/meta")


def test_private_blocked_when_public_only(monkeypatch):
    monkeypatch.setattr(settings, "enforce_model_egress_guard", True)
    _resolve_to(monkeypatch, "10.1.2.3")
    error = _assert_blocked("http://internal.example.com/meta", allow_local=True)
    assert "内网" in error.message


def test_loopback_requires_allow_local(monkeypatch):
    _resolve_to(monkeypatch, "127.0.0.1")
    assert "本机" in _assert_blocked("http://localhost:11434/v1").message
    net_guard.check_egress_url("http://localhost:11434/v1", allow_local=True)


def test_loopback_blocked_when_public_only(monkeypatch):
    monkeypatch.setattr(settings, "enforce_model_egress_guard", True)
    # 127.0.0.1 走环回分支；::1 在 ipaddress 里同时被判为 reserved，文案不同
    _resolve_to(monkeypatch, "127.0.0.1")
    assert "内网" in _assert_blocked("http://localhost/v1", allow_local=True).message


def test_unresolvable_host_passes_through(monkeypatch):
    def fail(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise socket.gaierror("offline")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    net_guard.check_egress_url("https://not-resolvable.example.com/")


def test_bad_scheme_rejected():
    error = _assert_blocked("file:///etc/passwd")
    assert "协议" in error.message
