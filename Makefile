.PHONY: sync test lint typecheck ui-build ci demo serve

sync:
	uv sync --python 3.13 --all-extras

lint:
	uv run ruff format --check src tests
	uv run ruff check src tests

typecheck:
	uv run mypy src tests

test:
	uv run pytest --cov=weavegraph --cov-report=term-missing --cov-fail-under=95

ui-build:
	cd ui && npm ci && npm run build

ci: lint typecheck test ui-build

demo:
	uv run weavegraph demo

serve:
	uv run weavegraph serve --port 8150
