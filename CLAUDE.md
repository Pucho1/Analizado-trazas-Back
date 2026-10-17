# Observa — backend

Repo git independiente con la API de Observa. Las reglas comunes del proyecto (forma de trabajar, alcance, Fase 2, principios de diseño) están en el `CLAUDE.md` de la carpeta de trabajo superior.

## Reglas del repo

- Este repo no depende del frontend. Su contrato público es el OpenAPI que genera FastAPI (`/openapi.json`); cualquier cambio en él puede romper el frontend y hay que señalarlo.
- No inventes campos ni endpoints de la API de Langfuse: verifícalos en la referencia oficial (https://api.reference.langfuse.com) o con la propia API antes de usarlos.
- Todas las funciones y métodos de `app/` (y los helpers de test) llevan docstring.

## Fuente de datos

- Langfuse Cloud, región EU: `https://cloud.langfuse.com`.
- Autenticación Basic Auth con clave pública (usuario) y clave secreta (contraseña).
- `GET /api/public/v2/observations` con `fields=core,basic,usage,trace_context,model` (nunca `io`).

## Stack

Python 3.14, FastAPI, uv, pydantic-settings, SQLAlchemy 2 (async, asyncpg), Alembic, PostgreSQL 17 (docker-compose), APScheduler 3 (la 4 sigue en alfa), pytest, Ruff, mypy (strict).

## Estructura

```
backend/
├── app/
│   ├── main.py                  # create_app, lifespan, CORS
│   ├── core/                    # config.py, constants.py, exceptions.py
│   ├── domain/                  # models.py (Trace, Observation, Summary, TimeRange, SyncRun...)
│   │                            # ports.py (TraceSource, TraceRepository, SyncRunRepository)
│   ├── application/             # trace_service.py (lectura), sync_service.py (sincronización)
│   ├── infrastructure/
│   │   ├── langfuse/            # client.py, mapper.py
│   │   ├── database/            # session.py, tables.py, errors.py,
│   │   │                        # trace_repository.py, sync_run_repository.py
│   │   └── scheduler.py         # APScheduler: escaneos diarios + escaneo inicial
│   └── api/v1/                  # routes/traces.py, routes/sync.py, schemas.py,
│                                # dependencies.py, errors.py
├── migrations/                  # Alembic (env.py, versions/)
├── docker/postgres/init/        # crea observa_test al iniciar el volumen
├── tests/                       # unit/, integration/
├── docker-compose.yml
├── alembic.ini
├── pyproject.toml
├── .pre-commit-config.yaml
├── .env
└── .env.example
```

- `TraceSource` (Protocol) es cualquier fuente de trazas; solo la usa `SyncService`. Añadir LangSmith = nueva carpeta en `infrastructure/`.
- `TraceRepository` (Protocol) guarda y lee trazas: `save_traces` (upsert idempotente; sustituye las observaciones) y `list_traces(start, end)`. `TraceService` (y por tanto los endpoints de trazas) lee solo de aquí.
- `SyncRunRepository` (Protocol): historial de sincronizaciones; el marcador es la ejecución correcta con mayor `window_end`.
- `SyncService`: ventana = marcador − `SYNC_OVERLAP` (1 h) hasta ahora (la primera vez, `SYNC_INITIAL_LOOKBACK_DAYS`); un fallo se registra con su `SyncFailureReason` y no avanza el marcador; un candado impide dos a la vez; `after_sync` es el punto de enganche para recalcular totales y comprobar umbrales.
- El ciclo de vida (motor, cliente de Langfuse, repositorios, `SyncService` único y planificador) se monta en el `lifespan` de `main.py`; las dependencias de FastAPI los leen de `app.state`.
- Modelo `Trace`: id, timestamp, name, environment, latency, cost (USD, `Decimal`), input/output/total tokens, tags, observations.
- Modelo `Observation`: id, name, type (texto libre), level (`ObservationLevel`), model, start_time. Solo metadatos, nunca input ni output.
- Endpoints: `GET /api/v1/traces` y `GET /api/v1/traces/summary` (rango `24h`, `7d`, `30d`), `POST /api/v1/sync` (sincronizar ahora, síncrono) y `GET /api/v1/sync` (estado).
- Errores propios del dominio mapeados a respuestas HTTP claras (`api/v1/errors.py`): 502 Langfuse, 503 base de datos, 409 sincronización en curso.

## Base de datos

- Tablas `traces`, `observations` (FK con `ON DELETE CASCADE`) y `sync_runs`, definidas en `infrastructure/database/tables.py`. Nombres de restricciones fijos (naming convention).
- Los repositorios convierten cualquier fallo de base de datos en `RepositoryError` (`database/errors.py`).
- Cambios de esquema: editar `tables.py` → `uv run alembic revision --autogenerate -m "..."` → revisar → `uv run alembic upgrade head`. Enseñar el esquema antes de crear migraciones.
- `migrations/env.py` toma la URL de `config.attributes["database_url"]` (tests) o de los `POSTGRES_*` del `.env`; nunca hay credenciales en `alembic.ini`.
- Host `127.0.0.1`, no `localhost` (en Windows cada conexión tarda ~2 s más).

## Seguridad y configuración

- Todos los secretos van en `.env` (Langfuse, PostgreSQL, y en el futuro email, Telegram, IA y login). Se leen y validan con pydantic-settings al arrancar.
- Constantes centralizadas en `app/core/constants.py`.

## Calidad

- `uv run ruff check`, `uv run ruff format --check`, `uv run mypy` y `uv run pytest` en verde.
- pre-commit propio del repo (`uv run pre-commit install`) con Ruff y mypy.
- Tests unitarios del servicio, del dominio y del mapper de Langfuse con datos simulados.
- Tests de los repositorios contra PostgreSQL real (`TEST_DATABASE_URL`, base `observa_test`; fixtures en `tests/integration/conftest.py`); aplican las migraciones y comprueban que coinciden con `tables.py`. Requieren `docker compose up -d`.
- Los tests construyen `Settings` sin `.env` y con `SYNC_ENABLED=false` (`tests/conftest.py`); nunca arrancan el planificador real ni llaman a Langfuse.
