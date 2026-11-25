# Production deployment (4 GB RAM VPS, local storage)

Target: ~100K monthly users, FastAPI + MongoDB + uploads on the VPS disk.
Two application workers and one local MongoDB fit comfortably in 4 GB.

## 0. Before anything else: rotate secrets

The previous `.env` in this workspace contained live-looking API keys and a
MongoDB Atlas credential in plaintext. Treat them as compromised:

1. Generate new values:
   ```bash
   openssl rand -hex 32
   python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   openssl rand -hex 32
   ```
2. Put them in `.env` as `SECRET_KEY`, `ENCRYPTION_KEY` and `ADMIN_API_KEY`.
3. Rotate the MongoDB credential itself. Do **not** reuse the old one.
4. Never commit `.env` (`.gitignore` and `.dockerignore` both exclude it).
5. Older variable names are accepted but deprecated: `ADMIN_TOKEN` →
   `ADMIN_API_KEY`, `FILE_UPLOAD_RATE_LIMIT` → `UPLOAD_RATE_LIMIT`, and
   `WORKERS` → `WEB_CONCURRENCY`. Rename them while rotating the keys.

Changing `ENCRYPTION_KEY` makes previously stored text shares unreadable;
let old shares expire first, or accept that they will return a decode error.

## 1. Run the tests locally (mandatory)

```bash
python3 -m venv venv
venv/bin/python -m pip install -r requirements-dev.txt
venv/bin/python -m pytest
venv/bin/python -m compileall -q app.py delete.py filetypes.py omebox tests
```

Expected: all tests pass. Tests use in-memory MongoDB and temporary folders;
they never touch production data.

## 2A. Docker Compose deployment (recommended)

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-plugin git curl
sudo usermod -aG docker "$USER"   # log out and back in

sudo mkdir -p /opt/omebox && sudo chown "$USER":"$USER" /opt/omebox
cd /opt/omebox
cp -r /path/to/OMEBOX_check/. .
cp .env.example .env && nano .env   # fill in real keys, BASE_URL, admin key
sudo chown -R 10001:10001 uploads cache   # container runs as UID 10001

docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:8000/health
```

Notes:
- Compose starts a local MongoDB with a 0.75 GB WiredTiger cache and mounts
  `./uploads` and `./cache` from the VPS (easy to back up).
- `MONGODB_URL` in `.env` is overridden to `mongodb://mongo:27017` by Compose.
  To use Atlas instead, remove the `mongo` service and that override.
- App container is limited to 2.6 GB, Mongo to 1.2 GB, leaving headroom for the
  OS and nginx.
- Rate limits and deletion are shared across both uvicorn workers.
- `.dockerignore` prevents `.env` and runtime data from entering image layers;
  the bind-mounted `uploads/` and `cache/` directories must be writable by
  UID 10001 on the host (`chown -R 10001:10001`).

## 2B. systemd deployment (no Docker)

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv libmagic1 nginx curl mongodb-org
sudo useradd --system --home /opt/omebox --shell /usr/sbin/nologin omebox

sudo mkdir -p /opt/omebox/{uploads,cache}
sudo cp -r /path/to/OMEBOX_check/. /opt/omebox/
cd /opt/omebox
python3.12 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env    # MONGODB_URL=mongodb://127.0.0.1:27017
sudo chown -R omebox:omebox /opt/omebox/uploads /opt/omebox/cache /opt/omebox/.env

sudo cp deploy/omebox.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mongod omebox
sudo systemctl status omebox
curl -fsS http://127.0.0.1:8000/health
```

If MongoDB is installed separately, set `--wiredTigerCacheSizeGB=0.75` in its
config so it does not compete with the app for RAM.

## 3. nginx + TLS

```bash
sudo cp deploy/nginx-omebox.conf /etc/nginx/sites-available/omebox
sudo ln -s /etc/nginx/sites-available/omebox /etc/nginx/sites-enabled/
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d omebox.com -d www.omebox.com
sudo nginx -t && sudo systemctl reload nginx
```

- Edit the domain names in the nginx config first.
- nginx listens on 443 and proxies to `127.0.0.1:8000`, so the app port never
  needs to be exposed publicly.
- `proxy_buffering off` prevents nginx from buffering large downloads in RAM.
- `TRUSTED_PROXIES` must include the address nginx connects from. For systemd
  that is `127.0.0.1`; for Docker with nginx on the host, add the bridge gateway
  (`docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}'`).
  Otherwise every request appears to come from one IP and rate limits collapse.

## 4. Verify after deploy

```bash
curl -fsS https://omebox.com/health
curl -fsS https://omebox.com/robots.txt
curl -fsS -o /dev/null -w '%{http_code}\n' https://omebox.com/s/does-not-exist

# Admin status (requires ADMIN_API_KEY)
curl -fsS -H "x-api-key: $ADMIN_API_KEY" https://omebox.com/api/maintenance/status
curl -fsS -X POST -H "x-api-key: $ADMIN_API_KEY" https://omebox.com/api/cleanup

# Maintenance directly on the VPS
cd /opt/omebox && python delete.py --status
```

