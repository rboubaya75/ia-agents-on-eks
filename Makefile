.PHONY: install lint format format-check typecheck test check

install:
	uv sync

lint:
	uv run ruff check packages apps tests

format:
	uv run ruff format packages apps tests

format-check:
	uv run ruff format --check packages apps tests

typecheck:
	uv run mypy

test:
	uv run pytest

check: lint format-check typecheck test
