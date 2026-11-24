"""Administrative, health and maintenance endpoints."""

from __future__ import annotations

import hmac
import logging
import time
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from ..cleanup import cleanup
from ..config import settings
from ..database import db
from ..ratelimit import limiter
from ..security import utcnow

logger = logging.getLogger("omebox.routes.admin")
router = APIRouter()


async def require_admin(request: Request) -> None:
    if not settings.admin_api_key:
        raise HTTPException(status_code=503, detail="Admin API disabled")
    supplied = request.headers.get("x-api-key", "")
    if not hmac.compare_digest(
        supplied.encode("utf-8"), settings.admin_api_key.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="Invalid API key")


_status_cache: dict = {"at": 0.0, "data": None}


async def cached_status() -> dict:
    if _status_cache["data"] is not None and time.monotonic() - _status_cache["at"] < 60:
        return _status_cache["data"]
    data = await cleanup.get_cleanup_status()
    _status_cache.update(at=time.monotonic(), data=data)
    return data


@router.get("/api/stats", dependencies=[Depends(require_admin)])
@limiter.limit(settings.admin_rate_limit)
async def get_system_stats(request: Request):
    try:
        return JSONResponse(await cached_status())
    except Exception:
        logger.exception("Stats error")
        raise HTTPException(status_code=500, detail="Failed to load stats")


@router.post("/api/cleanup", dependencies=[Depends(require_admin)])
@limiter.limit(settings.cleanup_rate_limit)
async def manual_cleanup(request: Request):
    try:
        stats = await cleanup.run_scheduled_cleanup()
        _status_cache["data"] = None
        return JSONResponse({
            "success": True,
            "message": "Cleanup completed",
            "stats": jsonable_encoder(stats),
        })
    except Exception as exc:
        logger.error("Manual cleanup error: %s", exc)
        raise HTTPException(status_code=500, detail="Cleanup failed")


@router.get("/api/maintenance/status", dependencies=[Depends(require_admin)])
@limiter.limit(settings.admin_rate_limit)
async def maintenance_status(request: Request):
    try:
        status = dict(await cached_status())
        scheduler = getattr(request.app.state, "scheduler", None)
        status["scheduler"] = {
            "running": bool(scheduler and scheduler.running),
            "jobs": [
                {
                    "id": job.id,
                    "name": job.name,
                    "next_run": str(job.next_run_time) if job.next_run_time else None,
                }
                for job in (scheduler.get_jobs() if scheduler else [])
            ],
        }
        return JSONResponse(status)
    except Exception:
        logger.exception("Maintenance status error")
        raise HTTPException(status_code=500, detail="Failed to load status")


@router.get("/health")
async def health_check():
    try:
        await db.ping()
        return JSONResponse({
            "status": "healthy",
            "timestamp": utcnow().isoformat(),
            "version": "3.0.0",
        })
    except Exception as exc:
        logger.error("Health check failed: %s", exc)
        return JSONResponse(
            {"status": "unhealthy", "timestamp": utcnow().isoformat()},
            status_code=503,
        )