Check response headers include `Content-Security-Policy`, `X-Content-Type-Options`
and `Strict-Transport-Security`.

## 5. Automatic deletion (what is cleaned)

| Data | Mechanism | Timing |
| --- | --- | --- |
| Expired text shares and encrypted text | `delete_expired_shares` in scheduler + on-access purge | every 5 min and immediately when an expired link is opened |
| Expired file shares and their disk folders | same job, DB record then files | every 5 min |
| Orphan upload folders (no DB record) | `delete_orphaned_uploads` | every 5 min, 30 min grace period |
| Interrupted `*.part` uploads | orphan sweep | every 5 min, 60 min grace period |
| Stale `cache/` files | orphan sweep | every 5 min, 24 h grace period |
| Rate-limit counters | MongoDB TTL index on BSON-date `expires_at`; legacy numeric counters are dropped at startup | automatic by MongoDB |

Cleanup uses a Mongo lock, so running 2 workers still produces a single cleanup
cycle. Manual cleanup is available at `POST /api/cleanup` and via
`python delete.py --cleanup --force`.

## 6. Backups and monitoring

```bash
sudo cp deploy/backup.sh /opt/omebox/deploy/backup.sh
sudo chmod +x /opt/omebox/deploy/backup.sh
# cron (edit with `crontab -e`):
30 3 * * * /opt/omebox/deploy/backup.sh >> /var/log/omebox-backup.log 2>&1
```

- Backups contain `uploads/`, `cache/` and a `mongodump` of metadata; keep at
  least one copy off the VPS.
- Watch `disk.free_percent` from `/api/stats` and set alerts below 15%.
- `MEMORY_MAX` in the systemd unit and `mem_limit` in Compose cap app RAM.
- Log rotation: use `journald` limits (`SystemMaxUse=500M`) for systemd, or
  Docker's `log-driver` options for Compose.

## 7. Capacity notes (100K/month)

- Average load is modest; peaks are absorbed by 2 workers plus uvloop/httptools.
- Rate limits are per client IP and shared across workers.
- Tune with `RATE_LIMIT`, `UPLOAD_RATE_LIMIT`, `MAX_FILE_SIZE`, `MAX_SHARE_SIZE`
  and `MONGO_MAX_POOL_SIZE` in `.env`; restart after changes.
- If uploads grow beyond the VPS disk, add a second disk and mount it at
  `/opt/omebox/uploads` rather than switching storage technology.

## 8. Known trade-offs

- The slowapi storage adapter uses a small synchronous PyMongo client because
  slowapi's check is synchronous. Each hit is a single indexed operation, but it
  briefly occupies the event loop. At the stated scale this is negligible; if
  you move to very high request rates, add Redis and switch `limiter._storage`
  to `limits` Redis storage.
- Static assets use a 1-hour `max-age` without filename hashes. After a CSS/JS
  change, either bust the cache with a query string in templates or lower
  `STATIC_CACHE_MAX_AGE` before deploying.
- The full-version package is rebuilt with
  `python3 tools/build_full_version.py`; verify downloads with
  `sha256sum -c MANIFEST.sha256`.

## 9. FastAPI Cloud

FastAPI Cloud expects the application at the **root of the repository**. No
Application Directory setting is needed for this repo: `app.py` at the root
exports `app`, and FastAPI Cloud auto-detects `app.py`, `main.py` or
`app/main.py`.

```bash
cd ~/D/OMEBOX_check
source venv/bin/activate
pip install -r requirements-dev.txt   # includes fastapi[standard] + CLI
fastapi dev                           # must start without a path argument
fastapi deploy                        # deploys the repository root
```

If you connect GitHub in the FastAPI Cloud dashboard, leave **Application
Directory** empty (repository root). To make the entry point explicit, add:

```toml
[tool.fastapi]
entrypoint = "app:app"
```

Set these as FastAPI Cloud environment variables (`.env` is git-ignored and is
not uploaded): `SECRET_KEY`, `ENCRYPTION_KEY`, `MONGODB_URL`, `ADMIN_API_KEY`,
`BASE_URL`, `FORCE_HTTPS`, `TRUSTED_PROXIES`. Use
`fastapi cloud env set --secret NAME value` for the secrets.

**Important limitation:** this app stores uploads on the local filesystem
(`UPLOAD_DIR`, default `<repo>/uploads`). Serverless/multi-instance platforms
may have ephemeral or instance-local disks, so downloaded files can disappear
after a redeploy or land on a different instance. FastAPI Cloud is fine for a
text-share-only deployment; for file sharing in production use a VPS with a
persistent volume (sections 2A/2B) or port `omebox/storage.py` to object
storage (S3/R2).
