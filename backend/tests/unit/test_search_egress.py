"""web.fetch 出网防护单测：内网/元数据拦截、重定向重验、正文提取（全离线）。"""

import socket

import httpx
import pytest
from app.core.errors import AppError
from app.modules.search import egress

PUBLIC_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def _reset_transport():
    egress.set_test_transport(None)
    yield
    egress.set_test_transport(None)


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


async def _assert_blocked(url: str):
    with pytest.raises(AppError) as exc:
        await egress.fetch_public_url(url)
    assert exc.value.code == "WEB_FETCH_BLOCKED"
    assert exc.value.http_status == 400


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/",
        "http://127.0.0.1/",
        "http://127.0.0.2:8080/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/",
        "http://172.16.3.4/",
        "http://192.168.1.10/",
        "http://[::1]/",
        "http://0.0.0.0/",
        "file:///etc/passwd",
        "ftp://example.com/a",
    ],
)
async def test_blocked_targets(url):
    egress.set_test_transport(_transport(lambda request: httpx.Response(200, text="leak")))
    await _assert_blocked(url)


async def test_redirect_to_internal_blocked():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="leak")

    egress.set_test_transport(_transport(handler))
    with pytest.raises(AppError) as exc:
        await egress.fetch_public_url(f"http://{PUBLIC_IP}/start")
    assert exc.value.code == "WEB_FETCH_BLOCKED"
    # 内网跳转不应被真正请求
    assert all("127.0.0.1" not in url for url in calls)


async def test_too_many_redirects():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": f"/r{request.url.path}"})

    egress.set_test_transport(_transport(handler))
    with pytest.raises(AppError) as exc:
        await egress.fetch_public_url(f"http://{PUBLIC_IP}/r0")
    assert "重定向" in exc.value.message


async def test_html_extraction_strips_scripts_and_merges_whitespace():
    html = """
    <html>
      <head><title> 测试   页面 </title><style>.x{color:red}</style></head>
      <body>
        <!-- 注释 -->
        <script>alert('xss')</script>
        <h1>标题</h1>
        <p>正文 &amp; 内容</p>
      </body>
    </html>
    """
    egress.set_test_transport(
        _transport(
            lambda request: httpx.Response(
                200, headers={"content-type": "text/html; charset=utf-8"}, text=html
            )
        )
    )
    page = await egress.fetch_public_url(f"http://{PUBLIC_IP}/page")

    assert page["title"] == "测试 页面"
    assert page["content_type"] == "text/html"
    assert page["url"] == f"http://{PUBLIC_IP}/page"
    assert "alert" not in page["text"]
    assert "color:red" not in page["text"]
    assert "正文 & 内容" in page["text"]
    assert "  " not in page["text"]


async def test_plain_text_and_max_chars():
    egress.set_test_transport(
        _transport(
            lambda request: httpx.Response(
                200, headers={"content-type": "text/plain"}, text="x" * 7000
            )
        )
    )
    page = await egress.fetch_public_url(f"http://{PUBLIC_IP}/raw", max_chars=100000)
    assert page["content_type"] == "text/plain"
    assert page["title"] == ""
    assert len(page["text"]) == 6000


async def test_follows_public_redirect():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new"})
        return httpx.Response(
            200, headers={"content-type": "text/html"}, text="<title>New</title><p>ok</p>"
        )

    egress.set_test_transport(_transport(handler))
    page = await egress.fetch_public_url(f"http://{PUBLIC_IP}/old")
    assert page["url"] == f"http://{PUBLIC_IP}/new"
    assert page["title"] == "New"


async def test_content_type_rejected():
    egress.set_test_transport(
        _transport(
            lambda request: httpx.Response(
                200, headers={"content-type": "application/json"}, text="{}"
            )
        )
    )
    with pytest.raises(AppError) as exc:
        await egress.fetch_public_url(f"http://{PUBLIC_IP}/api")
    assert exc.value.code == "WEB_FETCH_BLOCKED"


async def test_missing_content_type_rejected():
    egress.set_test_transport(
        _transport(lambda request: httpx.Response(200, content=b"<html></html>"))
    )
    with pytest.raises(AppError) as exc:
        await egress.fetch_public_url(f"http://{PUBLIC_IP}/no-type")
    assert exc.value.code == "WEB_FETCH_BLOCKED"


async def test_oversize_body_rejected():
    egress.set_test_transport(
        _transport(
            lambda request: httpx.Response(
                200,
                headers={
                    "content-type": "text/html",
                    "content-length": str(egress.MAX_BYTES + 1),
                },
                content=b"<html></html>",
            )
        )
    )
    with pytest.raises(AppError) as exc:
        await egress.fetch_public_url(f"http://{PUBLIC_IP}/big")
    assert "2MB" in exc.value.message


async def test_upstream_error_maps_to_blocked():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout")

    egress.set_test_transport(_transport(handler))
    with pytest.raises(AppError) as exc:
        await egress.fetch_public_url(f"http://{PUBLIC_IP}/slow")
    assert exc.value.code == "WEB_FETCH_BLOCKED"


# ---------------------------------------------------------------------------
# S-2：建连解析钉在刚校验过的地址上，堵住校验与实际连接之间的 DNS 重绑定窗口
# ---------------------------------------------------------------------------


def _answer(ip_text: str, port: int = 80) -> list:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip_text, port))]


def test_dns_pin_keeps_only_validated_addresses(monkeypatch) -> None:
    answers = _answer(PUBLIC_IP) + _answer("127.0.0.1")
    monkeypatch.setattr(egress, "_REAL_GETADDRINFO", lambda *args, **kwargs: answers)
    with egress._dns_pin("cdn.test", 80, frozenset({PUBLIC_IP})):
        kept = egress._pinned_getaddrinfo("cdn.test", 80)
    assert [item[4][0] for item in kept] == [PUBLIC_IP]


def test_dns_pin_fails_closed_when_answers_move_off_the_validated_set(monkeypatch) -> None:
    """重绑定典型形态：第二次解析只给内网地址——必须中止，而不是照着新答案连。"""
    moved = _answer("169.254.169.254")
    monkeypatch.setattr(egress, "_REAL_GETADDRINFO", lambda *args, **kwargs: moved)
    with egress._dns_pin("rebind.test", 80, frozenset({PUBLIC_IP})), pytest.raises(OSError):
        # asyncio 传给解析器的是 bytes 主机名
        egress._pinned_getaddrinfo(b"rebind.test", 80)


def test_dns_pin_leaves_other_hosts_untouched(monkeypatch) -> None:
    monkeypatch.setattr(egress, "_REAL_GETADDRINFO", lambda *args, **kwargs: _answer("203.0.113.7"))
    with egress._dns_pin("target.test", 80, frozenset({PUBLIC_IP})):
        assert egress._pinned_getaddrinfo("unrelated.test", 443)[0][4][0] == "203.0.113.7"
    assert egress._pinned_getaddrinfo("target.test", 8443)[0][4][0] == "203.0.113.7"


def test_resolver_shim_is_installed_once() -> None:
    assert socket.getaddrinfo is egress._pinned_getaddrinfo


def test_public_pin_dns_fails_closed_for_other_callers(monkeypatch) -> None:
    """自定义 HTTP 工具等调用方复用 pin_dns 时，同样享受“答案变了就中止”。"""
    monkeypatch.setattr(
        egress, "_REAL_GETADDRINFO", lambda *args, **kwargs: _answer("169.254.169.254")
    )
    with egress.pin_dns("rebind.test", 80, frozenset({PUBLIC_IP})), pytest.raises(OSError):
        egress._pinned_getaddrinfo("rebind.test", 80)
