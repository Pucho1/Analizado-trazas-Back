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
│   ├── domain/                  # models.py (Trace, Observation, Summary, TimeRange, SyncRun,
│   │                            #   Profile, ProfileRule, Thresholds, classify, DiscoveredGroup...)
│   │                            # monitoring.py (percentile, Baseline, umbrales, Alert, silencio)
│   │                            # spend.py (SpendTotals, comparaciones, motivo del cambio)
│   │                            # events.py (EventMapping, summarize_events)
│   │                            # reporting.py (Channel, ChannelSettings)
│   │                            # notifications.py (OutgoingMessage, Delivery, NotificationFailure)
│   │                            # ports.py (TraceSource, TraceRepository, SyncRunRepository,
│   │                            #   ProfileRepository, MonitoringRepository, SpendRepository,
│   │                            #   EventMappingRepository, ReportSettingsRepository,
│   │                            #   Notifier, TelegramChatDirectory, DeliveryRepository)
│   ├── application/             # trace_service.py (lectura), sync_service.py (sincronización),
│   │   │                        # profile_service.py (perfiles), monitoring_service.py (alertas),
│   │   │                        # spend_service.py (gasto), event_service.py (mapeo de eventos),
│   │   │                        # notification_service.py (envíos)
│   │   └── reports/             # content.py (bloques neutros), formatting.py (es-ES),
│   │                            # data.py (ReportData + builder), sections.py (ReportSection
│   │                            # y secciones), registry.py, presenters.py (email, Telegram),
│   │                            # messages.py (alertas, resumen de escaneo, prueba),
│   │                            # service.py (ReportService)
│   ├── infrastructure/
│   │   ├── langfuse/            # client.py, mapper.py
│   │   ├── database/            # session.py, tables.py, errors.py, trace_repository.py,
│   │   │                        # sync_run_repository.py, profile_repository.py,
│   │   │                        # monitoring_repository.py, reporting_repository.py,
│   │   │                        # delivery_repository.py
│   │   ├── notifications/       # email.py (SMTP), telegram.py (Bot API)
│   │   └── scheduler.py         # APScheduler: escaneos, escaneo inicial y reporte diario
│   └── api/v1/                  # routes/{traces,sync,profiles,alerts,spend,reports,
│                                # notifications}.py, schemas.py, dependencies.py, errors.py
├── migrations/                  # Alembic (env.py, versions/)
├── docker/postgres/init/        # crea observa_test al iniciar el volumen
├── tests/                       # unit/, integration/
├── docker-compose.yml           # PostgreSQL local
├── Dockerfile                   # imagen de producción (Railway)
├── .dockerignore
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
- Perfiles: `ProfileRule` (entorno obligatorio, por defecto `production`; nombre de traza y tags opcionales; los tags de la regla deben estar todos en la traza). `classify()` (dominio puro) es la única fuente de la asignación: gana la regla más específica y, en empate, el perfil más antiguo; los archivados siguen clasificando. `ProfileService` reclasifica al crear/cambiar regla y tras cada sync (`after_sync`). `traces.profile_id` NULL = «Sin clasificar»; borrar un perfil borra sus trazas en cascada. El upsert de trazas nunca toca `profile_id`.
- Línea base y alertas: todo el cálculo vive en `domain/monitoring.py` (puro, `Decimal`, percentil lineal tipo 7, comparaciones estrictas). Línea base por traza (coste, tokens, latencia; últimas `BASELINE_MAX_TRACES`) y por día local en `MONITORING_TIMEZONE` (consultas y coste; días vacíos = 0; sin el día en curso; últimos `BASELINE_DAYS`). Aprendizaje hasta `LEARNING_TRACES`; vigilancia diaria desde `MIN_BASELINE_DAYS`. Al recalcular se excluye lo crítico según la línea base anterior. Umbrales por perfil (`Thresholds`): alertas de anomalía en coste/latencia por traza y consultas/coste por día; presupuesto siempre; mínimos solo para coste; silencio por clave (perfil, tipo, ámbito, métrica, día) que se rompe al escalar. `MonitoringService` es el segundo gancho `after_sync` (tras reclasificar), solo para perfiles activos y datos de las últimas `MONITORING_MAX_LOOKBACK`. Las fronteras de día se devuelven en UTC (restar datetimes con la misma zona ignora el cambio de hora).
- Gasto y reportes: comparaciones con días cerrados y mismos días del periodo anterior (`comparison_ranges`); anterior = 0 → «nuevo» (None), nunca infinito; motivo del cambio = efecto volumen frente a efecto precio. «Día normal» = `typical_value` (mediana; si 0, media; mínimo `MIN_BASELINE_DAYS`). Eventos: mapeo por perfil que **empieza vacío** (lo decide el usuario); `WARNING`/`ERROR` sin mapear = técnicos.
- Reportes: una sección = clase con `key`, `title` y `build(ReportData) -> Section | None`, registrada en `SectionRegistry` (`DEFAULT_SECTIONS`). Las secciones solo leen `ReportData` (si necesitan un dato nuevo, se añade allí y en `ReportDataBuilder`), escriben español de negocio y responden cuánto / comparado con qué / si preocupa / qué hacer. Prohibido en el texto: «traza», «span», «observación», «tokens» (test `test_reports_never_use_technical_words`). Los presentadores (`EmailPresenter`, `TelegramPresenter`) solo maquetan; el envío por canal será un `Notifier` en infraestructura.
- Notificaciones: `Notifier` (Protocol) con adaptadores `SmtpEmailNotifier` (conectar → login → enviar, para distinguir causas; Gmail cuelga con credenciales malas → `auth_failed`) y `TelegramBotApi` (Bot API, `parse_mode=HTML`; 401/404 → `auth_failed`; el token va en la URL, así que los errores nunca llevan URL ni texto del proveedor). Solo hay notifier para los canales configurados en `.env`. `NotificationService` es el **tercer** gancho `after_sync` (tras reclasificar y evaluar) y nunca lanza en flujos automáticos: alertas pendientes en un mensaje por canal (`notified_at` si al menos un canal las recibe; si no, se reintentan hasta `PENDING_ALERTS_MAX_AGE`), resumen por escaneo opcional y reporte diario si toca. El reporte diario (cron `DAILY_REPORT_TIME` + recuperación al arrancar) es idempotente: índice único parcial en `deliveries` y candado en memoria. Las acciones manuales (enviar ya, prueba) sí lanzan.
- El ciclo de vida (motor, cliente de Langfuse, repositorios, `ProfileService`, `MonitoringService`, servicios de gasto, eventos, reportes y notificaciones, notifiers, `SyncService` único y planificador) se monta en el `lifespan` de `main.py`; las dependencias de FastAPI los leen de `app.state`.
- Modelo `Trace`: id, timestamp, name, environment, latency, cost (USD, `Decimal`), input/output/total tokens, tags, observations.
- Modelo `Observation`: id, name, type (texto libre), level (`ObservationLevel`), model, start_time. Solo metadatos, nunca input ni output.
- Endpoints: `GET /api/v1/traces` y `GET /api/v1/traces/summary` (rango `24h`, `7d`, `30d`), `POST /api/v1/sync` (sincronizar ahora, síncrono), `GET /api/v1/sync` (estado), `/api/v1/profiles` (listar, descubrir, crear, `PATCH`, archivar/desarchivar, `DELETE`, `baseline`, `thresholds`, `relearn`, `observation-names`, `event-mappings`), `GET /api/v1/alerts`, `/api/v1/spend` (+ `/comparisons`) y `/api/v1/reports` (`sections`, `settings/{channel}`, `daily`).
- Errores propios del dominio mapeados a respuestas HTTP claras (`api/v1/errors.py`): 502 Langfuse, 503 base de datos, 409 sincronización en curso o nombre de perfil repetido, 404 perfil inexistente, 400 umbrales incoherentes o sección de reporte desconocida, 409 canal sin configurar o reporte ya enviado, 502 fallo al notificar.

