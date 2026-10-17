# Observa — backend

API de Observa: sincroniza las trazas de Langfuse en PostgreSQL, calcula los agregados (totales, medias) y los expone por HTTP. Stack: Python 3.14, FastAPI, uv, PostgreSQL.

Es la única pieza que conoce las claves de Langfuse. El frontend de Observa (repo aparte) solo habla con esta API.

## Requisitos

- [uv](https://docs.astral.sh/uv/getting-started/installation/). Gestiona también la versión de Python (3.14, fijada en `.python-version`): no hace falta instalar Python a mano.
- Un proyecto en Langfuse con un par de claves de API (Langfuse → _Settings_ → _API Keys_).
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) para PostgreSQL en local.

## Instalación

```bash
uv sync
```

Crea `.venv/` e instala las dependencias exactas de `uv.lock`, incluidas las de desarrollo (Ruff, mypy, pytest, pre-commit).

## Configuración (`.env`)

Copia la plantilla y rellénala:

```bash
cp .env.example .env              # PowerShell: Copy-Item .env.example .env
```

| Variable               | Obligatoria | Descripción                                                                                       | Ejemplo                       |
| ---------------------- | ----------- | ------------------------------------------------------------------------------------------------- | ----------------------------- |
| `LANGFUSE_PUBLIC_KEY`  | Sí          | Clave pública del proyecto de Langfuse.                                                           | `pk-lf-…`                     |
| `LANGFUSE_SECRET_KEY`  | Sí          | Clave secreta del proyecto de Langfuse. **Secreta**: nunca sale del backend.                      | `sk-lf-…`                     |
| `LANGFUSE_HOST`        | Sí          | URL de Langfuse. Langfuse Cloud EU es `https://cloud.langfuse.com`.                                | `https://cloud.langfuse.com`  |
| `CORS_ALLOWED_ORIGINS` | No          | Lista JSON de orígenes del navegador que pueden llamar a la API. Vacía = ninguno.                 | `["http://localhost:5173"]`   |
| `POSTGRES_USER`        | Sí          | Usuario de PostgreSQL. Lo usan el backend y `docker-compose.yml`.                                  | `observa`                     |
| `POSTGRES_PASSWORD`    | Sí          | Contraseña de PostgreSQL. **Secreta**.                                                             | (una larga y aleatoria)       |
| `POSTGRES_DB`          | Sí          | Base de datos principal.                                                                           | `observa`                     |
| `POSTGRES_HOST`        | No          | Host de PostgreSQL. Por defecto `127.0.0.1` (en Windows, `localhost` hace cada conexión ~2 s más lenta). | `127.0.0.1`           |
| `POSTGRES_PORT`        | No          | Puerto de PostgreSQL. Por defecto `5432`.                                                          | `5432`                        |
| `TEST_DATABASE_URL`    | No          | Base para los tests del repositorio. Sin ella, esos tests se saltan.                               | ver abajo                     |

Ejemplo completo para desarrollo en local con el frontend en `http://localhost:5173`:

```dotenv
LANGFUSE_PUBLIC_KEY=pk-lf-xxxxxxxx
LANGFUSE_SECRET_KEY=sk-lf-xxxxxxxx
LANGFUSE_HOST=https://cloud.langfuse.com
CORS_ALLOWED_ORIGINS=["http://localhost:5173"]
POSTGRES_USER=observa
POSTGRES_PASSWORD=cambia-esto
POSTGRES_DB=observa
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
TEST_DATABASE_URL=postgresql+asyncpg://observa:cambia-esto@127.0.0.1:5432/observa_test
```

Genera una contraseña aleatoria con `uv run python -c "import secrets; print(secrets.token_urlsafe(24))"`.

- La configuración se valida al arrancar: si falta una variable obligatoria o tiene un valor inválido, el servidor no arranca y dice cuál es.
- Cada origen CORS debe ser exacto (`http(s)://host[:puerto]`), sin `*`, sin ruta y sin barra final. `localhost` y `127.0.0.1` son orígenes distintos: pon el que uses en el navegador.
- `.env` está en `.gitignore`; no lo subas nunca.

## PostgreSQL

```bash
docker compose up -d --wait       # arranca PostgreSQL 17 (solo accesible desde esta máquina)
uv run alembic upgrade head       # crea o actualiza las tablas
```

- Los datos persisten en el volumen `backend_postgres-data`. `docker compose down` para el contenedor sin borrarlos; `docker compose down -v` los borra.
- Al crear el volumen por primera vez se crea también la base `observa_test`, que usan los tests (`docker/postgres/init/`).
- Nueva migración tras cambiar `app/infrastructure/database/tables.py`: `uv run alembic revision --autogenerate -m "describe el cambio"`, revisarla y `uv run alembic upgrade head`.
- Solo se guardan métricas y metadatos (coste, tokens, latencia, entorno, tags, nombre/tipo/nivel/modelo de cada paso). Nunca entradas ni salidas.

