"""Shared, MongoDB-backed rate limiting.

slowapi's in-memory storage gives every uvicorn worker its own counter, so a
2-worker setup effectively doubles each configured limit.  A small storage
adapter backed by the existing MongoDB keeps limits consistent across workers
without adding Redis.  Counters expire through a TTL index on the collection,
so no separate cleanup job is needed.

MongoDB TTL indexes only expire **BSON dates**.  ``expires_at`` is therefore
stored as a UTC datetime (not an epoch float); ``Database.ensure_indexes()``
also drops any legacy numeric counters left by older builds on startup.

The adapter implements the synchronous ``limits.storage.Storage`` interface
that slowapi 0.1.x expects.  Calls are single, indexed Mongo operations and are
made only on rate-limited endpoints.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from limits.storage import Storage
from limits.strategies import FixedWindowRateLimiter
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError
from slowapi import Limiter
from slowapi.util import get_remote_address

from .config import settings

logger = logging.getLogger("omebox.ratelimit")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_datetime(value: object) -> Optional[datetime]:
    """Accept the current BSON date format and legacy epoch-second values."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    return None


class MongoRateLimitStorage(Storage):
    """Minimal storage implementing slowapi's fixed-window contract."""

    STORAGE_SCHEME = ["mongodb"]
    RETRIES = 4

    def __init__(self, collection, wrap_exceptions: bool = False, **_: object):
        self.collection = collection
        super().__init__(wrap_exceptions=wrap_exceptions, **_)

    @property
    def base_exceptions(self):
        return PyMongoError

    def incr(self, key: str, expiry: int, amount: int = 1) -> int:
        for _ in range(self.RETRIES):
            now = _utcnow()
            fresh = self.collection.find_one_and_update(
                {"_id": key, "expires_at": {"$gt": now}},
                {"$inc": {"value": amount}},
                return_document=ReturnDocument.AFTER,
            )
            if fresh is not None:
                return int(fresh.get("value", amount))

            # Reset an expired (or legacy) window atomically instead of
            # racing another worker's insert.  Numeric dates sort below BSON
            # dates, so legacy counters match "$lte" and are migrated here.
            reset = self.collection.find_one_and_update(
                {
                    "_id": key,
                    "$or": [
                        {"expires_at": {"$lte": now}},
                        {"expires_at": {"$exists": False}},
                    ],
                },
                {
                    "$set": {
                        "value": amount,
                        "expires_at": now + timedelta(seconds=expiry),
                    }
                },
                return_document=ReturnDocument.AFTER,
            )
            if reset is not None:
                return amount

            # mongomock (and mixed-version deployments) can hold counters with
            # an epoch number instead of a BSON date.  Those never match the
            # date comparisons above, so migrate them explicitly.
            existing = self.collection.find_one({"_id": key})
            if existing is not None and not isinstance(
                existing.get("expires_at"), datetime
            ):
                self.collection.update_one(
                    {"_id": key},
                    {"$set": {
                        "value": amount,
                        "expires_at": now + timedelta(seconds=expiry),
                    }},
                )
                return amount

            try:
                self.collection.insert_one({
                    "_id": key,
                    "value": amount,
                    "expires_at": _utcnow() + timedelta(seconds=expiry),
                })
                return amount
            except DuplicateKeyError:
                continue
        logger.warning("Rate limit counter %s could not be updated", key)
        return self.get(key)

    def get(self, key: str) -> int:
        document = self.collection.find_one({"_id": key})
        if not document:
            return 0
        expires_at = _as_datetime(document.get("expires_at"))
        if expires_at is None or expires_at <= _utcnow():
            return 0
        return int(document.get("value", 0))

    def get_expiry(self, key: str) -> float:
        document = self.collection.find_one({"_id": key})
        if not document:
            return time.time()
        expires_at = _as_datetime(document.get("expires_at"))
        return expires_at.timestamp() if expires_at else time.time()

    def check(self) -> bool:
        try:
            self.collection.database.command("ping")
            return True
        except PyMongoError:
            return False

    def reset(self) -> Optional[int]:
        return self.collection.delete_many({}).deleted_count

    def clear(self, key: str) -> None:
        self.collection.delete_one({"_id": key})


rate_client = MongoClient(
    settings.mongodb_url,
    maxPoolSize=10,
    minPoolSize=0,
    connectTimeoutMS=settings.mongo_connect_timeout_ms,
    serverSelectionTimeoutMS=settings.mongo_connect_timeout_ms,
    retryWrites=True,
)
storage = MongoRateLimitStorage(
    rate_client[settings.database_name].rate_limits
)

limiter = Limiter(key_func=get_remote_address, strategy="fixed-window",
                  headers_enabled=True)

# slowapi builds its strategy from a URI string at construction time.  Swapping
# the adapter afterwards keeps its decorators and 429 handling unchanged while
# sharing counters across uvicorn workers.
limiter._storage = storage
limiter._limiter = FixedWindowRateLimiter(storage)
