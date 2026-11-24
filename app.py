"""Entry point for uvicorn/gunicorn: ``app:app``.

The implementation lives in the ``omebox`` package; this module only keeps the
original import path working for existing deployment commands.
"""

from omebox.app import app, create_app

__all__ = ["app", "create_app"]
