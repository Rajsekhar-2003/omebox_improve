"""Expiry, orphan and temporary-file cleanup.

Runs in-process (APScheduler) but is safe with several uvicorn workers: a Mongo
lock guarantees that only one worker performs a cleanup cycle at a time.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import socket
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError, PyMongoError

from .config import settings
from .database import db as shared_db
from .security import SHARE_ID_RE, utcnow

logger = logging.getLogger("omebox.cleanup")

BATCH_SIZE = 100
LOCK_ID = "expired_shares_cleanup"


def _dir_stats(path: str) -> Tuple[int, int]:
    total, count = 0, 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
                count += 1
            except OSError:
                pass
    return total, count


def _remove_tree(path: str) -> Tuple[int, int]:
    if not os.path.isdir(path):
        return 0, 0
    stats = _dir_stats(path)
    shutil.rmtree(path, ignore_errors=True)
    return stats


def _remove_file(path: str) -> Optional[int]:
    try:
        size = os.path.getsize(path)
        os.remove(path)
        return size
    except FileNotFoundError:
        return None


def _is_stale(path: str, cutoff: datetime) -> bool:
    try:
        # File mtimes are epoch seconds; compare in naive UTC to match the
        # datetimes stored in MongoDB regardless of the server's timezone.
        modified = datetime.fromtimestamp(
            os.path.getmtime(path), timezone.utc
        ).replace(tzinfo=None)
        return modified < cutoff
    except OSError:
        return False


class omeboxCleanup:
    """Cleanup handler. Pass the app's ``db`` to reuse its Mongo connection."""

    def __init__(self, db=None, upload_dir: Optional[str] = None,
                 cache_dir: Optional[str] = None):
        self.upload_dir = os.path.abspath(
            upload_dir or str(settings.upload_dir)
        )
        self.cache_dir = os.path.abspath(cache_dir or str(settings.cache_dir))
        Path(self.upload_dir).mkdir(parents=True, exist_ok=True)
        Path(self.cache_dir).mkdir(parents=True, exist_ok=True)

        if db is None:
            self.client = AsyncIOMotorClient(
                settings.mongodb_url,
                maxPoolSize=10,
                minPoolSize=1,
                connectTimeoutMS=settings.mongo_connect_timeout_ms,
                socketTimeoutMS=settings.mongo_socket_timeout_ms,
            )
            self.db = self.client[settings.database_name]
        else:
            self.client = None
            self.db = db

        self.database_name = getattr(db, "name", settings.database_name) if db is not None else settings.database_name
        self.shares_collection = self.db.shares
        self.stats_collection = self.db.stats
        self.locks_collection = self.db.maintenance_locks
        self.owner = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex}"

    async def close(self) -> None:
        if self.client is not None:
            self.client.close()

    # ------------------------------------------------------------------
    # Path safety
    # ------------------------------------------------------------------
    def share_dir(self, share_id: Optional[str]) -> Optional[str]:
        if not share_id or not SHARE_ID_RE.fullmatch(share_id):
            return None
        return os.path.join(self.upload_dir, share_id)

    def inside_upload(self, path: str) -> bool:
        try:
            real = os.path.realpath(path)
            root = os.path.realpath(self.upload_dir)
            return real != root and os.path.commonpath([real, root]) == root
        except (ValueError, OSError):
            return False

    # ------------------------------------------------------------------
    # File removal for a single share
    # ------------------------------------------------------------------
    async def _delete_share_files(self, share: dict) -> Tuple[int, int]:
        freed = count = 0
        share_id = share.get("share_id")
        share_dir = self.share_dir(share_id)

        if share_dir:
            freed, count = await asyncio.to_thread(_remove_tree, share_dir)

        # Legacy layout: hash-named files shared between shares.  Only remove
        # ones that no other active share references.
        for info in share.get("files", []) or []:
            path = info.get("path")
            if not path or not self.inside_upload(path):
                continue
            if share_dir and os.path.realpath(path).startswith(
                os.path.realpath(share_dir) + os.sep
            ):
                continue
            still_used = await self.shares_collection.count_documents(
                {"share_id": {"$ne": share_id}, "files.path": path}, limit=1
            )
            if still_used:
                continue
            size = await asyncio.to_thread(_remove_file, path)
            if size is not None:
                freed += size
                count += 1
        return freed, count

    # ------------------------------------------------------------------
    # Core purge loop (DB record first, then files)
    # ------------------------------------------------------------------
    async def _purge(self, query: dict, stats: dict) -> None:
        projection = {"share_id": 1, "files": 1}
        while True:
            docs = await self.shares_collection.find(query, projection).limit(
                BATCH_SIZE
            ).to_list(BATCH_SIZE)
            if not docs:
                break

            ids = [document["_id"] for document in docs]
            try:
                result = await self.shares_collection.delete_many(
                    {"_id": {"$in": ids}}
                )
            except PyMongoError as exc:
                logger.error("Error deleting shares from database: %s", exc)
                stats["errors"] += len(ids)
                break

            stats["deleted_shares"] += result.deleted_count
            for document in docs:
                try:
                    freed, count = await self._delete_share_files(document)
                    stats["freed_bytes"] += freed
                    stats["deleted_files"] += count
                except Exception as exc:
                    logger.error(
                        "Error deleting files for share %s: %s",
                        document.get("share_id"), exc,
                    )
                    stats["errors"] += 1

            if result.deleted_count == 0:
                break

    @staticmethod
    def new_stats() -> dict:
        return {
            "deleted_shares": 0,
            "deleted_files": 0,
            "freed_bytes": 0,
            "errors": 0,
            "message": "",
        }

    # ------------------------------------------------------------------
    # Public operations
    # ------------------------------------------------------------------
    async def delete_expired_shares(self) -> dict:
        """Delete expired text and file shares together with their files."""
        stats = self.new_stats()
        started = utcnow()
        stats["start_time"] = started.isoformat()
        try:
            await self._purge({"expiry": {"$lt": utcnow()}}, stats)
            finished = utcnow()
            stats["end_time"] = finished.isoformat()
            stats["duration_seconds"] = (finished - started).total_seconds()
            await self._update_cleanup_stats(stats)
            stats["message"] = (
                f"Deleted {stats['deleted_shares']} expired shares"
                if stats["deleted_shares"] else "No expired shares found"
            )
        except Exception as exc:
            logger.exception("Fatal cleanup error")
            stats["fatal_error"] = str(exc)
            stats["message"] = f"Cleanup failed: {exc}"
        return stats

    async def delete_specific_share(self, share_id: str) -> bool:
        if not share_id or not SHARE_ID_RE.fullmatch(share_id):
            return False
        try:
            share = await self.shares_collection.find_one(
                {"share_id": share_id}, {"share_id": 1, "files": 1}
            )
            if not share:
                return False
            result = await self.shares_collection.delete_one({"_id": share["_id"]})
            if result.deleted_count == 0:
                return False
            await self._delete_share_files(share)
            logger.info("Deleted share %s", share_id)
            return True
        except Exception as exc:
            logger.error("Error deleting share %s: %s", share_id, exc)
            return False

    async def delete_shares_by_age(self, days: int) -> dict:
        stats = self.new_stats()
        cutoff = utcnow() - timedelta(days=days)
        await self._purge({"created_at": {"$lt": cutoff}}, stats)
        return stats

    async def delete_orphaned_uploads(self) -> dict:
        """Remove uploads with no DB record and stale in-progress temp files.

        The grace period protects a share that has been created on disk but
        whose Mongo document has not been written yet.
        """
        stats = {
            "deleted_orphan_dirs": 0,
            "deleted_temp_files": 0,
            "deleted_cache_files": 0,
            "deleted_files": 0,
            "freed_bytes": 0,
        }
        cutoff = utcnow() - timedelta(minutes=settings.orphan_grace_minutes)
        part_cutoff = utcnow() - timedelta(
            minutes=settings.part_file_max_age_minutes
        )

        def scan() -> dict:
            result = {
                "dirs": [], "temp": [], "cache": [],
                "bytes": 0, "files": 0,
            }
            with os.scandir(self.upload_dir) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if SHARE_ID_RE.fullmatch(entry.name) and _is_stale(entry.path, cutoff):
                                result["dirs"].append(entry.path)
                        elif entry.is_file(follow_symlinks=False) and (
                            entry.name.endswith(".part") or entry.name.startswith(".")
                        ) and _is_stale(entry.path, part_cutoff):
                            result["temp"].append(entry.path)
                    except OSError:
                        continue
            if os.path.isdir(self.cache_dir):
                with os.scandir(self.cache_dir) as entries:
                    for entry in entries:
                        try:
                            if entry.is_file(follow_symlinks=False) and _is_stale(
                                entry.path,
                                utcnow() - timedelta(minutes=settings.cache_max_age_minutes),
                            ):
                                result["cache"].append(entry.path)
                        except OSError:
                            continue
            return result

        found = await asyncio.to_thread(scan)
        for path in found["dirs"]:
            share_id = os.path.basename(path)
            exists = await self.shares_collection.find_one(
                {"share_id": share_id}, {"_id": 1}
            )
            if exists:
                continue
            freed, count = await asyncio.to_thread(_remove_tree, path)
            stats["deleted_orphan_dirs"] += 1
            stats["deleted_files"] += count
            stats["freed_bytes"] += freed

        for root, paths in (
            (self.upload_dir, found["temp"]),
            (self.cache_dir, found["cache"]),
        ):
            root_real = os.path.realpath(root)
            for path in paths:
                # Scanning is shallow, but re-check before deleting so a
                # symlink swapped in after the scan cannot escape the dir.
                if os.path.dirname(os.path.realpath(path)) != root_real:
                    continue
                if not os.path.isfile(path) or os.path.islink(path):
                    continue
                size = await asyncio.to_thread(_remove_file, path)
                if size is not None:
                    stats["freed_bytes"] += size
                    stats["deleted_files"] += 1
                    if path in found["temp"]:
                        stats["deleted_temp_files"] += 1
                    else:
                        stats["deleted_cache_files"] += 1

        if any(stats[key] for key in (
            "deleted_orphan_dirs", "deleted_temp_files", "deleted_cache_files"
        )):
            logger.info(
                "Removed %s orphan dirs and %s stale temp files",
                stats["deleted_orphan_dirs"],
                stats["deleted_temp_files"] + stats["deleted_cache_files"],
            )
        return stats

    # ------------------------------------------------------------------
    # Distributed lock for multi-worker deployments
    # ------------------------------------------------------------------
    async def acquire_lock(self) -> bool:
        now = utcnow()
        until = now + timedelta(seconds=settings.cleanup_lock_ttl_seconds)
        for _ in range(3):
            try:
                document = await self.locks_collection.find_one_and_update(
                    {
                        "_id": LOCK_ID,
                        "$or": [
                            {"locked_until": {"$lte": now}},
                            {"locked_until": {"$exists": False}},
                        ],
                    },
                    {"$set": {"locked_until": until, "owner": self.owner}},
                    upsert=True,
                    return_document=True,
                )
            except DuplicateKeyError:
                continue
            except PyMongoError as exc:
                logger.error("Could not acquire cleanup lock: %s", exc)
                return False
            if document and document.get("owner") == self.owner:
                return True
            return False
        return False

    async def release_lock(self) -> None:
        try:
            await self.locks_collection.delete_one(
                {"_id": LOCK_ID, "owner": self.owner}
            )
        except PyMongoError as exc:
            logger.error("Could not release cleanup lock: %s", exc)

    async def run_scheduled_cleanup(self) -> dict:
        """One scheduler cycle: expired shares, then orphans and temp files."""
        if not await self.acquire_lock():
            return {"skipped": True, "message": "Another worker holds the cleanup lock"}
        stats = self.new_stats()
        try:
            stats = await self.delete_expired_shares()
            orphan_stats = await self.delete_orphaned_uploads()
            stats.update({
                "deleted_orphan_dirs": orphan_stats["deleted_orphan_dirs"],
                "deleted_temp_files": orphan_stats["deleted_temp_files"],
                "deleted_cache_files": orphan_stats["deleted_cache_files"],
                "deleted_files": stats["deleted_files"] + orphan_stats["deleted_files"],
                "freed_bytes": stats["freed_bytes"] + orphan_stats["freed_bytes"],
            })
            return stats
        finally:
            stats["skipped"] = False
            await self.release_lock()

    async def _update_cleanup_stats(self, stats: dict) -> None:
        try:
            await self.stats_collection.update_one(
                {"_id": "cleanup_stats"},
                {
                    "$inc": {
                        "total_cleanups": 1,
                        "deleted_shares": stats["deleted_shares"],
                        "deleted_files": stats["deleted_files"],
                        "freed_bytes": stats["freed_bytes"],
                        "total_errors": stats["errors"],
                    },
                    "$set": {
                        "last_cleanup": utcnow(),
                        "last_cleanup_duration": stats.get("duration_seconds", 0),
                    },
                },
                upsert=True,
            )
        except PyMongoError as exc:
            logger.error("Error updating cleanup stats: %s", exc)

    async def get_cleanup_status(self) -> dict:
        now = utcnow()
        total_shares = await self.shares_collection.count_documents({})
        active_shares = await self.shares_collection.count_documents(
            {"expiry": {"$gt": now}}
        )
        expiring_soon = await self.shares_collection.count_documents(
            {"expiry": {"$gt": now, "$lt": now + timedelta(hours=1)}}
        )
        agg = await self.shares_collection.aggregate([
            {"$group": {
                "_id": None,
                "bytes": {"$sum": "$total_size"},
                "files": {"$sum": "$total_files"},
            }}
        ]).to_list(1)
        total_size = agg[0]["bytes"] if agg else 0
        file_count = agg[0]["files"] if agg else 0
        cleanup_stats = await self.stats_collection.find_one(
            {"_id": "cleanup_stats"}
        ) or {}

        try:
            usage = await asyncio.to_thread(shutil.disk_usage, self.upload_dir)
            disk = {
                "total_bytes": usage.total,
                "used_bytes": usage.used,
                "free_bytes": usage.free,
                "free_percent": round(100 * usage.free / usage.total, 2),
            }
        except OSError:
            disk = {}

        return {
            "timestamp": now.isoformat(),
            "database": {
                "name": self.database_name,
                "total_shares": total_shares,
                "active_shares": active_shares,
                "expired_shares": total_shares - active_shares,
                "expiring_soon": expiring_soon,
            },
            "storage": {
                "total_bytes": total_size,
                "total_mb": round(total_size / (1024 * 1024), 2),
                "total_files": file_count,
                "disk": disk,
            },
            "cleanup": {
                "last_cleanup": str(cleanup_stats.get("last_cleanup", "never")),
                "total_cleanups": cleanup_stats.get("total_cleanups", 0),
                "total_deleted_shares": cleanup_stats.get("deleted_shares", 0),
                "total_deleted_files": cleanup_stats.get("deleted_files", 0),
                "total_freed_mb": round(
                    cleanup_stats.get("freed_bytes", 0) / (1024 * 1024), 2
                ),
                "total_errors": cleanup_stats.get("total_errors", 0),
                "last_duration_seconds": cleanup_stats.get("last_cleanup_duration", 0),
            },
        }


cleanup = omeboxCleanup(db=shared_db)
