"""Full SSRF protection for the project API proxy (projects/proxy_service.py).

Before this, ``validated_api_base_url`` only blocked the metadata hostname and
169.254.0.0/16 by name, no DNS resolution at all: a private IP given directly,
a hostname that *resolves* to one, or an IPv6 loopback/link-local/IPv4-mapped
equivalent all sailed through. This now shares ``common.ssrf`` with
``tools.web.fetch_url``: every resolved address must be public, the connection
is pinned to the address that check approved (DNS rebinding), and a redirect
is re-validated from scratch rather than trusted.

The one deliberate hole is the operator's own machine (localhost, 127.0.0.1,
host.docker.internal, or whatever common.hostnet.host_service_url rewrote
onto the Docker host gateway): project backends legitimately run there.

DNS is always mocked; no network is touched.

Run: ``python -m pytest tests/test_proxy_ssrf.py -q``
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from common import ssrf as common_ssrf
from projects import proxy_service
from projects.errors import ServiceError


def _mock_dns(monkeypatch, mapping: dict[str, list[str]]):
    """``mapping`` is {hostname: [addresses]}. A gaierror for anything else."""
    def fake_getaddrinfo(host, port):
        if host not in mapping:
            raise common_ssrf.socket.gaierror(f"no such host: {host}")
        return [(2, 1, 6, "", (addr, 0)) for addr in mapping[host]]

    monkeypatch.setattr(common_ssrf.socket, "getaddrinfo", fake_getaddrinfo)


def _refusing_getaddrinfo(*a, **k):
    raise AssertionError("DNS should not be resolved for an exempt host")


# ── is_public_address: the named IPv6 cases ─────────────────────────────────

@pytest.mark.parametrize("addr", [
    "::1",                 # loopback
    "fc00::1",              # RFC4193 unique local (fc00::/7)
    "fe80::1",              # link-local (fe80::/10)
    "::ffff:10.0.0.1",      # IPv4-mapped private
    "::ffff:169.254.169.254",  # IPv4-mapped metadata/link-local
    "169.254.169.254",
    "10.0.0.1",
    "192.168.1.1",
    "127.0.0.1",
    "0.0.0.0",
    "::",
])
def test_non_public_addresses_are_refused(addr):
    assert common_ssrf.is_public_address(addr) is False


@pytest.mark.parametrize("addr", ["8.8.8.8", "93.184.216.34", "::ffff:8.8.8.8"])
def test_public_addresses_pass(addr):
    assert common_ssrf.is_public_address(addr) is True


# ── validated_api_base_url: resolution-based refusal ────────────────────────

def test_a_hostname_resolving_to_a_private_address_is_refused(monkeypatch):
    _mock_dns(monkeypatch, {"internal.example": ["10.0.0.5"]})
    with pytest.raises(ValueError, match="non-public"):
        proxy_service.validated_api_base_url("http://internal.example/api")


def test_dns_rebinding_one_public_one_private_answer_is_refused(monkeypatch):
    """Checking only the first answer is exactly the DNS-rebinding gap; every
    resolved address must be public."""
    _mock_dns(monkeypatch, {"rebind.example": ["93.184.216.34", "127.0.0.1"]})
    ok_or_raises = None
    try:
        proxy_service.validated_api_base_url("http://rebind.example/api")
        ok_or_raises = "did not raise"
    except ValueError as e:
        ok_or_raises = str(e)
    assert ok_or_raises != "did not raise"
    assert "127.0.0.1" in ok_or_raises


def test_a_public_hostname_is_accepted(monkeypatch):
    _mock_dns(monkeypatch, {"api.example": ["93.184.216.34"]})
    assert proxy_service.validated_api_base_url("http://api.example/v1") == "http://api.example/v1"


def test_an_unresolvable_hostname_is_refused(monkeypatch):
    _mock_dns(monkeypatch, {})
    with pytest.raises(ValueError, match="could not resolve"):
        proxy_service.validated_api_base_url("http://nowhere.example/")


def test_an_ipv6_loopback_literal_is_refused(monkeypatch):
    # A literal address needs no DNS lookup; getaddrinfo just echoes it back.
    _mock_dns(monkeypatch, {"::1": ["::1"]})
    with pytest.raises(ValueError, match="non-public"):
        proxy_service.validated_api_base_url("http://[::1]/api")


# ── the operator's-own-machine exemption ────────────────────────────────────

def test_localhost_is_exempt_and_never_resolved(monkeypatch):
    monkeypatch.delenv("AGENTS_HUB_IN_CONTAINER", raising=False)
    monkeypatch.setattr(common_ssrf.socket, "getaddrinfo", _refusing_getaddrinfo)
    assert proxy_service.validated_api_base_url("http://localhost:9/api") == "http://localhost:9/api"


def test_127_0_0_1_is_exempt_and_never_resolved(monkeypatch):
    monkeypatch.delenv("AGENTS_HUB_IN_CONTAINER", raising=False)
    monkeypatch.setattr(common_ssrf.socket, "getaddrinfo", _refusing_getaddrinfo)
    assert proxy_service.validated_api_base_url("http://127.0.0.1:9/api") == "http://127.0.0.1:9/api"


def test_host_docker_internal_named_directly_is_exempt(monkeypatch):
    monkeypatch.delenv("AGENTS_HUB_IN_CONTAINER", raising=False)
    monkeypatch.setattr(common_ssrf.socket, "getaddrinfo", _refusing_getaddrinfo)
    assert (proxy_service.validated_api_base_url("http://host.docker.internal:9/api")
            == "http://host.docker.internal:9/api")


def test_a_loopback_url_rewritten_to_the_gateway_inside_a_container_is_exempt(monkeypatch):
    monkeypatch.setenv("AGENTS_HUB_IN_CONTAINER", "1")
    monkeypatch.setattr(common_ssrf.socket, "getaddrinfo", _refusing_getaddrinfo)
    assert (proxy_service.validated_api_base_url("http://localhost:9/api")
            == "http://host.docker.internal:9/api")


def test_the_exemption_does_not_extend_to_an_ordinary_private_host(monkeypatch):
    """Being reachable at a private address is not, on its own, the
    operator's-own-machine exemption: only the specific names are."""
    _mock_dns(monkeypatch, {"internal-service": ["10.0.0.9"]})
    with pytest.raises(ValueError, match="non-public"):
        proxy_service.validated_api_base_url("http://internal-service/api")


