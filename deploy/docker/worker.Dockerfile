FROM python:3.12-slim

WORKDIR /srv/app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# 先复制全部源码再安装（与 api.Dockerfile 同步整改）
COPY pyproject.toml README.md ./
COPY backend/ ./backend/
RUN pip install --no-cache-dir .

WORKDIR /srv/app/backend

CMD ["celery", "-A", "app.workers.celery_app:celery_app", "worker", "--loglevel=info", "--concurrency=2"]
