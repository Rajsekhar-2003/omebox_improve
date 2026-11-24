"""Test bootstrap: deterministic secrets and in-memory MongoDB/file storage."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

_UPLOAD_DIR = tempfile.mkdtemp(prefix="omebox-test-uploads-")
_CACHE_DIR = tempfile.mkdtemp(prefix="omebox-test-cache-")

os.environ.update({
    "SECRET_KEY": "test-secret-key-" + "x" * 48,
    "ENCRYPTION_KEY": Fernet.generate_key().decode(),
    "MONGODB_URL": "mongodb://127.0.0.1:27017",
    "DATABASE_NAME": "omebox_test",
    "BASE_URL": "http://testserver",
    "ADMIN_API_KEY": "test-admin-key",
    "FORCE_HTTPS": "0",
    "UPLOAD_DIR": _UPLOAD_DIR,
    "CACHE_DIR": _CACHE_DIR,
    "BCRYPT_ROUNDS": "4",
    "RATE_LIMIT": "1000/minute",
    "UPLOAD_RATE_LIMIT": "1000/minute",
    "VERIFY_RATE_LIMIT": "1000/minute",
    "ACCESS_RATE_LIMIT": "1000/minute",
    "DELETE_RATE_LIMIT": "1000/minute",
    "ADMIN_RATE_LIMIT": "1000/minute",
    "CLEANUP_RATE_LIMIT": "1000/minute",
    "CLEANUP_INTERVAL_MINUTES": "60",
    "ENABLE_DOCS": "0",
})

# Patch the Mongo drivers before any omebox module creates its singleton
# clients.  No external database or network access is needed for the suite.
import motor.motor_asyncio  # noqa: E402
import mongomock  # noqa: E402
import mongomock_motor  # noqa: E402
import pymongo  # noqa: E402

motor.motor_asyncio.AsyncIOMotorClient = mongomock_motor.AsyncMongoMockClient
pymongo.MongoClient = mongomock.MongoClient

import app as app_module  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from omebox.cleanup import cleanup  # noqa: E402
from omebox.config import settings  # noqa: E402
from omebox.database import db  # noqa: E402
from omebox.ratelimit import limiter  # noqa: E402


@pytest.fixture(autouse=True)
def reset_storage():
    """Clear uploads and rate counters between tests."""
    for directory in (settings.upload_dir, settings.cache_dir):
        for entry in Path(directory).iterdir():
            path = Path(entry)
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
    limiter._storage.reset()
    yield


import pytest_asyncio  # noqa: E402


@pytest_asyncio.fixture
async def client():
    """ASGI client with the real lifespan (indexes, scheduler, startup cleanup)."""
    async with app_module.app.router.lifespan_context(app_module.app):
        await db.shares.delete_many({})
        await db.stats.delete_many({})
        await db.locks.delete_many({})
        limiter._storage.reset()
        transport = ASGITransport(app=app_module.app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as http:
            yield http


@pytest.fixture
def app_instance():
    return app_module.app


@pytest.fixture
def cleanup_handler():
    return cleanup
