"""Command-line interface for maintenance operations."""

from __future__ import annotations

import argparse
import asyncio
import logging

from .cleanup import omeboxCleanup


def _confirm(prompt: str) -> bool:
    return input(f"\n{prompt} (y/N): ").strip().lower() == "y"


async def show_status(cleanup: omeboxCleanup) -> None:
    status = await cleanup.get_cleanup_status()
    db, storage, cl = status["database"], status["storage"], status["cleanup"]
    print("\n" + "=" * 60)
    print("OmeBox System Status")
    print("=" * 60)
    print(f"Database: {db['name']}")
    print(
        f"  Total: {db['total_shares']}  Active: {db['active_shares']}  "
        f"Expired: {db['expired_shares']}  Expiring soon: {db['expiring_soon']}"
    )
    print(f"Storage: {storage['total_mb']} MB in {storage['total_files']} files")
    if storage.get("disk"):
        print(
            f"Disk: {storage['disk']['free_bytes'] // (1024 ** 3)} GB free "
            f"of {storage['disk']['total_bytes'] // (1024 ** 3)} GB"
        )
    print(
        f"Cleanup: last={cl['last_cleanup']}  runs={cl['total_cleanups']}  "
        f"shares={cl['total_deleted_shares']}  files={cl['total_deleted_files']}  "
        f"freed={cl['total_freed_mb']} MB  errors={cl['total_errors']}"
    )
    if db["expired_shares"]:
        print(f"\n{db['expired_shares']} expired shares need cleanup")


async def run_cleanup(cleanup: omeboxCleanup, force: bool) -> None:
    status = await cleanup.get_cleanup_status()
    expired = status["database"]["expired_shares"]
    print(f"\nFound {expired} expired shares")
    if expired == 0:
        return
    if not force and not _confirm("Proceed with deletion?"):
        print("Cancelled.")
        return
    stats = await cleanup.delete_expired_shares()
    print(
        f"Shares deleted: {stats['deleted_shares']}  "
        f"Files: {stats['deleted_files']}  "
        f"Freed: {stats['freed_bytes'] / (1024 * 1024):.2f} MB  "
        f"Errors: {stats['errors']}"
    )


async def delete_single_share(cleanup: omeboxCleanup, share_id: str,
                              force: bool) -> None:
    if not force and not _confirm(f"Delete share '{share_id}'?"):
        print("Cancelled.")
        return
    ok = await cleanup.delete_specific_share(share_id)
    print(
        f"Share '{share_id}' deleted." if ok
        else f"Could not delete '{share_id}' (not found?)."
    )


async def delete_old_shares(cleanup: omeboxCleanup, days: int,
                            force: bool) -> None:
    if not force and not _confirm(f"Delete ALL shares older than {days} days?"):
        print("Cancelled.")
        return
    stats = await cleanup.delete_shares_by_age(days)
    print(
        f"Shares deleted: {stats['deleted_shares']}  "
        f"Files: {stats['deleted_files']}  "
        f"Freed: {stats['freed_bytes'] / (1024 * 1024):.2f} MB"
    )


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="OmeBox Cleanup & Delete Operations",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  python delete.py --status
  python delete.py --cleanup --force
  python delete.py --delete-share abc123
  python delete.py --delete-older-than 30
""",
    )
    parser.add_argument("--cleanup", action="store_true",
                        help="Delete all expired shares and files")
    parser.add_argument("--delete-share", metavar="SHARE_ID",
                        help="Delete a share by share_id")
    parser.add_argument("--delete-older-than", type=int, metavar="DAYS",
                        help="Delete shares older than N days")
    parser.add_argument("--status", action="store_true", help="Show status")
    parser.add_argument("--orphans", action="store_true",
                        help="Delete orphaned uploads and stale temp files")
    parser.add_argument("--force", action="store_true", help="Skip confirmation")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    cleanup = omeboxCleanup()
    try:
        if args.status:
            await show_status(cleanup)
        elif args.cleanup:
            await run_cleanup(cleanup, args.force)
        elif args.delete_share:
            await delete_single_share(cleanup, args.delete_share, args.force)
        elif args.delete_older_than:
            await delete_old_shares(cleanup, args.delete_older_than, args.force)
        elif args.orphans:
            stats = await cleanup.delete_orphaned_uploads()
            print(f"Orphan cleanup: {stats}")
        else:
            parser.print_help()
    finally:
        await cleanup.close()


if __name__ == "__main__":
    asyncio.run(main())