## Base de datos

- Tablas `deliveries` (registro de envíos; índice único parcial que impide dos reportes diarios enviados por canal y día), `profiles` (nombre único sin distinguir mayúsculas; umbrales y `baseline_since` con sus `CHECK`), `baselines` (una por perfil, estadísticas en JSONB con decimales como texto), `alerts`, `event_mappings` (único por perfil y nombre de paso), `report_settings` (una fila por canal, sembrada en la migración; también `alerts_enabled` y `scan_summary_enabled`), `traces` (`profile_id` FK con `ON DELETE CASCADE`), `observations` (FK con `ON DELETE CASCADE`) y `sync_runs`, definidas en `infrastructure/database/tables.py`. Nombres de restricciones fijos (naming convention).
- Los repositorios convierten cualquier fallo de base de datos en `RepositoryError` (`database/errors.py`).
- Cambios de esquema: editar `tables.py` → `uv run alembic revision --autogenerate -m "..."` → revisar → `uv run alembic upgrade head`. Enseñar el esquema antes de crear migraciones. Autogenerate no añade `CHECK` a tablas existentes: escribirlos a mano en la migración.
- `migrations/env.py` toma la URL de `config.attributes["database_url"]` (tests) o de los `POSTGRES_*` del `.env`; nunca hay credenciales en `alembic.ini`.
- Host `127.0.0.1`, no `localhost` (en Windows cada conexión tarda ~2 s más).

## Despliegue (Railway)

- `Dockerfile` multi-etapa: `uv sync --frozen --no-dev` en la etapa de build; la final solo lleva Python, `.venv`, `app/`, `migrations/` y `alembic.ini`, con usuario sin privilegios. Ni `.env` ni `tests/` entran en la imagen (`.dockerignore`).
- Al arrancar: `alembic upgrade head` y después `uvicorn app.main:app` en el puerto `PORT` (por defecto 8000), con `--proxy-headers`.
- **Un solo proceso y una sola réplica**: APScheduler vive dentro del proceso; con más, los escaneos y el reporte diario se duplicarían.
- Variables en Railway: `POSTGRES_HOST/PORT/USER/PASSWORD/DB` referenciando las del servicio Postgres (`${{Postgres.PGHOST}}`, `PGPORT`, `PGUSER`, `PGPASSWORD`, `PGDATABASE`), `LANGFUSE_*`, `CORS_ALLOWED_ORIGINS` con la URL del frontend y, si se usan, SMTP y Telegram.
- Healthcheck: `/openapi.json`.

## Seguridad y configuración

- Todos los secretos van en `.env` (Langfuse, PostgreSQL, y en el futuro email, Telegram, IA y login). Se leen y validan con pydantic-settings al arrancar.
- Constantes centralizadas en `app/core/constants.py`.

## Calidad

- `uv run ruff check`, `uv run ruff format --check`, `uv run mypy` y `uv run pytest` en verde.
- pre-commit propio del repo (`uv run pre-commit install`) con Ruff y mypy.
- Tests unitarios del servicio, del dominio y del mapper de Langfuse con datos simulados.
- Tests de los repositorios contra PostgreSQL real (`TEST_DATABASE_URL`, base `observa_test`; fixtures en `tests/integration/conftest.py`); aplican las migraciones y comprueban que coinciden con `tables.py`. Requieren `docker compose up -d`.
- Los tests construyen `Settings` sin `.env` y con `SYNC_ENABLED=false` (`tests/conftest.py`); nunca arrancan el planificador real ni llaman a Langfuse.
