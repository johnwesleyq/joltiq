# Isolated browser worker. Runs Chromium as a non-root user; deploy on a separate node pool/network segment.
FROM mcr.microsoft.com/playwright/python:v1.56.0-noble
ENV PYTHONUNBUFFERED=1
WORKDIR /app
RUN pip install --no-cache-dir fastapi==0.115.6 "uvicorn[standard]==0.32.1" playwright==1.56.0 pydantic==2.10.3 python-multipart==0.0.32
COPY services/browser-worker /app
USER pwuser
EXPOSE 8100
HEALTHCHECK --interval=15s --timeout=3s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8100/healthz')"
CMD ["uvicorn", "worker.app:app", "--host", "0.0.0.0", "--port", "8100"]
