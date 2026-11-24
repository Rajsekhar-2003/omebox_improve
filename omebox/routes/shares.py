"""Create, unlock, view, download and delete share endpoints."""

import asyncio
import hmac
import logging
import os
import secrets
import shutil
import uuid
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pymongo.errors import DuplicateKeyError

from ..cleanup import cleanup
from ..config import settings
from ..database import db
from ..ratelimit import limiter
from ..security import (
    check_session_auth,
    hash_password,
    hash_token,
    make_session_token,
    parse_expiry,
    public_share,
    sanitize_filename,
    session_key,
    utcnow,
    valid_code,
    valid_share_id,
    verify_password,
)
from ..storage import encode_text, extract_text, generate_qr_code, storage
from ..templating import templates

logger = logging.getLogger("omebox.routes.shares")
router = APIRouter()


def error_page(request: Request, title: str, message: str, status_code: int = 500):
    return templates.TemplateResponse(
        request=request,
        name="error.html",
        context={"message": message, "status_code": status_code, "title": title},
        status_code=status_code,
    )


def expired_page(request: Request, title: str, message: str, status_code: int,
                 is_expired: bool = False, **extra):
    return templates.TemplateResponse(
        request=request,
        name="expired.html",
        context={"message": message, "title": title,
                 "is_expired": is_expired, **extra},
        status_code=status_code,
    )


def get_base_url(request: Request) -> str:
    """Prefer the configured public URL; never trust a spoofed Host header."""
    if settings.base_url:
        return settings.base_url.rstrip("/")
    scheme = "https" if settings.force_https else request.url.scheme
    return f"{scheme}://{request.url.netloc}"


async def insert_share(share_data: dict) -> str:
    """Insert a share with a unique 6-digit code (retries on collision)."""
    for _ in range(8):
        share_data["code_6digit"] = f"{secrets.randbelow(1_000_000):06d}"
        try:
            await db.shares.insert_one(share_data)
            return share_data["code_6digit"]
        except DuplicateKeyError as exc:
            if "code_6digit" in str(exc):
                share_data.pop("_id", None)
                continue
            raise
    raise HTTPException(status_code=503, detail="Could not generate unique code")


async def build_create_response(request: Request, share_data: dict, code: str,
                                delete_token: str, extra: dict) -> JSONResponse:
    share_url = f"{get_base_url(request)}/s/{share_data['share_id']}"
    qr = await asyncio.to_thread(generate_qr_code, share_url)
    return JSONResponse({
        "share_id": share_data["share_id"],
        "code": code,
        "url": share_url,
        "qr_code": qr,
        "expiry": share_data["expiry_option"],
        "share_type": share_data["share_type"],
        "has_password": share_data["has_password"],
        "delete_token": delete_token,
        **extra,
    })


async def get_active_share(share_id: str) -> dict:
    """Fetch a non-expired share or raise the correct API error."""
    if not valid_share_id(share_id):
        raise HTTPException(status_code=400, detail="Invalid share ID format")
    try:
        share = await db.shares.find_one({"share_id": share_id})
    except Exception as exc:
        logger.error("Database error fetching share %s: %s", share_id, exc)
        raise HTTPException(status_code=500, detail="Database error")
    if not share:
        raise HTTPException(status_code=404, detail="Share not found")
    if share["expiry"] < utcnow():
        raise HTTPException(status_code=410, detail="Share has expired")
    return share


async def get_active_share_for_request(share_id: str,
                                       background_tasks: BackgroundTasks) -> dict:
    try:
        return await get_active_share(share_id)
    except HTTPException as exc:
        if exc.status_code == 410:
            background_tasks.add_task(cleanup.delete_specific_share, share_id)
        raise


