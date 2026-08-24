import os
import re
import secrets
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional, Set

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from open_notebook.utils.encryption import get_secret_from_env

# Sliding window for failed password attempts (per client IP).
_AUTH_FAIL_WINDOW_SECONDS = 300
_AUTH_FAIL_MAX_ATTEMPTS = 20
_auth_failures: Dict[str, Deque[float]] = defaultdict(deque)


def _trusted_proxy_hosts() -> Set[str]:
    """Peers allowed to supply X-Forwarded-For (comma-separated env)."""
    raw = os.getenv("OPEN_NOTEBOOK_TRUSTED_PROXIES", "").strip()
    if not raw:
        return set()
    return {p.strip() for p in raw.split(",") if p.strip()}


def client_ip(request: Request) -> str:
    """Client IP for auth rate limiting.

    Default: TCP peer only (``request.client.host``). Client-controlled
    ``X-Forwarded-For`` is ignored unless the peer is listed in
    ``OPEN_NOTEBOOK_TRUSTED_PROXIES`` (comma-separated hostnames/IPs of
    reverse proxies).
    """
    peer = "unknown"
    if request.client and request.client.host:
        peer = request.client.host

    trusted = _trusted_proxy_hosts()
    if trusted and peer in trusted:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip() or peer
    return peer


# Back-compat name used in older tests/docs
_client_ip = client_ip


def _prune_failures(bucket: Deque[float], now: float) -> None:
    cutoff = now - _AUTH_FAIL_WINDOW_SECONDS
    while bucket and bucket[0] < cutoff:
        bucket.popleft()


def auth_rate_limited(ip: str, *, now: Optional[float] = None) -> bool:
    """True when this IP has too many recent failed auth attempts."""
    current = now if now is not None else time.time()
    bucket = _auth_failures[ip]
    _prune_failures(bucket, current)
    return len(bucket) >= _AUTH_FAIL_MAX_ATTEMPTS


def record_auth_failure(ip: str, *, now: Optional[float] = None) -> None:
    current = now if now is not None else time.time()
    bucket = _auth_failures[ip]
    _prune_failures(bucket, current)
    bucket.append(current)


def clear_auth_failures(ip: str) -> None:
    _auth_failures.pop(ip, None)


def reset_auth_rate_limit_state() -> None:
    """Test helper: clear all failure buckets."""
    _auth_failures.clear()


# Episode audio only: lets a bare <audio src> element stream natively
# (headers are impossible from media elements). Read-only route, unguessable
# record IDs; everything else stays behind the password wall.
_PUBLIC_AUDIO_PATH_RE = re.compile(r"^/api/podcasts/episodes/[^/]+/audio$")


def public_audio_enabled() -> bool:
    """True when OPEN_NOTEBOOK_PUBLIC_AUDIO opts episode audio out of auth."""
    return os.getenv("OPEN_NOTEBOOK_PUBLIC_AUDIO", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def is_public_audio_request(method: str, path: str) -> bool:
    """Read-only episode-audio requests exempt from auth when enabled."""
    return (
        method in ("GET", "HEAD")
        and public_audio_enabled()
        and _PUBLIC_AUDIO_PATH_RE.match(path) is not None
    )


class PasswordAuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware to check password authentication for all API requests.
    Auth is fully disabled (no hardcoded default password) if
    OPEN_NOTEBOOK_PASSWORD is not set — deploy_env refuses that in production
    unless OPEN_NOTEBOOK_ALLOW_INSECURE_DEFAULTS=1.
    Supports Docker secrets via OPEN_NOTEBOOK_PASSWORD_FILE.
    Failed attempts are rate-limited per client IP (see client_ip).
    """

    def __init__(
        self, app: ASGIApp, excluded_paths: Optional[list[str]] = None
    ) -> None:
        super().__init__(app)
        self.password = get_secret_from_env("OPEN_NOTEBOOK_PASSWORD")
        self.excluded_paths: list[str] = excluded_paths or [
            "/",
            "/health",
            "/docs",
            "/openapi.json",
            "/redoc",
        ]

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Skip authentication if no password is set
        if not self.password:
            return await call_next(request)

        # Skip authentication for excluded paths
        if request.url.path in self.excluded_paths:
            return await call_next(request)

        # Skip authentication for CORS preflight requests (OPTIONS)
        if request.method == "OPTIONS":
            return await call_next(request)

        # Opt-in exemption so media elements can stream episode audio
        if is_public_audio_request(request.method, request.url.path):
            return await call_next(request)

        ip = client_ip(request)
        if auth_rate_limited(ip):
            return JSONResponse(
                status_code=429,
                content={
                    "detail": (
                        "Too many failed authentication attempts. "
                        "Try again in a few minutes."
                    )
                },
                headers={"Retry-After": str(_AUTH_FAIL_WINDOW_SECONDS)},
            )

        # Check authorization header
        auth_header = request.headers.get("Authorization")

        if not auth_header:
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing authorization header"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Expected format: "Bearer {password}"
        try:
            scheme, credentials = auth_header.split(" ", 1)
            if scheme.lower() != "bearer":
                raise ValueError("Invalid authentication scheme")
        except ValueError:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid authorization header format"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Check password (constant-time to avoid a timing side-channel)
        if not secrets.compare_digest(
            credentials.encode("utf-8"), self.password.encode("utf-8")
        ):
            record_auth_failure(ip)
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid password"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        clear_auth_failures(ip)
        response = await call_next(request)
        return response
