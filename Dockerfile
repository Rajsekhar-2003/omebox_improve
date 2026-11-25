FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        libmagic1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin omebox

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=omebox:omebox . .
RUN mkdir -p /app/uploads /app/cache && chown -R omebox:omebox /app/uploads /app/cache

USER omebox
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:3000/health || exit 1

# 2 workers fits in 4 GB RAM.  WORKERS is accepted for older .env files.
CMD ["sh", "-c", "export WEB_CONCURRENCY=\"${WEB_CONCURRENCY:-${WORKERS:-2}}\"; exec uvicorn app:app --host 0.0.0.0 --port 3000 --workers \"${WEB_CONCURRENCY}\" --no-access-log"]
