FROM python:3.12-slim

WORKDIR /srv/app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# 先复制全部源码再安装（生产不应使用 editable 模式），并创建非特权运行用户 appuser
COPY pyproject.toml README.md ./
COPY backend/ ./backend/
RUN pip install --no-cache-dir . \
    && groupadd -r appuser \
    && useradd -r -g appuser -d /srv/app -s /sbin/nologin appuser \
    && mkdir -p /srv/app/backend/data/logs /data/files \
    && chown -R appuser:appuser /srv/app /data/files

WORKDIR /srv/app/backend
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
