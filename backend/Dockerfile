# ClipFusion AI backend (API + Celery workers share this image).
#   docker build -t clipfusion-backend backend
#   API:    docker run ... clipfusion-backend
#   Worker: docker run ... clipfusion-backend celery -A app.workers.celery_app worker -Q analysis,render,publish,default -c 2
#   Beat:   docker run ... clipfusion-backend celery -A app.workers.celery_app beat
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LOCAL_STORAGE_DIR=/data/storage \
    TEMP_DIR=/data/tmp \
    MODELS_DIR=/app/models \
    FONTS_DIR=/app/assets/fonts

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-liberation libgl1 libglib2.0-0 ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first (better layer caching): install from pyproject.toml only.
COPY pyproject.toml ./
RUN python -c "import tomllib; print(chr(10).join(tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']))"     > /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt && rm /tmp/requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY scripts ./scripts
COPY assets ./assets

# Face-detection models and built-in OFL fonts (best effort: the app reports missing assets
# through /api/v1/system/capabilities and falls back gracefully).
RUN mkdir -p /app/models /app/assets/fonts \
    && (python scripts/download_assets.py || echo "WARNING: asset download failed; run scripts/download_assets.py later")

RUN useradd --create-home --uid 10001 clipfusion \
    && mkdir -p /data/storage /data/tmp \
    && chown -R clipfusion:clipfusion /data /app/models /app/assets
USER clipfusion

EXPOSE 8010
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost:8010/api/v1/health || exit 1

# Behind a reverse proxy set FORWARDED_ALLOW_IPS to the proxy's address so that rate limits see
# the real client IP. Never use "*": uvicorn would then believe any client's X-Forwarded-For
# header, which lets callers pick their own rate-limit identity (e.g. for login attempts).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8010", "--proxy-headers"]
