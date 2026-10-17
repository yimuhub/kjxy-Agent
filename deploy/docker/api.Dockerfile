FROM python:3.12-slim

WORKDIR /srv/app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# 先复制全部源码再安装（整改修正：此前在 backend/ 缺失时执行 editable 安装，
# setuptools 因 package-dir backend/app 不存在而构建失败；生产也不应 editable）
COPY pyproject.toml README.md ./
COPY backend/ ./backend/
RUN pip install --no-cache-dir .

WORKDIR /srv/app/backend

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
