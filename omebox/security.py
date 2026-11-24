"""Security helpers: secrets, tokens, upload policy and output sanitisation."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import bcrypt

from filetypes import ALLOWED_MIME_TYPES, FILE_CATEGORIES
from .config import settings

SHARE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
CODE_RE = re.compile(r"^\d{6}$")

_PRIVATE_FIELDS = frozenset({
    "password_hash", "delete_token_hash", "text_enc", "text_b64",
})

ALLOWED_EXTENSIONS = frozenset(
    ext for extensions in FILE_CATEGORIES.values() for ext in extensions
)

# libmagic strings that must never be accepted, whatever the filename says.
DANGEROUS_MIME_FRAGMENTS = (
    "x-dosexec", "x-msdownload", "x-executable", "x-elf", "x-pie-executable",
    "x-sharedlib", "x-object", "x-shellscript", "x-java-applet",
    "x-shockwave-flash", "x-php", "x-httpd-php", "x-msi", "x-coff",
)

# libmagic often reports source documents as plain text or octet stream.
GENERIC_MIME = frozenset({
    "text/plain", "application/octet-stream", "application/xml",
    "application/json", "text/xml",
})

CATEGORY_MIME_PREFIX = {
    "image": ("image/",),
    "video": ("video/",),
    "audio": ("audio/",),
    "pdf": ("application/pdf",),
}


def utcnow() -> datetime:
    """Naive UTC datetime, matching values stored by MongoDB/Motor."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def valid_share_id(share_id: str) -> bool:
    return bool(share_id and SHARE_ID_RE.fullmatch(share_id))


def valid_code(code: str) -> bool:
    return bool(code and CODE_RE.fullmatch(code))


def _prehash(password: str) -> bytes:
    """SHA-256 + base64 keeps bcrypt input safely below its 72-byte limit."""
    return base64.b64encode(hashlib.sha256(password.encode("utf-8")).digest())


def hash_password(password: str) -> str:
    return bcrypt.hashpw(
        _prehash(password), bcrypt.gensalt(rounds=settings.bcrypt_rounds)
    ).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    if not password or not hashed:
        return False
    try:
        return bcrypt.checkpw(_prehash(password), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def make_session_token(share: dict) -> str:
    """Session proof bound to the share's id and password hash."""
    msg = f"{share['share_id']}:{share.get('password_hash', '')}".encode("utf-8")
    return hmac.new(settings.secret_key.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def session_key(share_id: str) -> str:
    return f"share_{share_id}_auth"


def check_session_auth(session: Any, share: dict) -> bool:
    token = session.get(session_key(share["share_id"])) if session else None
    if not token:
        return False
    return hmac.compare_digest(str(token), make_session_token(share))


def sanitize_filename(filename: str) -> str:
    # Treat Windows separators as separators even when running on Linux.
    filename = os.path.basename((filename or "").replace("\\", "/"))
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename)
    filename = filename.lstrip(". ")
    return filename[:200] or "unnamed"


def file_extension(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def get_file_category(filename: str, mime_type: str) -> str:
    ext = file_extension(filename)
    for category, extensions in FILE_CATEGORIES.items():
        if ext in extensions:
            return category
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("video/"):
        return "video"
    if mime_type.startswith("audio/"):
        return "audio"
    if mime_type == "application/pdf":
        return "pdf"
    if mime_type.startswith("text/"):
        return "text"
    return "other"


def is_file_allowed(filename: str, mime_type: str) -> bool:
    """Validate a file using both its extension and its detected content.

    The previous implementation accepted any content when the extension was
    known, which allowed executables to be disguised as images or documents.
    """
    ext = file_extension(sanitize_filename(filename))
    mime = (mime_type or "application/octet-stream").lower()
    if ext not in ALLOWED_EXTENSIONS:
        return False
    if any(fragment in mime for fragment in DANGEROUS_MIME_FRAGMENTS):
        return False

    category = get_file_category(filename, mime)
    expected = CATEGORY_MIME_PREFIX.get(category)
    if expected and not mime.startswith(expected):
        return False
    if mime in ALLOWED_MIME_TYPES:
        return True
    # Source/code files are commonly detected as plain text, and archives or
    # CAD files as octet-stream.  Allow those only for non-media categories.
    return mime in GENERIC_MIME and not expected


def public_share(share: dict) -> dict:
    """Copy of a share safe for templates: no hashes, plaintext or disk paths."""
    out: Dict[str, Any] = {
        key: value for key, value in share.items() if key not in _PRIVATE_FIELDS
    }
    if "files" in out:
        out["files"] = [
            {key: value for key, value in item.items() if key != "path"}
            for item in out["files"]
        ]
    return out


def is_inside_upload(path: str, root: Optional[str] = None) -> bool:
    root = os.path.realpath(root or settings.upload_dir)
    try:
        real = os.path.realpath(path)
        return real != root and os.path.commonpath([real, root]) == root
    except (ValueError, OSError):
        return False


def parse_expiry(option: str) -> datetime:
    delta = settings.expiry_options.get(option)
    if delta is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Invalid expiry option")
    return utcnow() + delta
