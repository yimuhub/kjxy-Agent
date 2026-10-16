.PHONY: up down logs migrate seed-dev test test-security lint type-check contract contract-check check lock build ps
.PHONY: native-install native-migrate native-seed native-api native-web

# 本机 Python 解释器：优先用仓库 .venv（native 流程产物）
PY := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

up:
	docker compose up --build -d

down:
	docker compose down

ps:
	docker compose ps

logs:
	docker compose logs -f api worker

migrate:
	docker compose exec api alembic upgrade head

seed-dev:
	docker compose exec api python -m app.db.seed

# ---------------- 纯本机运行（不依赖 Docker） ----------------
# 前置：python3.12+、node 18+；backend/.env 按 backend/.env.example 配好
native-install:
	python3 -m venv .venv && ./.venv/bin/pip install --upgrade pip && ./.venv/bin/pip install -e ".[dev]"
	cd frontend && npm install

native-migrate:
	cd backend && ../.venv/bin/alembic upgrade head

native-seed:
	cd backend && ../.venv/bin/python -m app.db.seed

native-api:
	cd backend && ../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000

native-web:
	cd frontend && npm run dev

# ---------------- 质量门禁（本地与 CI 对齐） ----------------
test:
	cd backend && $(PY) -m pytest -q

test-security:
	cd backend && $(PY) -m pytest tests/security -q

lint:
	cd backend && $(PY) -m ruff check app tests && $(PY) -m ruff format --check app tests
	cd frontend && npm run lint

type-check:
	cd backend && $(PY) -m mypy app

# 重新生成 OpenAPI 契约（接口变更后执行，随代码同 PR 提交）
contract:
	$(PY) backend/scripts/generate_openapi.py

# 契约漂移检查：重新生成后若与已提交版本不一致则失败（CI 用）
contract-check:
	$(PY) backend/scripts/generate_openapi.py
	@git diff --quiet --exit-code contracts/openapi.yaml 2>/dev/null \
		|| { echo "contracts/openapi.yaml 与代码不一致，请运行 'make contract' 并提交"; exit 1; }

# 一键质量门禁：lint + 类型 + 测试 + 契约漂移
check: lint type-check test contract-check

# 依赖锁文件（spec §2.1 版本原则：提交 uv.lock 与前端 lockfile）
lock:
	uv lock
	cd frontend && npm install --package-lock-only

build:
	docker compose build

# 危险操作：删除全部数据卷（含数据库）。执行前必须已备份。
nuke:
	@echo "警告：该命令将删除 postgres / redis / 文件全部数据卷！"
	@read -p "确认已备份并继续？输入 YES 继续: " c; [ "$$c" = "YES" ] && docker compose down -v || echo "已取消"