## Sincronización con Langfuse

Los endpoints de trazas leen siempre de PostgreSQL. La sincronización copia las trazas de Langfuse a la base:

- **Escaneos diarios** a las horas de `SYNC_SCHEDULE` (por defecto 08:00, 14:00 y 20:00, hora de `SYNC_TIMEZONE`), con APScheduler dentro del propio backend. Si el equipo estaba suspendido a la hora de un escaneo, se lanza al despertar (hasta 1 h después).
- **Escaneo inicial** al arrancar, solo si nunca se ha sincronizado (lee los últimos `SYNC_INITIAL_LOOKBACK_DAYS` días).
- **Sincronizar ahora**: `POST /api/v1/sync`.
- Cada escaneo lee desde donde acabó el último correcto **menos 1 h de solape** hasta ahora. Guardar es idempotente (una traza leída dos veces se actualiza, no se duplica). Un escaneo fallido queda registrado pero no avanza el marcador: el siguiente recupera el hueco.
- Historial en la tabla `sync_runs`. Nunca hay dos escaneos a la vez.

| Variable                     | Por defecto                     | Descripción                                                  |
| ---------------------------- | ------------------------------- | ------------------------------------------------------------ |
| `SYNC_ENABLED`               | `true`                          | `false` desactiva los escaneos automáticos (no el manual).   |
| `SYNC_SCHEDULE`              | `["08:00","14:00","20:00"]`     | Horas locales de los escaneos, lista JSON. `[]` = ninguno.   |
| `SYNC_TIMEZONE`              | `Europe/Madrid`                 | Zona horaria IANA de esas horas.                             |
| `SYNC_INITIAL_LOOKBACK_DAYS` | `30`                            | Días hacia atrás que lee el primer escaneo.                  |

## Perfiles

Un perfil agrupa las trazas de una herramienta o agente bajo un nombre de negocio («Asistente de la web»).

- **Regla**: entorno (por defecto `production`), nombre de traza (opcional) y tags (opcional). Una traza encaja si coincide el entorno, el nombre (si se fija) y lleva todos los tags de la regla (puede llevar más). El `userId` no se usa.
- **Varias reglas encajan**: gana la más específica (nombre + nº de tags); en empate, el perfil más antiguo.
- **Sin clasificar**: las trazas que no encajan en ningún perfil.
- **Autodescubrimiento** (`GET /profiles/discover`): agrupa las trazas sin clasificar por entorno + nombre + tags y las propone, de mayor a menor. No crea nada; las trazas `(unnamed)` (sin paso raíz) no se proponen.
- Las trazas se **reclasifican** al crear o cambiar la regla de un perfil y tras cada sincronización.
- **Archivar**: deja de vigilarse, conserva su historial y sigue recibiendo sus trazas nuevas. **Eliminar**: borra el perfil y, en cascada, sus trazas y observaciones.

## Línea base y alertas

Cálculo determinista (sin IA) tras cada escaneo, para cada perfil **activo** (los archivados no se vigilan).

**Línea base** (lo normal del perfil): mediana y percentil 95 —«el techo de lo normal»— con interpolación lineal y aritmética decimal exacta.

- **Por traza**: coste, tokens de entrada, tokens de salida y latencia de las últimas 1000 trazas.
- **Por día** (días naturales en `MONITORING_TIMEZONE`): número de consultas y coste de los últimos 30 días completos; los días sin trazas cuentan como 0 y el día en curso no cuenta.
- **Aprendiendo** hasta reunir 100 trazas: sin alertas de anomalía (el presupuesto sí avisa). La vigilancia diaria necesita además 7 días completos.
- Al recalcular se dejan fuera los valores que la línea base anterior consideraba críticos.
- **Reaprender** (`POST /profiles/{id}/relearn`) tras cambiar de modelo o de prompt: solo cuentan las trazas posteriores.

**Umbrales** (por defecto; ajustables por perfil con `PATCH /profiles/{id}/thresholds`):

| Umbral | Por defecto | Se aplica a |
| --- | --- | --- |
| Aviso | > 1 × p95 | coste y latencia por traza; consultas y coste por día |
| Crítico | > 2 × p95 | lo mismo |
| Presupuesto diario | sin presupuesto | coste del día; avisa siempre, también aprendiendo |
| Mínimo para molestar | 0,01 $ por traza · 0,10 $ por día | solo alertas de coste |
| Periodo de silencio | 24 h | misma alerta (perfil, tipo, nivel, métrica y día); subir de aviso a crítico la rompe |

