"""Landing, shared-content entry and informational pages."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse

from ..config import settings
from ..templating import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "max_file_size_mb": settings.max_file_size // (1024 * 1024),
            "max_text_length": settings.max_text_length,
        },
    )


@router.get("/share/text", response_class=HTMLResponse)
async def share_text_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="share_text.html",
        context={"max_text_length": settings.max_text_length},
    )


@router.get("/share/files", response_class=HTMLResponse)
async def share_files_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="share_files.html",
        context={"max_file_size_mb": settings.max_file_size // (1024 * 1024)},
    )


@router.get("/access", response_class=HTMLResponse)
async def access_page(request: Request):
    return templates.TemplateResponse(request=request, name="access.html")


STATIC_PAGES = (
    ("/main", "home.html", "Home Page"),
    ("/about", "about.html", "about Page"),
    ("/contact", "contact.html", "Contact"),
    ("/anonymous-file-share", "anonymous.html", "anonymous"),
    ("/omebox-article", "article.html", "OmeBox Article"),
    ("/privacy-policy", "privacy.html", "privacy-policy"),
    ("/best-programming-langues", "programming.html", "best programming langues 2026"),
    ("/best-file-share-website", "fileshare.html", "best file share website free"),
)


def _register_static_page(path: str, template: str, title: str) -> None:
    async def page(request: Request):
        return templates.TemplateResponse(
            request=request, name=template, context={"title": title}
        )

    router.add_api_route(
        path, page, methods=["GET"], response_class=HTMLResponse,
        include_in_schema=False, name=f"page_{template}",
    )


for _path, _template, _title in STATIC_PAGES:
    _register_static_page(_path, _template, _title)


@router.get("/webnotepad")
async def webnotepad():
    return RedirectResponse(url="https://webnotepad.online/", status_code=301)


@router.get("/home")
async def home_redirect():
    return RedirectResponse(url="/main", status_code=301)


@router.get("/receiver")
async def receiver():
    return RedirectResponse(url="/access", status_code=301)


@router.get("/ads.txt", response_class=PlainTextResponse, include_in_schema=False)
async def ads_txt():
    return "google.com, pub-1872078023246662, DIRECT, f08c47fec0942fa0\n"


@router.get("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)
async def robots_txt():
    path = settings.base_dir / "robots.txt"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


@router.get("/sitemap.xml", include_in_schema=False)
async def sitemap_xml():
    path = settings.base_dir / "sitemap.xml"
    if not path.is_file():
        return PlainTextResponse("", status_code=404)
    return FileResponse(path, media_type="application/xml")
