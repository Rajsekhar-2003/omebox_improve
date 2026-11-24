#!/usr/bin/env python3
"""Rebuild the distributable OmeBox 'full version' artefacts.

Creates, from the checked-out source tree:

  * ``omebox-full-version.zip``
  * ``omebox-full-version.tar.gz``
  * ``MANIFEST.sha256``
  * ``FULL_VERSION.md`` (checksum index plus every text file inline)

The real ``.env``, runtime data, virtualenv and previous release artefacts are
never included.  Run from anywhere::

    python3 tools/build_full_version.py
"""

from __future__ import annotations

import hashlib
import tarfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAGE_NAME = "omebox-full-version"
ARCHIVE_BASE = "omebox-full-version"

EXCLUDED_DIRS = {
    ".git", ".mypy_cache", ".pytest_cache", "__pycache__",
    "cache", "uploads", "venv", ".venv",
    "omebox-full-version",  # an extracted copy of a previous release
}
EXCLUDED_FILES = {
    ".env", "FULL_VERSION.md", "MANIFEST.sha256",
    "omebox-full-version.zip", "omebox-full-version.tar.gz",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".log", ".zip", ".tar.gz"}
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico"}
LANGUAGE = {
    ".py": "python", ".sh": "bash", ".html": "html", ".css": "css",
    ".md": "markdown", ".yaml": "yaml", ".yml": "yaml", ".txt": "text",
    ".ini": "ini", ".json": "json", ".xml": "xml", ".example": "ini",
}


def source_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if any(part in EXCLUDED_DIRS for part in rel.parts[:-1]):
            continue
        if rel.name in EXCLUDED_FILES:
            continue
        if rel.name.startswith(".env.") and rel.name != ".env.example":
            continue
        if any(str(rel).endswith(suffix) for suffix in EXCLUDED_SUFFIXES):
            continue
        files.append(path)
    return sorted(files, key=lambda p: str(p.relative_to(ROOT)).lower())


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_docs(files: list[Path], rel_names: list[str]) -> str:
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    parts = [
        "# OmeBox — FULL VERSION (production hardened)",
        "",
        f"Generated: {generated}",
        "",
        "This file contains every source/config/documentation file in the production "
        "version. Binary image assets (static/*.png) are in the archive but omitted "
        "below. The real `.env`, uploads/, cache/ and venv/ are intentionally NOT "
        "included. Create `.env` from `.env.example` and rotate all previous secrets.",
        "",
        "Verify a downloaded archive with `sha256sum -c MANIFEST.sha256`.",
        "",
        "## File index",
        "",
        "| File | Bytes | SHA-256 |",
        "| --- | ---: | --- |",
    ]
    for path, rel in zip(files, rel_names):
        parts.append(
            f"| `{rel}` | {path.stat().st_size} | `{sha256(path)}` |"
        )

    parts.extend(["", "---", "", "## File contents", ""])
    for path, rel in zip(files, rel_names):
        if path.suffix.lower() in BINARY_SUFFIXES:
            continue
        try:
            body = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        lang = LANGUAGE.get(path.suffix.lower(), "")
        parts.extend([
            f"### `{rel}`",
            "",
            f"````{lang}",
            body.rstrip("\n"),
            "````",
            "",
        ])
    return "\n".join(parts) + "\n"


def main() -> None:
    files = source_files()
    rel_names = [str(path.relative_to(ROOT)) for path in files]

    manifest = [
        "# SHA-256 checksums of OmeBox source files "
        "(excludes .env, uploads, cache, venv)",
    ]
    manifest.extend(
        f"{sha256(path)}  {rel}" for path, rel in zip(files, rel_names)
    )
    (ROOT / "MANIFEST.sha256").write_text("\n".join(manifest) + "\n", encoding="utf-8")
    (ROOT / "FULL_VERSION.md").write_text(
        build_docs(files, rel_names), encoding="utf-8"
    )

    with zipfile.ZipFile(
        ROOT / f"{ARCHIVE_BASE}.zip", "w", zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path, rel in zip(files, rel_names):
            archive.write(path, f"{STAGE_NAME}/{rel}")

    with tarfile.open(ROOT / f"{ARCHIVE_BASE}.tar.gz", "w:gz", compresslevel=9) as archive:
        for path, rel in zip(files, rel_names):
            archive.add(
                path, arcname=f"{STAGE_NAME}/{rel}", recursive=False
            )

    print(f"Packaged {len(files)} files into {ARCHIVE_BASE}.zip / .tar.gz")


if __name__ == "__main__":
    main()
