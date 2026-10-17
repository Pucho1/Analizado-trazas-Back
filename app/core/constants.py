from datetime import timedelta

# Prefix under which every v1 endpoint is mounted.
API_V1_PREFIX = "/api/v1"

# CORS. No login yet, so no credentials.
CORS_ALLOWED_METHODS = ("GET", "POST", "PATCH", "DELETE")
# A browser origin: scheme, host and optional port; no wildcard, path or trailing slash.
CORS_ORIGIN_PATTERN = r"https?://[^/\s:*]+(:\d+)?"

# Name given to traces whose source does not report one (e.g. their root span never arrived).
UNNAMED_TRACE_NAME = "(unnamed)"
# Environment Langfuse assigns to traces sent without one.
DEFAULT_ENVIRONMENT = "default"

# Profiles. Only this environment is monitored unless a profile says otherwise.
MONITORED_ENVIRONMENT = "production"
PROFILE_NAME_MAX_LENGTH = 100

# Langfuse public API. v2 observations is the only per-trace read path that will survive
# the removal of `GET /api/public/traces` on Langfuse Cloud.
LANGFUSE_OBSERVATIONS_PATH = "/api/public/v2/observations"
# core: ids, times, type · basic: name, level, environment, root flag · usage: tokens, cost
# trace_context: tags, traceName · model: model. Never `io` (inputs and outputs).
LANGFUSE_OBSERVATION_FIELDS = "core,basic,usage,trace_context,model"
LANGFUSE_PAGE_SIZE = 1000
LANGFUSE_REQUEST_TIMEOUT_SECONDS = 30.0
# Observations are filtered by their own start time, so we look back a bit further to
# capture every observation of traces that started near the beginning of the window.
LANGFUSE_FETCH_MARGIN = timedelta(hours=1)

# PostgreSQL. Traces are written in batches so that no statement exceeds the driver's
# limit of 32767 bind parameters.
DATABASE_DRIVER = "postgresql+asyncpg"
DATABASE_SAVE_BATCH_SIZE = 1000

# Synchronisation from Langfuse into PostgreSQL.
SYNC_SOURCE_LANGFUSE = "langfuse"
# Each scan starts this long before where the previous successful one ended, so that
# traces still in progress at the previous scan are read again once complete.
SYNC_OVERLAP = timedelta(hours=1)
# A scan missed while the machine was asleep still runs if it wakes up within this time.
SYNC_MISFIRE_GRACE = timedelta(hours=1)
