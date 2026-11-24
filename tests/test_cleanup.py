import os
import time
from datetime import timedelta

from omebox.cleanup import LOCK_ID
from omebox.config import settings
from omebox.database import db
from omebox.security import utcnow


import secrets


async def make_share(share_id: str, expiry_delta=timedelta(hours=-1),
                     with_file: bool = False, code: str = None):
    files = []
    if with_file:
        directory = settings.upload_dir / share_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "file.txt"
        path.write_bytes(b"payload" * 100)
        files = [{
            "id": "file-id",
            "filename": "file.txt",
            "size": path.stat().st_size,
            "mime_type": "text/plain",
            "path": str(path),
        }]
    document = {
        "share_id": share_id,
        "code_6digit": code or f"{secrets.randbelow(1_000_000):06d}",
        "share_type": "files" if files else "text",
        "text_enc": "enc",
        "expiry": utcnow() + expiry_delta,
        "created_at": utcnow(),
        "expiry_option": "1h",
        "views": 0,
        "has_password": False,
        "files": files,
        "total_files": len(files),
        "total_size": sum(item["size"] for item in files),
    }
    await db.shares.insert_one(document)
    return document


async def test_expired_shares_and_files_are_deleted(cleanup_handler):
    await make_share("expired01", with_file=True)
    await make_share("active001", timedelta(hours=2), with_file=True)

    stats = await cleanup_handler.delete_expired_shares()
    assert stats["deleted_shares"] == 1
    assert stats["deleted_files"] == 1
    assert stats["freed_bytes"] > 0
    assert await db.shares.find_one({"share_id": "expired01"}) is None
    assert not (settings.upload_dir / "expired01").exists()
    assert (settings.upload_dir / "active001" / "file.txt").exists()

    status = await cleanup_handler.get_cleanup_status()
    assert status["cleanup"]["total_cleanups"] >= 1
    assert "disk" in status["storage"]


async def test_delete_specific_share(cleanup_handler):
    await make_share("delete001", timedelta(hours=2), with_file=True)
    assert await cleanup_handler.delete_specific_share("delete001")
    assert await db.shares.find_one({"share_id": "delete001"}) is None
    assert not (settings.upload_dir / "delete001").exists()
    assert not await cleanup_handler.delete_specific_share("delete001")
    assert not await cleanup_handler.delete_specific_share("../escape")


async def test_orphans_part_files_and_cache_are_removed(cleanup_handler):
    old = time.time() - 7200
    orphan = settings.upload_dir / "orphan0001"
    orphan.mkdir()
    (orphan / "old.txt").write_bytes(b"x" * 512)
    os.utime(orphan, (old, old))
    os.utime(orphan / "old.txt", (old, old))

    fresh = settings.upload_dir / "fresh0001"
    fresh.mkdir()

    part = settings.upload_dir / "upload.part"
    part.write_bytes(b"partial")
    os.utime(part, (old, old))

    cache_file = settings.cache_dir / "old-cache.bin"
    cache_file.write_bytes(b"cache")
    old_cache = time.time() - 3 * 86400
    os.utime(cache_file, (old_cache, old_cache))

    stats = await cleanup_handler.delete_orphaned_uploads()
    assert stats["deleted_orphan_dirs"] == 1
    assert stats["deleted_temp_files"] == 1
    assert stats["deleted_cache_files"] == 1
    assert not orphan.exists()
    assert fresh.exists()
    assert not part.exists()
    assert not cache_file.exists()


async def test_scheduler_lock_prevents_duplicate_cleanup(cleanup_handler):
    await make_share("locked001")
    other_owner = "another-worker"
    await db.locks.insert_one({
        "_id": LOCK_ID,
        "owner": other_owner,
        "locked_until": utcnow() + timedelta(minutes=5),
    })
    result = await cleanup_handler.run_scheduled_cleanup()
    assert result["skipped"] is True
    assert await db.shares.find_one({"share_id": "locked001"}) is not None

    await db.locks.delete_one({"_id": LOCK_ID})
    result = await cleanup_handler.run_scheduled_cleanup()
    assert result["skipped"] is False
    assert await db.shares.find_one({"share_id": "locked001"}) is None
    assert await db.locks.find_one({"_id": LOCK_ID}) is None
