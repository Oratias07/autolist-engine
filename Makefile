.PHONY: check test lint types imports audit
check: lint types imports test audit
lint:
	uv run ruff check src tests
	uv run ruff format --check src tests
types:
	uv run mypy src
imports:
	uv run lint-imports
test:
	uv run pytest -m "not live"
audit:
	uv run pip-audit
