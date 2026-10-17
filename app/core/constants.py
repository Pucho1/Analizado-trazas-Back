from datetime import timedelta
from decimal import Decimal

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

# Baselines (deterministic: median and 95th percentile, no LLM).
# A profile is "learning" (no anomaly alerts) until it has this many traces.
LEARNING_TRACES = 100
# Per-trace baseline: the most recent traces of the profile.
BASELINE_MAX_TRACES = 1000
# Per-day baseline: the most recent complete days, and the minimum needed to use it.
BASELINE_DAYS = 30
MIN_BASELINE_DAYS = 7
# Alert thresholds: defaults for new profiles, adjustable per profile.
DEFAULT_WARNING_FACTOR = Decimal(1)  # warning above 1 x p95
DEFAULT_CRITICAL_FACTOR = Decimal(2)  # critical above 2 x p95
DEFAULT_MIN_TRACE_COST = Decimal("0.01")  # USD; cheaper traces never raise cost alerts
DEFAULT_MIN_DAILY_COST = Decimal("0.10")  # USD; cheaper days never raise cost alerts
DEFAULT_SILENCE_HOURS = 24
# Each scan only judges traces and days of at most this far back, so that the first
# scan after a long backfill does not raise alerts about old data.
MONITORING_MAX_LOOKBACK = timedelta(days=1)
# `GET /alerts` page size.
ALERTS_DEFAULT_LIMIT = 100
ALERTS_MAX_LIMIT = 500

# Reports. Business labels of events, like profile names, have a maximum length.
EVENT_LABEL_MAX_LENGTH = 100
# Telegram rejects messages longer than this (characters after entity parsing).
TELEGRAM_MAX_LENGTH = 4096
# Default sections per channel (also seeded by the migration that creates the table).
DEFAULT_EMAIL_SECTIONS = (
    "overview",
    "alerts",
    "business_events",
    "spend_by_profile",
    "period_comparison",
    "text_volume",
)
DEFAULT_TELEGRAM_SECTIONS = ("overview", "alerts", "business_events")

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
