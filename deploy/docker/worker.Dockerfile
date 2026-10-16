FROM python:3.12-slim

WORKDIR /srv/app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .

COPY backend/ ./backend/
WORKDIR /srv/app/backend

CMD ["celery", "-A", "app.workers.celery_app:celery_app", "worker", "--loglevel=info", "--concurrency=2"]
