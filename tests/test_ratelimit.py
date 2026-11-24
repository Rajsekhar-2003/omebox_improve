from datetime import datetime, timedelta, timezone
import time

from limits import parse
from limits.strategies import FixedWindowRateLimiter

from omebox.database import db
from omebox.ratelimit import limiter, storage


def test_counter_increments_and_expires():
    storage.reset()
    assert storage.incr("ip:1", 60) == 1
    assert storage.incr("ip:1", 60) == 2
    assert storage.incr("ip:1", 60, amount=2) == 4
    assert storage.get("ip:1") == 4
    assert storage.get("missing") == 0
    assert storage.get_expiry("ip:1") > time.time()

    # MongoDB only expires BSON dates with a TTL index.
    document = storage.collection.find_one({"_id": "ip:1"})
    assert isinstance(document["expires_at"], datetime)

    storage.collection.update_one(
        {"_id": "ip:1"},
        {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}},
    )
    assert storage.get("ip:1") == 0
    assert storage.incr("ip:1", 60) == 1  # stale window restarts cleanly


def test_legacy_numeric_counter_is_treated_as_expired():
    storage.reset()
    storage.collection.insert_one({
        "_id": "legacy", "value": 99, "expires_at": time.time() - 1,
    })
    assert storage.get("legacy") == 0
    assert storage.incr("legacy", 60) == 1
    document = storage.collection.find_one({"_id": "legacy"})
    assert isinstance(document["expires_at"], datetime)


async def test_startup_removes_legacy_numeric_counters():
    await db.rate_limits.insert_one({
        "_id": "old", "value": 5, "expires_at": time.time() + 60,
    })
    await db.ensure_indexes()
    assert await db.rate_limits.find_one({"_id": "old"}) is None


def test_fixed_window_enforces_limit_across_calls():
    storage.reset()
    strategy = FixedWindowRateLimiter(storage)
    limit = parse("3/minute")
    assert [strategy.hit(limit, "client-a") for _ in range(4)] == [True, True, True, False]
    assert strategy.hit(limit, "client-b") is True
    stats = strategy.get_window_stats(limit, "client-a")
    assert stats.remaining == 0
    storage.clear(limit.key_for("client-a"))
    assert strategy.hit(limit, "client-a") is True
    assert limiter._storage is storage


def test_counter_collection_is_resettable():
    storage.reset()
    storage.incr("a", 60)
    storage.incr("b", 60)
    assert storage.reset() == 2
    assert storage.get("a") == 0
    assert storage.get("b") == 0