# ── the metadata endpoint's own message ─────────────────────────────────────

def test_the_metadata_hostname_has_its_own_message_even_when_unresolvable(monkeypatch):
    _mock_dns(monkeypatch, {})  # metadata.google.internal only answers on GCP
    with pytest.raises(ValueError, match="metadata"):
        proxy_service.validated_api_base_url("http://metadata.google.internal/x")


def test_the_metadata_ip_literal_has_its_own_message(monkeypatch):
    _mock_dns(monkeypatch, {"169.254.169.254": ["169.254.169.254"]})
    with pytest.raises(ValueError, match="metadata"):
        proxy_service.validated_api_base_url("http://169.254.169.254/latest")


# ── connection pinning ───────────────────────────────────────────────────────

def test_the_pinned_transport_rewrites_the_host_and_keeps_the_original_as_sni(monkeypatch):
    captured = {}

    async def fake_handle(self, request):
        captured["url_host"] = request.url.host
        captured["host_header"] = request.headers.get("host")
        captured["sni"] = request.extensions.get("sni_hostname")
        return httpx.Response(200, request=request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", fake_handle)

    transport = proxy_service._PinnedTransport("93.184.216.34")
    request = httpx.Request("GET", "http://api.example/v1")

    asyncio.run(transport.handle_async_request(request))

    assert captured["url_host"] == "93.184.216.34"
    assert captured["host_header"] == "api.example"
    assert captured["sni"] == "api.example"


# ── proxy_api_request: redirects are re-validated per hop ──────────────────

class _StubTransportClient:
    """Stands in for httpx.AsyncClient across the whole redirect chain."""

    def __init__(self, responses):
        self._responses = list(responses)

    def __call__(self, *a, **k):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def request(self, **kwargs):
        resp = self._responses.pop(0)
        return resp


class _FakeResp:
    def __init__(self, status_code, headers=None, json_body=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._json = json_body
        self.text = "" if json_body is None else ""

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


def test_proxy_follows_a_redirect_to_another_public_host(monkeypatch):
    _mock_dns(monkeypatch, {
        "old.example": ["93.184.216.34"],
        "new.example": ["93.184.216.35"],
    })
    responses = [
        _FakeResp(302, headers={"location": "http://new.example/moved"}),
        _FakeResp(200, json_body={"ok": True}),
    ]
    monkeypatch.setattr(httpx, "AsyncClient", _StubTransportClient(responses))

    result = asyncio.run(proxy_service.proxy_api_request("http://old.example", "GET", "/x"))
    assert result == {"status_code": 200, "headers": {}, "body": {"ok": True}}


def test_proxy_refuses_a_redirect_to_a_private_target(monkeypatch):
    _mock_dns(monkeypatch, {"old.example": ["93.184.216.34"]})
    responses = [
        _FakeResp(302, headers={"location": "http://10.0.0.5/steal"}),
    ]
    monkeypatch.setattr(httpx, "AsyncClient", _StubTransportClient(responses))

    with pytest.raises(ServiceError) as exc:
        asyncio.run(proxy_service.proxy_api_request("http://old.example", "GET", "/x"))
    assert exc.value.status == 400
    assert "refused" in exc.value.detail.lower()


def test_proxy_stops_following_redirects_past_the_configured_cap(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "web_fetch_max_redirects", 1)
    _mock_dns(monkeypatch, {
        "a.example": ["93.184.216.1"],
        "b.example": ["93.184.216.2"],
        "c.example": ["93.184.216.3"],
    })
    responses = [
        _FakeResp(302, headers={"location": "http://b.example/"}),
        _FakeResp(302, headers={"location": "http://c.example/"}),
    ]
    monkeypatch.setattr(httpx, "AsyncClient", _StubTransportClient(responses))

    result = asyncio.run(proxy_service.proxy_api_request("http://a.example", "GET", "/"))
    # The second redirect (to c.example) is not followed: one hop was spent
    # getting to b.example, and the cap is 1.
    assert result["status_code"] == 302


def test_proxy_still_rejects_a_bad_base_url_before_any_request(monkeypatch):
    with pytest.raises(ServiceError) as exc:
        asyncio.run(proxy_service.proxy_api_request("file:///etc/passwd", "GET", "/"))
    assert exc.value.status == 400
