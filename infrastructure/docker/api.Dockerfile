# JoltIQ API + job worker image (same image, different command).
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY apps/api/requirements.lock /app/requirements.lock
RUN pip install -r requirements.lock boto3==1.35.81 opentelemetry-exporter-otlp-proto-http==1.29.0
COPY apps/api /app
RUN pip install --no-deps -e . && useradd -r -u 10001 joltiq && mkdir -p /app/.storage && chown -R joltiq /app/.storage
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1
CMD ["uvicorn", "joltiq.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4", "--proxy-headers", "--forwarded-allow-ips", "*"]
