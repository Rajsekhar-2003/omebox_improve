#!/usr/bin/env bash
# Nightly local backup of uploads + MongoDB metadata. Add to cron:
#   30 3 * * * /opt/omebox/deploy/backup.sh >> /var/log/omebox-backup.log 2>&1
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/omebox}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/omebox}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
STAMP="$(date +%Y%m%d-%H%M%S)"
DEST="${BACKUP_DIR}/${STAMP}"

mkdir -p "${DEST}"
tar -C "${APP_DIR}" -czf "${DEST}/uploads.tar.gz" uploads cache

if command -v mongodump >/dev/null 2>&1; then
    mongodump --uri="${MONGODB_URL:-mongodb://127.0.0.1:27017}" \
        --db="${DATABASE_NAME:-sharebin}" --out="${DEST}/mongo"
elif docker compose -f "${APP_DIR}/docker-compose.yaml" ps mongo >/dev/null 2>&1; then
    docker compose -f "${APP_DIR}/docker-compose.yaml" exec -T mongo \
        mongodump --quiet --db="${DATABASE_NAME:-sharebin}" --archive \
        > "${DEST}/mongo.archive"
fi

find "${BACKUP_DIR}" -mindepth 1 -maxdepth 1 -type d \
    -mtime "+${RETENTION_DAYS}" -exec rm -rf {} +

echo "Backup completed: ${DEST}"
