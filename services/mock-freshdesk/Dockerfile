# MOCK Freshdesk (DEV/TEST ONLY - not Freshdesk, not affiliated with Freshworks): in-memory emulation of
# the Freshdesk API v2 subset the JoltIQ adapter uses. Ported from the JoltIQ reference build. Never deploy.
# Build context: services/mock-freshdesk.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
RUN pip install --no-cache-dir "fastapi==0.143.3" "uvicorn[standard]==0.54.0" "httpx==0.28.1"
COPY mock_freshdesk mock_freshdesk
RUN useradd --system --uid 10001 app
USER app
EXPOSE 8500
CMD ["uvicorn", "mock_freshdesk.app:app", "--host", "0.0.0.0", "--port", "8500"]