# ---------------------------------------------------------------------------
# Create shares
# ---------------------------------------------------------------------------
@router.post("/api/share/text")
@limiter.limit(settings.rate_limit)
async def create_text_share(
    request: Request,
    text: str = Form(""),
    expiry: str = Form("7d"),
    password: Optional[str] = Form(None),
):
    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="Text content is required")
    if len(text) > settings.max_text_length:
        raise HTTPException(
            status_code=413,
            detail=f"Text too long. Maximum {settings.max_text_length:,} characters.",
        )
    if password and len(password) > settings.max_password_len:
        raise HTTPException(
            status_code=400,
            detail=f"Password too long (max {settings.max_password_len})",
        )
    expiry_dt = parse_expiry(expiry)

    try:
        share_id = secrets.token_urlsafe(12)
        text_enc, is_compressed = await asyncio.to_thread(encode_text, text)
        delete_token = secrets.token_urlsafe(24)
        share_data = {
            "share_id": share_id,
            "text_enc": text_enc,
            "text_length": len(text),
            "share_type": "text",
            "expiry": expiry_dt,
            "created_at": utcnow(),
            "expiry_option": expiry,
            "views": 0,
            "is_compressed": is_compressed,
            "has_password": bool(password),
            "delete_token_hash": hash_token(delete_token),
        }
        if password:
            share_data["password_hash"] = await asyncio.to_thread(
                hash_password, password
            )
        code = await insert_share(share_data)
        return await build_create_response(request, share_data, code, delete_token, {})
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error creating text share: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to create share")


@router.post("/api/share/files")
@limiter.limit(settings.upload_rate_limit)
async def create_files_share(
    request: Request,
    expiry: str = Form("7d"),
    password: Optional[str] = Form(None),
    files: List[UploadFile] = File([]),
):
    valid_files = [item for item in files if item.filename]
    if not valid_files:
        raise HTTPException(status_code=400, detail="At least one file is required")
    if len(valid_files) > settings.max_files_per_share:
        raise HTTPException(
            status_code=413,
            detail=f"Maximum {settings.max_files_per_share} files per share",
        )
    if password and len(password) > settings.max_password_len:
        raise HTTPException(
            status_code=400,
            detail=f"Password too long (max {settings.max_password_len})",
        )
    expiry_dt = parse_expiry(expiry)

    share_id = secrets.token_urlsafe(12)
    share_dir = storage.share_dir(share_id)
    inserted = False
    try:
        os.makedirs(share_dir, exist_ok=True)
        saved_files, errors, total_size = [], [], 0
        for upload in valid_files:
            try:
                info = await storage.save_upload_file(upload, share_dir)
                saved_files.append(info)
                total_size += info["size"]
                if total_size > settings.max_share_size:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"Share exceeds {settings.max_share_size // (1024 * 1024)} "
                            "MB total limit"
                        ),
                    )
            except HTTPException as exc:
                if exc.status_code == 413 and "total limit" in str(exc.detail):
                    raise
                errors.append(f"File '{upload.filename}': {exc.detail}")
            except Exception as exc:
                logger.error("Error saving file %s: %s", upload.filename, exc)
                errors.append(f"File '{upload.filename}': Unexpected error")

        if not saved_files:
            raise HTTPException(
                status_code=400,
                detail=f"No valid files. Errors: {'; '.join(errors)}",
            )

        delete_token = secrets.token_urlsafe(24)
        share_data = {
            "share_id": share_id,
            "files": saved_files,
            "share_type": "files",
            "total_files": len(saved_files),
            "total_size": total_size,
            "expiry": expiry_dt,
            "created_at": utcnow(),
            "expiry_option": expiry,
            "views": 0,
            "has_password": bool(password),
            "delete_token_hash": hash_token(delete_token),
        }
        if password:
            share_data["password_hash"] = await asyncio.to_thread(
                hash_password, password
            )
        code = await insert_share(share_data)
        inserted = True
        extra = {"total_files": len(saved_files), "total_size": total_size}
        if errors:
            extra["warnings"] = errors
        return await build_create_response(request, share_data, code, delete_token, extra)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error creating file share: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to create share")
    finally:
        if not inserted:  # never leave orphaned files behind
            await asyncio.to_thread(shutil.rmtree, share_dir, True)


