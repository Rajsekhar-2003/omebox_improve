"""Central configuration for OmeBox.

All environment access lives here.  A single immutable ``Settings`` instance is
created at import time so every module uses exactly the same configuration.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Tuple

from cryptography.fernet import Fernet
from dotenv import load_dotenv

logger = logging.getLogger("omebox.config")

BASE_DIR = Path(__file__).resolve().parent.parent

# Shell/CLI runs (uvicorn, delete.py) do not have systemd's EnvironmentFile or
# Compose's env_file.  Load .env explicitly, but never override variables that
# were exported by the deployment environment or by the test suite.
load_dotenv(BASE_DIR / ".env", override=False)

DEFAULT_CSP = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "form-action 'self'; "
    "script-src 'self' 'unsafe-inline' https://www.googletagmanager.com "
    "https://www.google-analytics.com https://pagead2.googlesyndication.com "
    "https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com "
    "https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
    "font-src 'self' data: https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
    "img-src 'self' data: blob: https:; "
    "connect-src 'self' https://www.google-analytics.com "
    "https://pagead2.googlesyndication.com https://googleads.g.doubleclick.net; "
    "frame-src https://www.google.com https://googleads.g.doubleclick.net "
    "https://pagead2.googlesyndication.com; "
    "media-src 'self' blob:; "
    "upgrade-insecure-requests"
)

SAFE_INLINE_MIME = frozenset({
    "image/jpeg", "image/png", "image/gif", "image/webp",
    "video/mp4", "video/webm",
    "audio/mpeg", "audio/ogg", "audio/wav",
    "application/pdf",
})

EXPIRY_OPTIONS = {
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "1d": timedelta(days=1),
    "3d": timedelta(days=3),
    "7d": timedelta(days=7),
}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str) -> Tuple[str, ...]:
    return tuple(
        item.strip() for item in os.environ.get(name, "").split(",") if item.strip()
    )


def _env_first(name: str, legacy_name: str, default: str = "") -> str:
    """Read ``name``, falling back to a legacy variable from older .env files."""
    value = _env(name)
    if value:
        return value
    legacy = _env(legacy_name)
    if legacy:
        logger.warning("%s is deprecated; rename it to %s", legacy_name, name)
    return legacy or default


@dataclass(frozen=True)
class Settings:
    """Validated runtime configuration."""

    secret_key: str
    encryption_key: str
    mongodb_url: str

    database_name: str = "omebox"
    base_dir: Path = BASE_DIR
    upload_dir: Path = field(default_factory=lambda: BASE_DIR / "uploads")
    cache_dir: Path = field(default_factory=lambda: BASE_DIR / "cache")

    max_file_size: int = 100 * 1024 * 1024
    max_share_size: int = 500 * 1024 * 1024
    max_text_length: int = 200_000
    max_password_len: int = 128
    max_files_per_share: int = 20

    rate_limit: str = "200/minute"
    upload_rate_limit: str = "20/minute"
    verify_rate_limit: str = "10/minute"
    access_rate_limit: str = "5/minute"
    delete_rate_limit: str = "10/minute"
    admin_rate_limit: str = "30/minute"
    cleanup_rate_limit: str = "5/minute"

    force_https: bool = True
    base_url: str = ""
    admin_api_key: str = ""
    trusted_proxies: Tuple[str, ...] = ("127.0.0.1",)
    allowed_origins: Tuple[str, ...] = ()
    session_max_age: int = 7 * 24 * 3600
    bcrypt_rounds: int = 10

    mongo_max_pool_size: int = 30
    mongo_min_pool_size: int = 2
    mongo_connect_timeout_ms: int = 5_000
    mongo_socket_timeout_ms: int = 30_000

    cleanup_enabled: bool = True
    cleanup_interval_minutes: int = 5
    cleanup_lock_ttl_seconds: int = 600
    orphan_grace_minutes: int = 30
    part_file_max_age_minutes: int = 60
    cache_max_age_minutes: int = 1_440
    static_cache_max_age: int = 3_600

    csp: str = DEFAULT_CSP
    enable_docs: bool = False
    log_level: str = "INFO"

    expiry_options: dict = field(default_factory=lambda: dict(EXPIRY_OPTIONS))
    safe_inline_mime: frozenset = field(default_factory=lambda: SAFE_INLINE_MIME)

    def __post_init__(self) -> None:
        if self.secret_key.startswith("replace-with") or len(self.secret_key) < 32:
            raise RuntimeError(
                "SECRET_KEY must be at least 32 random characters; "
                "copy .env.example to .env and replace the placeholders"
            )
        try:
            Fernet(self.encryption_key.encode("utf-8"))
        except Exception as exc:
            raise RuntimeError("ENCRYPTION_KEY is not a valid Fernet key") from exc
        if self.admin_api_key.startswith("replace-with"):
            raise RuntimeError("ADMIN_API_KEY is still the .env.example placeholder")
        if not self.mongodb_url:
            raise RuntimeError("MONGODB_URL environment variable is required")
        if not 4 <= self.bcrypt_rounds <= 15:
            raise RuntimeError("BCRYPT_ROUNDS must be between 4 and 15")
        if "*" in self.trusted_proxies:
            logger.warning(
                "TRUSTED_PROXIES='*' allows clients to spoof their IP address; "
                "restrict it to your reverse proxy"
            )
        if "*" in self.allowed_origins:
            raise RuntimeError("ALLOWED_ORIGINS='*' cannot be used with credentials")

        # absolute, normalised paths keep filesystem checks reliable regardless
        # of the process working directory (systemd, Docker, tests, CLI).
        for name in ("upload_dir", "cache_dir"):
            value = Path(getattr(self, name)).expanduser().resolve()
            object.__setattr__(self, name, value)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def base_url_host(self) -> str:
        return self.base_url.rstrip("/") if self.base_url else ""

    def public_url(self, share_id: str) -> str:
        base = self.base_url.rstrip("/")
        return f"{base}/s/{share_id}" if base else f"/s/{share_id}"


def load_settings() -> Settings:
    """Build settings from the environment with production-friendly defaults."""
    settings = Settings(
        secret_key=_env("SECRET_KEY"),
        encryption_key=_env("ENCRYPTION_KEY"),
        mongodb_url=_env("MONGODB_URL"),
        database_name=_env("DATABASE_NAME", "omebox"),
        upload_dir=Path(_env("UPLOAD_DIR", str(BASE_DIR / "uploads"))),
        cache_dir=Path(_env("CACHE_DIR", str(BASE_DIR / "cache"))),
        max_file_size=_env_int("MAX_FILE_SIZE", 100 * 1024 * 1024),
        max_share_size=_env_int("MAX_SHARE_SIZE", 500 * 1024 * 1024),
        max_text_length=_env_int("MAX_TEXT_LENGTH", 200_000),
        max_password_len=_env_int("MAX_PASSWORD_LEN", 128),
        max_files_per_share=_env_int("MAX_FILES_PER_SHARE", 20),
        rate_limit=_env("RATE_LIMIT", "200/minute"),
        upload_rate_limit=_env_first(
            "UPLOAD_RATE_LIMIT", "FILE_UPLOAD_RATE_LIMIT", "20/minute"
        ),
        verify_rate_limit=_env("VERIFY_RATE_LIMIT", "10/minute"),
        access_rate_limit=_env("ACCESS_RATE_LIMIT", "5/minute"),
        delete_rate_limit=_env("DELETE_RATE_LIMIT", "10/minute"),
        admin_rate_limit=_env("ADMIN_RATE_LIMIT", "30/minute"),
        cleanup_rate_limit=_env("CLEANUP_RATE_LIMIT", "5/minute"),
        force_https=_env_bool("FORCE_HTTPS", True),
        base_url=_env("BASE_URL"),
        admin_api_key=_env_first("ADMIN_API_KEY", "ADMIN_TOKEN"),
        trusted_proxies=_env_list("TRUSTED_PROXIES") or ("127.0.0.1",),
        allowed_origins=_env_list("ALLOWED_ORIGINS"),
        session_max_age=_env_int("SESSION_MAX_AGE", 7 * 24 * 3600),
        bcrypt_rounds=_env_int("BCRYPT_ROUNDS", 10),
        mongo_max_pool_size=_env_int("MONGO_MAX_POOL_SIZE", 30),
        mongo_min_pool_size=_env_int("MONGO_MIN_POOL_SIZE", 2),
        mongo_connect_timeout_ms=_env_int("MONGO_CONNECT_TIMEOUT_MS", 5_000),
        mongo_socket_timeout_ms=_env_int("MONGO_SOCKET_TIMEOUT_MS", 30_000),
        cleanup_enabled=_env_bool("CLEANUP_ENABLED", True),
        cleanup_interval_minutes=_env_int("CLEANUP_INTERVAL_MINUTES", 5),
        cleanup_lock_ttl_seconds=_env_int("CLEANUP_LOCK_TTL_SECONDS", 600),
        orphan_grace_minutes=_env_int("ORPHAN_GRACE_MINUTES", 30),
        part_file_max_age_minutes=_env_int("PART_FILE_MAX_AGE_MINUTES", 60),
        cache_max_age_minutes=_env_int("CACHE_MAX_AGE_MINUTES", 1_440),
        static_cache_max_age=_env_int("STATIC_CACHE_MAX_AGE", 3_600),
        csp=_env("CSP_HEADER", DEFAULT_CSP),
        enable_docs=_env_bool("ENABLE_DOCS", False),
        log_level=_env("LOG_LEVEL", "INFO").upper(),
    )
    return settings


settings = load_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

if not settings.admin_api_key:
    logger.warning(
        "ADMIN_API_KEY is unset; /api/stats, /api/cleanup and "
        "/api/maintenance/status are disabled"
    )
if not settings.base_url:
    logger.warning(
        "BASE_URL is unset; share links fall back to the request Host header. "
        "Set BASE_URL in production to prevent Host-header poisoning."
    )
