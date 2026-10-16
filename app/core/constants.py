from datetime import timedelta

# Prefix under which every v1 endpoint is mounted.
API_V1_PREFIX = "/api/v1"

# CORS. The API is read-only and has no login yet, so only GET without credentials.
CORS_ALLOWED_METHODS = ("GET",)
# A browser origin: scheme, host and optional port; no wildcard, path or trailing slash.
CORS_ORIGIN_PATTERN = r"https?://[^/\s:*]+(:\d+)?"

# Name given to traces whose source does not report one (e.g. their root span never arrived).
UNNAMED_TRACE_NAME = "(unnamed)"

# Langfuse public API. v2 observations is the only per-trace read path that will survive
# the removal of `GET /api/public/traces` on Langfuse Cloud.
LANGFUSE_OBSERVATIONS_PATH = "/api/public/v2/observations"
LANGFUSE_OBSERVATION_FIELDS = "core,basic,usage,trace_context"
LANGFUSE_PAGE_SIZE = 1000
LANGFUSE_REQUEST_TIMEOUT_SECONDS = 30.0
# Observations are filtered by their own start time, so we look back a bit further to
# capture every observation of traces that started near the beginning of the window.
LANGFUSE_FETCH_MARGIN = timedelta(hours=1)
