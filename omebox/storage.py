"""Local filesystem storage plus text encryption helpers."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import mimetypes
import os
import re
import uuid
import zlib
from functools import lru_cache
from io import BytesIO
from typing import Any, Dict, Optional

import aiofiles
import magic
import qrcode
from cryptography.fernet import Fernet
from fastapi import HTTPException, UploadFile

from .config import settings
from .security import (
    get_file_category,
    is_file_allowed,
    is_inside_upload,
    sanitize_filename,
)

logger = logging.getLogger("omebox.storage")

UPLOAD_CHUNK = 65_536
MIME_SNIFF_BYTES = 8_192

cipher = Fernet(settings.encryption_key.encode("utf-8"))


@lru_cache(maxsize=2_000)
def cached_mime_type(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


def detect_mime(head: bytes, filename: str) -> str:
    try:
        return magic.from_buffer(head, mime=True) or cached_mime_type(filename)
    except Exception as exc:
        logger.error("MIME detection error: %s", exc)
        return cached_mime_type(filename)


def encode_text(text: str) -> tuple[str, bool]:
    """Compress when worthwhile, then encrypt.  Returns (token, compressed)."""
    raw = text.encode("utf-8")
    compressed = False
    if len(raw) > 5_000:
        packed = zlib.compress(raw, level=6)
        if len(packed) < len(raw):
            raw, compressed = packed, True
    return cipher.encrypt(raw).decode("utf-8"), compressed


def decode_text(share: dict) -> str:
    if "text_enc" in share:
        data = cipher.decrypt(share["text_enc"].encode("utf-8"))
    else:  # legacy shares stored as base64
        data = base64.b64decode(share["text_b64"])
    if share.get("is_compressed"):
        data = zlib.decompress(data)
    return data.decode("utf-8")


async def extract_text(share: dict) -> str:
    try:
        return await asyncio.to_thread(decode_text, share)
    except Exception as exc:
        logger.error("Text extraction error: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to decode text content")


def generate_qr_code(data: str) -> str:
    qr = qrcode.QRCode(version=1, box_size=6, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


class LocalStorage:
    """All uploads stay on the VPS disk under ``settings.upload_dir``."""

    def share_dir(self, share_id: str) -> str:
        return os.path.join(str(settings.upload_dir), share_id)

    def is_inside_upload(self, path: str) -> bool:
        return is_inside_upload(path, str(settings.upload_dir))

    async def save_upload_file(self, upload: UploadFile, share_dir: str) -> Dict[str, Any]:
        """Stream one upload to disk, validating size, type and content."""
        filename = sanitize_filename(upload.filename or "unnamed")
        file_id = str(uuid.uuid4())
        ext = re.sub(r"[^A-Za-z0-9.]", "", os.path.splitext(filename)[1])[:16]
        final_path = os.path.join(share_dir, f"{file_id}{ext}")
        tmp_path = final_path + ".part"

        hasher = hashlib.sha256()
        head = b""
        total = 0
        try:
            async with aiofiles.open(tmp_path, "wb") as out:
                while chunk := await upload.read(UPLOAD_CHUNK):
                    total += len(chunk)
                    if total > settings.max_file_size:
                        raise HTTPException(
                            status_code=413,
                            detail=(
                                f"File '{filename}' exceeds "
                                f"{settings.max_file_size // (1024 * 1024)} MB limit"
                            ),
                        )
                    if len(head) < MIME_SNIFF_BYTES:
                        head += chunk[: MIME_SNIFF_BYTES - len(head)]
                    hasher.update(chunk)
                    await out.write(chunk)

            if total == 0:
                raise HTTPException(status_code=400, detail="Empty file not allowed")

            mime = await asyncio.to_thread(detect_mime, head, filename)
            if not is_file_allowed(filename, mime):
                logger.warning("Rejected upload %r with detected MIME %s", filename, mime)
                raise HTTPException(status_code=415, detail=f"File type not allowed: {mime}")

            os.replace(tmp_path, final_path)
        except BaseException:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise

        return {
            "id": file_id,
            "filename": filename,
            "size": total,
            "mime_type": mime,
            "path": final_path,
            "hash": hasher.hexdigest(),
            "category": get_file_category(filename, mime),
        }


storage = LocalStorage()
