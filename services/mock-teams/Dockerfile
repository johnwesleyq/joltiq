# MOCK Teams (DEV/TEST ONLY - not Microsoft Teams, not affiliated with Microsoft). Records incoming-webhook calls in
# memory so approval alerts can be tested without sending anything to real Teams. Never deploy.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
RUN pip install --no-cache-dir "fastapi==0.143.3" "uvicorn[standard]==0.54.0"
COPY mock_teams mock_teams
RUN useradd --system --uid 10001 app
USER app
EXPOSE 8610
CMD ["uvicorn", "mock_teams.app:app", "--host", "0.0.0.0", "--port", "8610"]

