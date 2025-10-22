.PHONY: format mypy

format:
	uv run ruff check --fix
	uv run ruff format

mypy:
	uv run mypy .
