.PHONY: install dev test lint typecheck fmt check run dry-run

install:
	uv sync

dev:
	uv sync --dev

test:
	uv run pytest -v --tb=short

test-cov:
	uv run pytest -v --cov=src/event_engine --cov-report=term-missing

lint:
	uv run ruff check src/ tests/

typecheck:
	uv run mypy src/event_engine

fmt:
	uv run ruff format src/ tests/
	uv run ruff check --fix src/ tests/

check: lint typecheck test

run:
	uv run python -m event_engine

dry-run:
	uv run python -m event_engine --dry-run
