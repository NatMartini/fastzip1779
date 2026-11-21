FROM python:3.12-slim-trixie AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/* && useradd --uid 10001 --create-home app
WORKDIR /app
COPY requirements.lock pyproject.toml ./
COPY backend ./backend
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir --no-deps .
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts
USER 10001:10001

FROM base AS api
EXPOSE 8000
HEALTHCHECK --interval=20s --timeout=10s --start-period=30s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health/ready', timeout=8)"]
CMD ["uvicorn", "fastzip.api:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]

FROM base AS worker
HEALTHCHECK --interval=20s --timeout=5s --start-period=60s CMD ["python", "-c", "import pathlib,time; assert time.time()-pathlib.Path('/tmp/fastzip-worker-heartbeat').stat().st_mtime < 60"]
CMD ["python", "-m", "fastzip.worker"]

FROM base AS backup
USER root
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client age \
    && rm -rf /var/lib/apt/lists/*
USER 10001:10001
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD ["python", "-c", "import pathlib,time; assert time.time()-pathlib.Path('/tmp/fastzip-backup-heartbeat').stat().st_mtime < 60"]
CMD ["python", "-m", "fastzip.backup"]
