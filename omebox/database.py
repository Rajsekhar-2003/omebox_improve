"""MongoDB connection and index management.

Motor/PyMongo are unchanged; only the pool is sized for a 4 GB VPS and the
collections are centralised so cleanup, rate limiting and the API share one
connection pool.
"""

from __future__ import annotations

import logging

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import PyMongoError

from .config import settings

logger = logging.getLogger("omebox.database")


class Database:
    def __init__(self) -> None:
        self.client = AsyncIOMotorClient(
            settings.mongodb_url,
            maxPoolSize=settings.mongo_max_pool_size,
            minPoolSize=settings.mongo_min_pool_size,
            maxIdleTimeMS=60_000,
            connectTimeoutMS=settings.mongo_connect_timeout_ms,
            socketTimeoutMS=settings.mongo_socket_timeout_ms,
            retryWrites=True,
        )
        self.db = self.client[settings.database_name]
        self.shares = self.db.shares
        self.stats = self.db.stats
        self.locks = self.db.maintenance_locks
        self.rate_limits = self.db.rate_limits
        # Friendly aliases used by maintenance helpers.
        self.maintenance_locks = self.locks

    async def ensure_indexes(self) -> None:
        indexes = (
            (self.shares, "share_id", {"unique": True}),
            (self.shares, "code_6digit", {"unique": True}),
            (self.shares, "expiry", {}),
            (self.shares, "created_at", {}),
            # TTL removes expired rate-limit counters without a second job.
            (self.rate_limits, "expires_at", {"expireAfterSeconds": 0}),
        )
        for collection, key, kwargs in indexes:
            try:
                await collection.create_index(key, **kwargs)
            except PyMongoError as exc:
                logger.error("Could not create index on %s: %s", key, exc)

        # TTL indexes only expire BSON dates.  Earlier builds stored epoch
        # seconds, which MongoDB never expires; drop those counters once so the
        # rate_limits collection cannot grow without bound after an upgrade.
        try:
            removed = 0
            for bson_type in ("double", "int", "long"):
                result = await self.rate_limits.delete_many({
                    "expires_at": {"$type": bson_type}
                })
                removed += result.deleted_count
            if removed:
                logger.info("Removed %s legacy rate-limit counters", removed)
        except PyMongoError as exc:
            logger.error("Could not migrate legacy rate-limit counters: %s", exc)

    async def ping(self) -> None:
        await self.db.command("ping")

    async def close(self) -> None:
        self.client.close()


db = Database()
