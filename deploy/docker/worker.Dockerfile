FROM python:3.12-slim

WORKDIR /srv/app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# 先复制全部源码再安装（与 api.Dockerfile 同步整改），并创建非特权运行用户 appuser
COPY pyproject.toml README.md ./
COPY backend/ ./backend/
RUN pip install --no-cache-dir . \
    && groupadd -r appuser \
    && useradd -r -g appuser -d /srv/app -s /sbin/nologin appuser \
    && mkdir -p /srv/app/backend/data/logs /data/files \
    && chown -R appuser:appuser /srv/app /data/files

WORKDIR /srv/app/backend
USER appuser

CMD ["celery", "-A", "app.workers.celery_app:celery_app", "worker", "--loglevel=info", "--concurrency=2"]
