"""FastAPI application factory and lifespan management."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from .cleanup import cleanup
from .config import settings
from .database import db
from .middleware import SecurityHeadersMiddleware, SelectiveGZipMiddleware
from .ratelimit import limiter
from .routes import admin, pages, shares
from .templating import templates

logger = logging.getLogger("omebox.app")


class CachedStaticFiles(StaticFiles):
    """Static assets are versioned on deployment, so cache them briefly."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers.setdefault(
            "Cache-Control", f"public, max-age={settings.static_cache_max_age}"
        )
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting OmeBox...")
    await db.ensure_indexes()

    scheduler = AsyncIOScheduler()
    if settings.cleanup_enabled:
        scheduler.add_job(
            cleanup.run_scheduled_cleanup,
            IntervalTrigger(minutes=settings.cleanup_interval_minutes),
            id="auto_cleanup",
            name="Expiry and orphan cleanup",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.start()
    app.state.scheduler = scheduler

    startup_cleanup = None
    if settings.cleanup_enabled:
        startup_cleanup = asyncio.create_task(cleanup.run_scheduled_cleanup())

    logger.info("OmeBox started")
    try:
        yield
    finally:
        logger.info("Shutting down OmeBox...")
        if startup_cleanup is not None and not startup_cleanup.done():
            startup_cleanup.cancel()
        if scheduler.running:
            scheduler.shutdown(wait=False)
        await cleanup.close()
        await db.close()


def create_app() -> FastAPI:
    application = FastAPI(
        title="OmeBox - Secure Text & File Sharing",
        description="A secure platform for sharing text and files",
        version="3.0.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url="/redoc" if settings.enable_docs else None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
    )
    application.state.limiter = limiter
    application.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # Middleware added last is outermost.  Proxy headers are handled first so
    # rate limiting sees the real client IP behind the trusted reverse proxy.
    application.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        max_age=settings.session_max_age,
        same_site="lax",
        https_only=settings.force_https,
    )
    if settings.allowed_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.allowed_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Content-Type", "X-Delete-Token"],
        )
    application.add_middleware(
        SelectiveGZipMiddleware, minimum_size=1_000, compresslevel=1
    )
    application.add_middleware(SecurityHeadersMiddleware)
    application.add_middleware(
        ProxyHeadersMiddleware,
        trusted_hosts=(
            "*" if settings.trusted_proxies == ("*",) else list(settings.trusted_proxies)
        ),
    )

    application.mount(
        "/static",
        CachedStaticFiles(directory=str(settings.base_dir / "static")),
        name="static",
    )

    application.include_router(pages.router)
    application.include_router(shares.router)
    application.include_router(admin.router)
    _register_error_handlers(application)
    return application


def _register_error_handlers(application: FastAPI) -> None:
    @application.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        wants_json = (
            request.headers.get("accept") == "application/json"
            or request.url.path.startswith("/api/")
        )
        if wants_json:
            return JSONResponse(
                status_code=exc.status_code,
                content={"error": exc.detail, "status_code": exc.status_code},
                headers=getattr(exc, "headers", None),
            )
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context={
                "message": str(exc.detail),
                "status_code": exc.status_code,
                "title": f"Error {exc.status_code}",
            },
            status_code=exc.status_code,
        )

    @application.exception_handler(404)
    async def not_found_handler(request: Request, exc: Exception):
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                status_code=404,
                content={"error": "Not found", "status_code": 404},
            )
        return templates.TemplateResponse(
            request=request,
            name="expired.html",
            context={
                "message": (
                    "The page you're looking for doesn't exist. "
                    "Please check the URL and try again."
                ),
                "title": "Page Not Found",
                "is_expired": False,
                "show_home_button": True,
            },
            status_code=404,
        )

    @application.exception_handler(500)
    async def internal_error_handler(request: Request, exc: Exception):
        logger.error("Internal server error: %s", exc)
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                status_code=500,
                content={"error": "Internal server error", "status_code": 500},
            )
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context={
                "message": "An unexpected error occurred. Please try again later.",
                "status_code": 500,
                "title": "Server Error",
            },
            status_code=500,
        )


app = create_app()
