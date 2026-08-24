"""Unit tests for password auth rate limiting helpers."""

from unittest.mock import MagicMock

from api.auth import (
    auth_rate_limited,
    clear_auth_failures,
    client_ip,
    record_auth_failure,
    reset_auth_rate_limit_state,
)


def setup_function():
    reset_auth_rate_limit_state()


def test_auth_rate_limit_trips_after_max_failures():
    ip = "203.0.113.9"
    now = 1_000_000.0
    for i in range(20):
        record_auth_failure(ip, now=now + i)
    assert auth_rate_limited(ip, now=now + 19)
    clear_auth_failures(ip)
    assert not auth_rate_limited(ip, now=now + 19)


def test_auth_rate_limit_window_prunes_old_failures():
    ip = "203.0.113.10"
    now = 2_000_000.0
    for i in range(20):
        record_auth_failure(ip, now=now - 400 + i)  # outside 300s window
    assert not auth_rate_limited(ip, now=now)


def test_client_ip_ignores_xff_without_trusted_proxies(monkeypatch):
    monkeypatch.delenv("OPEN_NOTEBOOK_TRUSTED_PROXIES", raising=False)
    req = MagicMock()
    req.client.host = "10.0.0.5"
    req.headers.get = lambda k, d=None: (
        "203.0.113.9" if k.lower() == "x-forwarded-for" else d
    )
    assert client_ip(req) == "10.0.0.5"


def test_client_ip_uses_xff_when_peer_trusted(monkeypatch):
    monkeypatch.setenv("OPEN_NOTEBOOK_TRUSTED_PROXIES", "10.0.0.5")
    req = MagicMock()
    req.client.host = "10.0.0.5"
    req.headers.get = lambda k, d=None: (
        "203.0.113.9, 10.0.0.5" if k.lower() == "x-forwarded-for" else d
    )
    assert client_ip(req) == "203.0.113.9"
