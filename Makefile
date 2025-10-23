.PHONY: format mypy migrations

format:
	uv run ruff check --fix
	uv run ruff format

mypy:
	uv run mypy .

migrations:
	PYTHONPATH=. DJANGO_SETTINGS_MODULE=tests.settings uv run django-admin makemigrations
