.PHONY: test lint install

install:
	uv pip install -e ".[dev]"

test:
	uv run pytest -vvv tests/

lint:
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/

format:
	uv run ruff format src/ tests/
