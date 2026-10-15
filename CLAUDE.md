# Observa — backend

Repo git independiente con la API de Observa. Las reglas comunes del proyecto (forma de trabajar, alcance del MVP, principios de diseño) están en el `CLAUDE.md` de la carpeta de trabajo superior.

## Reglas del repo

- Este repo no depende del frontend. Su contrato público es el OpenAPI que genera FastAPI (`/openapi.json`); cualquier cambio en él puede romper el frontend y hay que señalarlo.
- No inventes campos ni endpoints de la API de Langfuse: verifícalos en la referencia oficial (https://api.reference.langfuse.com) o con la propia API antes de usarlos.

## Fuente de datos

- Langfuse Cloud, región EU: `https://cloud.langfuse.com`.
- Autenticación Basic Auth con clave pública (usuario) y clave secreta (contraseña).

## Stack

Python 3.14, FastAPI, uv (gestor de paquetes), pydantic-settings, pytest, Ruff (lint y formato), mypy (strict).

## Estructura

```
backend/
├── app/
│   ├── main.py
│   ├── core/            # config.py, constants.py, exceptions.py
│   ├── domain/          # models.py (Trace, Summary), ports.py (TraceSource)
│   ├── application/     # trace_service.py (casos de uso)
│   ├── infrastructure/
│   │   └── langfuse/    # client.py, mapper.py
│   └── api/v1/          # routes/traces.py, schemas.py, dependencies.py
├── tests/               # unit/, integration/
├── pyproject.toml
├── .pre-commit-config.yaml
├── .env
└── .env.example
```

- `TraceSource` es la interfaz (Protocol) que cumple cualquier fuente. El servicio depende de ella, no de Langfuse. Añadir LangSmith = nueva carpeta en `infrastructure/`.
- Modelo `Trace`: id, timestamp, name (agente o tool), latency, cost, input_tokens, output_tokens, total_tokens.
- Endpoints: `GET /api/v1/traces` y `GET /api/v1/traces/summary`, ambos con parámetro de rango (`24h`, `7d`, `30d`).
- Errores propios del dominio mapeados a respuestas HTTP claras.
- Documentación automática con Swagger (FastAPI).

## Seguridad y configuración

- Todos los secretos van en `.env` (claves de Langfuse, host, y en el futuro claves de IA y login). Se leen y validan con pydantic-settings al arrancar.
- Constantes centralizadas en `app/core/constants.py`.

## Calidad

- `uv run ruff check`, `uv run ruff format --check`, `uv run mypy` y `uv run pytest` en verde.
- pre-commit propio del repo (`uv run pre-commit install`) con Ruff y mypy.
- Tests unitarios del servicio y del mapper de Langfuse con datos simulados.
