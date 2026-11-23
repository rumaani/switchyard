# ---- 1. build the UI ----------------------------------------------------------------
FROM node:22-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- 2. runtime ---------------------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    STATIC_DIR=/app/static DATABASE_URL=sqlite:////data/switchyard.db
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install -r requirements.txt
COPY backend/app ./app
COPY mock ./mock
COPY --from=ui /ui/dist ./static
COPY docker-start.sh /app/docker-start.sh
RUN useradd --uid 10001 --create-home switchyard && mkdir -p /data && chown switchyard /data
USER switchyard
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
# App by default (behind a TLS-terminating proxy); SWITCHYARD_ROLE=mock runs the demo vendor servers.
CMD ["sh", "/app/docker-start.sh"]
