from __future__ import annotations

import ipaddress
import os
from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


LOOPBACK_TEST_HOSTS = {"testclient", "testserver"}
SECURITY_HEADERS = (
    (b"content-security-policy", b"default-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'; object-src 'none'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; worker-src 'self' blob:"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
)


def _is_loopback_host(value: str | None) -> bool:
    if not value:
        return False
    host = value.strip().lower().rstrip(".")
    # Starlette's in-process TestClient uses these synthetic client/Host names.
    # They are safe here because the independent socket-client check must also
    # pass; a remote caller cannot become loopback merely by sending one of
    # these Host headers.
    if host == "localhost" or host in LOOPBACK_TEST_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return value.decode("latin-1")
    return None


def _host_header_name(value: str | None) -> str | None:
    if not value:
        return None
    try:
        # A scheme makes urlsplit handle host:port and bracketed IPv6 without
        # treating the host as a path.
        return urlsplit(f"http://{value}").hostname
    except ValueError:
        return None


def _origin_host(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return urlsplit(value).hostname
    except ValueError:
        return None


class LocalOnlyMiddleware:
    """Keep the unauthenticated desktop API on the local machine by default.

    The service can move or recycle media files, launch downloads, and update
    tags. Binding Uvicorn to loopback is the first protection; this middleware
    is a second application-level guard against accidental LAN exposure, DNS
    rebinding, and cross-origin browser requests to localhost.

    KARAOKE_ALLOW_REMOTE=1 is an explicit escape hatch for an operator who has
    supplied authentication/TLS in a trusted reverse proxy. It is intentionally
    not enabled by any bundled startup script.
    """

    def __init__(self, app: ASGIApp, allow_remote: bool | None = None) -> None:
        self.app = app
        self.allow_remote = (
            os.environ.get("KARAOKE_ALLOW_REMOTE") == "1"
            if allow_remote is None
            else allow_remote
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message = dict(message)
                message["headers"] = [*message.get("headers", []), *SECURITY_HEADERS]
            await send(message)

        app_send = send_with_security_headers if scope["type"] == "http" else send
        if self.allow_remote:
            await self.app(scope, receive, app_send)
            return

        client = scope.get("client")
        client_host = client[0] if client else None
        host = _host_header_name(_header(scope, b"host"))
        origin_value = _header(scope, b"origin")
        origin = _origin_host(origin_value)
        trusted = (
            _is_loopback_host(client_host)
            and _is_loopback_host(host)
            and (origin_value is None or _is_loopback_host(origin))
        )
        if trusted:
            await self.app(scope, receive, app_send)
            return

        detail = "Karaoke Media Manager accepts local requests only"
        if scope["type"] == "http":
            response = JSONResponse({"detail": detail}, status_code=403)
            await response(scope, receive, app_send)
            return

        message: Message = await receive()
        if message["type"] == "websocket.connect":
            await send({"type": "websocket.close", "code": 1008, "reason": detail})
