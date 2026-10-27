.DEFAULT_GOAL := help
PORT ?= 3205

help: ## List targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

install: ## Install locked dependencies (incl. dev)
	uv sync --frozen

migrate: ## Apply database migrations
	uv run alembic upgrade head

run: migrate ## Run the dev server with reload
	IMPORTKIT_LOG_FORMAT=console uv run uvicorn --factory importkit.app:create_app --reload --port $(PORT)

lint: ## Ruff lint + format check
	uv run ruff check .
	uv run ruff format --check .

format: ## Auto-format and fix lint
	uv run ruff format .
	uv run ruff check --fix .

typecheck: ## mypy --strict
	uv run mypy

test: ## Tests with coverage
	uv run pytest --cov=importkit --cov-report=term-missing

check: lint typecheck test ## Everything CI runs

docker: ## Build the container image
	docker build -t importkit:local .

.PHONY: help install migrate run lint format typecheck test check docker