- Las comparaciones son estrictas: un valor igual al umbral es normal. Si el p95 es 0, esa métrica no se evalúa.
- Una alerta por traza agrupa todas las trazas que cruzan en un escaneo (cuántas, la peor y su valor).
- Cada escaneo solo juzga datos de las últimas 24 h, para no avisar de datos antiguos tras una carga inicial.
- Las alertas se guardan en `alerts` (`GET /alerts`) con `notified_at` vacío hasta que se envíen.

| Variable              | Por defecto     | Descripción                                          |
| --------------------- | --------------- | ---------------------------------------------------- |
| `MONITORING_TIMEZONE` | `Europe/Madrid` | Zona horaria de los días de la línea base y alertas. |

## Arranque

```bash
uv run uvicorn app.main:app --reload
```

La API queda en `http://127.0.0.1:8000`:

- Swagger: http://127.0.0.1:8000/docs
- OpenAPI (contrato con el frontend): http://127.0.0.1:8000/openapi.json

Para comprobar que sincroniza y lee tus datos:

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/sync"
curl "http://127.0.0.1:8000/api/v1/traces/summary?range=7d"
```

## API

| Endpoint                      | Descripción                                                                  |
| ----------------------------- | ---------------------------------------------------------------------------- |
| `GET /api/v1/traces`          | Trazas del rango, de la más reciente a la más antigua.                       |
| `GET /api/v1/traces/summary`  | Número de trazas, coste total, tokens totales y latencia media.              |
| `POST /api/v1/sync`           | Sincroniza ahora; espera a que acabe y devuelve la ejecución.                |
| `GET /api/v1/sync`            | Si hay una sincronización en curso, la última ejecución y la última correcta. |
| `GET /api/v1/profiles`        | Perfiles con su número de trazas, más el grupo «Sin clasificar».             |
| `GET /api/v1/profiles/discover` | Grupos de trazas sin perfil (entorno + nombre + tags) propuestos como perfiles. |
| `POST /api/v1/profiles`       | Crea un perfil (`name` + `rule`) y clasifica las trazas guardadas.           |
| `PATCH /api/v1/profiles/{id}` | Renombra y/o cambia la regla (reclasifica si cambia la regla).               |
| `POST /api/v1/profiles/{id}/archive` · `/unarchive` | Deja de vigilar el perfil (conserva su historial) o lo reactiva. |
| `DELETE /api/v1/profiles/{id}` | Borra el perfil **y todas sus trazas**. No se puede deshacer.               |
| `GET /api/v1/profiles/{id}/baseline` | Aprendiendo o listo, mediana y p95 por traza y por día.               |
| `PATCH /api/v1/profiles/{id}/thresholds` | Ajusta umbrales (`"daily_budget": null` quita el presupuesto).    |
| `POST /api/v1/profiles/{id}/relearn` | Vuelve a aprender desde ahora.                                        |
| `GET /api/v1/alerts`          | Alertas, de la más reciente a la más antigua (`profile_id`, `since`, `limit`). |

Los de trazas aceptan `range` = `24h` (por defecto), `7d` o `30d`.

Errores (`error.code` en el cuerpo):

- `503` `database_unavailable`: PostgreSQL no responde. ¿Está `docker compose up -d`?
- `502` `trace_source_auth_failed` (solo `POST /sync`): Langfuse ha rechazado las claves. Revisa `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` y que `LANGFUSE_HOST` sea la región de tu proyecto. Los escaneos automáticos que fallan por esto quedan en `GET /sync` (`last_run.error_code`).
- `502` `trace_source_unavailable` (solo `POST /sync`): cualquier otro fallo al hablar con Langfuse.
- `409` `sync_already_running` (solo `POST /sync`): ya hay una sincronización en curso.
- `404` `profile_not_found`: no existe un perfil con ese id.
- `409` `profile_name_taken`: ya hay un perfil con ese nombre (sin distinguir mayúsculas).
- `400` `invalid_thresholds`: los umbrales resultantes serían incoherentes (p. ej. aviso ≥ crítico).
- `422`: parámetro o cuerpo inválido (p. ej. `range=1y`, nombre vacío).

## Calidad

```bash
uv run ruff check
uv run ruff format --check
uv run mypy
uv run pytest
```

Los tests no llaman a Langfuse ni usan tu base principal: usan credenciales falsas y fuentes de datos simuladas. Los del repositorio usan PostgreSQL real en `TEST_DATABASE_URL` (aplican las migraciones y vacían las tablas en cada test); si no está configurada o el contenedor está parado, se saltan indicando el motivo.

Hooks de pre-commit (Ruff y mypy, más comprobaciones básicas de archivos):

```bash
uv run pre-commit install
uv run pre-commit run --all-files
```
