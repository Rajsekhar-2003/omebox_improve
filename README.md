# OmeBox (ShareBin)

Secure anonymous text and file sharing. FastAPI + MongoDB (Motor/PyMongo) +
local disk storage. Designed for ~100K monthly users on a 4 GB RAM VPS.

## What changed

- **Code structure**: the old single 1,000-line `app.py` is now a package:
  - `omebox/config.py` – validated environment configuration
  - `omebox/security.py` – password/token hashing, upload policy, sanitisation
  - `omebox/storage.py` – local disk streaming, MIME checks, text encryption
  - `omebox/cleanup.py` – expiry, orphan and temp-file cleanup with a Mongo lock
  - `omebox/ratelimit.py` – rate limits shared across uvicorn workers
  - `omebox/middleware.py` – security headers and selective gzip
  - `omebox/routes/` – pages, shares and admin endpoints
  - `app.py` and `delete.py` remain as backwards-compatible entry points.
- **Security**: stricter content-type validation (no disguised executables),
  Windows/Unix path sanitisation, CSP/HSTS/clickjacking headers, Host-header
  protection via `BASE_URL`, docs disabled by default, no third-party service
  worker (`sw.js` removed), and weaker 72-byte bcrypt truncation fixed.
- **Performance**: 2 uvicorn workers, Mongo pool sized for 4 GB, templates
  cached, static asset caching, gzip only for text responses, no gzip on file
  downloads, shared fixed-window rate limiting.
- **Automatic deletion**: expired text/file shares are purged every 5 minutes
  (one worker at a time), orphaned upload folders and `.part` files are swept,
  and Mongo TTL expires rate-limit counters. Expired links also trigger an
  immediate delete on access.
- **Tests**: 33 automated tests in `tests/` covering API flows, encryption,
  password flow, uploads, cleanup, locking, rate limiting and concurrency.

## Production hardening (v3.1)

- `.dockerignore` keeps the real `.env`, runtime data, virtualenv and release
  archives out of the Docker build context and image layers.
- Rate-limit counters are stored as BSON dates so MongoDB's TTL index actually
  expires them; legacy epoch-based counters are removed at startup.
- Admin, share-view and download endpoints now have explicit rate limits.
- Older variable names (`FILE_UPLOAD_RATE_LIMIT`, `ADMIN_TOKEN`, `WORKERS`) are
  accepted with a deprecation warning so existing `.env` files keep working.
- `.env` is loaded automatically for CLI/shell runs; exported environment
  variables still take precedence. Placeholder secrets are rejected at startup.
- `python3 tools/build_full_version.py` rebuilds the zip/tar, checksum manifest
  and full source dump reproducibly.

## Quick start (development / tests)

```bash
python3 -m venv venv
venv/bin/python -m pip install -r requirements-dev.txt
cp .env.example .env          # then fill in real values
venv/bin/python -m pytest     # all tests use in-memory Mongo + temp storage
venv/bin/uvicorn app:app --reload
```

## Production

See [DEPLOYMENT.md](DEPLOYMENT.md) for the 4 GB VPS runbook (Docker Compose or
systemd), nginx TLS, backups, monitoring and secret rotation.

## Maintenance commands

```bash
python delete.py --status
python delete.py --cleanup --force
python delete.py --orphans --force
python delete.py --delete-share <share_id> --force
```

## Routes

Existing routes are preserved: `/`, `/share/text`, `/share/files`, `/access`,
`/s/{id}`, `/s/{id}/verify`, `/api/share/*`, `/main`, `/about`, `/contact`,
`/privacy-policy`, `/omebox-article`, `/anonymous-file-share`,
`/best-programming-langues`, `/best-file-share-website`, `/ads.txt`, `/health`.
Additionally `/robots.txt` and `/sitemap.xml` are now served correctly.