# ---------------------------------------------------------------------------
# View / unlock shares
# ---------------------------------------------------------------------------
@router.get("/s/{share_id}", response_class=HTMLResponse)
@limiter.limit(settings.rate_limit)
async def view_share(request: Request, share_id: str,
                     background_tasks: BackgroundTasks):
    if not valid_share_id(share_id):
        return error_page(request, "Invalid Link", "Invalid share ID format.", 400)

    try:
        share = await db.shares.find_one({"share_id": share_id})
    except Exception as exc:
        logger.error("Database error fetching share %s: %s", share_id, exc)
        return error_page(request, "Server Error", "Please try again later.", 500)

    if not share:
        return expired_page(
            request, "Share Not Found",
            "This share does not exist. It may have been deleted or the link is incorrect.",
            404,
        )
    if share["expiry"] < utcnow():
        background_tasks.add_task(cleanup.delete_specific_share, share_id)
        return expired_page(
            request, "Share Expired",
            "This share has expired and is no longer available.", 410,
            is_expired=True,
            expiry_date=share["expiry"].strftime("%Y-%m-%d %H:%M UTC"),
        )
    if share.get("has_password") and not check_session_auth(request.session, share):
        return templates.TemplateResponse(
            request=request, name="password.html",
            context={"share_id": share_id},
        )

    view = public_share(share)
    if share.get("share_type") == "text":
        try:
            view["text"] = await extract_text(share)
        except HTTPException:
            return error_page(request, "Decoding Error", "Failed to decode share content.", 500)

    background_tasks.add_task(
        db.shares.update_one, {"_id": share["_id"]}, {"$inc": {"views": 1}}
    )
    return templates.TemplateResponse(
        request=request, name="view.html",
        context={"share": view, "share_id": share_id},
    )


@router.post("/s/{share_id}/verify")
@limiter.limit(settings.verify_rate_limit)
async def verify_share_password(request: Request, share_id: str,
                                background_tasks: BackgroundTasks,
                                password: str = Form(...)):
    if not valid_share_id(share_id):
        return error_page(request, "Invalid Link", "Invalid share ID format.", 400)

    try:
        share = await db.shares.find_one({"share_id": share_id})
    except Exception as exc:
        logger.error("Database error verifying password: %s", exc)
        return error_page(request, "Server Error", "An error occurred. Please try again.", 500)

    if not share:
        return expired_page(request, "Share Not Found", "This share does not exist.", 404)
    if share["expiry"] < utcnow():
        background_tasks.add_task(cleanup.delete_specific_share, share_id)
        return expired_page(request, "Share Expired", "This share has expired.", 410,
                            is_expired=True)

    ok = False
    if share.get("password_hash") and len(password) <= settings.max_password_len:
        ok = await asyncio.to_thread(verify_password, password, share["password_hash"])
    if ok:
        request.session[session_key(share_id)] = make_session_token(share)
        return RedirectResponse(url=f"/s/{share_id}", status_code=302)

    return templates.TemplateResponse(
        request=request,
        name="password.html",
        context={"share_id": share_id, "error": "Invalid password"},
        status_code=403,
    )


@router.post("/access")
@limiter.limit(settings.access_rate_limit)
async def access_by_code(request: Request, background_tasks: BackgroundTasks,
                         code: str = Form(...)):
    if not valid_code(code):
        return templates.TemplateResponse(
            request=request, name="access.html",
            context={"error": "Please enter a valid 6-digit code."},
        )
    try:
        share = await db.shares.find_one({"code_6digit": code}, {"share_id": 1, "expiry": 1})
    except Exception as exc:
        logger.error("Database error accessing by code: %s", exc)
        return error_page(request, "Server Error", "An error occurred. Please try again.", 500)

    if not share:
        return templates.TemplateResponse(
            request=request, name="access.html",
            context={"error": "Invalid code. Please check and try again."},
        )
    if share["expiry"] < utcnow():
        background_tasks.add_task(cleanup.delete_specific_share, share["share_id"])
        return expired_page(request, "Share Expired", "This share has expired.", 410,
                            is_expired=True)
    return RedirectResponse(url=f"/s/{share['share_id']}", status_code=302)


