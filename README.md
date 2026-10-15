# Observa — backend

API de Observa: extrae las trazas de Langfuse, calcula los agregados y los expone por HTTP. Stack: Python, FastAPI, uv.

## Requisitos

- [uv](https://docs.astral.sh/uv/) (gestiona también la versión de Python)

## Puesta en marcha

```bash
cp .env.example .env      # rellenar las claves de Langfuse
uv sync
uv run uvicorn app.main:app --reload
```

Swagger: http://localhost:8000/docs · OpenAPI: http://localhost:8000/openapi.json

## Calidad

```bash
uv run ruff check
uv run ruff format --check
uv run mypy
uv run pytest
```

Hooks de pre-commit:

```bash
uv run pre-commit install
uv run pre-commit run --all-files
```
