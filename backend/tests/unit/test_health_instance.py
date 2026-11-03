"""桌面壳归属校验：/health/ready 仅在注入令牌时回显 X-TechYard-Instance。

壳只接管回显一致的那一个引擎，避免把一次性引导令牌交给端口段里不相干的本地服务。
"""

from __future__ import annotations


def test_ready_echoes_instance_token(client, monkeypatch):
    monkeypatch.setenv("TECHYARD_INSTANCE_TOKEN", "instance-token-for-test-0123456789")
    res = client.get("/health/ready")
    assert res.headers.get("x-techyard-instance") == "instance-token-for-test-0123456789"


def test_ready_omits_header_without_env(client, monkeypatch):
    monkeypatch.delenv("TECHYARD_INSTANCE_TOKEN", raising=False)
    res = client.get("/health/ready")
    assert "x-techyard-instance" not in res.headers