# ---------------------------------------------------------------------------
# Download / raw / delete
# ---------------------------------------------------------------------------
@router.get("/api/share/{share_id}/file/{file_id}")
@limiter.limit(settings.rate_limit)
async def download_file(share_id: str, file_id: str, request: Request,
                        background_tasks: BackgroundTasks):
    try:
        uuid.UUID(file_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid file ID format")

    share = await get_active_share_for_request(share_id, background_tasks)
    if share.get("has_password") and not check_session_auth(request.session, share):
        raise HTTPException(status_code=403, detail="Password required")

    file_info = next(
        (item for item in share.get("files", []) if item.get("id") == file_id), None
    )
    if not file_info:
        raise HTTPException(status_code=404, detail="File not found in share")

    path = file_info.get("path", "")
    if (
        not storage.is_inside_upload(path)
        or not await asyncio.to_thread(os.path.isfile, path)
    ):
        raise HTTPException(status_code=404, detail="File not found on disk")

    mime = file_info.get("mime_type", "application/octet-stream")
    inline = mime in settings.safe_inline_mime
    headers = {
        "Cache-Control": "private, max-age=300",
        "X-Content-Type-Options": "nosniff",
    }
    if mime != "application/pdf":  # Chrome's PDF viewer breaks under a sandbox CSP
        headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return FileResponse(
        path,
        media_type=mime if inline else "application/octet-stream",
        filename=sanitize_filename(file_info.get("filename", "")),
        content_disposition_type="inline" if inline else "attachment",
        headers=headers,
    )


@router.get("/api/share/{share_id}/raw")
@limiter.limit(settings.rate_limit)
async def get_raw_share(request: Request, share_id: str,
                        background_tasks: BackgroundTasks):
    share = await get_active_share_for_request(share_id, background_tasks)
    if share.get("has_password") and not check_session_auth(request.session, share):
        raise HTTPException(status_code=403, detail="Password required")

    response = {
        "share_id": share["share_id"],
        "share_type": share["share_type"],
        "created_at": share["created_at"].isoformat(),
        "expiry": share["expiry"].isoformat(),
        "views": share.get("views", 0),
    }
    if share["share_type"] == "text":
        response["text"] = await extract_text(share)
        response["text_length"] = share.get("text_length", 0)
    else:
        response["files"] = [
            {
                "filename": item["filename"],
                "size": item["size"],
                "mime_type": item["mime_type"],
                "id": item["id"],
                "category": item.get("category", "other"),
            }
            for item in share.get("files", [])
        ]
        response["total_files"] = share.get("total_files", 0)
        response["total_size"] = share.get("total_size", 0)
    return JSONResponse(response)


@router.delete("/api/share/{share_id}")
@limiter.limit(settings.delete_rate_limit)
async def delete_share_endpoint(request: Request, share_id: str):
    """Delete a share using the X-Delete-Token returned at creation."""
    if not valid_share_id(share_id):
        raise HTTPException(status_code=400, detail="Invalid share ID format")
    try:
        share = await db.shares.find_one({"share_id": share_id})
    except Exception as exc:
        logger.error("Database error deleting share: %s", exc)
        raise HTTPException(status_code=500, detail="Database error")
    if not share:
        raise HTTPException(status_code=404, detail="Share not found")

    stored = share.get("delete_token_hash")
    supplied = request.headers.get("x-delete-token", "")
    if not stored or not supplied or not hmac.compare_digest(
        hash_token(supplied), stored
    ):
        raise HTTPException(status_code=403, detail="Invalid delete token")
    if not await cleanup.delete_specific_share(share_id):
        raise HTTPException(status_code=500, detail="Failed to delete share")
    return JSONResponse(
        {"success": True, "message": f"Share {share_id} deleted successfully"}
    )
