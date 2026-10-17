# Observa — backend

API de Observa: extrae las trazas de Langfuse, calcula los agregados (totales, medias) y los expone por HTTP. Stack: Python 3.14, FastAPI, uv.

Es la única pieza que conoce las claves de Langfuse. El frontend de Observa (repo aparte) solo habla con esta API.

## Requisitos

- [uv](https://docs.astral.sh/uv/getting-started/installation/). Gestiona también la versión de Python (3.14, fijada en `.python-version`): no hace falta instalar Python a mano.
- Un proyecto en Langfuse con un par de claves de API (Langfuse → _Settings_ → _API Keys_).

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

Ejemplo completo para desarrollo en local con el frontend en `http://localhost:5173`:

```dotenv
LANGFUSE_PUBLIC_KEY=pk-lf-xxxxxxxx
LANGFUSE_SECRET_KEY=sk-lf-xxxxxxxx
LANGFUSE_HOST=https://cloud.langfuse.com
CORS_ALLOWED_ORIGINS=["http://localhost:5173"]
```

- La configuración se valida al arrancar: si falta una variable obligatoria o tiene un valor inválido, el servidor no arranca y dice cuál es.
- Cada origen CORS debe ser exacto (`http(s)://host[:puerto]`), sin `*`, sin ruta y sin barra final. `localhost` y `127.0.0.1` son orígenes distintos: pon el que uses en el navegador.
- `.env` está en `.gitignore`; no lo subas nunca.

## Arranque

```bash
uv run uvicorn app.main:app --reload
```

La API queda en `http://127.0.0.1:8000`:

- Swagger: http://127.0.0.1:8000/docs
- OpenAPI (contrato con el frontend): http://127.0.0.1:8000/openapi.json

Para comprobar que lee tus datos de Langfuse:

```bash
curl "http://127.0.0.1:8000/api/v1/traces/summary?range=7d"
```

## API

| Endpoint                      | Descripción                                                     |
| ----------------------------- | --------------------------------------------------------------- |
| `GET /api/v1/traces`          | Trazas del rango, de la más reciente a la más antigua.          |
| `GET /api/v1/traces/summary`  | Número de trazas, coste total, tokens totales y latencia media. |

Ambos aceptan `range` = `24h` (por defecto), `7d` o `30d`.

Errores:

- `502` con `error.code = "trace_source_auth_failed"`: Langfuse ha rechazado las claves. Revisa `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` y que `LANGFUSE_HOST` sea la región de tu proyecto.
- `502` con `error.code = "trace_source_unavailable"`: cualquier otro fallo al hablar con Langfuse (red, caída, respuesta inesperada).
- `422`: parámetro inválido (p. ej. `range=1y`).

## Calidad

```bash
uv run ruff check
uv run ruff format --check
uv run mypy
uv run pytest
```

Los tests no leen el `.env` ni llaman a Langfuse: usan credenciales falsas y fuentes de datos simuladas.

Hooks de pre-commit (Ruff y mypy, más comprobaciones básicas de archivos):

```bash
uv run pre-commit install
uv run pre-commit run --all-files
```
