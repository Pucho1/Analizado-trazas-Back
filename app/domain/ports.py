from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from app.domain.models import SyncRun, Trace


class TraceSource(Protocol):
    """Any provider of traces (Langfuse, LangSmith, ...)."""

    async def list_traces(self, start: datetime, end: datetime) -> Sequence[Trace]:
        """Return every trace whose timestamp falls within [start, end].

        Implementations handle their own pagination and raise
        `TraceSourceAuthenticationError` when the credentials are rejected or
        `TraceSourceError` for any other failure.
        """
        ...


class TraceRepository(Protocol):
    """Persistent storage of trace metrics (never their content)."""

    async def save_traces(self, traces: Sequence[Trace]) -> None:
        """Insert or update the given traces, idempotently.

        A trace that already exists is overwritten and its observations are replaced
        by the ones given, so saving the same trace twice never duplicates anything.
        """
        ...

    async def list_traces(self, start: datetime, end: datetime) -> Sequence[Trace]:
        """Return every stored trace whose timestamp falls within [start, end].

        Traces include their observations. Raises `RepositoryError` if the storage
        is unavailable (as does `save_traces`).
        """
        ...


class SyncRunRepository(Protocol):
    """History of synchronisations; the last successful one is the sync marker."""

    async def save(self, run: SyncRun) -> None:
        """Record a finished run."""
        ...

    async def latest(self, source: str) -> SyncRun | None:
        """The most recent run of `source`, whatever its outcome; None if never run."""
        ...

    async def latest_successful(self, source: str) -> SyncRun | None:
        """The successful run of `source` that reached furthest; None if none succeeded."""
        ...
