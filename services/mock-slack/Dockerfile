# MOCK Slack (DEV/TEST ONLY - not Slack, not affiliated with Slack Technologies). Records incoming-webhook and
# chat.postMessage calls in memory so approval alerts can be tested without sending anything to real Slack. Never deploy.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
RUN pip install --no-cache-dir "fastapi==0.143.3" "uvicorn[standard]==0.54.0"
COPY mock_slack mock_slack
RUN useradd --system --uid 10001 app
USER app
EXPOSE 8600
CMD ["uvicorn", "mock_slack.app:app", "--host", "0.0.0.0", "--port", "8600"]

