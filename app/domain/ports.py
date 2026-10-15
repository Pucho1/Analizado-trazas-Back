from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from app.domain.models import Trace


class TraceSource(Protocol):
    """Any provider of traces (Langfuse, LangSmith, ...)."""

    async def list_traces(self, start: datetime, end: datetime) -> Sequence[Trace]:
        """Return every trace whose timestamp falls within [start, end].

        Implementations handle their own pagination and raise
        `TraceSourceAuthenticationError` when the credentials are rejected or
        `TraceSourceError` for any other failure.
        """
        ...
