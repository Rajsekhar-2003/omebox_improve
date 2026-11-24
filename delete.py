"""Backward-compatible entry point: ``python delete.py --status``."""

from omebox.cleanup_cli import main

if __name__ == "__main__":
    main()
