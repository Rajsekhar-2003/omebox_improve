"""OmeBox / ShareBin application package.

The package is intentionally split by responsibility so that the HTTP layer,
storage, cleanup and security policy can be tested independently.  The public
ASGI application remains available as ``app:app`` from the project root.
"""

from .app import app

__all__ = ["app"]
