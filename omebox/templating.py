"""Jinja2 environment shared by all page routes."""

from fastapi.templating import Jinja2Templates

from .config import settings

templates = Jinja2Templates(directory=str(settings.base_dir / "templates"))

# Templates are deployed with the image, so there is no need to stat ~18 large
# files on every request in production.
templates.env.auto_reload = False
