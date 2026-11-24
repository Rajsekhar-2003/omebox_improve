"""HTTP middleware: security headers and bandwidth-friendly compression."""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import settings


class SecurityHeadersMiddleware:
    """Add baseline browser defences to every response.

    Endpoint-specific headers (for example the sandbox CSP on downloads) win:
    this middleware never replaces a header that is already present.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        secure = settings.force_https or scope.get("scheme") == "https"
        headers = {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=()",
        }
        if secure:
            headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if settings.csp:
            headers["Content-Security-Policy"] = settings.csp

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                mutable = MutableHeaders(scope=message)
                present = {key.lower() for key, _ in mutable.items()}
                for key, value in headers.items():
                    if key.lower() not in present:
                        mutable.append(key, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)


class SelectiveGZipMiddleware(GZipMiddleware):
    """GZip text responses, never spend CPU compressing uploaded files."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            path = scope.get("path", "")
            if path.startswith("/api/share/") and "/file/" in path:
                await self.app(scope, receive, send)
                return
        await super().__call__(scope, receive, send)
